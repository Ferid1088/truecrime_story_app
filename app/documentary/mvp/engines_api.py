"""The planner's inspector for the cinematic engines: which engine each beat got and why, its steps, sources and
missing evidence, a preview of every step, and the owner's controls (switch off, switch to another engine, edit the
texts, change the length, plan again). Choices are kept on the needs plan and applied to every rerun."""

from __future__ import annotations

import io
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Case, EditorialBlueprint, StoryVersion, VisualPlan
from app.documentary.graphics import shots as GS
from app.documentary.graphics import theme as T
from app.documentary.graphics.registry import graphic
from app.documentary.jobs import _load_manifest
from app.documentary.mvp import engines as EN
from app.documentary.mvp import evidence as EV

router = APIRouter(tags=["main-visual-planner"])


def needs_plan_of(db: Session, case_id: int) -> VisualPlan:
    """The case's shared needs plan: the planner's plan row that is no language's own direction."""
    for row in db.query(VisualPlan).filter_by(case_id=case_id).order_by(VisualPlan.id.desc()).all():
        mvp = json.loads(row.validation_json or "{}").get("mvp", {})
        if mvp.get("reveal_graph_id") and not mvp.get("language"):
            return row
    raise HTTPException(404, "This case has no Main Visual Planner needs plan yet")


def language_shots(db: Session, case_id: int, language: str) -> dict[str, dict]:
    """{beat the engine was planned for: its kind} for the engines on screen in the film's direction in `language`."""
    for row in db.query(VisualPlan).filter_by(case_id=case_id).order_by(VisualPlan.id.desc()).all():
        validation = json.loads(row.validation_json or "{}")
        if validation.get("mvp", {}).get("language") == language and "plan" in validation:
            plan = json.loads(row.plan_json or "{}")
            return {s["engine"]["beat"]: s["graphic"]["type"] for b in plan.get("beats") or [] for s in b["shots"]
                    if s.get("engine")}
    return {}


def view(db: Session, case_id: int, language: str) -> dict:
    needs = needs_plan_of(db, case_id)
    mvp = json.loads(needs.validation_json or "{}").get("mvp", {})
    overrides = mvp.get("engine_overrides") or {}
    placed = language_shots(db, case_id, language)
    rows = []
    for bid, d in sorted((mvp.get("engine_decisions") or {}).items()):
        key = f"{d['engine']}|{bid}" if d.get("engine") else None
        data = d.get("data")
        steps = graphic(d["engine"]).steps(data) if key and data else []
        rows.append({**d, "key": key, "steps": steps, "disabled": bool(key and key in (overrides.get("disabled") or [])),
                     "forced": (overrides.get("forced") or {}).get(bid),
                     "edited": bool(key and key in (overrides.get("edits") or {})),
                     "seconds": (overrides.get("timing") or {}).get(key, {}).get("seconds") if key else None,
                     "default_seconds": EN.engine_seconds(d["engine"], len(steps)) if steps else None,
                     "on_screen": bid in placed and placed[bid] == d.get("engine")})
    return {"language": language, "engines": [{"kind": s.kind, "name": s.name, "intents": list(s.intents),
                                               "requires": s.requires, "version": graphic(s.kind).version}
                                              for s in EN.SPECS.values()],
            "beats": rows, "chosen": sum(1 for r in rows if r["engine"]),
            "on_screen": sum(1 for r in rows if r["on_screen"])}


@router.get("/api/cases/{case_id}/mvp/engines")
def engines_view(case_id: int, language: str = "en", db: Session = Depends(get_db)):
    return view(db, case_id, language)


@router.get("/api/cases/{case_id}/mvp/engines/preview")
def engines_preview(case_id: int, beat: str, step: int | None = None, language: str = "en",
                    db: Session = Depends(get_db)):
    """One step of the planned engine as a PNG (the last step when `step` is left out)."""
    needs = needs_plan_of(db, case_id)
    decision = (json.loads(needs.validation_json or "{}").get("mvp", {}).get("engine_decisions") or {}).get(beat)
    if not decision or not decision.get("data"):
        raise HTTPException(404, "This beat has no planned engine")
    kind, data = decision["engine"], decision["data"]
    patch = ((json.loads(needs.validation_json).get("mvp", {}).get("engine_overrides") or {}).get("edits") or {}).get(
        f"{kind}|{beat}")
    data = patch or data
    g = graphic(kind)
    n = g.required_cues(data)
    seconds = EN.engine_seconds(kind, n)
    cues = [round(f * seconds, 3) for f in GS.cue_fractions(kind, n)]
    try:
        rendered = g.render(data, seconds, cues, language)
    except Exception as e:  # noqa: BLE001 - the inspector shows why a page cannot be drawn
        raise HTTPException(422, str(e)) from e
    buf = io.BytesIO()
    rendered.state(n if step is None else max(0, min(step, n))).save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png")


