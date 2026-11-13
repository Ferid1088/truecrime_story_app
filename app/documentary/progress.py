"""The progress bars of the Monitoring page: for a job, the work shared by all languages and the chain of each language, as segments (phases) whose
state is read from the job's stages. The segments are in config/parameters/monitoring.json (`progress`)."""

from __future__ import annotations

from app.core.ai_config import ProgressPhase, ai_config

RUNNING, FAILED, BLOCKED, DONE, DEGRADED, WAITING, PENDING = "running", "failed", "blocked", "done", "degraded", "waiting", "pending"


def _split(name: str) -> tuple[str, str | None]:
    base, _, language = name.partition(":")
    return base, language or None


def phase_state(stages: list[dict]) -> str:
    """The state of a phase from the states of its stages: a failure or a running stage shows first; a phase with only some stages finished is waiting."""
    if not stages:
        return PENDING
    states = {s["status"] for s in stages}
    for state in (FAILED, RUNNING, BLOCKED):
        if state in states:
            return state
    finished = states & {"done", "degraded", "skipped"}
    if finished == states:
        return DEGRADED if "degraded" in states else DONE
    return WAITING if finished - {"skipped"} else PENDING


def _phases(phases: list[ProgressPhase], stages: list[dict]) -> list[dict]:
    out = []
    for phase in phases:
        mine = [s for s in stages if _split(s["name"])[0] in phase.stages]
        running = next((s for s in mine if s["status"] == RUNNING), None)
        failed = next((s for s in mine if s["status"] == FAILED), None)
        shown = running or failed
        out.append({"key": phase.key, "label": phase.label, "state": phase_state(mine),
                    "done": sum(s["status"] in ("done", "degraded", "skipped") for s in mine), "total": len(mine),
                    "stage": shown["name"] if shown else None, "seconds": shown["seconds"] if shown else None,
                    "error": shown.get("error") if shown else None,
                    "stages": [{"name": s["name"], "status": s["status"], "attempts": s["attempts"], "seconds": s["seconds"],
                                "error": s["error"]} for s in mine]})
    return out


def _current(phases: list[dict]) -> dict | None:
    """The phase to name under a bar: the running one, else the failed one, else the first that is not finished; a phase the job does not plan is none."""
    planned = [p for p in phases if p["total"]]
    for state in (RUNNING, FAILED, BLOCKED, WAITING, PENDING):
        found = next((p for p in planned if p["state"] == state), None)
        if found:
            return found
    return None


def _stuck(phase: dict | None) -> bool:
    cfg = ai_config.monitoring_settings()
    if not phase or phase["state"] != RUNNING or phase["seconds"] is None:
        return False
    limit = cfg.stuck_stage_minutes_by_stage.get(_split(phase["stage"] or "")[0], cfg.stuck_stage_minutes)
    return phase["seconds"] > limit * 60


def progress_view(stages: list[dict], languages: list[str]) -> dict:
    """The bars of one job: `shared` (stages without a language) and one row per language."""
    cfg = ai_config.monitoring_settings().progress
    shared_stages = [s for s in stages if _split(s["name"])[1] is None]
    shared = _phases(cfg.shared, shared_stages)
    rows = []
    for language in languages:
        mine = [s for s in stages if _split(s["name"])[1] == language]
        phases = _phases(cfg.language, mine)
        current = _current(phases)
        rows.append({"language": language, "phases": phases, "current": current["label"] if current else None,
                     "stuck": _stuck(current), "finished": all(p["state"] in (DONE, DEGRADED) or p["total"] == 0 for p in phases)
                     and any(p["total"] for p in phases)})
    return {"shared": {"phases": shared}, "languages": rows}
