"""The orchestrator's monitor: what the machine and the providers allow, which films run or wait and why, and where each Autopilot run is."""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.autopilot import service as autopilot
from app.db.base import get_db
from app.db.models import AutopilotRun, Case, DocumentaryJob, OrchestratorState
from app.documentary import jobs as documentary_jobs
from app.utils import utc_now

router = APIRouter()

# The worker rewrites the state every tick (seconds); a state older than this means no worker is looking.
STALE_AFTER = timedelta(seconds=60)


def _waiting_reason(snapshot: dict, position: int) -> str:
    decision = snapshot.get("decision") or {}
    if decision.get("allowed", True):
        return "next in line" if position == 0 else f"{position} ahead in the queue"
    return str(decision.get("reason") or "waiting for room")


@router.get("/api/orchestrator")
def orchestrator(db: Session = Depends(get_db)):
    row = db.get(OrchestratorState, 1)
    snapshot = json.loads(row.snapshot_json) if row else {}
    alive = row is not None and utc_now() - row.updated_at < STALE_AFTER
    jobs = (db.query(DocumentaryJob).filter(DocumentaryJob.status.in_(("queued", "running", "cancelling")))
            .order_by(DocumentaryJob.id).all())
    titles = {c.id: c.canonical_title for c in db.query(Case).filter(Case.id.in_({j.case_id for j in jobs})).all()} if jobs else {}
    queued = [j for j in jobs if j.status == "queued"]
    return {
        "worker_alive": alive, "updated_at": row.updated_at if row else None,
        "host": snapshot.get("host"), "host_limits": snapshot.get("limits"),
        "running": snapshot.get("running", 0), "cap": snapshot.get("cap", 0),
        "decision": snapshot.get("decision"), "providers": snapshot.get("providers", {}),
        "events": list(reversed(snapshot.get("events", []))),
        "jobs": [{**documentary_jobs.job_dict(j), "case_title": titles.get(j.case_id),
                  "waiting_because": _waiting_reason(snapshot, queued.index(j)) if j.status == "queued" else None} for j in jobs],
        "runs": [_run(db, r) for r in db.query(AutopilotRun).filter(AutopilotRun.status.in_(autopilot.ACTIVE_RUN)).order_by(AutopilotRun.id).all()],
    }


def _run(db: Session, run: AutopilotRun) -> dict:
    view = autopilot.run_view(db, run)
    return {"id": run.id, "status": run.status, "stage": run.stage, "case": view["case"],
            "languages": {lang: (v["film_job"] or {}).get("status") for lang, v in view["languages"].items()},
            "needs_you": [n for n in view["needs_you"] if n["blocking"]]}
