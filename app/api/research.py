from app.core.prompts import prompt
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.tasks import spawn
from app.db.base import get_db
from app.db.models import (
    Case,
    Source,
    SourceChunk,
    Fact,
    Contradiction,
    ResearchJob,
    ResearchQuery,
    VideoSource,
    TranscriptSegment,
    TranscriptClaim,
    ClaimCluster,
    NarrativeInsight,
)
from app.core.ai_config import ai_config
from app.utils import utc_now
from app.agents.story import (
    research_gap_plan,
)
from app.providers.base import ProviderError
from app.services import research_jobs
from app.services.readiness import build_readiness


from app.api.deps import get_case_or_404
from app.api.serializers import claim_dict, video_dict
from app.api import deps

router = APIRouter()


@router.post("/api/cases/{case_id}/research")
async def run_research(case_id: int, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    if not deps.research_provider().is_configured():
        raise HTTPException(status_code=503,
                            detail="Search engine not configured (set TRUECRIME_SEARXNG_URL).")
    try:
        job = await research_jobs.start_research_job(db, case)
    except ProviderError as e:
        raise HTTPException(status_code=503, detail=f"Research provider unavailable: {e}")
    return {"job_id": job.id, "status": job.status}


@router.get("/api/cases/{case_id}/research")
def get_research(case_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)

    facts = db.query(Fact).filter(Fact.case_id == case_id).all()
    contradictions = db.query(Contradiction).filter(Contradiction.case_id == case_id).all()
    sources = db.query(Source).filter(Source.case_id == case_id).all()

    return {
        "sources": [
            {
                "id": s.id,
                "title": s.title,
                "url": s.url,
                "source_type": s.source_type,
                "reliability_score": s.reliability_score,
            }
            for s in sources
        ],
        "facts": [
            {
                "id": f.id,
                "claim": f.claim,
                "category": f.category,
                "confidence": f.confidence,
                "disputed": f.disputed,
                "event_date": f.event_date,
                "source_ids": json.loads(f.source_ids_json or "[]"),
            }
            for f in facts
        ],
        "contradictions": [
            {
                "id": c.id,
                "topic": c.topic,
                "description": c.description,
                "severity": c.severity,
                "source_ids": json.loads(c.source_ids_json or "[]"),
            }
            for c in contradictions
        ],
    }


@router.get("/api/cases/{case_id}/research/languages")
def research_languages(case_id: int, db: Session = Depends(get_db)):
    """Per-language research coverage: queries run, sources found, and how
    many canonical evidence items each language contributes to."""
    get_case_or_404(db, case_id)
    sources = db.query(Source).filter(Source.case_id == case_id).all()
    facts = db.query(Fact).filter(Fact.case_id == case_id).all()
    {s.id: s for s in sources}

    queries: dict[str, list[str]] = {}
    lang_stats: dict[str, dict] = {}
    job = (
        db.query(ResearchJob)
        .filter(
            ResearchJob.case_id == case_id,
            ResearchJob.job_type == "research",
            ResearchJob.status == "completed",
        )
        .order_by(ResearchJob.completed_at.desc())
        .first()
    )
    if job:
        # Relational queries first (Part 24); legacy result_json
        # "languages" key is the fallback for older jobs.
        qrows = (
            db.query(ResearchQuery)
            .filter(ResearchQuery.research_job_id == job.id)
            .order_by(ResearchQuery.id)
            .all()
        )
        for q in qrows:
            queries.setdefault(q.language, []).append(q.query_text)
        if job.result_json:
            try:
                res = json.loads(job.result_json)
                if not queries:
                    for lang, info in (res.get("languages") or {}).items():
                        queries[lang] = info.get("queries") or []
                lang_stats = res.get("language_stats") or {}
            except (ValueError, TypeError):
                pass

    langs = sorted(
        set(ai_config.multilingual.research_languages)
        | {s.language for s in sources if s.language}
        | set(queries)
    )
    out = {}
    for lang in langs:
        lang_sources = [s for s in sources if s.language == lang]
        ids = {s.id for s in lang_sources}
        evidence = [
            f for f in facts
            if ids & set(json.loads(f.source_ids_json or "[]"))
        ]
        depth_counts = {"metadata_only": 0, "summary_only": 0,
                        "partial_text": 0, "full_text": 0, "unavailable": 0}
        for s in lang_sources:
            depth_counts[s.content_status or "summary_only"] = (
                depth_counts.get(s.content_status or "summary_only", 0) + 1
            )
        chunk_count = (
            db.query(SourceChunk)
            .filter(SourceChunk.source_id.in_(ids))
            .count()
            if ids else 0
        )
        st = lang_stats.get(lang) or {}
        out[lang] = {
            "searched": lang in queries or bool(lang_sources)
            or bool(st.get("queries")),
            "queries": queries.get(lang, []),
            "sources": len(lang_sources),
            "queries_run": st.get("queries"),
            "search_calls": st.get("search_calls"),
            "fetch_calls": st.get("fetch_calls"),
            "sources_found": st.get("sources_found"),
            "sources_accepted": st.get("sources_accepted"),
            "rejected": st.get("rejected"),
            "cost_usd": st.get("cost_usd"),
            "stop_reason": st.get("stop_reason"),
            **depth_counts,
            "full_text_sources": depth_counts["full_text"],
            "chunks": chunk_count,
            "source_families": len(
                {s.source_family for s in lang_sources if s.source_family}
            ),
            "evidence_items": len(evidence),
            "unique_evidence": sum(
                1
                for f in evidence
                if set(json.loads(f.source_ids_json or "[]")) <= ids
            ),
        }
    return {
        "canonical_language": ai_config.multilingual.canonical_language,
        "languages": out,
        "total_sources": len(sources),
    }


@router.get("/api/cases/{case_id}/research/capacity")
async def research_capacity(
    case_id: int,
    target_minutes: int = 45,
    db: Session = Depends(get_db),
):
    """Layered readiness: source depth, evidence extraction completeness,
    narrative capacity and the master-generation verdict — kept separate
    so 'capacity ready' can never masquerade as 'master ready'."""
    case = get_case_or_404(db, case_id)
    provider_status = await deps.generation_provider_status()
    report = build_readiness(
        db, case.id, target_minutes, provider_status=provider_status
    )
    capacity = report["narrative_capacity"]
    # Flat capacity fields preserved for existing consumers; the layered
    # readiness objects are authoritative.
    return {**capacity, **report}


@router.get("/api/cases/{case_id}/research/depth")
async def research_depth(
    case_id: int,
    target_minutes: int = 45,
    db: Session = Depends(get_db),
):
    """Case-level research-depth panel: source depth, evidence depth,
    source families, chunk volume and the conservative capacity verdict."""
    case = get_case_or_404(db, case_id)
    sources = db.query(Source).filter(Source.case_id == case.id).all()
    facts = db.query(Fact).filter(Fact.case_id == case.id).all()
    contradictions = (
        db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
    )
    source_ids = [s.id for s in sources]
    chunks = (
        db.query(SourceChunk)
        .filter(SourceChunk.source_id.in_(source_ids))
        .count()
        if source_ids else 0
    )
    by_status: dict[str, int] = {}
    for s in sources:
        k = s.content_status or "summary_only"
        by_status[k] = by_status.get(k, 0) + 1
    provider_status = await deps.generation_provider_status()
    report = build_readiness(
        db, case.id, target_minutes, provider_status=provider_status
    )
    capacity = report["narrative_capacity"]
    return {
        "sources": len(sources),
        "by_status": by_status,
        "independent_source_families": capacity["depth"][
            "independent_source_families"
        ],
        "chunks": chunks,
        "capacity": capacity,
        "gap_plan": research_gap_plan(
            facts, contradictions, sources, target_minutes
        )["missing"],
        "source_readiness": report["source_readiness"],
        "evidence_readiness": report["evidence_readiness"],
        "master_readiness": report["master_readiness"],
    }


@router.get("/api/cases/{case_id}/research/gaps")
def research_gaps(
    case_id: int,
    target_minutes: int = 45,
    db: Session = Depends(get_db),
):
    """Targeted ResearchGapPlan — what follow-up research should chase."""
    case = get_case_or_404(db, case_id)
    facts = db.query(Fact).filter(Fact.case_id == case.id).all()
    contradictions = (
        db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
    )
    sources = db.query(Source).filter(Source.case_id == case.id).all()
    return research_gap_plan(facts, contradictions, sources, target_minutes)


@router.post("/api/cases/{case_id}/research/followup")
async def research_followup(
    case_id: int,
    target_minutes: int = 45,
    db: Session = Depends(get_db),
):
    """Targeted follow-up (Part 15): focuses the next research
    pass on the identified evidence gaps rather than re-running broad
    research blindly."""
    from app.services.research_jobs import create_job

    case = get_case_or_404(db, case_id)
    facts = db.query(Fact).filter(Fact.case_id == case.id).all()
    contradictions = (
        db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
    )
    sources = db.query(Source).filter(Source.case_id == case.id).all()
    plan = research_gap_plan(facts, contradictions, sources, target_minutes)
    missing = plan["missing"]
    if not missing:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "No research gaps identified — evidence depth is "
                           "sufficient for the requested duration.",
                "gap_plan": plan,
            },
        )

    provider = deps.research_provider()
    if not provider.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Research engine not configured "
                   "(SearXNG backend unreachable)",
        )

    gap_lines = "; ".join(
        f"{m['type']} ({m['priority']}): {m['reason']}" for m in missing
    )
    objective = (
        prompt("main/research_followup").format(gap_lines=gap_lines)
    )
    job = create_job(
        db,
        job_type="research",
        case_id=case.id,
        input_data={
            "case_title": case.canonical_title,
            "language": case.language,
            "follow_up": True,
            "gaps": missing,
        },
    )
    try:
        external_id = await provider.start_case_research(
            case_title=case.canonical_title,
            language=case.language,
            objective=objective,
            research_languages=ai_config.multilingual.research_languages,
        )
    except Exception as e:
        job.status = "failed"
        job.error = str(e)[:500]
        job.completed_at = utc_now()
        db.commit()
        raise HTTPException(status_code=502, detail=str(e))

    job.external_job_id = external_id
    job.status = "running"
    job.started_at = utc_now()
    db.commit()
    db.refresh(job)
    return {"job_id": job.id, "gap_plan": plan}


