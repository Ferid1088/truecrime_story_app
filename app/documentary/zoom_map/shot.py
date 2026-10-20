"""The library asset of one zoom-in map shot: rendered once per case, language, place and return,
then reused. The asset is a silent video with its own attribution and provenance."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals import tiers as T
from app.documentary.visuals.research import next_asset_code
from app.documentary.zoom_map import camera as CAM
from app.documentary.zoom_map import data as DATA
from app.documentary.zoom_map import registry as REG
from app.documentary.zoom_map import renderer as RENDER
from app.documentary.zoom_map import runtime as RUNTIME
from app.documentary.zoom_map.limits import head
from app.documentary.zoom_map.server import Fetcher


class ZoomMapUnavailable(RuntimeError):
    """Why a map beat could not get its zoom shot (the old map is used and this is reported)."""


def variant_for(info: dict) -> str:
    """first mention -> zoom_in, a move from another place -> route, a place coming back -> pitched_3d."""
    rules = ai_config.zoom().variant_rules
    if info.get("return"):
        return rules["return"]
    if info.get("after"):
        return rules["move"]
    return rules["first"]


def spec_key(language: str, place: str, returns: int, variant: str, lat: float, lon: float) -> str:
    """One shot per language, place (where it was found, so another answer of the geocoder is another shot), return
    and variant."""
    return f"zoom|{language}|{head(place).lower()}@{lat:.3f},{lon:.3f}|{returns}|{variant}"


def usage_place(place: str, returns: int) -> str:
    """The place name the repetition tracker counts: every allowed return is a new appearance."""
    return place if not returns else f"{place} (return {returns})"


def within_budget(render_seconds: float, video_seconds: float) -> bool:
    return render_seconds <= float(ai_config.zoom().render["budget_seconds_per_video_second"]) * video_seconds


def find(db: Session, case_id: int, key: str) -> VisualAsset | None:
    for a in db.query(VisualAsset).filter(
            VisualAsset.case_id == case_id, VisualAsset.provider == "generated",
            VisualAsset.asset_type == "map").all():
        if json.loads(a.spec_json or "{}").get("key") == key:
            return a
    return None


async def zoom_asset(db: Session, case: Case, language: str, info: dict,
                     fetcher: Fetcher | None = None, renderer=RENDER.render) -> VisualAsset:
    """The zoom shot for one map beat. `info`: place, lat, lon, granularity, return (0 = first time),
    after = {"place","lat","lon"} of the place shown before (route variant)."""
    cfg = ai_config.zoom()
    place, returns = info["place"], int(info.get("return") or 0)
    variant = variant_for(info)
    key = spec_key(language, place, returns, variant, float(info["lat"]), float(info["lon"]))
    existing = find(db, case.id, key)
    if existing is not None and Path(storage.ROOT / (existing.local_path or "")).exists():
        return existing
    missing = RUNTIME.missing(cfg)
    if missing:
        raise ZoomMapUnavailable(f"map runtime missing: {', '.join(missing)}")
    target = (float(info["lat"]), float(info["lon"]))
    other = (float(info["after"]["lat"]), float(info["after"]["lon"])) if info.get("after") else None
    try:
        extract = DATA.ensure_extract(case.id, [target] + ([other] if other else []), cfg)
    except DATA.MapDataError as e:
        raise ZoomMapUnavailable(str(e)) from e
    plan = CAM.build_plan(cfg, variant, target, info.get("granularity") or "city", returns,
                          other=other if variant == "route" else None)
    code = next_asset_code(db)
    out = storage.visuals_dir(case.id, "map") / f"{code}.mp4"
    render = ai_config.render
    try:
        res = await renderer(plan, case.id, Path(extract["path"]), out, language,
                             render.width, render.height, render.fps, fetcher=fetcher)
    except RENDER.RenderError as e:
        raise ZoomMapUnavailable(str(e)) from e
    thumb = storage.thumbs_dir(case.id) / f"{code}.jpg"
    IM.save_thumbnail(Image.open(io.BytesIO(res.last_frame)).convert("RGB"), thumb)
    tier, why = T.provisional_why("generated", "context", "place")
    attribution = res.attribution
    spec = {
        "key": key, "kind": "zoom", "variant": variant, "language": language,
        "place": usage_place(place, returns), "place_name": place, "return": returns,
        "lat": target[0], "lon": target[1], "granularity": info.get("granularity"),
        "route_from": other, "seconds": res.seconds, "frames": res.frames,
        "fps": render.fps, "size": [render.width, render.height],
        "attribution": attribution, "imagery": res.imagery,
        "imagery_best_zoom": REG.best_zoom(*target, cfg),
        "vector_data": {k: extract[k] for k in ("build", "source", "bbox", "created", "licence")},
        "frames_sha256": hashlib.sha256("".join(res.frame_hashes).encode()).hexdigest(),
        "render_seconds": res.render_seconds, "bytes": res.file_bytes,
        "within_budget": within_budget(res.render_seconds, res.seconds),
        "tier_reason": why,
        "marker": {"lat": target[0], "lon": target[1]}}
    a = VisualAsset(
        case_id=case.id, asset_code=code, asset_type="map", subject_type="geography",
        title=f"Zoom map: {place} ({variant})", description=f"{variant} to {place}",
        location=place, provider="generated", asset_role="context",
        rights_status="owned", rights_reason="generated from OpenStreetMap data and open imagery",
        credit="; ".join(attribution), license="ODbL (map data); imagery licences in the asset spec",
        verification_status="verified", verification_confidence=1.0,
        local_path=storage.rel(out), thumbnail_path=storage.rel(thumb),
        width=render.width, height=render.height, duration_seconds=res.seconds,
        relevance_tier=tier, case_relevance=T.tier_label(tier),
        entity_type="place", found_during="generated", spec_json=json.dumps(spec))
    db.add(a)
    db.commit()
    return a


def zoom_shot(plan_shot: dict, assets: dict[str, VisualAsset], language: str) -> dict | None:
    """The fields of a map shot played from its zoom video, or None when the plan's map beat has no
    zoom video in this language (the old map takes its place and the stage reported why)."""
    info = plan_shot.get("map_zoom")
    if not info:
        return None
    key = spec_key(language, info["place"], int(info.get("return") or 0), variant_for(info),
                   float(info["lat"]), float(info["lon"]))
    for a in assets.values():
        if a.asset_type != "map" or a.provider != "generated":
            continue
        spec = json.loads(a.spec_json or "{}")
        if spec.get("key") == key and spec.get("kind") == "zoom":
            return {"fields": {
                "kind": "map", "zoom_video": True, "path": a.local_path, "clip_start": 0.0,
                "clip_end": spec["seconds"], "asset_id": a.asset_code, "type": "map",
                "motion": "NONE", "marker": spec.get("marker"), "attribution": spec["attribution"],
                "map_variant": spec["variant"]}}
    return None


def fallback_chain(assets: dict[str, VisualAsset], place: str) -> list[VisualAsset]:
    """The old map (stills from wide to close) of a place, when its zoom shot could not be made."""
    prefix = f"map|{place.lower()}|"
    found = []
    for a in assets.values():
        spec = json.loads(a.spec_json or "{}") if a.asset_type == "map" else {}
        if str(spec.get("key") or "").startswith(prefix):
            found.append((spec.get("zoom") or 0, a))
    return [a for _, a in sorted(found, key=lambda x: x[0])]
