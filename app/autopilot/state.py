"""The run's step log and "needs you" list: small helpers over the two JSON columns, so every
writer records the same shape."""

from __future__ import annotations

import json

from app.db.models import AutopilotRun
from app.utils import utc_now

PHASES = ("research", "texts", "films", "titles", "shorts", "publish")


def steps(run: AutopilotRun) -> list[dict]:
    return json.loads(run.steps_json or "[]")


def initial_steps() -> list[dict]:
    return [{"name": "case", "status": "done", "detail": "case opened from the suggestion",
             "at": utc_now().isoformat()}] + [
        {"name": p, "status": "pending", "detail": None, "at": None} for p in PHASES]


def step_status(run: AutopilotRun, name: str) -> str:
    return next((s["status"] for s in steps(run) if s["name"] == name), "pending")


def step_detail(run: AutopilotRun, name: str) -> str | None:
    return next((s["detail"] for s in steps(run) if s["name"] == name), None)


def set_step(run: AutopilotRun, name: str, status: str, detail: str | None = None) -> None:
    rows = steps(run)
    for row in rows:
        if row["name"] == name:
            if (row["status"], row["detail"]) != (status, detail):
                row.update(status=status, detail=detail, at=utc_now().isoformat())
            break
    run.steps_json = json.dumps(rows, ensure_ascii=False)


def needs(run: AutopilotRun) -> list[dict]:
    return json.loads(run.needs_you_json or "[]")


def add_need(run: AutopilotRun, key: str, kind: str, message: str, *, language: str | None = None,
             blocking: bool = True) -> None:
    rows = [n for n in needs(run) if n["key"] != key]
    rows.append({"key": key, "kind": kind, "message": message, "language": language,
                 "blocking": blocking})
    run.needs_you_json = json.dumps(rows, ensure_ascii=False)


def drop_blocking_needs(run: AutopilotRun) -> None:
    """Blocking items are worked out again on every pass: whatever is still wrong is added back."""
    run.needs_you_json = json.dumps([n for n in needs(run) if not n["blocking"]],
                                    ensure_ascii=False)


def drop_needs(run: AutopilotRun, prefix: str) -> None:
    run.needs_you_json = json.dumps([n for n in needs(run) if not n["key"].startswith(prefix)],
                                    ensure_ascii=False)


def film_job_ids(run: AutopilotRun) -> dict[str, int]:
    return json.loads(run.film_job_ids_json or "{}")


def attempts(run: AutopilotRun, phase: str) -> int:
    return next((s.get("attempts", 0) for s in steps(run) if s["name"] == phase), 0)


def set_attempts(run: AutopilotRun, phase: str, count: int) -> None:
    rows = steps(run)
    for row in rows:
        if row["name"] == phase:
            row["attempts"] = count
    run.steps_json = json.dumps(rows, ensure_ascii=False)


def remember(run: AutopilotRun, phase: str, key: str, value) -> None:
    rows = steps(run)
    for row in rows:
        if row["name"] == phase:
            row.setdefault("memory", {})[key] = value
    run.steps_json = json.dumps(rows, ensure_ascii=False)


def recall(run: AutopilotRun, phase: str, key: str, default=None):
    return next((s.get("memory", {}).get(key, default) for s in steps(run) if s["name"] == phase),
                default)
