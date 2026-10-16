import asyncio
import hashlib

from app.agents.runner import run_agent
from app.core.prompts import prompt
from app.core.quote_check import quote_is_supported
import json
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import Case, Source, Fact, Contradiction, ExtractionCache
from app.agents.fact_merge import FactMerger, default_embedder
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.tracking import track_research_call, track_run, stamp_run


def _source_bundle(sources: list[Source], *, with_texts: bool = True) -> list[dict]:
    """with_texts=False drops the raw text and chunks: only fact extraction
    reads them, later stages work on the extracted claims."""
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
            "chunks": [
                {"id": c.id, "text": c.text}
                for c in (s.chunks or [])[
                    : ai_config.source_chunking.max_chunks_per_source_for_extraction
                ]
            ] if with_texts else [],
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
         "chunk_ids": [chunk_key_by_id.get(cid, cid) for cid in f.get("chunk_ids", [])] or f.get("chunk_ids", [])}
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
            "supporting_text": "verbatim passage or null",
            "chunk_ids": [1],
            "quote_status": "verified_direct|reported_quote|paraphrase|uncertain|null",
            "speaker": "name or null",
            "people": ["..."],
            "locations": ["..."],
        }
    ]
}


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

        previous_status = case.status
        case.status = "researching"
        db.commit()
        try:
            facts_data = await self._merge_duplicates(
                case, await self._extract_facts(db, case, sources))
            fact_rows = self._store_facts(db, case, facts_data, sources)

            await self._build_timeline(db, case, fact_rows, sources)

            contradictions = await self._analyze_contradictions(
                db, case, fact_rows, sources, contradiction_hints or []
            )
            n_contra = self._store_contradictions(db, case, contradictions)

            case.status = "researched"
            db.commit()
        except Exception:
            case.status = previous_status
            db.commit()
            raise
        return {"facts": len(fact_rows), "contradictions": n_contra}

    # ------------------------------------------------------------------
    # fact_extractor (cheap)
    # ------------------------------------------------------------------

    async def _extract_facts(self, db: Session, case: Case, sources: list[Source]) -> list[dict]:
        """Single-language batches, extracted concurrently. The bundles are
        read here, in this session; each call then tracks itself in its own."""
        system = prompt("agents/research/extract_facts")
        bundles = [
            _source_bundle(batch) for batch in _extraction_batches(
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
                stamp_run(run, res, "fact_extractor")
                found = data.get("facts", [])
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
        with track_research_call(
            "Fact Merger", f"case={case.id} facts={len(facts)}", case_id=case.id,
        ) as run:
            merged = await merger.merge(facts)
            run.provider, run.model = "embeddings", self.embedder.model
            run.input_tokens, run.output_tokens = self.embedder.total_tokens, 0
            run.estimated_cost_usd = self.embedder.total_cost_usd
            run.output_summary = f"facts={len(merged)} merged={len(facts) - len(merged)}"
        return merged

    def _store_facts(self, db: Session, case: Case, facts_data: list[dict],
                     sources: list[Source]) -> list[Fact]:
        db.query(Fact).filter(Fact.case_id == case.id).delete()
        source_text_by_id = {s.id: _source_text(s) for s in sources}
        rows = []
        for f in facts_data:
            supporting_text = f.get("supporting_text") or None
            cited_text = " ".join(
                source_text_by_id.get(sid, "") for sid in f.get("source_ids", [])
            )
            row = Fact(
                case_id=case.id,
                claim=f["claim"],
                original_claim=f.get("original_claim") or None,
                original_language=(f.get("original_language") or "en")[:20],
                category=f.get("category", "general"),
                confidence=float(f.get("confidence", 0.5)),
                source_ids_json=json.dumps(f.get("source_ids", [])),
                disputed=bool(f.get("disputed", False)),
                quote_verified=quote_is_supported(supporting_text, cited_text),
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
