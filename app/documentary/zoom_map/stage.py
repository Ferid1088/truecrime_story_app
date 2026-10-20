"""The pipeline stage of the zoom maps: one shot per map beat of the visual plan, in the film's language.

A beat whose shot cannot be made keeps the old map of the place (and the stage says so): the film
still gets its map, and the report names the beat and the reason."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, VisualPlan
from app.documentary.visuals.generated import map_chain
from app.documentary.zoom_map.shot import ZoomMapUnavailable, zoom_asset


async def render_zoom_maps(db: Session, case: Case, plan_row: VisualPlan, language: str) -> dict:
    if not (ai_config.zoom_map and ai_config.zoom_map.enabled):
        return {"skipped": True, "reason": "zoom maps are off"}
    plan = json.loads(plan_row.plan_json or "{}")
    made, fallbacks, seconds, size = [], [], 0.0, 0
    for pb in plan.get("beats") or []:
        for s in pb["shots"]:
            info = s.get("map_zoom")
            if s.get("command") != "SHOW_MAP" or not info:
                continue
            try:
                a = await zoom_asset(db, case, language, info)
            except ZoomMapUnavailable as e:
                chain = await map_chain(db, case, info["place"])
                fallbacks.append({"beat": pb["beat_id"], "place": info["place"], "reason": str(e),
                                  "old_map": bool(chain)})
                continue
            spec = json.loads(a.spec_json or "{}")
            made.append({"beat": pb["beat_id"], "asset": a.asset_code, "place": info["place"],
                         "variant": spec["variant"], "render_seconds": spec["render_seconds"],
                         "bytes": spec["bytes"], "within_budget": spec["within_budget"]})
            seconds += spec["render_seconds"]
            size += spec["bytes"]
    out = {"shots": made, "fallbacks": fallbacks, "render_seconds": round(seconds, 1), "bytes": size}
    if fallbacks:
        out["degraded"] = (f"{len(fallbacks)} map beat(s) use the old map: "
                           + "; ".join(f"{f['place']}: {f['reason']}" for f in fallbacks))
    return out
