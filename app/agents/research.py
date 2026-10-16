import asyncio
import hashlib
import logging
from contextlib import contextmanager

from pydantic import BaseModel, ValidationError

from app.agents.research_schemas import (
    ContradictionAnalyzerOutput,
    FactExtractorOutput,
    TimelineBuilderOutput,
)
from app.agents.runner import run_agent
from app.core.prompts import prompt
from app.core.quote_check import quote_is_supported
import json
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import Case, Source, Fact, Contradiction, ExtractionCache
from app.agents.fact_merge import FactMerger, default_embedder
from app.research_engine.embeddings import cosine_matrix
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.tracking import track_research_call, track_run, stamp_run

log = logging.getLogger(__name__)


def _source_bundle(sources: list[Source], *, with_texts: bool = True,
                   chunk_ids_by_source: dict[int, list[int]] | None = None) -> list[dict]:
    """with_texts=False drops the raw text and chunks: only fact extraction
    reads them, later stages work on the extracted claims.

    chunk_ids_by_source (Part 2(e) item 2): which chunk ids to send for a
    source, already selected by relevance (see
    ResearchAgent._select_chunks_for_extraction) instead of the first N in
    document order. Falls back to the first N when not given (callers that
    don't do extraction, e.g. timeline/contradiction prompts with
    with_texts=False, never need this)."""
    def chunks_for(s: Source) -> list:
        if not with_texts:
            return []
        if chunk_ids_by_source is not None:
            wanted = set(chunk_ids_by_source.get(s.id, []))
            return [c for c in (s.chunks or []) if c.id in wanted]
        return (s.chunks or [])[: ai_config.source_chunking.max_chunks_per_source_for_extraction]

    return [
        {
            "id": s.id,
            "title": s.title,
            "url": s.url,
            "type": s.source_type,
            "publisher": s.publisher,
            "reliability_score": s.reliability_score,
            "language": s.language,
            "content_status": s.content_status,
            "source_family": s.source_family,
            "text": (
                s.raw_text
                if with_texts and s.is_authorized_text and not s.chunks else None
            ),
            "summary": s.summary,
            "summary_en": s.summary_en,
            "notes": s.notes,
            "chunks": [{"id": c.id, "text": c.text} for c in chunks_for(s)],
        }
        for s in sources
    ]


def _source_content_key(source_dict: dict) -> str:
    texts = [c["text"] for c in source_dict.get("chunks", [])] or [source_dict.get("text") or ""]
    return hashlib.sha256("\x1f".join(texts).encode("utf-8")).hexdigest()


def _chunk_content_key(chunk_dict: dict) -> str:
    return hashlib.sha256((chunk_dict.get("text") or "").encode("utf-8")).hexdigest()


def _bundle_has_cacheable_content(bundle: list[dict]) -> bool:
    """A source with no real text (summary-only, fetch failed, a test
    fixture with no fixture text) must never be cached: every such source
    hashes identically (empty content), so caching it would silently
    return one source's facts for a completely different one."""
    return any(
        any(c.get("text", "").strip() for c in s.get("chunks", []))
        or (s.get("text") or "").strip()
        for s in bundle
    )


def _bundle_content_hash(bundle: list[dict]) -> str:
    """Stable hash of exactly the text a batch sends to the model —
    independent of source/chunk row ids, so the same real-world content
    hashes the same even if it was re-ingested under a different Source
    row (a different case, or a re-fetch of the same page)."""
    keys = sorted(_source_content_key(s) for s in bundle)
    return hashlib.sha256("\x1e".join(keys).encode("utf-8")).hexdigest()


def _facts_for_cache(facts: list[dict], bundle: list[dict]) -> list[dict]:
    """Before storing: replace this bundle's real source/chunk ids in each
    fact with content-hash keys, so the cached result is portable to
    whatever ids the same content has in a future bundle."""
    source_key_by_id = {s["id"]: _source_content_key(s) for s in bundle}
    chunk_key_by_id = {c["id"]: _chunk_content_key(c) for s in bundle for c in s.get("chunks", [])}
    return [
        {**f,
         "source_ids": [source_key_by_id.get(sid, sid) for sid in f.get("source_ids", [])],
         "chunk_ids": [chunk_key_by_id.get(cid, cid) for cid in f.get("chunk_ids", [])]}
        for f in facts
    ]


