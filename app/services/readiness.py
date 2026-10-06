"""Layered readiness reporting.

Separates three concerns that used to collapse into one "capacity" verdict:

- source_readiness: does the corpus have real retrieved depth?
- evidence_readiness: has extraction actually run on that corpus?
- master_readiness: can master generation start right now?

A case can be source-rich yet master-blocked because evidence extraction
needs a provider that is unauthorized. The layers keep that honest.
"""

from sqlalchemy.orm import Session

from app.agents.story import estimate_narrative_capacity
from app.core.ai_config import ai_config
from app.db.models import Contradiction, Fact, ResearchJob, Source

_EXTRACTION_FAILURE_MARKERS = ("extraction failed", "normalization failed")
_PROVIDER_CAUSE_MARKERS = (
    "permission/quota",
    "403",
    "openrouter",
    "unauthorized",
    "permission",
)


def _provider_blocked(provider_status: dict | None) -> bool:
    return (provider_status or {}).get("authorized") is False


def _source_readiness(sources: list[Source], capacity: dict) -> dict:
    """Source-depth readiness: weighted retrieved-text depth + diversity,
    independent of whether evidence extraction has run."""
    cfg = ai_config.research_depth
    depth = capacity["depth"]
    src_minutes = (
        depth["full_text_sources"] * cfg.minutes_per_fulltext_source
        + depth["partial_text_sources"] * cfg.minutes_per_partial_source
        + depth["summary_only_sources"] * cfg.minutes_per_summary_source
        + depth["metadata_sources"] * cfg.minutes_per_metadata_source
    )
    retrieved = depth["full_text_sources"] + depth["partial_text_sources"]
    required = capacity["minimum_required_minutes"] * cfg.source_readiness_min_ratio
    reasons = []
    if retrieved < cfg.min_retrieved_sources:
        reasons.append("no retrieved source text (full/partial)")
    if src_minutes < required:
        reasons.append(
            f"source depth {round(src_minutes, 1)} min < "
            f"{round(required, 1)} min required"
        )
    return {
        "status": "insufficient" if reasons else "ready",
        "reasons": reasons,
        "source_depth_minutes": round(src_minutes, 1),
        "required_source_minutes": round(required, 1),
        "retrieved_sources": retrieved,
        "independent_source_families": depth["independent_source_families"],
        "by_status": {
            "full_text": depth["full_text_sources"],
            "partial_text": depth["partial_text_sources"],
            "summary_only": depth["summary_only_sources"],
            "metadata_only": depth["metadata_sources"],
        },
    }


def _evidence_readiness(
    db: Session,
    case_id: int,
    sources: list[Source],
    facts: list[Fact],
    contradictions: list[Contradiction],
    capacity: dict,
    provider_status: dict | None,
) -> dict:
    """Extraction-completeness readiness — coverage *adequacy* belongs to
    narrative capacity; this layer only answers 'did extraction run?'.

    Signals: retrieved text with zero evidence (never extracted) and the
    latest research job reporting an extraction/normalization failure."""
    latest_job = (
        db.query(ResearchJob)
        .filter(
            ResearchJob.case_id == case_id,
            ResearchJob.job_type.in_(["research", "gap_followup"]),
        )
        .order_by(ResearchJob.id.desc())
        .first()
    )
    # Post-ingest failures (normalization/fact extraction) are recorded in
    # result_summary — the job itself still completes.
    job_error = (
        f"{latest_job.error or ''} {latest_job.result_summary or ''}"
        if latest_job else ""
    )
    extraction_failed = any(m in job_error for m in _EXTRACTION_FAILURE_MARKERS)
    provider_caused = any(
        m.lower() in job_error.lower() for m in _PROVIDER_CAUSE_MARKERS
    )

    retrieved = sum(
        1 for s in sources
        if s.content_status in ("partial_text", "full_text") and s.raw_text
    )
    evidence_items = len(facts) + len(contradictions)
    extraction_missing = retrieved > 0 and evidence_items == 0

    depth = capacity["depth"]
    metrics = {
        "evidence_items": evidence_items,
        "facts": len(facts),
        "contradictions": len(contradictions),
        "timeline_events": depth["timeline_events"],
        "human_details": depth["human_details"],
        "scene_details": depth["scene_details"],
        "quotes": depth["quotes"],
        "facts_with_strength": sum(1 for f in facts if f.evidence_strength),
        "facts_with_provenance_text": sum(
            1 for f in facts if f.supporting_text
        ),
        "retrieved_sources": retrieved,
    }

    if not sources:
        status, detail = "incomplete", "no sources — run research first"
    elif extraction_missing:
        status = "blocked_provider" if _provider_blocked(provider_status) else "incomplete"
        detail = "retrieved source text present but no evidence extracted"
    elif extraction_failed:
        if _provider_blocked(provider_status):
            status = "blocked_provider"
        elif provider_caused:
            status = "incomplete"
        else:
            status = "failed"
        detail = job_error[:300]
    else:
        status, detail = "ready", None

    return {
        "status": status,
        "detail": detail,
        "extraction_failed": extraction_failed,
        **metrics,
    }


def _master_readiness(
    source_r: dict,
    evidence_r: dict,
    capacity: dict,
    provider_status: dict | None,
) -> dict:
    """Master-generation gate — every layer must hold, provider first."""
    ps = provider_status or {}
    if not ps.get("configured", True):
        return {"status": "provider_blocked", "reason": "provider not configured"}
    if ps.get("reachable") is False:
        return {"status": "provider_blocked", "reason": "provider unreachable"}
    if _provider_blocked(ps):
        return {
            "status": "provider_blocked",
            "reason": f"provider {ps.get('status') or 'unauthorized'}",
        }
    if source_r["status"] != "ready":
        return {
            "status": "insufficient_research",
            "reason": "; ".join(source_r["reasons"]),
        }
    if evidence_r["status"] == "failed":
        return {"status": "failed", "reason": evidence_r.get("detail")}
    if evidence_r["status"] == "blocked_provider":
        return {"status": "provider_blocked", "reason": "evidence extraction needs provider authorization"}
    if evidence_r["status"] != "ready":
        return {"status": "incomplete_evidence", "reason": evidence_r.get("detail")}
    if capacity["status"] != "ready":
        return {
            "status": "insufficient_research",
            "reason": (
                f"capacity {capacity['estimated_supported_minutes']} min < "
                f"{capacity['minimum_required_minutes']} min required"
            ),
        }
    return {"status": "ready", "reason": None}


def build_readiness(
    db: Session,
    case_id: int,
    target_minutes: int = 45,
    provider_status: dict | None = None,
) -> dict:
    """Full layered readiness report for a case."""
    sources = db.query(Source).filter(Source.case_id == case_id).all()
    facts = db.query(Fact).filter(Fact.case_id == case_id).all()
    contradictions = (
        db.query(Contradiction).filter(Contradiction.case_id == case_id).all()
    )
    capacity = estimate_narrative_capacity(
        facts, contradictions, sources, target_minutes
    )
    source_r = _source_readiness(sources, capacity)
    evidence_r = _evidence_readiness(
        db, case_id, sources, facts, contradictions, capacity, provider_status
    )
    master_r = _master_readiness(source_r, evidence_r, capacity, provider_status)
    return {
        "source_readiness": source_r,
        "evidence_readiness": evidence_r,
        "narrative_capacity": capacity,
        "master_readiness": master_r,
    }
