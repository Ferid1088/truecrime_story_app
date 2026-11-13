"""The progress bars of the Monitoring page: for a job, the work shared by all languages and the chain of each language, as segments (phases) whose
state is read from the job's stages. The segments are in config/parameters/monitoring.json (`progress`)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ai_config import ProgressPhase, ai_config
from app.db.models import CoverImage, FilmOpening, HostScene, ShortClip, Video

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


def _item(name: str, status: str, error: str | None = None) -> dict:
    return {"name": name, "status": status, "attempts": 1, "seconds": None, "error": error}


def _made(phase: ProgressPhase, items: list[dict]) -> dict:
    """A phase read from what the case holds (not from the job's stages): one item per scene, clip or cover."""
    return {"key": phase.key, "label": phase.label, "state": phase_state(items),
            "done": sum(i["status"] in ("done", "degraded") for i in items), "total": len(items),
            "stage": None, "seconds": None, "error": next((i["error"] for i in items if i["status"] == FAILED), None), "stages": items}


def _case_items(db: Session, case_id: int, language: str) -> dict[str, list[dict]]:
    """What the case holds for a language: its host scenes (avatar), its opening, its short clips and its covers, newest rows only."""
    cfg = ai_config.monitoring_settings().progress
    scenes = db.query(HostScene).filter(HostScene.case_id == case_id, HostScene.language == language).all()
    newest = max((s.host_segments_id for s in scenes), default=None)
    avatar = [_item(f"scene {s.host_segment_id}", DONE if s.status in ("avatar_ready", "composited") else FAILED if s.failed_step else RUNNING if s.status == "avatar_requested" else WAITING,
                    s.last_error) for s in scenes if s.host_segments_id == newest]
    opening_row = db.query(FilmOpening).filter(FilmOpening.case_id == case_id, FilmOpening.language == language).order_by(FilmOpening.id.desc()).first()
    opening = [_item("opening", {"valid": DONE, "needs_review": DEGRADED, "invalid": FAILED}.get(opening_row.status, WAITING))] if opening_row else []
    clips: dict[str, ShortClip] = {}
    for c in db.query(ShortClip).filter(ShortClip.case_id == case_id, ShortClip.language == language).order_by(ShortClip.id):
        clips[c.clip_key] = c
    short = [_item(key, {"valid": DONE, "needs_review": DEGRADED, "invalid": FAILED}.get(clips[key].status, WAITING) if key in clips else PENDING) for key in cfg.clip_keys]
    pictures: dict[str, CoverImage] = {}
    for c in db.query(CoverImage).filter(CoverImage.case_id == case_id, CoverImage.language == language).order_by(CoverImage.id):
        pictures[c.cover_key] = c
    covers = [_item(key, (DONE if pictures[key].ok else FAILED) if key in pictures else PENDING, pictures[key].error if key in pictures else None) for key in cfg.cover_keys]
    return {"avatar": avatar, "opening": opening, "clips": short, "covers": covers}


def _phases(phases: list[ProgressPhase], stages: list[dict], made: dict[str, list[dict]] | None = None) -> list[dict]:
    out = []
    for phase in phases:
        mine = [s for s in stages if _split(s["name"])[0] in phase.stages]
        if phase.source != "stages" and not mine:
            out.append(_made(phase, (made or {}).get(phase.source, [])) | {"group": phase.group})
            continue
        running = next((s for s in mine if s["status"] == RUNNING), None)
        failed = next((s for s in mine if s["status"] == FAILED), None)
        shown = running or failed
        out.append({"key": phase.key, "label": phase.label, "group": phase.group, "state": phase_state(mine),
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


def progress_view(db: Session, case_id: int, stages: list[dict], languages: list[str]) -> dict:
    """The bars of one case: `shared` (stages without a language) and one row per language, the job's languages and the languages the case already has a film in."""
    cfg = ai_config.monitoring_settings().progress
    have = {v.language for v in db.query(Video).filter(Video.case_id == case_id).all()}
    order = ai_config.multilingual.producible_languages
    shown = sorted({*languages, *have}, key=lambda c: order.index(c) if c in order else len(order))
    shared_stages = [s for s in stages if _split(s["name"])[1] is None]
    shared = _phases(cfg.shared, shared_stages)
    research, story = shared[0], shared[1]
    if research["total"] == 0 and story["state"] in (DONE, DEGRADED):          # the research ran before this job: the story built on it exists
        shared[0] = research | {"state": DONE, "done": 1, "total": 1, "stages": [_item("made before this job", DONE)]}
    rows = []
    for language in shown:
        mine = [s for s in stages if _split(s["name"])[1] == language]
        phases = _phases(cfg.language, mine, _case_items(db, case_id, language))
        current = _current(phases)
        rows.append({"language": language, "phases": phases, "current": current["label"] if current else None,
                     "stuck": _stuck(current), "finished": all(p["total"] and p["state"] in (DONE, DEGRADED) for p in phases)})
    return {"shared": {"phases": shared}, "languages": rows}