def _facts_from_cache(cached: list[dict], bundle: list[dict]) -> list[dict]:
    """After a cache hit: replace the stored content-hash keys with this
    bundle's actual real source/chunk ids. Falls back to the stored value
    unchanged if a key isn't found (should not happen — the content hash
    already matched this exact bundle)."""
    id_by_source_key = {_source_content_key(s): s["id"] for s in bundle}
    id_by_chunk_key = {_chunk_content_key(c): c["id"] for s in bundle for c in s.get("chunks", [])}
    return [
        {**f,
         "source_ids": [id_by_source_key.get(key, key) for key in f.get("source_ids", [])],
         "chunk_ids": [id_by_chunk_key.get(key, key) for key in f.get("chunk_ids", [])]}
        for f in cached
    ]


def _extraction_cache_get(content_hash: str, prompt_version: str, model: str) -> list[dict] | None:
    with SessionLocal() as db:
        row = db.query(ExtractionCache).filter_by(
            content_hash=content_hash, prompt_version=prompt_version, model=model,
        ).first()
        return json.loads(row.result_json) if row else None


def _extraction_cache_put(content_hash: str, prompt_version: str, model: str, facts: list[dict]) -> None:
    with SessionLocal() as db:
        # Another concurrent batch may have just written the same key.
        existing = db.query(ExtractionCache).filter_by(
            content_hash=content_hash, prompt_version=prompt_version, model=model,
        ).first()
        if existing:
            return
        db.add(ExtractionCache(
            content_hash=content_hash, prompt_version=prompt_version, model=model,
            result_json=json.dumps(facts, ensure_ascii=False),
        ))
        db.commit()


def _extractable_content(source: Source) -> str:
    """Everything _source_bundle actually sends the extractor for this
    source, concatenated — chunks/raw text plus summary/notes, since a
    summary_only source (no full text) is still extracted from its
    summary in production. Used to decide whether a source has anything
    worth extracting at all; narrower than this for quote verification,
    where only the full text can confirm a verbatim quote."""
    parts = [_source_text(source)]
    parts.extend(filter(None, (source.summary, source.summary_en, source.notes)))
    return " ".join(p for p in parts if p)


def _drop_unknown_fact_ids(fact: dict, valid_source_ids: set[int], valid_chunk_ids: set[int]) -> dict:
    """The model is only ever shown this bundle's own source/chunk ids, but
    a hallucinated id in its reply must never reach the cache or the DB: a
    cached fact's ids are later remapped onto a *different* bundle's real
    ids (see _facts_for_cache/_facts_from_cache), so an out-of-bundle id
    passed through here would silently resolve to the wrong source/chunk
    (or a stale, case-specific raw id) on a future cross-case cache hit."""
    source_ids = [sid for sid in fact.get("source_ids", []) if sid in valid_source_ids]
    chunk_ids = [cid for cid in fact.get("chunk_ids", []) if cid in valid_chunk_ids]
    return {**fact, "source_ids": source_ids, "chunk_ids": chunk_ids}