@router.post("/api/cases/{case_id}/video-research")
async def start_video_research(case_id: int, db: Session = Depends(get_db)):
    """Launch the multilingual video-research pipeline for a case.

    Runs in the background (video discovery + transcript acquisition can
    take far longer than a request timeout); the returned job is polled
    via /api/research-jobs/{job_id} like any other research job."""

    from app.db.base import SessionLocal
    from app.services.video_research import run_video_research

    case = get_case_or_404(db, case_id)
    job = research_jobs.create_job(
        db, job_type="video_research", case_id=case.id,
        input_data={"case_title": case.canonical_title},
    )

    async def _run(job_id: int):
        bg = SessionLocal()
        try:
            j = bg.get(ResearchJob, job_id)
            c = bg.get(Case, case_id)
            await run_video_research(bg, c, j)
        except Exception as e:  # pragma: no cover — defensive
            j = bg.get(ResearchJob, job_id)
            if j:
                j.status = "failed"
                j.error = str(e)[:500]
                j.completed_at = utc_now()
                bg.commit()
        finally:
            bg.close()

    spawn(_run(job.id), name=f"video_research:{job.id}")
    return {"job_id": job.id, "status": "queued"}


@router.get("/api/cases/{case_id}/videos")
def list_videos(case_id: int, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    videos = (
        db.query(VideoSource)
        .filter(VideoSource.case_id == case.id)
        .order_by(VideoSource.value_score.desc().nullslast(), VideoSource.id)
        .all()
    )
    return [video_dict(v) for v in videos]


@router.get("/api/cases/{case_id}/videos/{video_id}")
def video_detail(case_id: int, video_id: int, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    v = db.get(VideoSource, video_id)
    if not v or v.case_id != case.id:
        raise HTTPException(status_code=404, detail="Video not found")
    claims = (
        db.query(TranscriptClaim)
        .filter(TranscriptClaim.video_source_id == v.id)
        .order_by(TranscriptClaim.timestamp_start)
        .all()
    )
    segments = (
        db.query(TranscriptSegment)
        .filter(TranscriptSegment.video_source_id == v.id)
        .order_by(TranscriptSegment.segment_index)
        .all()
    )
    insights = (
        db.query(NarrativeInsight)
        .filter(NarrativeInsight.video_source_id == v.id)
        .all()
    )
    out = video_dict(v)
    out.update(
        {
            "claims": [claim_dict(c) for c in claims],
            "insights": [
                {
                    "id": i.id,
                    "type": i.insight_type,
                    "text_original": i.text_original,
                    "canonical_text_en": i.canonical_text_en,
                    "language": i.language,
                    "segment_ids": json.loads(i.segment_ids_json or "[]"),
                }
                for i in insights
            ],
            "segments": [
                {
                    "id": s.id,
                    "index": s.segment_index,
                    "start_seconds": s.start_seconds,
                    "end_seconds": s.end_seconds,
                    "text": s.text_original,
                    "canonical": s.canonical_text_en,
                }
                for s in segments
            ],
        }
    )
    return out


@router.get("/api/cases/{case_id}/claim-clusters")
def list_claim_clusters(case_id: int, db: Session = Depends(get_db)):
    """Cross-video claim clusters with member claims — the verification
    view (independent-family support, corroboration, contradictions)."""
    case = get_case_or_404(db, case_id)
    clusters = (
        db.query(ClaimCluster)
        .filter(ClaimCluster.case_id == case.id)
        .order_by(ClaimCluster.independent_source_family_count.desc())
        .all()
    )
    claims_by_id = {
        c.id: c
        for c in db.query(TranscriptClaim)
        .filter(TranscriptClaim.case_id == case.id)
        .all()
    }
    out = []
    for cl in clusters:
        member_ids = json.loads(cl.member_claim_ids_json or "[]")
        members = [claims_by_id[i] for i in member_ids if i in claims_by_id]
        out.append(
            {
                "id": cl.id,
                "canonical_claim_en": cl.canonical_claim_en,
                "claim_type": cl.claim_type,
                "narrative_value": cl.narrative_value,
                "languages": json.loads(cl.languages_json or "[]"),
                "confidence": cl.confidence,
                "support_status": cl.support_status,
                "independent_source_family_count":
                    cl.independent_source_family_count,
                "member_count": len(members),
                "promoted_fact_id": cl.promoted_fact_id,
                "claims": [
                    {
                        **claim_dict(m),
                        "video_source_id": m.video_source_id,
                        "video": (
                            m.video_source.video_id if m.video_source else None
                        ),
                        "channel": (
                            m.video_source.channel_name
                            if m.video_source else None
                        ),
                    }
                    for m in members
                ],
            }
        )
    return out
