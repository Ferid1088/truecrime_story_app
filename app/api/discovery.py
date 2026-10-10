import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import (
    Case,
    DiscoveryCandidate,
    ResearchJob,
    ResearchQuery,
)
from app.schemas import (
    TopicDiscoveryRequest,
    InvestigateCandidateRequest,
)
from app.providers.base import ProviderError
from app.services import research_jobs


from app.lifecycle.api import resolution_dict
from app.api.deps import duplicate_or_409, unique_slug
from app.api import deps

router = APIRouter()


@router.post("/api/topics/discover")
async def discover_topics(payload: TopicDiscoveryRequest, db: Session = Depends(get_db)):
    """Candidate discovery runs as a job on the search engine (duplicate and
    status checks included); there is no second, unchecked path."""
    if not deps.research_provider().is_configured():
        raise HTTPException(status_code=503,
                            detail="Search engine not configured (set TRUECRIME_SEARXNG_URL).")
    try:
        job = await research_jobs.start_discovery_job(db, payload)
    except ProviderError as e:
        raise HTTPException(status_code=503, detail=f"Research provider unavailable: {e}")
    return {"job_id": job.id, "status": job.status}


@router.get("/api/research-jobs/{job_id}")
async def get_research_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ResearchJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Research job not found")
    job = await research_jobs.poll_job(db, job)
    return research_jobs.job_dict(job)


@router.get("/api/research-jobs")
async def list_research_jobs(case_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(ResearchJob).order_by(ResearchJob.created_at.desc())
    if case_id is not None:
        query = query.filter(ResearchJob.case_id == case_id)
    rows = query.limit(50).all()
    out = []
    for job in rows:
        job = await research_jobs.poll_job(db, job, ingest=False)   # list views only read
        out.append(research_jobs.job_dict(job, include_result=False))
    return out


@router.get("/api/research-jobs/{job_id}/queries")
def research_job_queries(job_id: int, db: Session = Depends(get_db)):
    """Query inspector (Part 24): every executed query with its results —
    language, purpose, round, acceptance and rejection reasons."""
    job = db.get(ResearchJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Research job not found")
    queries = (
        db.query(ResearchQuery)
        .filter(ResearchQuery.research_job_id == job_id)
        .order_by(ResearchQuery.id)
        .all()
    )
    return [
        {
            "id": q.id,
            "language": q.language,
            "query": q.query_text,
            "purpose": q.purpose,
            "priority": q.priority,
            "round": q.round,
            "created_at": q.created_at,
            "results": [
                {
                    "id": r.id,
                    "url": r.url,
                    "final_url": r.final_url,
                    "title": r.title,
                    "rank": r.rank,
                    "model": r.model,
                    "result_language": r.result_language,
                    "relevance_score": r.relevance_score,
                    "credibility_score": r.credibility_score,
                    "novelty_score": r.novelty_score,
                    "fetch_status": r.fetch_status,
                    "accepted": r.accepted,
                    "rejection_reason": r.rejection_reason,
                    "source_ids": [l.source_id for l in r.source_links],
                }
                for r in q.results
            ],
        }
        for q in queries
    ]


@router.get("/api/discovery/history")
def discovery_history(state: str | None = None, db: Session = Depends(get_db)):
    """Every suggestion with its status, ranking reason — and the
    duplicates/filtered ones with the reason they were not suggested."""
    from app.lifecycle.selection import candidate_dict

    q = db.query(DiscoveryCandidate)
    if state:
        q = q.filter(DiscoveryCandidate.state == state)
    rows = q.order_by(DiscoveryCandidate.created_at.desc()).limit(200).all()
    return [candidate_dict(r) for r in rows]


@router.post("/api/discovery/{candidate_id}/investigate")
def investigate_candidate(
    candidate_id: int,
    payload: InvestigateCandidateRequest,
    db: Session = Depends(get_db),
):
    from app.lifecycle.identity import candidate_identity
    from app.lifecycle.status import set_resolution

    cand = db.get(DiscoveryCandidate, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if cand.case_id and db.get(Case, cand.case_id):
        existing = db.get(Case, cand.case_id)
        return {"id": existing.id, "canonical_title": existing.canonical_title,
                "slug": existing.slug, "existing": True}
    duplicate_or_409(db, candidate_identity(cand), payload.force)

    case = Case(
        canonical_title=cand.title,
        slug=unique_slug(db, cand.title),
        language=payload.language,
        summary=cand.rationale,
        aliases_json=cand.aliases_json or "[]",
        people_json=cand.people_json or "[]",
        location=cand.location,
        incident_date=cand.incident_date,
        latest_development_date=cand.latest_development_date,
        origin="discovery",
    )
    db.add(case)
    db.flush()
    set_resolution(
        db, case, cand.resolution_status or "UNKNOWN", changed_by="discovery",
        reason=cand.resolution_evidence or cand.suggestion_reason or "from the suggestion",
        confidence=cand.resolution_confidence,
        sources=[{"url": u} for u in json.loads(cand.source_urls_json or "[]")][:10],
        summary=cand.resolution_evidence, commit=False)
    cand.selected = True
    cand.rejected = False
    cand.state = "accepted"
    cand.case_id = case.id
    db.commit()
    db.refresh(case)
    return {"id": case.id, "canonical_title": case.canonical_title, "slug": case.slug,
            **resolution_dict(case)}


@router.post("/api/discovery/{candidate_id}/ignore")
def ignore_candidate(candidate_id: int, db: Session = Depends(get_db)):
    cand = db.get(DiscoveryCandidate, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    cand.rejected = True
    cand.selected = False
    cand.state = "ignored"   # stays in the history: never suggested again
    db.commit()
    return {"id": cand.id, "rejected": True}
