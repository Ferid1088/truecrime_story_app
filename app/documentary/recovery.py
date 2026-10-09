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


def recover_after_restart(db: Session) -> dict:
    """Run once when the app starts (single process: anything marked
    running belongs to a process that is gone)."""
    at = utc_now()
    report = {"documentary_jobs": [], "monitor_runs": [], "video_research_jobs": [],
              "host_scenes": 0}

    for job in db.query(DocumentaryJob).filter(
            DocumentaryJob.status.in_(("queued", "running", "cancelling"))).all():
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
            job.error = (f"{INTERRUPTED} during {job.stage}" if job.stage
                         else INTERRUPTED)[:1000]
        job.stage = None
        job.updated_at = at
        report["documentary_jobs"].append(job.id)

    for run in db.query(MonitorRun).filter(MonitorRun.status == "running").all():
        run.status = "failed"
        run.error = INTERRUPTED
        run.finished_at = at
        report["monitor_runs"].append(run.id)

    # video research runs inside this process; web research jobs are
    # remote and keep being polled, so they are left alone
    for rj in db.query(ResearchJob).filter(
            ResearchJob.job_type == "video_research",
            ResearchJob.status.in_(("queued", "running"))).all():
        rj.status = "failed"
        rj.error = INTERRUPTED
        rj.completed_at = at
        report["video_research_jobs"].append(rj.id)

    from app.documentary.host_scenes import _log

    for s in db.query(HostScene).filter(HostScene.running_since.isnot(None)).all():
        s.running_since = None
        _log(s, "run", "interrupted", INTERRUPTED)
        report["host_scenes"] += 1

    db.commit()
    if any(report.values()):
        log.warning("recovered after restart: %s", report)
    return report
