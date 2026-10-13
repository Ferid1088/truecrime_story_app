"""Durable, timeout-bounded jobs started by HTTP endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.tasks import spawn
from app.db.base import SessionLocal
from app.db.models import BackgroundJob, Case, StoryVersion
from app.utils import utc_now

log = logging.getLogger(__name__)


def create_job(db: Session, *, case_id: int | None, kind: str,
               input_data: dict[str, Any], timeout_seconds: int = 120) -> BackgroundJob:
    job = BackgroundJob(
        case_id=case_id,
        kind=kind,
        input_json=json.dumps(input_data, ensure_ascii=False),
        timeout_seconds=max(1, timeout_seconds),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def job_dict(job: BackgroundJob) -> dict[str, Any]:
    try:
        result = json.loads(job.result_json or "{}")
    except (TypeError, ValueError):
        result = {}
    return {
        "id": job.id, "case_id": job.case_id, "kind": job.kind,
        "status": job.status, "result": result, "error": job.error,
        "timeout_seconds": job.timeout_seconds, "attempts": job.attempts,
        "created_at": job.created_at, "started_at": job.started_at,
        "updated_at": job.updated_at, "completed_at": job.completed_at,
    }


async def _dispatch(db: Session, job: BackgroundJob) -> dict[str, Any]:
    """Run a job from persisted input; imports stay lazy to avoid router cycles."""
    data = json.loads(job.input_json or "{}")
    case = db.get(Case, job.case_id) if job.case_id is not None else None
    if case is None and job.case_id is not None:
        raise RuntimeError("case not found")

    if job.kind == "naming_generate":
        from app.naming import api as naming_api
        from app.naming import agent as naming_agent

        result = await naming_agent.generate_case_titles(
            db, case, naming_api._gen(), naming_api._embedder(),
            data.get("languages"),
        )
        return {**result, "state": naming_api.naming_state(db, case)}

    if job.kind in {"story_generate", "master_story_generate"}:
        from app.core.ai_config import ai_config
        from app.agents.story import MASTER_ROLES, StoryPipeline
        from app.api import deps
        from app.api.serializers import story_full_dict

        await deps.require_generation_authorized()
        kwargs = {
            "db": db, "case": case, "target_minutes": data["target_minutes"],
            "language": data.get("language") or "en",
            "tone": data.get("tone") or "documentary",
            "iterations": data.get("iterations", 1),
        }
        if job.kind == "master_story_generate":
            kwargs.update({
                "kind": "master",
                "words_per_minute": ai_config.words_per_minute_for(kwargs["language"]),
            })
            story = await StoryPipeline(roles=MASTER_ROLES).run(**kwargs)
        else:
            story = await StoryPipeline().run(**kwargs)
        return story_full_dict(story)

    if job.kind == "story_improve":
        from app.agents.story import StoryPipeline
        from app.api.serializers import story_full_dict

        story = db.query(StoryVersion).filter(
            StoryVersion.id == data["story_version_id"],
            StoryVersion.case_id == case.id,
        ).first()
        if story is None:
            raise RuntimeError("story version not found")
        return story_full_dict(await StoryPipeline().improve(
            db=db, case=case, story_version=story, instruction=data["instruction"],
        ))

    raise RuntimeError(f"unknown background job kind: {job.kind}")


async def _run(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(BackgroundJob, job_id)
        if job is None or job.status != "queued":
            return
        job.status = "running"
        job.attempts = int(job.attempts or 0) + 1
        job.started_at = utc_now()
        job.updated_at = utc_now()
        db.commit()
        try:
            result = await asyncio.wait_for(_dispatch(db, job), timeout=job.timeout_seconds)
        except asyncio.TimeoutError:
            job.status = "failed"
            job.error = f"Timed out after {job.timeout_seconds} seconds. Retry the job."
            job.completed_at = utc_now()
            job.updated_at = utc_now()
            db.commit()
            return
        except Exception as exc:
            log.exception("background job %s failed", job_id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"[:2000]
            job.completed_at = utc_now()
            job.updated_at = utc_now()
            db.commit()
            return
        job.status = "done"
        job.result_json = json.dumps(result, ensure_ascii=False, default=str)
        job.completed_at = utc_now()
        job.updated_at = utc_now()
        db.commit()
    finally:
        db.close()


def launch(job_id: int) -> None:
    spawn(_run(job_id), name=f"background_job:{job_id}")


def retry(db: Session, job: BackgroundJob) -> BackgroundJob:
    if job.status not in {"failed", "done"} and not (job.status == "queued" and job.attempts > 0):
        raise ValueError("only failed or done jobs can be retried")
    job.status = "queued"
    job.error = None
    job.result_json = "{}"
    job.started_at = None
    job.completed_at = None
    job.updated_at = utc_now()
    db.commit()
    db.refresh(job)
    launch(job.id)
    return job


def recover_after_restart(db: Session, *, include_documentary_jobs: bool = True) -> int:
    """A task held only in an API process cannot survive its process."""
    rows = db.query(BackgroundJob).filter(BackgroundJob.status == "running").all()
    for job in rows:
        job.status = "failed"
        job.error = "API restarted while this job was running. Retry it."
        job.completed_at = utc_now()
        job.updated_at = utc_now()
    if rows:
        db.commit()
    return len(rows)
