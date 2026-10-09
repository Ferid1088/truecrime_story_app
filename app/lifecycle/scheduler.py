"""In-app scheduler for the unsolved-case monitor (~twice a week).

Runs inside the API server process: every `poll_minutes` it checks when
the monitor last ran (MonitorRun rows survive restarts) and starts a run
when `interval_hours` have passed. A server that was off for a week runs
the monitor once when it comes back — no missed-run pile-up.
Set TRUECRIME_DISABLE_SCHEDULER=1 to keep it off (tests, one-off scripts).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from app.core.ai_config import ai_config
from app.db.models import MonitorRun
from app.utils import ensure_utc, utc_now

log = logging.getLogger(__name__)
_task: asyncio.Task | None = None


def last_run(db) -> MonitorRun | None:
    return (db.query(MonitorRun).filter(MonitorRun.status != "running")
            .order_by(MonitorRun.started_at.desc()).first())


def next_due(db) -> datetime | None:
    cfg = ai_config.case_monitor
    if not cfg.enabled:
        return None
    last = last_run(db)
    if last is None:
        return utc_now()
    return ensure_utc(last.started_at) + timedelta(hours=cfg.interval_hours)


async def tick(session_factory=None, monitor=None) -> MonitorRun | None:
    """Start a monitor run when one is due; returns it (or None)."""
    from app.db.base import SessionLocal
    from app.lifecycle.monitor import UnsolvedCaseMonitor

    if not ai_config.case_monitor.enabled:
        return None
    db = (session_factory or SessionLocal)()
    try:
        due = next_due(db)
        if due is None or utc_now() < due:
            return None
        if db.query(MonitorRun).filter(MonitorRun.status == "running").first():
            return None
        return await (monitor or UnsolvedCaseMonitor()).run(db, trigger="scheduled")
    finally:
        db.close()


async def _loop():
    while True:
        try:
            run = await tick()
            if run is not None:
                log.info("unsolved-case monitor run %s: %s cases, %s deep, %s changes",
                         run.id, run.cases_checked, run.deep_checks, run.status_changes)
        except Exception as e:  # noqa: BLE001 — the scheduler never dies
            log.warning("monitor scheduler tick failed: %s", e)
        await asyncio.sleep(ai_config.case_monitor.poll_minutes * 60)


def disabled() -> bool:
    return (os.getenv("TRUECRIME_DISABLE_SCHEDULER", "").lower() in ("1", "true", "yes")
            or not ai_config.case_monitor.enabled or not ai_config.case_monitor.autostart)


def start() -> bool:
    global _task
    if disabled() or (_task is not None and not _task.done()):
        return False
    _task = asyncio.get_event_loop().create_task(_loop())
    return True


def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None


def status(db) -> dict:
    last = last_run(db)
    due = next_due(db)
    return {
        "enabled": ai_config.case_monitor.enabled,
        "running_in_process": _task is not None and not _task.done(),
        "interval_hours": ai_config.case_monitor.interval_hours,
        "last_run_at": last.started_at if last else None,
        "next_run_at": due,
    }