def _resolve_fact_span(fact: dict, chunk_text_by_id: dict[int, str]) -> dict:
    """Part 2(e) item 4: the model points at its evidence (a chunk id plus
    character offsets) instead of retyping it — resolved here, once, right
    at the model-response boundary, into the same plain "supporting_text"
    string the rest of the pipeline (merge, cache, storage, serializers)
    already expects, so nothing downstream needs to know spans exist.

    A fact with no "supporting_span" key (older/test-provided facts that
    still set "supporting_text" directly) is left untouched — verified
    later by app.core.quote_check's fuzzy match against the full cited
    source text, exactly as before this change.
    """
    if "supporting_span" not in fact:
        return fact
    span = fact.pop("supporting_span")
    if not isinstance(span, dict):
        fact["supporting_text"] = None
        fact["_quote_verified"] = None
        return fact
    chunk_text = chunk_text_by_id.get(span.get("chunk_id"))
    if chunk_text is None:
        fact["supporting_text"] = None
        fact["_quote_verified"] = None
        return fact
    start, end = span.get("start"), span.get("end")
    valid = (isinstance(start, int) and isinstance(end, int)
             and 0 <= start < end <= len(chunk_text))
    fact["supporting_text"] = chunk_text[start:end] if valid else None
    fact["_quote_verified"] = valid if valid else False
    return fact


def _source_text(source: Source) -> str:
    """All text a fact's supporting_text could plausibly quote: every
    extraction-eligible chunk, plus the raw page text if the extractor saw
    it directly (no chunks)."""
    parts = [c.text for c in (source.chunks or [])]
    if source.is_authorized_text and not source.chunks and source.raw_text:
        parts.append(source.raw_text)
    return " ".join(parts)


_FACT_SCHEMA = {
    "facts": [
        {
            "claim": "...",
            "category": "timeline|person|evidence|investigation|legal|context",
            "confidence": 0.0,
            "source_ids": [1, 2],
            "disputed": False,
            "event_date": "YYYY-MM-DD or null",
            "original_claim": "claim in the source language or null",
            "original_language": "en",
            "narrative_value": "human_detail|scene_detail|investigation_detail|...",
            "evidence_strength": "primary|strong_secondary|secondary|tertiary|weak|disputed",
            "supporting_span": {"chunk_id": 1, "start": 0, "end": 1},
            "chunk_ids": [1],
            "quote_status": "verified_direct|reported_quote|paraphrase|uncertain|null",
            "speaker": "name or null",
            "people": ["..."],
            "locations": ["..."],
        }
    ]
}


@contextmanager
def _track_embedding_cost(embedder, case_id: int, stage: str, detail: str):
    """Book only this block's own embedding usage under `stage` — never the
    embedder's lifetime totals. `embedder.total_tokens`/`total_cost_usd`
    accumulate for as long as the instance lives (app.research_engine.
    embeddings.EmbeddingClient never resets them); a single ResearchAgent
    shares one embedder across source selection, chunk selection and fact
    merging within one run(), so reading the raw totals at the end (as
    _merge_duplicates used to) silently folds every earlier stage's
    embedding cost into whichever stage happens to read them last."""
    tokens_before, cost_before = embedder.total_tokens, embedder.total_cost_usd
    with track_research_call(stage, detail, case_id=case_id) as run:
        yield run
        run.provider, run.model = "embeddings", embedder.model
        run.input_tokens = embedder.total_tokens - tokens_before
        run.output_tokens = 0
        run.estimated_cost_usd = embedder.total_cost_usd - cost_before


def _sum_optional(a: float | None, b: float | None) -> float | None:
    """Sum two provider-reported usage values, None meaning "not reported"
    rather than zero; None only when neither side reported anything
    (mirrors app.providers.generation.openai_compat's own helper of the
    same name — duplicated rather than imported across a provider/agent
    layer boundary for a 4-line function)."""
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


