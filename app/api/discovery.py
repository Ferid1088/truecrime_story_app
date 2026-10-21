from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import duplicate_or_409
from app.db.base import get_db
from app.db.models import (
    Case,
    DiscoveryCandidate,
    ResearchJob,
    ResearchQuery,
)
from app.lifecycle.api import resolution_dict
from app.lifecycle.candidates import open_case_from_candidate
from app.schemas import (
    BulkCandidateActionRequest,
    InvestigateCandidateRequest,
    RenameSearchRequest,
    TopicDiscoveryRequest,
)
from app.services import research_jobs

router = APIRouter()

# Filled in below, after the single-action functions are defined.
_SINGLE_CANDIDATE_ACTIONS: dict[str, Callable[[int, Session], dict]] = {}


@router.post("/api/discovery/bulk/{action}")
def bulk_candidate_action(action: str, payload: BulkCandidateActionRequest,
                          db: Session = Depends(get_db)):
    """D4 bulk archive/delete/restore: each id is applied independently —
    one candidate's failure (not found, or delete blocked by an existing
    case) never stops the rest."""
    fn = _SINGLE_CANDIDATE_ACTIONS.get(action)
    if fn is None:
        raise HTTPException(status_code=404, detail=f"Unknown bulk action {action!r}")
    succeeded, failed = [], []
    for candidate_id in payload.candidate_ids:
        try:
            fn(candidate_id, db)
            succeeded.append(candidate_id)
        except HTTPException as e:
            failed.append({"candidate_id": candidate_id, "detail": e.detail})
    return {"succeeded": succeeded, "failed": failed}


@router.post("/api/topics/discover")
async def discover_topics(payload: TopicDiscoveryRequest, db: Session = Depends(get_db)):
    """Create the durable job and return immediately.

    Provider submission belongs to the background task. A search-engine health
    check or a slow provider must never hold the browser request open.
    """
    input_data = payload.model_dump()
    job = research_jobs.create_job(db, job_type="discovery", input_data=input_data)
    from app.core.tasks import spawn

    spawn(
        research_jobs.run_discovery_job(job.id, input_data),
        name=f"discovery:{job.id}",
    )
    return {"job_id": job.id, "status": job.status}


@router.get("/api/research-jobs/{job_id}")
async def get_research_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ResearchJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Research job not found")
    job = await research_jobs.poll_job(db, job)
    return research_jobs.job_dict(job)


@router.patch("/api/research-jobs/{job_id}/name")
def rename_research_job(job_id: int, payload: RenameSearchRequest, db: Session = Depends(get_db)):
    """A Search's user-renamed display name (D2). An empty/null value
    clears the override, reverting to the deterministic generated name."""
    job = db.get(ResearchJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Research job not found")
    job.display_name = payload.display_name or None
    db.commit()
    db.refresh(job)
    return research_jobs.job_dict(job, include_result=False)


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


@router.get("/api/discovery/countries")
def discovery_countries():
    from app.lifecycle.geography import country_options

    return country_options()


@router.get("/api/discovery/cost-estimate")
def discovery_cost_estimate(count: int = 1, db: Session = Depends(get_db)):
    if not 1 <= count <= 10:
        raise HTTPException(status_code=422, detail="count must be between 1 and 10")
    return research_jobs.discovery_cost_estimate(db, count)


@router.get("/api/discovery/history")
def discovery_history(state: str | None = None, discovery_job_id: int | None = None,
                      db: Session = Depends(get_db)):
    """Every suggestion with its status, ranking reason — and the
    duplicates/filtered ones with the reason they were not suggested.
    Each row also carries its live D3 rollup (`case_status`,
    `language_badges`), computed fresh, never a stored copy."""
    from app.lifecycle.search_status import candidate_status, language_badges
    from app.lifecycle.selection import candidate_dict

    q = db.query(DiscoveryCandidate)
    if state:
        q = q.filter(DiscoveryCandidate.state == state)
    if discovery_job_id is not None:
        q = q.filter(DiscoveryCandidate.discovery_job_id == discovery_job_id)
    rows = q.order_by(DiscoveryCandidate.created_at.desc()).limit(200).all()
    return [
        {**candidate_dict(r), "case_status": candidate_status(db, r),
         "language_badges": language_badges(db, r.case_id)}
        for r in rows
    ]


@router.get("/api/discovery/searches")
def discovery_searches(db: Session = Depends(get_db)):
    """D2: every discovery run as a Search — one row per `ResearchJob`
    (job_type="discovery"), newest first, with its name (generated or
    user-renamed), its stored criteria, its real cost and D3's live counts."""
    from app.lifecycle.search_status import search_counts

    rows = (
        db.query(ResearchJob)
        .filter(ResearchJob.job_type == "discovery")
        .order_by(ResearchJob.created_at.desc())
        .limit(200)
        .all()
    )
    return [
        {**research_jobs.job_dict(job, include_result=False), "counts": search_counts(db, job.id)}
        for job in rows
    ]


@router.post("/api/discovery/{candidate_id}/investigate")
def investigate_candidate(
    candidate_id: int,
    payload: InvestigateCandidateRequest,
    db: Session = Depends(get_db),
):
    from app.lifecycle.identity import candidate_identity

    cand = db.get(DiscoveryCandidate, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if cand.case_id and db.get(Case, cand.case_id):
        existing = db.get(Case, cand.case_id)
        return {"id": existing.id, "canonical_title": existing.canonical_title,
                "slug": existing.slug, "existing": True}
    duplicate_or_409(db, candidate_identity(cand), payload.force)

    case = open_case_from_candidate(db, cand, payload.language)
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


def _get_candidate_or_404(candidate_id: int, db: Session) -> DiscoveryCandidate:
    cand = db.get(DiscoveryCandidate, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return cand


def _archive_candidate_action(candidate_id: int, db: Session) -> dict:
    from app.lifecycle.archive import archive_candidate

    cand = _get_candidate_or_404(candidate_id, db)
    archive_candidate(db, cand)
    db.commit()
    return {"id": cand.id, "state": cand.state}


def _delete_candidate_action(candidate_id: int, db: Session) -> dict:
    from app.lifecycle.archive import DeleteNotAllowed, soft_delete_candidate

    cand = _get_candidate_or_404(candidate_id, db)
    try:
        soft_delete_candidate(db, cand)
    except DeleteNotAllowed as e:
        raise HTTPException(
            status_code=409,
            detail="A case already exists for this suggestion; archive it instead "
                   "from the discover list (the film is untouched).") from e
    db.commit()
    return {"id": cand.id, "state": cand.state}


def _restore_candidate_action(candidate_id: int, db: Session) -> dict:
    from app.lifecycle.archive import RestoreWindowExpired, restore_candidate

    cand = _get_candidate_or_404(candidate_id, db)
    try:
        restore_candidate(db, cand)
    except RestoreWindowExpired as e:
        raise HTTPException(
            status_code=409,
            detail="The restore window for this deleted suggestion has passed.") from e
    db.commit()
    return {"id": cand.id, "state": cand.state}


@router.post("/api/discovery/{candidate_id}/archive")
def archive_candidate_route(candidate_id: int, db: Session = Depends(get_db)):
    """D4: permanent — matched by fingerprint/identity, never suggested
    again by any later Search, even under a different title."""
    return _archive_candidate_action(candidate_id, db)


@router.post("/api/discovery/{candidate_id}/delete")
def delete_candidate_route(candidate_id: int, db: Session = Depends(get_db)):
    """D4: soft delete of just this suggestion — restorable for the
    configured window; a later Search may suggest the same case again."""
    return _delete_candidate_action(candidate_id, db)


@router.post("/api/discovery/{candidate_id}/restore")
def restore_candidate_route(candidate_id: int, db: Session = Depends(get_db)):
    """D4: undo an archive or a (still-restorable) soft delete."""
    return _restore_candidate_action(candidate_id, db)


_SINGLE_CANDIDATE_ACTIONS.update({
    "archive": _archive_candidate_action,
    "delete": _delete_candidate_action,
    "restore": _restore_candidate_action,
})
