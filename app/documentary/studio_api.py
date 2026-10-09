"""Channel studios (registry, previews, profile edits) and host scenes
(plan, run/retry voice and avatar steps, inspect the trace)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import SessionLocal, get_db
from app.db.models import HostScene, HostSegments
from app.documentary import storage
from app.documentary import studio as ST
from app.documentary.host_scenes import plan_host_scenes, run_scene, scene_dict

log = logging.getLogger(__name__)
router = APIRouter()
_tasks: dict[int, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# studios
# ---------------------------------------------------------------------------


@router.get("/api/studios")
def list_studios():
    return ST.studios_view()


class StudioPatch(BaseModel):
    primary_background: str | None = None
    presets: dict[str, str] | None = None   # HOST_CLOSE/HOST_MEDIUM/HOST_WIDE → asset id
    confirm: bool | None = None


@router.patch("/api/studios/{language}")
def update_studio(language: str, payload: StudioPatch):
    try:
        check = ST.update_profile(language, payload.primary_background, payload.presets,
                                  payload.confirm, reviewer="studio settings")
    except ST.StudioError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"validation": check, **ST.studios_view()}


@router.get("/api/studios/assets/{asset_id}/image")
def studio_image(asset_id: str, full: bool = False):
    reg = ST.load_registry()
    a = reg.asset(asset_id)
    if a is None:
        raise HTTPException(status_code=404, detail="unknown studio asset")
    try:
        if full:
            p = ST.asset_path(reg, a)
            if not p.exists():
                raise ST.StudioError("image file missing")
            return FileResponse(p, media_type="image/png")
        return FileResponse(ST.thumbnail(asset_id, reg), media_type="image/jpeg")
    except ST.StudioError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/api/studios/sync")
def sync_studio_files():
    """Re-read size and sha256 from the image files (no model involved);
    reports what changed. Classification and approvals are kept."""
    reg = ST.load_registry()
    changed = []
    for a in reg.assets:
        p = ST.asset_path(reg, a)
        if not p.exists():
            continue
        facts = ST.file_facts(p)
        diff = {k: v for k, v in facts.items() if getattr(a, k) != v}
        if diff:
            changed.append({"id": a.id, **{k: True for k in diff}})
            for k, v in facts.items():
                setattr(a, k, v)
    if changed:
        ST.save_registry(reg)
    return {"changed": changed, "validation": ST.validate_all(reg)}


# ---------------------------------------------------------------------------
# host scenes
# ---------------------------------------------------------------------------


@router.get("/api/host-scenes")
def list_host_scenes(case_id: int | None = None, language: str | None = None,
                     db: Session = Depends(get_db)):
    q = db.query(HostScene)
    if case_id is not None:
        q = q.filter(HostScene.case_id == case_id)
    if language:
        q = q.filter(HostScene.language == language)
    return [scene_dict(s) for s in q.order_by(HostScene.id.desc()).limit(200)]


@router.get("/api/host-scenes/{scene_id}")
def get_host_scene(scene_id: int, db: Session = Depends(get_db)):
    s = db.get(HostScene, scene_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Host scene not found")
    return scene_dict(s)


@router.post("/api/host-segments/{host_segments_id}/scenes")
def plan_scenes(host_segments_id: int, db: Session = Depends(get_db)):
    row = db.get(HostSegments, host_segments_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Host segments not found")
    try:
        return [scene_dict(s) for s in plan_host_scenes(db, row)]
    except ST.StudioError as e:
        raise HTTPException(status_code=422, detail=str(e))


class SceneRun(BaseModel):
    until: str = "voice"   # voice | avatar


async def _run_task(scene_id: int, until: str) -> None:
    db = SessionLocal()
    try:
        s = db.get(HostScene, scene_id)
        if s is not None:
            await run_scene(db, s, until)
    except Exception as e:  # recorded on the scene by run_scene
        log.warning("host scene %s stopped: %s", scene_id, e)
    finally:
        db.close()
        _tasks.pop(scene_id, None)


@router.post("/api/host-scenes/{scene_id}/run", status_code=202)
async def run_host_scene(scene_id: int, payload: SceneRun, db: Session = Depends(get_db)):
    """Start (or retry) the scene's steps up to `until` in the background.
    Steps whose saved result still matches are skipped; see the scene's
    history for every attempt."""
    s = db.get(HostScene, scene_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Host scene not found")
    if payload.until not in ("voice", "avatar"):
        raise HTTPException(status_code=422, detail="until must be 'voice' or 'avatar'")
    task = _tasks.get(scene_id)
    if task is not None and not task.done():
        raise HTTPException(status_code=409, detail="This scene is already running.")
    from app.core.ai_config import ai_config

    if payload.until == "avatar" and not ai_config.avatar.enabled:
        raise HTTPException(status_code=409, detail=(
            "Avatar generation is disabled (config avatar.enabled). The voice step works."))
    _tasks[scene_id] = asyncio.create_task(_run_task(scene_id, payload.until))
    return scene_dict(s)


@router.get("/api/host-scenes/{scene_id}/voice")
def host_scene_voice(scene_id: int, db: Session = Depends(get_db)):
    s = db.get(HostScene, scene_id)
    p = storage.resolve(s.voice_path) if s is not None and s.voice_path else None
    if p is None or not p.exists():
        raise HTTPException(status_code=404, detail="No voice for this scene yet")
    return FileResponse(p, media_type="audio/mpeg")
