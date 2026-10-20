"""Durable, timeout-bounded jobs started by HTTP endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.tasks import spawn
from app.db.base import SessionLocal
from app.db.models import BackgroundJob, Case
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
        assert case is not None, "this job belongs to a case"
        from app.naming import agent as naming_agent
        from app.naming import api as naming_api

        result = await naming_agent.generate_case_titles(
            db, case, naming_api._gen(), naming_api._embedder(),
            data.get("languages"),
        )
        return {**result, "state": naming_api.naming_state(db, case)}

    if job.kind == "language_pipeline":
        assert case is not None, "this job belongs to a case"
        from app.api import deps
        from app.language_pipeline.orchestrator import LanguagePipeline
        from app.naming.agent import default_embedder

        await deps.require_generation_authorized()
        events: list[dict] = []

        def progress(event: dict) -> None:
            events.append({**event, "at": utc_now().isoformat()})
            with SessionLocal() as other:
                row = other.get(BackgroundJob, job.id)
                assert row is not None
                row.result_json = json.dumps({"events": events[-200:]}, ensure_ascii=False, default=str)
                row.updated_at = utc_now()
                other.commit()

        pipeline = LanguagePipeline(embedder=default_embedder(), progress=progress)
        result = await pipeline.run_case(case.id, data.get("languages"),
                                         data.get("requested_minutes"),
                                         data.get("tone") or "documentary",
                                         new_attempt=bool(data.get("new_attempt")))
        return {**result, "events": events[-200:]}

    if job.kind == "language_improve":
        assert case is not None, "this job belongs to a case"
        from app.api import deps
        from app.language_pipeline.orchestrator import LanguagePipeline
        from app.naming.agent import default_embedder

        await deps.require_generation_authorized()
        row = await LanguagePipeline(embedder=default_embedder()).improve_final(
            case.id, data["language"], data["instruction"], data.get("requested_minutes"))
        return {"final_version_id": row.id, "status": row.status}

    if job.kind == "language_revise":
        assert case is not None, "this job belongs to a case"
        from app.api import deps
        from app.language_pipeline.orchestrator import LanguagePipeline
        from app.naming.agent import default_embedder

        await deps.require_generation_authorized()
        row = await LanguagePipeline(embedder=default_embedder()).revise_final(
            case.id, data["language"], data.get("requested_minutes"))
        return {"final_version_id": row.id, "status": row.status}

    if job.kind == "language_final_audit":
        assert case is not None, "this job belongs to a case"
        from app.api import deps
        from app.language_pipeline.orchestrator import LanguagePipeline
        from app.naming.agent import default_embedder

        await deps.require_generation_authorized()
        report = await LanguagePipeline(embedder=default_embedder()).audit_final(case.id, data["language"])
        return {"status": report["status"], "items": len(report["items"]),
                "cost_eur": (report.get("usage") or {}).get("cost_eur")}

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
        except TimeoutError:
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
