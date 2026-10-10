"""After a restart: work that was running in the old process is not
running any more. Nothing is thrown away — every saved step stays — but
the rows say so ("interrupted") instead of "running" forever, and the
interrupted documentaries can be resumed from where they stopped."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.db.models import DocumentaryJob, HostScene, MonitorRun, ResearchJob
from app.utils import utc_now

log = logging.getLogger(__name__)

INTERRUPTED = "interrupted by an app restart"


def _interrupt_stages(stages: list[dict], at: str) -> int:
    n = 0
    for s in stages:
        if s.get("status") == "running":
            s["status"] = "pending"
            s["errors"] = (s.get("errors") or [])[-4:] + [
                {"at": at, "attempt": s.get("attempts"), "type": "Interrupted",
                 "message": INTERRUPTED}]
            n += 1
    return n


def interrupt_job(job: DocumentaryJob, at, reason: str = INTERRUPTED) -> None:
    """Mark a job whose process is gone: running stages go back to pending (every saved
    result stays), the job itself becomes "interrupted" (or "cancelled" if it was being cancelled)."""
    try:
        stages = json.loads(job.stages_json or "[]")
    except ValueError:
        stages = []
    _interrupt_stages(stages, at.isoformat())
    job.stages_json = json.dumps(stages)
    if job.status == "cancelling":
        job.status = "cancelled"
        job.completed_at = at
    else:
        job.status = "interrupted"
        job.error = (f"{reason} during {job.stage}" if job.stage else reason)[:1000]
    job.stage = None
    job.updated_at = at


def requeue_job(job: DocumentaryJob, at, *, count_attempt: bool) -> None:
    """Put an interrupted job back in the queue; finished stages are skipped when it runs again."""
    job.status = "queued"
    job.worker_id = None
    job.heartbeat_at = None
    job.completed_at = None
    job.updated_at = at
    if count_attempt:
        job.auto_resumes = (job.auto_resumes or 0) + 1


def reap_stale_jobs(db: Session, *, lease_seconds: float, max_auto_resumes: int, now=None) -> dict:
    """External-worker mode: find jobs whose worker stopped reporting (crash, power loss) and either
    queue them again (up to max_auto_resumes times) or leave them "interrupted" for a manual resume."""
    from datetime import timedelta

    now = now or utc_now()
    cutoff = now - timedelta(seconds=lease_seconds)
    report = {"requeued": [], "interrupted": []}
    held = db.query(DocumentaryJob).filter(
        DocumentaryJob.status.in_(("running", "cancelling")) | (
            (DocumentaryJob.status == "queued") & DocumentaryJob.worker_id.isnot(None))).all()
    for job in held:
        beat = job.heartbeat_at or job.updated_at
        if beat is not None and beat.tzinfo is None:
            from datetime import timezone

            beat = beat.replace(tzinfo=timezone.utc)
        if beat is not None and beat > cutoff:
            continue  # its worker is alive
        was_cancelling = job.status == "cancelling"
        interrupt_job(job, now, "its worker stopped responding")
        if job.status == "interrupted" and not was_cancelling and (job.auto_resumes or 0) < max_auto_resumes:
            requeue_job(job, now, count_attempt=True)
            report["requeued"].append(job.id)
        else:
            job.worker_id = None
            report["interrupted"].append(job.id)
    db.commit()
    if report["requeued"] or report["interrupted"]:
        log.warning("stale jobs: %s", report)
    return report


def recover_after_restart(db: Session, *, include_documentary_jobs: bool = True) -> dict:
    """Run once when the app starts (single process: anything marked
    running belongs to a process that is gone). With an external worker the documentary
    jobs belong to the worker, which is still alive: they are left alone here."""
    at = utc_now()
    report = {"documentary_jobs": [], "monitor_runs": [], "research_jobs": [],
              "host_scenes": 0}

    if include_documentary_jobs:
        for job in db.query(DocumentaryJob).filter(
                DocumentaryJob.status.in_(("queued", "running", "cancelling"))).all():
            interrupt_job(job, at)
            report["documentary_jobs"].append(job.id)

    for run in db.query(MonitorRun).filter(MonitorRun.status == "running").all():
        run.status = "failed"
        run.error = INTERRUPTED
        run.finished_at = at
        report["monitor_runs"].append(run.id)

    # every research job runs inside this process (the engine keeps its jobs
    # in memory): after a restart none of them can continue
    for rj in db.query(ResearchJob).filter(
            ResearchJob.status.in_(("queued", "running"))).all():
        rj.status = "failed"
        rj.error = INTERRUPTED
        rj.completed_at = at
        report["research_jobs"].append(rj.id)

    from app.documentary.host_scenes import _log

    for s in db.query(HostScene).filter(HostScene.running_since.isnot(None)).all():
        s.running_since = None
        _log(s, "run", "interrupted", INTERRUPTED)
        report["host_scenes"] += 1

    db.commit()
    if any(report.values()):
        log.warning("recovered after restart: %s", report)
    return report
