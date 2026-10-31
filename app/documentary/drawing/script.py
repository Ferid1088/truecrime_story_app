"""The drawing as the film's script carries it: which portrait is on screen when, in which window, how far along and how fast, in this
language's corner. Built from the finished shot list once the portraits are made; a film is not made without it."""

from __future__ import annotations

import asyncio
import time

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset, VisualPlan
from app.documentary.drawing import plan as DP
from app.documentary.drawing import portrait as PD
from app.documentary.drawing import portraits as DRP
from app.documentary.drawing import service as S
from app.documentary.pipeline.errors import AwaitingPortrait
from app.documentary.visuals.typography import RTL

CONTIGUOUS = 1e-3


def not_made(db: Session, case_id: int, codes: list[str]) -> list[str]:
    """The portraits among `codes` that are not made yet."""
    assets = {a.asset_code: a for a in db.query(VisualAsset).filter(VisualAsset.case_id == case_id, VisualAsset.asset_code.in_(codes))}
    return [c for c in codes if c not in assets or S.state_of(assets[c])["status"] != "ready"]


def awaiting_creation(missing: list[str], needed: int, ordered: list[str] | None = None) -> AwaitingPortrait:
    started = f" The planner has ordered {', '.join(ordered)}: they are being made, resume the job when they are ready." if ordered else ""
    return AwaitingPortrait(
        f"Awaiting portrait creation: {len(missing)} of the {needed} portraits the film needs are not made yet ({', '.join(missing)}).{started} "
        "Open Review pictures to see them and press Create portraits; no film is rendered without them.")


def build(db: Session, case: Case, plan_row: VisualPlan, script: dict, language: str) -> dict:
    """The drawing of this film: every gap has a portrait and every portrait it uses is made, or the run stops and says why."""
    sentences = [{"start": x["start"], "end": x["end"], "display": x["text"]} for x in script.get("subtitles") or []]
    pool = DRP.portrait_pool(db, case.id, sentences, DRP.person_keys(db, case, plan_row))
    plan = DP.plan_drawing(script["shots"], pool)
    if plan.uncovered:
        raise AwaitingPortrait(DRP.no_portrait_message(language, plan.uncovered, pool))
    if not plan.fits:                      # the film's drawing time is more than the chosen photos give, even at the slowest speed
        usable = len([p for p in pool if p.refused is None])
        visible = sum(i.seconds for i in DP.intervals_of(script["shots"]))
        raise AwaitingPortrait(
            f"Awaiting portrait choice: the film shows the drawing for {visible / 60:.0f} minutes, and the {usable} photo(s) you chose cannot cover "
            f"that even at the slowest speed ({ai_config.drawing_settings().min_speed}x). Open Review pictures and give more approved photos of "
            "people a drawing priority.")
    codes = [p["code"] for p in plan.portraits]
    missing = not_made(db, case.id, codes)
    if missing:
        raise awaiting_creation(missing, len(codes), S.order(db, case.id, missing))     # the planner orders what is missing
    assets = {a.asset_code: a for a in db.query(VisualAsset).filter(VisualAsset.case_id == case.id, VisualAsset.asset_code.in_(codes))}
    portraits = {}
    for code in codes:
        state = S.state_of(assets[code])
        portraits[code] = {"video": state["video"], "pen": state["pen"], "fps": state["fps"], "frames": state["frames"],
                           "seconds": state.get("seconds"), "width": PD.CW, "height": PD.CH}
    segments = []
    for i, seg in enumerate(plan.segments):
        before = plan.segments[i - 1] if i else None
        after = plan.segments[i + 1] if i + 1 < len(plan.segments) else None
        same_gap_before = before is not None and before["mode"] == "full" and seg["mode"] == "full" and seg["start"] - before["end"] < CONTIGUOUS
        same_gap_after = after is not None and after["mode"] == "full" and seg["mode"] == "full" and after["start"] - seg["end"] < CONTIGUOUS
        segments.append({**seg, "fade_in": before is None or seg["start"] - before["end"] > CONTIGUOUS,
                         "fade_out": after is None or after["start"] - seg["end"] > CONTIGUOUS,
                         "grow_in": not same_gap_before, "grow_out": not same_gap_after})
    cfg = ai_config.drawing_settings()
    fraction = cfg.window.width / cfg.window.reference_width
    for overlay in script.get("overlays") or []:                       # the "illustration" label goes beside the window
        if overlay["kind"] == "label" and any(s["start"] < overlay["end"] and s["end"] > overlay["start"] for s in segments):
            overlay["offset_fraction"] = round(fraction, 4)
    return {"language": language, "rtl": language in RTL, "portraits": portraits, "segments": segments,
            "speed": round(plan.speed, 3), "order": plan.portraits}


async def build_when_made(db: Session, case: Case, plan_row: VisualPlan, script: dict, language: str) -> dict:
    """`build`, waiting for the portraits it orders: when the film needs portraits that are not made yet, the planner orders them (see `build`)
    and this looks again every few seconds until they are made; only a portrait still missing after the configured time stops the run. Every
    other reason (a spoiler gap, too few photos) stops it at once."""
    cfg = ai_config.drawing_settings()
    deadline = time.monotonic() + cfg.creation_wait_seconds
    while True:
        try:
            return build(db, case, plan_row, script, language)
        except AwaitingPortrait as stopped:
            if not str(stopped).startswith("Awaiting portrait creation") or time.monotonic() >= deadline:
                raise
        await asyncio.sleep(cfg.poll_seconds)
        db.expire_all()                        # the worker making a portrait wrote its state in another session
