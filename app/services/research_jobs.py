from app.agents.runner import run_agent
from app.core.prompts import prompt
import json
import time
import re
from datetime import timedelta

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    AgentRun,
    Case,
    DiscoveryCandidate,
    Fact,
    ResearchJob,
    ResearchQuery,
    ResearchResult,
    ResearchSourceLink,
    Source,
)
from app.providers import get_research_provider
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.utils import ensure_utc, utc_now
from app.providers.base import ProviderError, ResearchProvider

def _job_timeout() -> timedelta:
    return timedelta(minutes=ai_config.research.job_timeout_minutes)


def _normalize_title(t: str) -> str:
    return " ".join((t or "").lower().strip().split())


def _fingerprint(title: str, people: list[str] | None = None) -> str:
    base = _normalize_title(title)
    if people:
        base += "|" + "|".join(sorted(_normalize_title(p) for p in people[:4]))
    return base[:200]


def known_case_titles(db: Session) -> list[str]:
    titles = [c.canonical_title for c in db.query(Case.canonical_title).all()]
    titles += [r.title for r in db.query(DiscoveryCandidate.title).all()]
    return [t for t in titles if t]


def _provider_for(job: ResearchJob) -> ResearchProvider:
    return get_research_provider()


def create_job(
    db: Session,
    job_type: str,
    input_data: dict,
    case_id: int | None = None,
    provider: str = "truecrime",
) -> ResearchJob:
    job = ResearchJob(
        case_id=case_id,
        provider=provider,
        job_type=job_type,
        status="queued",
        current_stage="queued",
        profile=ai_config.research.default_profile,
        input_json=json.dumps(input_data, ensure_ascii=False),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _persist_provenance(db: Session, job: ResearchJob, result: dict) -> dict[int, int]:
    """Relational query -> result provenance (Part 5).

    Every executed query and every raw result — accepted or rejected —
    is persisted so audits can replay the run. Returns
    {result_index: research_result_id} for source linking."""
    qmap: dict[int, int] = {}
    for i, q in enumerate(result.get("queries") or []):
        rq = ResearchQuery(
            research_job_id=job.id,
            language=(q.get("language") or "unknown")[:20],
            query_text=(q.get("query") or "")[:4000],
            purpose=(q.get("purpose") or None) or None,
            priority=(q.get("priority") or None) or None,
            round=int(q.get("round") or 0),
        )
        db.add(rq)
        db.flush()
        qmap[i] = rq.id
    rmap: dict[int, int] = {}
    for i, r in enumerate(result.get("results") or []):
        rr = ResearchResult(
            research_query_id=qmap.get(r.get("query_idx")),
            url=(r.get("url") or "")[:4000],
            canonical_url=(r.get("canonical_url") or None) or None,
            final_url=(r.get("final_url") or None) or None,
            title=(r.get("title") or None) or None,
            snippet=(r.get("snippet") or None) or None,
            rank=r.get("rank"),
            provider=job.provider,
            model=(r.get("model") or None) or None,
            result_language=(r.get("result_language") or None) or None,
            relevance_score=r.get("relevance"),
            credibility_score=r.get("credibility"),
            novelty_score=r.get("novelty"),
            fetch_status=(r.get("fetch_status") or None) or None,
            accepted=bool(r.get("accepted")),
            rejection_reason=(r.get("rejection_reason") or None) or None,
        )
        db.add(rr)
        db.flush()
        rmap[i] = rr.id
    return rmap


def _apply_job_telemetry(job: ResearchJob, result: dict | None):
    """Promote provider telemetry into first-class job columns
    (Part 8) — readable without parsing result_json."""
    tel = (result or {}).get("_telemetry") or {}
    job.search_calls = tel.get("searches")
    job.fetch_calls = tel.get("fetches")
    job.input_tokens = tel.get("input_tokens")
    job.output_tokens = tel.get("output_tokens")
    if tel.get("input_tokens") is not None or tel.get("output_tokens") is not None:
        job.total_tokens = int(tel.get("input_tokens") or 0) + int(
            tel.get("output_tokens") or 0
        )
    job.search_cost_usd = tel.get("search_cost_usd")
    job.fetch_cost_usd = tel.get("fetch_cost_usd")
    job.model_cost_usd = tel.get("aux_cost_usd")
    job.total_cost_usd = tel.get("cost_usd")
    raw_results = (result or {}).get("results") or []
    sources = (result or {}).get("sources") or []
    if raw_results:
        job.sources_discovered = len(raw_results)
        job.sources_accepted = sum(1 for r in raw_results if r.get("accepted"))
        job.sources_rejected = len(raw_results) - job.sources_accepted
    else:
        job.sources_discovered = len(sources) or job.sources_discovered
        job.sources_accepted = job.sources_accepted or len(sources) or None
    stats = (result or {}).get("language_stats") or {}
    if stats:
        job.languages_completed = json.dumps(sorted(stats))
    models = tel.get("models_used") or []
    if models:
        job.model = ", ".join(models)[:200]


async def start_discovery_job(db: Session, payload) -> ResearchJob:
    provider = get_research_provider()
    if not provider.is_configured():
        raise ProviderError(
            "missing_key",
            "Research engine is not configured — set TRUECRIME_SEARXNG_URL or search_engine.searxng_url.",
        )

    job = create_job(
        db,
        job_type="discovery",
        input_data={
            "count": payload.count,
            "languages": payload.languages,
            "theme": payload.theme,
            "include_unsolved": bool(getattr(payload, "include_unsolved", False)),
        },
    )
    from app.lifecycle.identity import IdentityIndex

    try:
        external_id = await provider.start_discovery(
            count=payload.count,
            languages=payload.languages,
            theme=payload.theme,
            prefer_undercovered=payload.prefer_undercovered,
            require_multiple_sources=payload.require_multiple_sources,
            existing_titles=known_case_titles(db),
            known_identities=IdentityIndex.from_db(db).to_list(),
            include_unsolved=bool(getattr(payload, "include_unsolved", False)),
        )
    except Exception as e:
        job.status = "failed"
        job.error = str(e)[:500]
        job.completed_at = utc_now()
        db.commit()
        raise

    job.external_job_id = external_id
    job.status = "running"
    job.started_at = utc_now()
    db.commit()
    db.refresh(job)
    return job


async def start_research_job(db: Session, case: Case) -> ResearchJob:
    provider = get_research_provider()
    if not provider.is_configured():
        raise ProviderError(
            "missing_key",
            "Research engine is not configured — set TRUECRIME_SEARXNG_URL or search_engine.searxng_url.",
        )

    job = create_job(
        db,
        job_type="research",
        case_id=case.id,
        input_data={"case_title": case.canonical_title, "language": case.language},
    )
    research_languages = ai_config.multilingual.research_languages
    job.input_json = json.dumps(
        {
            "case_title": case.canonical_title,
            "language": case.language,
            "research_languages": research_languages,
        },
        ensure_ascii=False,
    )
    # Engine context: existing corpus lets the search engine dedupe and
    # score novelty against what the case already has — not just URLs.
    existing = (
        db.query(Source).filter(Source.case_id == case.id).all()
    )
    people: set[str] = set()
    for f in db.query(Fact).filter(Fact.case_id == case.id).all():
        try:
            for p in json.loads(f.people_json or "[]"):
                if isinstance(p, str) and 2 < len(p) < 80:
                    people.add(p.strip())
        except (ValueError, TypeError):
            continue
    context = {
        "title": case.canonical_title,
        "summary": case.summary or "",
        "location": "",
        "key_people": sorted(people)[:12],
        "corpus": {
            "canonical_urls": [
                _canonical_url(s.url) for s in existing if s.url
            ],
            "family_corpus": [
                {"source_id": s.id,
                 "text": (s.raw_text or s.summary or "")[:4000],
                 "family": s.source_family}
                for s in existing
                if s.raw_text or s.summary
            ],
        },
    }
    try:
        external_id = await provider.start_case_research(
            case_title=case.canonical_title,
            language=case.language,
            research_languages=research_languages,
            context=context,
        )
    except Exception as e:
        job.status = "failed"
        job.error = str(e)[:500]
        job.completed_at = utc_now()
        db.commit()
        raise

    case.status = "researching"
    job.external_job_id = external_id
    job.status = "running"
    job.started_at = utc_now()
    db.commit()
    db.refresh(job)
    return job


def _record_run(
    db: Session,
    job: ResearchJob,
    agent_name: str,
    output: str,
    error: str | None = None,
    completed: bool = True,
):
    db.add(
        AgentRun(
            case_id=job.case_id,
            agent_name=agent_name,
            provider=job.provider,
            status="completed" if not error else "failed",
            started_at=ensure_utc(job.started_at or job.created_at),
            completed_at=utc_now() if completed else None,
            input_summary=(job.input_json or "")[:300],
            output_summary=output[:300],
            error=(error or "")[:300] or None,
        )
    )


def _ingest_discovery(db: Session, job: ResearchJob, result: dict):
    """Store the suggestions of a discovery job: duplicate check against
    everything the system holds (identity, not titles), ranking RECENT +
    SOLVED, and every decision with its reason (app/lifecycle/selection)."""
    from app.lifecycle.selection import ingest_candidates

    params = json.loads(job.input_json or "{}")
    out = ingest_candidates(
        db, result, count=int(params.get("count") or 5),
        include_unsolved=bool(params.get("include_unsolved")), job_id=job.id)
    job.result_summary = (f"candidates={len(out['candidates'])} "
                          f"duplicates_rejected={out['skipped_duplicates']}")
    return out


def _canonical_url(url: str) -> str:
    """Strip scheme/www/tracking noise so the same article under slightly
    different URLs (or found via different languages) dedupes."""
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    return u


_CONTENT_STATUSES = {
    "metadata_only",
    "summary_only",
    "partial_text",
    "full_text",
    "unavailable",
}


def _source_content(raw: dict) -> tuple[str | None, str]:
    """Resolve stored text + honest content_status from a research source.

    Full text only when the provider actually retrieved it; otherwise verbatim
    excerpts become partial text; a summary alone stays summary_only.
    Never fabricates content."""
    full = (raw.get("full_text") or "").strip()
    excerpts = [
        (e or {}).get("text", "").strip()
        for e in (raw.get("excerpts") or [])
        if (e or {}).get("text", "").strip()
    ]
    declared = (raw.get("content_status") or "").strip()

    if full and (raw.get("full_text_available") or declared == "full_text"):
        return full, "full_text"
    if excerpts:
        text = "\n\n".join(excerpts)
        return text, "partial_text"
    if declared in _CONTENT_STATUSES:
        return None, declared
    return None, "summary_only" if (raw.get("summary") or "").strip() else "metadata_only"


_CONTENT_RANK = {
    "unavailable": 0,
    "metadata_only": 0,
    "summary_only": 1,
    "partial_text": 2,
    "full_text": 3,
}


def _ingest_research(db: Session, job: ResearchJob, result: dict):
    from app.services.chunking import chunk_source

    # Relational provenance first (Part 5/7): queries + raw results are
    # durable before any Source exists, so rejected/duplicate results are
    # auditable too.
    rmap = _persist_provenance(db, job, result)

    case = db.get(Case, job.case_id)
    existing = db.query(Source).filter(Source.case_id == job.case_id).all()
    existing_by_url = {_canonical_url(s.url): s for s in existing}
    canon_to_src: dict[str, int] = {
        c: s.id for c, s in existing_by_url.items()
    }
    existing_urls = set(existing_by_url)
    existing_titles = [
        (_normalize_title(s.title), _normalize_title(s.publisher or ""))
        for s in existing
    ]
    existing_families = {
        s.source_family for s in existing if s.source_family
    }
    now = utc_now()
    added = 0
    dupes = 0
    chunked = 0
    enriched = 0
    for raw in result.get("sources") or []:
        url = (raw.get("url") or "").strip()
        title = (raw.get("title") or "").strip()
        if not url or not title:
            continue
        c_url = _canonical_url(url)
        if c_url in existing_urls:
            # Re-research may return richer content for a known source —
            # upgrade it instead of discarding, so evidence depth can grow.
            prev = existing_by_url.get(c_url)
            if prev is not None:
                new_text, new_status = _source_content(raw)
                if _CONTENT_RANK.get(new_status, 0) > _CONTENT_RANK.get(
                    prev.content_status or "", 0
                ):
                    if new_text:
                        prev.raw_text = new_text
                    prev.content_status = new_status
                    prev.is_authorized_text = bool(new_text) or prev.is_authorized_text
                    if new_text:
                        prev.retrieval_method = (
                            raw.get("retrieval_method") or "http_fetch"
                        )
                    prev.retrieval_notes = (raw.get("retrieval_notes") or None) or prev.retrieval_notes
                    prev.retrieved_at = now
                    if not prev.summary and (raw.get("summary") or "").strip():
                        prev.summary = raw["summary"].strip()
                    if new_text:
                        chunked += chunk_source(db, prev)
                    enriched += 1
                    db.flush()
            ridx = raw.get("_result_idx")
            if ridx in rmap and prev is not None:
                db.add(ResearchSourceLink(
                    research_result_id=rmap[ridx],
                    source_id=prev.id,
                    link_type="enriched",
                ))
            dupes += 1
            continue
        # Translated-duplicate check: same normalized title + publisher under
        # a different URL or language name is one source family, not two.
        t_norm = _normalize_title(title)
        p_norm = _normalize_title(raw.get("publisher") or "")
        is_dupe = False
        declared_family = (raw.get("source_family") or "").strip()
        if declared_family and declared_family in existing_families:
            # Provider identified this as the same reporting under a translated
            # or republished URL — one evidence family, not a new source.
            is_dupe = True
        for prev_t, prev_p in existing_titles:
            if is_dupe:
                break
            if t_norm and prev_t and fuzz.token_set_ratio(t_norm, prev_t) >= 92:
                if not p_norm or not prev_p or fuzz.ratio(p_norm, prev_p) >= 85:
                    is_dupe = True
                    break
        if is_dupe:
            dupes += 1
            existing_urls.add(c_url)
            continue
        existing_urls.add(c_url)
        existing_titles.append((t_norm, p_norm))

        raw_text, content_status = _source_content(raw)
        family = (raw.get("source_family") or "").strip()[:300] or (
            f"{p_norm}|{t_norm[:80]}"[:300] if p_norm or t_norm else None
        )
        src = Source(
            case_id=job.case_id,
            title=title[:1000],
            url=url,
            source_type=(raw.get("source_type") or "other")[:100],
            language=(raw.get("language") or "unknown")[:20],
            declared_language=(raw.get("declared_language") or None),
            detected_language=(raw.get("detected_language") or None),
            language_confidence=raw.get("language_confidence"),
            language_detection_method=(
                raw.get("language_detection_method") or None
            ),
            publisher=(raw.get("publisher") or None),
            summary=(raw.get("summary") or None),
            raw_text=raw_text,
            is_authorized_text=bool(raw_text),
            content_status=content_status,
            source_family=family,
            retrieval_method=(
                (raw.get("retrieval_method") or "http_fetch")
                if raw_text else None
            ),
            retrieval_notes=(raw.get("retrieval_notes") or None),
            published_at=(raw.get("published_at") or None),
            retrieved_at=now,
            research_provider=job.provider,
            external_reference=job.external_job_id,
            reliability_score=min(max(float(raw.get("reliability", 0.5)), 0.0), 1.0)
            if raw.get("reliability") is not None
            else 0.5,
            status="active",
        )
        db.add(src)
        db.flush()
        existing_families.add(src.source_family)
        canon_to_src[c_url] = src.id
        ridx = raw.get("_result_idx")
        if ridx in rmap:
            db.add(ResearchSourceLink(
                research_result_id=rmap[ridx],
                source_id=src.id,
                link_type="canonical",
            ))
        if raw_text:
            chunked += chunk_source(db, src)
        added += 1

    # Duplicate-result links (Part 7): a result rejected as duplicate
    # still traces back to the canonical Source it re-discovered —
    # every discovery path stays visible.
    dup_links = 0
    for i, r in enumerate(result.get("results") or []):
        if r.get("rejection_reason") != "duplicate":
            continue
        src_id = canon_to_src.get(r.get("canonical_url") or "")
        if src_id and i in rmap:
            db.add(ResearchSourceLink(
                research_result_id=rmap[i],
                source_id=src_id,
                link_type="duplicate",
            ))
            dup_links += 1
    db.flush()

    case_info = result.get("case") or {}
    if case and case_info.get("summary") and not case.summary:
        case.summary = case_info["summary"]

    job.result_summary = (
        f"sources_added={added} duplicates_rejected={dupes} enriched={enriched} "
        f"chunks={chunked} "
        f"possible_facts={len(result.get('possible_facts') or [])} "
        f"possible_contradictions={len(result.get('possible_contradictions') or [])} "
        f"gaps={len(result.get('research_gaps') or [])}"
    )
    return {
        "sources_added": added,
        "duplicates_rejected": dupes,
        "sources_enriched": enriched,
        "chunks_created": chunked,
    }


async def _normalize_sources(db: Session, case_id: int) -> int:
    """Normalize non-English source summaries into canonical English
    (`summary_en`). Original-language text is preserved untouched; this is
    evidence normalization, not storytelling — factual fidelity only."""
    canonical = ai_config.multilingual.canonical_language
    pending = (
        db.query(Source)
        .filter(
            Source.case_id == case_id,
            Source.summary.isnot(None),
            Source.summary_en.is_(None),
            Source.language != canonical,
            Source.language != "unknown",
        )
        .all()
    )
    if not pending:
        return 0
    gen = get_generation_provider()
    system = (
        prompt("services/research_jobs/normalize_sources")
    )
    user = json.dumps(
        {
            "items": [
                {"id": s.id, "language": s.language, "summary": s.summary}
                for s in pending[:30]
            ],
            "output_schema": {"summaries": [{"id": 1, "summary_en": "..."}]},
        },
        ensure_ascii=False,
    )
    with track_run(
        db, case_id, "Evidence Normalizer",
        input_summary=f"sources={len(pending)}",
    ) as run:
        data, res = await run_agent("evidence.normalize_sources", gen, user, system=system)
        stamp_run(run, res, "evidence_normalizer")
        by_id = {s.id: s for s in pending}
        done = 0
        for item in data.get("summaries", []):
            src = by_id.get(item.get("id"))
            if src and item.get("summary_en"):
                src.summary_en = item["summary_en"]
                done += 1
        run.output_summary = f"normalized={done}"
    return done


# Jobs whose results are being ingested right now (this process): a second
# poller must neither re-enter the ingestion nor see a half-finished job as
# "completed".
_INGESTING: dict[int, float] = {}
_INGEST_STALE_S = 1800.0


async def poll_job(db: Session, job: ResearchJob, ingest: bool = True) -> ResearchJob:
    """Refresh a job's state from the provider and ingest on completion.

    Safe to call repeatedly — terminal jobs return immediately, a job that
    is being ingested is left to the poller that started it, and source
    ingestion dedupes by URL. `ingest=False` only reads (list views): a
    finished engine result then stays unconsumed until a real poll.
    """
    started = _INGESTING.get(job.id)
    if job.status in ("completed", "failed") or (
            started is not None and time.monotonic() - started < _INGEST_STALE_S):
        return job
    # video_research jobs are driven by their own background task —
    # external_job_id refers only to the *discovery* phase, and polling it
    # marks the job terminal while transcript processing is still running.
    if job.job_type == "video_research" or not job.external_job_id:
        return job

    provider = _provider_for(job)
    try:
        pjob = await provider.poll(job.external_job_id)
    except ProviderError as e:
        job.status = "failed"
        job.error = str(e)[:500]
        job.error_code = e.kind
        job.current_stage = "failed"
        job.completed_at = utc_now()
        db.commit()
        db.refresh(job)
        return job

    # Live progress (Parts 9-10): stage + telemetry meta land on the job
    # row on every poll, so the UI shows planning/searching:de/… directly.
    meta = getattr(pjob, "meta", None) or {}
    if meta.get("profile"):
        job.profile = meta["profile"]
    if meta.get("research_model") and not job.model:
        job.model = meta["research_model"]
    if meta.get("telemetry"):
        _apply_job_telemetry(job, {"_telemetry": meta["telemetry"]})

    if pjob.status == "running":
        # The timeout guard lives inside the running branch (Part 10):
        # a job that finished between polls is always ingested — never
        # discarded because a clock elapsed while the provider worked.
        if job.created_at and utc_now() - ensure_utc(job.created_at) > _job_timeout():
            job.status = "failed"
            job.error = "Research job timed out waiting for provider."
            job.error_code = "timeout"
            job.current_stage = "failed"
            job.completed_at = utc_now()
            _record_run(
                db,
                job,
                "Discovery Agent" if job.job_type == "discovery" else "Research Agent",
                output="",
                error=job.error,
            )
            db.commit()
            db.refresh(job)
            return job
        job.current_stage = meta.get("stage") or job.current_stage or "running"
        db.commit()
        return job

    job.completed_at = utc_now()
    job.result_json = json.dumps(pjob.result or {}, ensure_ascii=False) if pjob.result else None
    _apply_job_telemetry(job, pjob.result)
    # A successful result stays "running" (stage "ingesting") until its
    # sources and facts are really stored — only then "completed"; the
    # _INGESTING guard keeps a concurrent poll out of the ingestion.
    if pjob.status == "completed" and not ingest:
        db.rollback()
        return job
    if pjob.status == "completed":
        job.current_stage = "ingesting"
        _INGESTING[job.id] = time.monotonic()
    else:
        job.status = "failed"
        job.current_stage = "failed"
        job.error = (pjob.error or "provider failed")[:500]
        job.error_code = (meta.get("error_kind") or "provider_error")[:50]
    db.commit()

    if pjob.status == "completed" and pjob.result:
        try:
            if job.job_type == "discovery":
                job.current_stage = "deduplicating"
                extra = _ingest_discovery(db, job, pjob.result)
                job.result_json = json.dumps(extra, ensure_ascii=False)
                _record_run(db, job, "Discovery Agent", output=job.result_summary or "")
            else:
                job.current_stage = "deduplicating"
                extra = _ingest_research(db, job, pjob.result)
                _record_run(db, job, "Research Agent", output=job.result_summary or "")
                try:
                    norm = await _normalize_sources(db, job.case_id)
                    if norm:
                        job.result_summary = (
                            f"{job.result_summary} | normalized={norm}"
                        )
                except Exception as e:
                    job.result_summary = (
                        f"{job.result_summary} | normalization failed: {e}"
                    )[:500]
                # Pipeline: research sources -> fact extraction stage.
                # Unverified provider claims never enter the Fact layer
                # directly; the ResearchAgent re-derives them from sources.
                try:
                    from app.agents.research import ResearchAgent

                    job.current_stage = "extracting"
                    db.commit()
                    case = db.get(Case, job.case_id)
                    if case:
                        res = await ResearchAgent().run(
                            db,
                            case,
                            contradiction_hints=pjob.result.get(
                                "possible_contradictions"
                            )
                            or [],
                        )
                        job.result_summary = (
                            f"{job.result_summary} "
                            f"| extraction: facts={res['facts']} "
                            f"contradictions={res['contradictions']}"
                        )
                except Exception as e:
                    job.result_summary = (
                        f"{job.result_summary} | fact extraction failed: {e}"
                    )[:500]
            job.status = "completed"
            job.current_stage = "completed"
            # Provider-side usage (engine searches/fetches + LLM calls/
            # tokens/cost) surfaces in the job summary for observability.
            tel = (pjob.result or {}).get("_telemetry")
            if tel:
                job.result_summary = (
                    f"{job.result_summary or ''} | provider calls="
                    f"{tel.get('calls', 0)} searches={tel.get('searches', 0)} "
                    f"fetches={tel.get('fetches', 0)} tokens="
                    f"{tel.get('input_tokens', 0)}+{tel.get('output_tokens', 0)} "
                    f"cost=${tel.get('cost_usd', 0.0):.4f}"
                ).strip()[:500]
        except Exception as e:
            job.status = "failed"
            job.error = f"Ingest failed: {e}"[:500]
            _record_run(
                db,
                job,
                "Discovery Agent" if job.job_type == "discovery" else "Research Agent",
                output="",
                error=job.error,
            )
    else:
        job.status = "failed"
        job.current_stage = "failed"
        job.error = (pjob.error or "Provider job failed.")[:500]
        job.error_code = meta.get("error_kind") or None
        _record_run(
            db,
            job,
            "Discovery Agent" if job.job_type == "discovery" else "Research Agent",
            output="",
            error=job.error,
        )
        if job.job_type == "research" and job.case_id:
            case = db.get(Case, job.case_id)
            if case and case.status == "researching":
                case.status = "new"

    _INGESTING.pop(job.id, None)
    db.commit()
    db.refresh(job)
    return job


def job_dict(job: ResearchJob, include_result: bool = True) -> dict:
    result = None
    if include_result and job.result_json:
        try:
            result = json.loads(job.result_json)
        except (ValueError, TypeError):
            result = None
    duration_ms = None
    if job.started_at and job.completed_at:
        duration_ms = int(
            (ensure_utc(job.completed_at) - ensure_utc(job.started_at))
            .total_seconds() * 1000
        )
    return {
        "id": job.id,
        "case_id": job.case_id,
        "provider": job.provider,
        "external_job_id": job.external_job_id,
        "job_type": job.job_type,
        "status": job.status,
        "current_stage": job.current_stage,
        "profile": job.profile,
        "model": job.model,
        "search_calls": job.search_calls,
        "fetch_calls": job.fetch_calls,
        "input_tokens": job.input_tokens,
        "output_tokens": job.output_tokens,
        "total_tokens": job.total_tokens,
        "model_cost_usd": job.model_cost_usd,
        "search_cost_usd": job.search_cost_usd,
        "fetch_cost_usd": job.fetch_cost_usd,
        "total_cost_usd": job.total_cost_usd,
        "sources_discovered": job.sources_discovered,
        "sources_accepted": job.sources_accepted,
        "sources_rejected": job.sources_rejected,
        "languages_completed": (
            json.loads(job.languages_completed)
            if job.languages_completed else None
        ),
        "error_code": job.error_code,
        "duration_ms": duration_ms,
        "result_summary": job.result_summary,
        "error": job.error,
        "result": result,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }
