"""Cost observability for finished documentary videos.

AgentRun is the source of truth for model usage. Documentary runs are tagged
with their job, language and pipeline stage so shared production work can be
shown once and language-specific work can be opened per video.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session
from fastapi import Depends

from app.api.serializers import agent_run_dict
from app.db.base import get_db
from app.db.models import AgentRun, Case, DocumentaryJob, Video
from app.lifecycle.videos import video_dict


router = APIRouter(tags=["costs"])


ARCHIVE_STATES = ("published", "archived")


def _money(runs: list[AgentRun]) -> float | None:
    values = [float(r.estimated_cost_usd) for r in runs if r.estimated_cost_usd is not None]
    return round(sum(values), 8) if values else None


def _stats(runs: list[AgentRun]) -> dict:
    return {
        "run_count": len(runs),
        "priced_run_count": sum(r.estimated_cost_usd is not None for r in runs),
        "missing_cost_runs": sum(r.estimated_cost_usd is None for r in runs),
        "cost_usd": _money(runs),
        "input_tokens": sum(r.input_tokens or 0 for r in runs),
        "output_tokens": sum(r.output_tokens or 0 for r in runs),
        "total_tokens": sum(r.total_tokens or 0 for r in runs),
    }


def _run_payload(run: AgentRun, scope: str) -> dict:
    return {**agent_run_dict(run), "scope": scope}


def _job_outputs(db: Session, job_id: int | None) -> list[Video]:
    if job_id is None:
        return []
    return db.query(Video).filter(Video.job_id == job_id).order_by(Video.language, Video.id).all()


def _video_breakdown(db: Session, video: Video) -> dict:
    case = db.get(Case, video.case_id)
    job = db.get(DocumentaryJob, video.job_id) if video.job_id else None
    runs: list[AgentRun] = []
    if job:
        runs = (db.query(AgentRun)
                .filter(AgentRun.documentary_job_id == job.id)
                .order_by(AgentRun.started_at.desc(), AgentRun.id.desc()).all())

    direct = [r for r in runs if r.language == video.language]
    shared = [r for r in runs if r.language is None]
    outputs = _job_outputs(db, job.id if job else None)
    output_count = max(len(outputs), 1)
    shared_cost = _money(shared)
    shared_allocation = round(shared_cost / output_count, 8) if shared_cost is not None else None
    direct_cost = _money(direct)
    fully_loaded = (
        round(direct_cost + shared_allocation, 8)
        if direct_cost is not None and shared_allocation is not None
        else None
    )

    case_title = case.canonical_title if case else None
    return {
        "video": {**video_dict(video), "case_title": case_title},
        "job": ({
            "id": job.id,
            "status": job.status,
            "mode": job.mode,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "languages": _loads_languages(job.languages_json),
        } if job else None),
        "summary": {
            "direct": _stats(direct),
            "shared": _stats(shared),
            "shared_allocation_usd": shared_allocation,
            "fully_loaded_cost_usd": fully_loaded,
            "output_count": len(outputs),
            "attribution": "documentary_job" if job else "unavailable",
        },
        "outputs": [_output_summary(db, output, job.id if job else None, shared_cost, output_count)
                    for output in outputs],
        "runs": [_run_payload(r, "direct") for r in direct]
                 + [_run_payload(r, "shared") for r in shared],
    }


def _loads_languages(raw: str | None) -> list[str]:
    import json
    try:
        value = json.loads(raw or "[]")
        return [str(x) for x in value] if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _output_summary(
    db: Session,
    video: Video,
    job_id: int | None,
    shared_cost: float | None,
    output_count: int,
) -> dict:
    runs = []
    if job_id is not None:
        runs = (db.query(AgentRun)
                .filter(AgentRun.documentary_job_id == job_id,
                        AgentRun.language == video.language).all())
    direct = _money(runs)
    allocation = round(shared_cost / output_count, 8) if shared_cost is not None else None
    return {
        "video_id": video.id,
        "language": video.language,
        "title": video.title,
        "state": video.state,
        "direct_cost_usd": direct,
        "shared_allocation_usd": allocation,
        "fully_loaded_cost_usd": (
            round(direct + allocation, 8)
            if direct is not None and allocation is not None else None
        ),
        "run_count": len(runs),
        "missing_cost_runs": sum(r.estimated_cost_usd is None for r in runs),
    }


@router.get("/api/costs/videos")
def video_cost_summary(db: Session = Depends(get_db)):
    """Cost ledger for the archived library (published + archived films).

    Shared stages are included once in the overall total and allocated evenly
    across outputs only for the per-video fully-loaded estimate.
    """
    videos = (db.query(Video)
              .filter(Video.state.in_(ARCHIVE_STATES))
              .order_by(Video.id.desc()).all())
    rows = []
    total_direct = 0.0
    total_shared = 0.0
    missing = 0
    seen_jobs: set[int] = set()
    priced_cost_seen = False

    for video in videos:
        item = _video_breakdown(db, video)
        summary = item["summary"]
        direct = summary["direct"]["cost_usd"]
        shared = summary["shared"]["cost_usd"]
        if direct is not None:
            total_direct += direct
            priced_cost_seen = True
        if video.job_id is not None and video.job_id not in seen_jobs:
            if shared is not None:
                total_shared += shared
                priced_cost_seen = True
            missing += summary["shared"]["missing_cost_runs"]
            seen_jobs.add(video.job_id)
        missing += summary["direct"]["missing_cost_runs"]
        rows.append({
            "video_id": video.id,
            "case_id": video.case_id,
            "case_title": item["video"]["case_title"],
            "language": video.language,
            "title": video.title,
            "state": video.state,
            "direct_cost_usd": direct,
            "shared_allocation_usd": summary["shared_allocation_usd"],
            "fully_loaded_cost_usd": summary["fully_loaded_cost_usd"],
            "run_count": summary["direct"]["run_count"],
            "missing_cost_runs": summary["direct"]["missing_cost_runs"],
        })

    rows.sort(key=lambda row: (row["fully_loaded_cost_usd"] is not None,
                               row["fully_loaded_cost_usd"] or -1), reverse=True)
    total = round(total_direct + total_shared, 8) if priced_cost_seen else None
    return {
        "scope": "archived_library",
        "states": list(ARCHIVE_STATES),
        "summary": {
            "video_count": len(videos),
            "job_count": len(seen_jobs),
            "direct_cost_usd": round(total_direct, 8) if total_direct else None,
            "shared_cost_usd": round(total_shared, 8) if total_shared else None,
            "total_cost_usd": total,
            "missing_cost_runs": missing,
        },
        "videos": rows,
    }


@router.get("/api/costs/videos/{video_id}")
def video_cost_detail(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return _video_breakdown(db, video)
