"""The pictures and clips the owner checked in the review must be used 100 % in the film: each one that the direction left
out is put into the beat of the requirement it was approved for, in a bare moment of that beat when there is one, otherwise in
the longest picture shot there that is not a card or a map. A checked item that is not approved cannot be shown and is reported."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import MvpCandidate, MvpRequirement, VisualAsset, VisualPlan

PICTURE_COMMANDS = ("NEW_IMAGE", "SHOW_CLIP", "SHOW_DOCUMENT", "ATMOSPHERIC_BROLL", "CROP_EXISTING", "ZOOM_EXISTING")


def _beat_of(db: Session, asset: VisualAsset, needs_plan_id: int) -> str | None:
    row = (db.query(MvpRequirement.beat_id).join(MvpCandidate, MvpCandidate.requirement_id == MvpRequirement.id)
           .filter(MvpRequirement.visual_plan_id == needs_plan_id, MvpCandidate.visual_asset_id == asset.id,
                   MvpCandidate.final_status == "approved").first())
    return row[0] if row else None


def place_must_use(db: Session, case_id: int, needs_plan: VisualPlan, target: VisualPlan) -> dict:
    """Put the owner's checked items that the direction left out into the plan `target`; returns what was done."""
    plan = json.loads(target.plan_json or "{}")
    beats = {b["beat_id"]: b for b in plan.get("beats") or []}
    used = {s.get("asset_id") for b in beats.values() for s in b["shots"] if s.get("asset_id")}
    report: dict = {"placed": [], "already": [], "failed": []}
    for asset in db.query(VisualAsset).filter_by(case_id=case_id, must_use=True).order_by(VisualAsset.id):
        if asset.asset_code in used:
            report["already"].append(asset.asset_code)
            continue
        beat = _beat_of(db, asset, needs_plan.id)
        if beat is None or beat not in beats:
            report["failed"].append({"asset": asset.asset_code, "why": "it is not approved for any moment of the film"})
            continue
        shots = beats[beat]["shots"]
        spot = next((s for s in shots if s.get("gap")), None) or max(
            (s for s in shots if s.get("command") in PICTURE_COMMANDS and not s.get("fixed")),
            key=lambda s: s.get("share", 0.0), default=None)
        if spot is None:
            report["failed"].append({"asset": asset.asset_code, "why": f"beat {beat} has no picture shot to give up"})
            continue
        for key in ("gap", "topical", "score", "repeat", "clip_start", "clip_end", "map_place", "overlay"):
            spot.pop(key, None)
        spot.update({"command": "SHOW_CLIP" if asset.asset_type == "video" else "NEW_IMAGE", "asset_id": asset.asset_code,
                     "motion": "NONE" if asset.asset_type == "video" else "SLOW_PUSH", "role": "evidence",
                     "why": "the owner checked this picture: it must be shown", "must_use": True})
        used.add(asset.asset_code)
        report["placed"].append({"asset": asset.asset_code, "beat": beat})
    target.plan_json = json.dumps(plan, ensure_ascii=False)
    db.commit()
    return report