async def _validate_or_repair(
    name: str, gen, user: str, system: str, schema: type[BaseModel], data: dict, res,
) -> tuple[dict, object]:
    """Part 2(b): every extraction/analysis stage's JSON is validated
    against its own pydantic schema, not just checked for being parseable
    JSON (generate_structured already guarantees that part, with its own
    one-shot repair — see app.providers.generation.openai_compat). A
    schema-shape failure (missing field, wrong type, malformed nested
    object) gets exactly one repair call telling the model what failed;
    still-invalid after that is a recorded failure, never a silent skip
    (re-raised as the same GenerationError the JSON-parse path already
    uses, carrying the summed real billed usage of both calls, so no
    spend is lost from the ledger).

    Takes the call site's own already-made run_agent() result rather than
    making that first call itself: tests/test_architecture.py statically
    scans the source for a literal run_agent call naming each agent, to
    confirm every agent defined in config/agents.json is actually used —
    a name passed through a shared helper as a variable wouldn't match
    that scan. Only the (rare) repair call is made in here.

    Returns the validated data as a plain dict, not the pydantic model, so
    every existing call site keeps reading it exactly as before — dumped
    with exclude_unset=True so a field the model never set (e.g. an old
    fact giving "supporting_text" directly, with no "supporting_span" key
    at all) stays ABSENT rather than appearing as an explicit null; several
    downstream checks (_resolve_fact_span's backward-compat path chief
    among them) distinguish "key absent" from "key present but null"."""
    try:
        return schema.model_validate(data).model_dump(exclude_unset=True), res
    except ValidationError as first_error:
        log.warning(
            "schema validation failed for agent=%s model=%s; retrying with "
            "the validation errors: %.300s", name, res.model, str(first_error),
        )
        repair_system = (
            system
            + "\n\nCRITICAL: your previous JSON output failed schema validation "
              f"with these errors:\n{str(first_error)[:1000]}\n"
              "Return ONLY corrected JSON with the same meaning, fixed to match "
              "the schema exactly — no prose, no markdown fences."
        )
        repaired_data, repaired_res = await run_agent(name, gen, user, system=repair_system)
        try:
            return schema.model_validate(repaired_data).model_dump(exclude_unset=True), repaired_res
        except ValidationError as second_error:
            log.error(
                "agent=%s model=%s returned schema-invalid output after one "
                "repair attempt: %.300s", name, repaired_res.model, str(second_error),
            )
            raise GenerationError(
                "invalid_output",
                f"Model {repaired_res.model} returned schema-invalid output for "
                f"agent {name} after one repair attempt: {str(second_error)[:300]}",
                provider=repaired_res.provider, model=repaired_res.model,
                input_tokens=_sum_optional(res.input_tokens, repaired_res.input_tokens),
                output_tokens=_sum_optional(res.output_tokens, repaired_res.output_tokens),
                total_tokens=_sum_optional(res.total_tokens, repaired_res.total_tokens),
                cost_usd=_sum_optional(res.cost_usd, repaired_res.cost_usd),
            ) from second_error


def _extraction_batches(sources: list[Source], size: int) -> list[list[Source]]:
    """Single-language batches of at most `size` sources: one call over every
    source overruns the output limit and starves all but the first language."""
    by_language: dict[str, list[Source]] = {}
    for s in sources:
        by_language.setdefault(s.language or "en", []).append(s)
    return [group[i:i + size] for group in by_language.values()
            for i in range(0, len(group), size)]