class Override(BaseModel):
    beat: str
    action: str                       # disable | enable | switch | edit | timing | replan
    engine: str | None = None
    data: dict | None = None
    seconds: float | None = None


def load_overrides(needs: VisualPlan) -> tuple[dict, dict]:
    validation = json.loads(needs.validation_json or "{}")
    return validation, validation.setdefault("mvp", {}).setdefault("engine_overrides", {})


@router.post("/api/cases/{case_id}/mvp/engines/override")
async def engines_override(case_id: int, payload: Override, db: Session = Depends(get_db)):
    needs = needs_plan_of(db, case_id)
    validation, overrides = load_overrides(needs)
    decision = (validation["mvp"].get("engine_decisions") or {}).get(payload.beat) or {}
    key = f"{decision.get('engine')}|{payload.beat}" if decision.get("engine") else None
    action = payload.action
    if action in ("disable", "enable", "edit", "timing") and not key:
        raise HTTPException(409, "This beat has no planned engine")
    if action == "disable":
        overrides["disabled"] = sorted({*(overrides.get("disabled") or []), key})
    elif action == "enable":
        overrides["disabled"] = [k for k in overrides.get("disabled") or [] if k != key]
    elif action == "edit":
        case = db.get(Case, case_id)
        ev = gather_for(db, case, needs)
        clean, problems = EN.validate(decision["engine"], payload.data or {}, EN.reference_texts(ev))
        if clean is None:
            raise HTTPException(422, "; ".join(problems))
        overrides.setdefault("edits", {})[key] = clean
    elif action == "timing":
        seconds = float(payload.seconds or 0)
        steps = len(graphic(decision["engine"]).steps(decision["data"]))
        floor = T.config().planner["min_seconds"][decision["engine"]]
        problem = EN.drawn_check(decision["engine"], decision["data"], seconds) if seconds >= floor else \
            f"at least {floor:.0f} seconds"
        if problem or seconds > 90:
            raise HTTPException(422, f"{steps} steps cannot be drawn in {seconds:.1f}s: {problem or 'at most 90 seconds'}")
        overrides.setdefault("timing", {})[key] = {"seconds": seconds}
    elif action in ("switch", "replan"):
        if action == "switch":
            if payload.engine not in EN.SPECS:
                raise HTTPException(422, f"engine must be one of {sorted(EN.SPECS)}")
            overrides.setdefault("forced", {})[payload.beat] = payload.engine
        else:
            (overrides.get("forced") or {}).pop(payload.beat, None)
        overrides["disabled"] = [k for k in overrides.get("disabled") or [] if not k.endswith(f"|{payload.beat}")]
        needs.validation_json = json.dumps(validation, ensure_ascii=False)
        db.commit()
        case = db.get(Case, case_id)
        ev = gather_for(db, case, needs)
        planner = EN.EnginePlanner(db, case, needs)
        only = EN.beat_only(ev, payload.beat)
        await planner.decide(only, overrides.get("forced"), refresh=payload.beat)
        return view(db, case_id, "en")
    else:
        raise HTTPException(422, "action must be disable, enable, switch, edit, timing or replan")
    needs.validation_json = json.dumps(validation, ensure_ascii=False)
    db.commit()
    return view(db, case_id, "en")


def spoken_version(db: Session, case_id: int, blueprint_id: int, language: str = "en") -> StoryVersion | None:
    """The spoken version of `language` that the blueprint's film is narrated from (the one with the timed audio)."""
    for version in (db.query(StoryVersion).filter_by(case_id=case_id, language=language, kind="spoken")
                    .order_by(StoryVersion.id.desc())):
        if json.loads(version.narrative_structure or "{}").get("blueprint_id") == blueprint_id:
            return version
    return None


def gather_for(db: Session, case: Case, needs: VisualPlan):
    """The planner's evidence of the film the needs plan belongs to: its own blueprint and its English narration."""
    bp = db.get(EditorialBlueprint, needs.blueprint_id)
    version = spoken_version(db, case.id, needs.blueprint_id)
    if bp is None or version is None:
        raise HTTPException(409, "The film has no blueprint or spoken English version yet")
    return EV.gather(db, case, bp, needs, _load_manifest(case.id, version))