class ResearchAgent:
    """Fact extraction -> timeline dating -> contradiction analysis.

    Each stage uses its own configured role. The provider's *possible* facts and
    contradictions are never promoted directly — they arrive only as hints
    the analyzer must corroborate against source material.
    """

    def __init__(self):
        self.gen = get_generation_provider()
        self.embedder = default_embedder()

    async def run(
        self,
        db: Session,
        case: Case,
        contradiction_hints: list | None = None,
    ) -> dict:
        sources = db.query(Source).filter(Source.case_id == case.id).all()
        extraction_sources = await self._select_sources_for_extraction(db, case, sources)

        previous_status = case.status
        case.status = "researching"
        db.commit()
        try:
            facts_data = await self._merge_duplicates(
                case, await self._extract_facts(db, case, extraction_sources))
            fact_rows = self._store_facts(db, case, facts_data, sources)

            all_facts = db.query(Fact).filter(Fact.case_id == case.id).all()
            stage_facts = self._facts_for_stage(db, case, all_facts, sources)
            await self._build_timeline(db, case, stage_facts, sources)

            contradictions = await self._analyze_contradictions(
                db, case, stage_facts, sources, contradiction_hints or []
            )
            n_contra = self._store_contradictions(db, case, contradictions)

            case.status = "researched"
            db.commit()
        except Exception:
            case.status = previous_status
            db.commit()
            raise
        return {"facts": len(all_facts), "contradictions": n_contra}

    # ------------------------------------------------------------------
    # Part 2(e) item 1: select before extracting
    # ------------------------------------------------------------------

    async def _select_sources_for_extraction(
        self, db: Session, case: Case, sources: list[Source]
    ) -> list[Source]:
        """Rank, dedupe and cap before any extraction call. Every skipped
        source is recorded with its reason (never silently dropped);
        re-running research clears stale reasons first, since which
        sources are kept can change with newly-added sources."""
        for s in sources:
            s.extraction_skip_reason = None

        with_content = []
        for s in sources:
            if _extractable_content(s).strip():
                with_content.append(s)
            else:
                s.extraction_skip_reason = "no extractable content (no chunks, raw text or summary)"
        if not with_content:
            db.flush()
            return []

        # Dedup by content similarity, WITHIN THE SAME LANGUAGE ONLY: the
        # same real event reported independently in two languages is
        # valuable corroboration, not a syndicated copy, and each is kept
        # so facts can be merged with both languages' original quotes
        # (app.agents.fact_merge). A near-duplicate/syndicated copy is
        # skipped in favour of the more reliable (or longer) source.
        texts = [_extractable_content(s)[:4000] for s in with_content]
        with _track_embedding_cost(
            self.embedder, case.id, "Source Selection", f"case={case.id} sources={len(with_content)}",
        ) as run:
            vectors = await self.embedder.embed(texts)
            run.output_summary = f"candidates={len(with_content)}"
        sims = cosine_matrix(vectors, vectors)
        threshold = ai_config.source_chunking.duplicate_source_min_similarity
        kept = [True] * len(with_content)
        for i in range(len(with_content)):
            if not kept[i]:
                continue
            for j in range(i + 1, len(with_content)):
                if (not kept[j] or sims[i][j] < threshold
                        or with_content[i].language != with_content[j].language):
                    continue
                key_i = (with_content[i].reliability_score, len(texts[i]))
                key_j = (with_content[j].reliability_score, len(texts[j]))
                loser = j if key_i >= key_j else i
                winner = with_content[j if loser == i else i]
                with_content[loser].extraction_skip_reason = (
                    f"near-duplicate/syndicated copy of source {winner.id}"
                )
                kept[loser] = False
                if loser == i:
                    break
        deduped = [s for s, k in zip(with_content, kept) if k]

        # Rank the rest by reliability then content richness; keep only
        # the configured top N per case.
        ranked = sorted(
            deduped,
            key=lambda s: (s.reliability_score, len(_extractable_content(s))),
            reverse=True,
        )
        limit = ai_config.source_chunking.max_sources_per_case_for_extraction
        kept_ids = {s.id for s in ranked[:limit]}
        for s in ranked[limit:]:
            s.extraction_skip_reason = f"ranked below the per-case extraction limit ({limit})"
        db.flush()
        return [s for s in sources if s.id in kept_ids]

    async def _select_chunks_for_extraction(
        self, case: Case, sources: list[Source]
    ) -> dict[int, list[int]]:
        """Part 2(e) item 2: cut the text, not the source. A source with
        more chunks than the configured per-source limit sends only the
        ones most relevant to the case (by embedding similarity to the
        case's own identity — canonical_title and summary, the only
        "central question" proxy available this early; EditorialBlueprint's
        real central_question does not exist until after the master is
        written), never just the first N in document order (mostly
        boilerplate for a long page).

        Chunks are returned in their original document order (narrative
        coherence for the extractor), not similarity-ranked order.
        """
        limit = ai_config.source_chunking.max_chunks_per_source_for_extraction
        query = " ".join(filter(None, (case.canonical_title, case.summary)))
        result: dict[int, list[int]] = {}
        to_rank: list[Source] = []
        for s in sources:
            chunks = s.chunks or []
            if len(chunks) <= limit:
                result[s.id] = [c.id for c in chunks]
            else:
                to_rank.append(s)
        if not to_rank or not query.strip():
            for s in to_rank:
                result[s.id] = [c.id for c in (s.chunks or [])[:limit]]
            return result

        # Part 2(d): independent per-source ranking calls, concurrent and
        # bounded (previously one source at a time) — each source's
        # relevance ranking depends on nothing from any other source's,
        # so there is no shared state to race (unlike per-language
        # research's novelty dedup — see ROADMAP.md Part 2(d)).
        limit_sem = asyncio.Semaphore(ai_config.source_chunking.extraction_concurrency)

        async def rank_one(s: Source) -> tuple[int, list[int]]:
            chunks = s.chunks or []
            # The query and the chunks must come from the same embed() call:
            # a fresh call can return a different vector space (dimension
            # even varies with a fake/test embedder keyed on batch size).
            async with limit_sem:
                vectors = await self.embedder.embed([query] + [c.text for c in chunks])
            query_vector, chunk_vectors = vectors[:1], vectors[1:]
            sims = cosine_matrix(query_vector, chunk_vectors)[0]
            ranked = sorted(range(len(chunks)), key=lambda i: sims[i], reverse=True)[:limit]
            return s.id, [chunks[i].id for i in sorted(ranked)]

        with _track_embedding_cost(
            self.embedder, case.id, "Chunk Selection",
            f"case={case.id} sources_ranked={len(to_rank)}",
        ) as run:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(rank_one(s)) for s in to_rank]
            for task in tasks:
                sid, chunk_ids = task.result()
                result[sid] = chunk_ids
            run.output_summary = f"sources_ranked={len(to_rank)}"
        return result

    # ------------------------------------------------------------------
    # fact_extractor (cheap)
    # ------------------------------------------------------------------

    async def _extract_facts(self, db: Session, case: Case, sources: list[Source]) -> list[dict]:
        """Single-language batches, extracted concurrently. The bundles are
        read here, in this session; each call then tracks itself in its own."""
        system = prompt("agents/research/extract_facts")
        chunk_ids_by_source = await self._select_chunks_for_extraction(case, sources)
        bundles = [
            _source_bundle(batch, chunk_ids_by_source=chunk_ids_by_source)
            for batch in _extraction_batches(
                sources, ai_config.source_chunking.max_sources_per_extraction_call)
        ]
        limit = asyncio.Semaphore(ai_config.source_chunking.extraction_concurrency)

        async def run(number: int, bundle: list[dict]) -> list[dict]:
            async with limit:
                return await self._extract_batch(
                    case, bundle, system, f"{number}/{len(bundles)}")

        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(run(n, bundle)) for n, bundle in enumerate(bundles, 1)]
        except ExceptionGroup as failed:
            raise failed.exceptions[0] from None
        return [fact for task in tasks for fact in task.result()]

    async def _extract_batch(self, case: Case, bundle: list[dict], system: str,
                             label: str) -> list[dict]:
        """One call for the batch; if the reply is unusable (truncated, empty,
        malformed) the batch is halved and each half retried. A single failing
        source raises.

        Part 2(e) item 3: the exact same content, prompt and model is never
        extracted twice — a restart, or the same source shared by another
        case, is served from app.db.models.ExtractionCache with no provider
        call at all."""
        cacheable = _bundle_has_cacheable_content(bundle)
        content_hash = _bundle_content_hash(bundle) if cacheable else None
        prompt_version = hashlib.sha256(system.encode("utf-8")).hexdigest()
        model = ai_config.model_for("fact_extractor")
        if cacheable:
            cached = _extraction_cache_get(content_hash, prompt_version, model)
            if cached is not None:
                return _facts_from_cache(cached, bundle)

        user = json.dumps(
            {"case": case.canonical_title, "sources": bundle, "output_schema": _FACT_SCHEMA},
            ensure_ascii=False,
        )
        try:
            with track_research_call(
                "Fact Extractor", f"case={case.id} batch={label} sources={len(bundle)}",
                case_id=case.id,
            ) as run:
                data, res = await run_agent("research.extract_facts", self.gen, user, system=system)
                data, res = await _validate_or_repair(
                    "research.extract_facts", self.gen, user, system, FactExtractorOutput, data, res)
                stamp_run(run, res, "fact_extractor")
                chunk_text_by_id = {c["id"]: c["text"] for s in bundle for c in s.get("chunks", [])}
                valid_source_ids = {s["id"] for s in bundle}
                valid_chunk_ids = set(chunk_text_by_id)
                found = [_resolve_fact_span(f, chunk_text_by_id) for f in data.get("facts", [])]
                found = [_drop_unknown_fact_ids(f, valid_source_ids, valid_chunk_ids) for f in found]
                run.output_summary = f"facts={len(found)}"
            if cacheable:
                _extraction_cache_put(content_hash, prompt_version, model, _facts_for_cache(found, bundle))
            return found
        except (GenerationError, ValueError):
            if len(bundle) == 1:
                raise
            middle = len(bundle) // 2
            return (await self._extract_batch(case, bundle[:middle], system, f"{label}a")
                    + await self._extract_batch(case, bundle[middle:], system, f"{label}b"))

    async def _merge_duplicates(self, case: Case, facts: list[dict]) -> list[dict]:
        """One fact per event: the same claim extracted from sources in several
        languages is merged (embedding cost is part of the case ledger)."""
        if len(facts) < 2:
            return facts
        merger = FactMerger(self.embedder, ai_config.source_chunking.merge_min_similarity)
        with _track_embedding_cost(
            self.embedder, case.id, "Fact Merger", f"case={case.id} facts={len(facts)}",
        ) as run:
            merged = await merger.merge(facts)
            run.output_summary = f"facts={len(merged)} merged={len(facts) - len(merged)}"
        return merged

    def _store_facts(self, db: Session, case: Case, facts_data: list[dict],
                     sources: list[Source]) -> list[Fact]:
        # Facts promoted from video transcripts are not rebuilt from web sources:
        # deleting them lost the videos' claims and left their clusters pointing
        # at facts that no longer exist.
        db.query(Fact).filter(Fact.case_id == case.id, Fact.transcript_claim_ids_json == "[]").delete()
        source_text_by_id = {s.id: _source_text(s) for s in sources}
        rows = []
        for f in facts_data:
            supporting_text = f.get("supporting_text") or None
            if "_quote_verified" in f:
                # Already resolved deterministically in _extract_batch from
                # a chunk id + character span — true by construction (or
                # False for an out-of-bounds span), no fuzzy match needed.
                quote_verified = f["_quote_verified"]
            else:
                cited_text = " ".join(
                    source_text_by_id.get(sid, "") for sid in f.get("source_ids", [])
                )
                quote_verified = quote_is_supported(supporting_text, cited_text)
            row = Fact(
                case_id=case.id,
                claim=f["claim"],
                original_claim=f.get("original_claim") or None,
                original_language=(f.get("original_language") or "en")[:20],
                category=f.get("category", "general"),
                confidence=float(f.get("confidence", 0.5)),
                source_ids_json=json.dumps(f.get("source_ids", [])),
                disputed=bool(f.get("disputed", False)),
                quote_verified=quote_verified,
                event_date=f.get("event_date") or None,
                narrative_value=(f.get("narrative_value") or None),
                evidence_strength=(f.get("evidence_strength") or None),
                supporting_text=supporting_text,
                chunk_ids_json=json.dumps(f.get("chunk_ids") or []),
                quote_status=(f.get("quote_status") or None),
                speaker=(f.get("speaker") or None),
                people_json=json.dumps(
                    [p for p in (f.get("people") or []) if p],
                    ensure_ascii=False,
                ),
                locations_json=json.dumps(
                    [l for l in (f.get("locations") or []) if l],
                    ensure_ascii=False,
                ),
            )
            db.add(row)
            rows.append(row)
        db.flush()
        return rows

    @staticmethod
    def _facts_for_stage(db: Session, case: Case, facts: list[Fact],
                         sources: list[Source]) -> list[Fact]:
        """The timeline and contradiction stages receive at most the configured
        number of facts (video-backed first, then the strongest; disputed and
        contradiction facts always); no Fact row is deleted by the cap."""
        from app.services.evidence_selection import EvidenceSelector

        contradictions = db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
        kept, _ = EvidenceSelector(
            ai_config.evidence_selection.max_facts_per_research_stage, sources,
            contradictions).select(facts)
        return kept

    # ------------------------------------------------------------------
    # timeline_builder (cheap)
    # ------------------------------------------------------------------

    async def _build_timeline(
        self, db: Session, case: Case, fact_rows: list[Fact], sources: list[Source]
    ) -> None:
        """Refine event_date assignments — approximate dates allowed, never invented."""
        if not fact_rows:
            return
        system = prompt("agents/research/build_timeline")
        user = json.dumps(
            {
                "case": case.canonical_title,
                "claims": [
                    {"id": f.id, "claim": f.claim, "current_event_date": f.event_date}
                    for f in fact_rows
                ],
                "sources": _source_bundle(sources, with_texts=False),
                "output_schema": {
                    "timeline": [{"id": 1, "event_date": "YYYY-MM-DD or null"}]
                },
            },
            ensure_ascii=False,
        )
        with track_run(
            db, case.id, "Timeline Builder",
            input_summary=f"claims={len(fact_rows)}",
        ) as run:
            data, res = await run_agent("research.build_timeline", self.gen, user, system=system)
            data, res = await _validate_or_repair(
                "research.build_timeline", self.gen, user, system, TimelineBuilderOutput, data, res)
            stamp_run(run, res, "timeline_builder")
            dated = 0
            by_id = {f.id: f for f in fact_rows}
            for item in data.get("timeline", []):
                fact = by_id.get(item.get("id"))
                if fact and item.get("event_date"):
                    fact.event_date = item["event_date"]
                    dated += 1
            run.output_summary = f"dated={dated}"
        db.commit()

    # ------------------------------------------------------------------
    # contradiction_analyzer (cheap)
    # ------------------------------------------------------------------

    async def _analyze_contradictions(
        self,
        db: Session,
        case: Case,
        fact_rows: list[Fact],
        sources: list[Source],
        hints: list,
    ) -> list[dict]:
        system = prompt("agents/research/analyze_contradictions")
        user = json.dumps(
            {
                "case": case.canonical_title,
                "facts": [{"id": f.id, "claim": f.claim, "disputed": f.disputed} for f in fact_rows],
                "sources": _source_bundle(sources, with_texts=False),
                "research_hints": hints,
                "output_schema": {
                    "contradictions": [
                        {
                            "topic": "...",
                            "description": "version A vs version B, with who says what",
                            "source_ids": [1, 2],
                            "severity": "low|medium|high",
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )
        with track_run(
            db, case.id, "Contradiction Analyzer",
            input_summary=f"facts={len(fact_rows)} hints={len(hints)}",
        ) as run:
            data, res = await run_agent("research.analyze_contradictions", self.gen, user, system=system)
            data, res = await _validate_or_repair(
                "research.analyze_contradictions", self.gen, user, system,
                ContradictionAnalyzerOutput, data, res)
            stamp_run(run, res, "contradiction_analyzer")
            contradictions = data.get("contradictions", [])
            run.output_summary = f"contradictions={len(contradictions)}"
        return contradictions

    def _store_contradictions(
        self, db: Session, case: Case, contradictions: list[dict]
    ) -> int:
        db.query(Contradiction).filter(Contradiction.case_id == case.id).delete()
        for c in contradictions:
            db.add(
                Contradiction(
                    case_id=case.id,
                    topic=c["topic"],
                    description=c["description"],
                    source_ids_json=json.dumps(c.get("source_ids", [])),
                    severity=c.get("severity", "medium"),
                )
            )
        return len(contradictions)
