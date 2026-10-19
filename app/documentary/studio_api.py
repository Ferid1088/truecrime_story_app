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
        raise HTTPException(status_code=422, detail=str(e)) from e
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
        raise HTTPException(status_code=404, detail=str(e)) from e


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


@router.get("/api/cases/{case_id}/host-time/{language}")
def host_time(case_id: int, language: str, db: Session = Depends(get_db)):
    """Host time of the film in this language on the real voice seconds."""
    from app.core.ai_config import ai_config
    from app.db.models import StoryVersion
    from app.documentary.host_scenes import real_time_report

    latest = (db.query(HostSegments).filter(HostSegments.case_id == case_id,
                                            HostSegments.language == language)
              .order_by(HostSegments.id.desc()).first())
    if latest is None:
        raise HTTPException(status_code=404, detail="No host segments for this language")
    scenes = db.query(HostScene).filter(HostScene.host_segments_id == latest.id).all()
    version = db.get(StoryVersion, latest.story_version_id)
    narration = len((version.story_text or "").split()) / ai_config.documentary.wpm(language) * 60
    from app.documentary.intros import intro_seconds

    return {"host_segments_id": latest.id, "language": language,
            "narration_seconds": round(narration, 1),
            **real_time_report(scenes, narration, intro_seconds(language) or 0.0)}


@router.get("/api/host-appearances")
def list_host_appearances(case_id: int | None = None, channel: str | None = None,
                          db: Session = Depends(get_db)):
    """The host's visual memory, newest first."""
    from app.db.models import HostAppearance
    from app.documentary.visual_memory import appearance_dict

    q = db.query(HostAppearance)
    if case_id is not None:
        q = q.filter(HostAppearance.case_id == case_id)
    if channel:
        q = q.filter(HostAppearance.channel == channel)
    return [appearance_dict(r) for r in q.order_by(
        HostAppearance.created_at.desc(), HostAppearance.id.desc()).limit(200)]


@router.delete("/api/host-appearances/{appearance_id}")
def delete_host_appearance(appearance_id: int, db: Session = Depends(get_db)):
    """Remove a wrong entry from the visual memory (it stops steering the
    director; the scene keeps its stored direction)."""
    from app.db.models import HostAppearance

    row = db.get(HostAppearance, appearance_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Appearance not found")
    db.delete(row)
    db.commit()
    return {"id": appearance_id, "deleted": True}


class LookRequest(BaseModel):
    language: str
    camera: str
    framing: str
    outfit_id: str
    pose: str
    prompt: str | None = None


def _look(db: Session, look_id: int):
    from app.db.models import HostLook

    look = db.get(HostLook, look_id)
    if look is None:
        raise HTTPException(status_code=404, detail="Look not found")
    return look


def _looks_call(fn):
    from app.documentary import looks as LK

    try:
        return LK.look_dict(fn())
    except (LK.LookError, ST.StudioError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/api/host-looks")
def list_host_looks(language: str | None = None, db: Session = Depends(get_db)):
    from app.db.models import HostLook
    from app.documentary import looks as LK

    q = db.query(HostLook)
    if language:
        q = q.filter(HostLook.language == language)
    return [LK.look_dict(x) for x in q.order_by(HostLook.id.desc()).limit(200)]


@router.post("/api/host-looks")
def request_host_look(payload: LookRequest, db: Session = Depends(get_db)):
    from app.documentary import looks as LK

    return _looks_call(lambda: LK.request_look(
        db, payload.language, payload.camera, payload.framing, payload.outfit_id,
        payload.pose, payload.prompt))


@router.post("/api/host-looks/{look_id}/generate")
async def generate_host_look(look_id: int, db: Session = Depends(get_db)):
    """One real image generation (booked in the ledger): the locked studio
    master and the outfit picture are the references."""
    from app.core.ai_config import ai_config
    from app.core.env import env_value
    from app.documentary import looks as LK
    from app.outfits.registry import OutfitRegistry

    look = _look(db, look_id)
    try:
        reg = ST.load_registry()
        masters = [a for a in reg.assets_of(look.language) if a.approved_for_host]
        studio = ST.asset_path(reg, masters[0]) if masters else None
        if studio is None or not studio.exists():
            raise ST.StudioError(f"no studio master picture for {look.language} on disk "
                                 f"(data/studio): add it before generating looks")
        outfit = OutfitRegistry().resolve(look.outfit_id).poses["front"].path
        gen = ai_config.generation_provider()
        provider = LK.ApimasterImageProvider(
            gen.base_url, env_value(gen.secret_env) or "", ai_config.host_looks.image_model,
            ai_config.host_looks.image_timeout_s)
        await LK.generate(db, look, provider, studio.read_bytes(), outfit.read_bytes())
        return LK.look_dict(look)
    except (LK.LookError, ST.StudioError, KeyError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/api/host-looks/{look_id}/upload")
async def upload_host_look(look_id: int, db: Session = Depends(get_db)):
    """Upload an APPROVED look to HeyGen as a look of the configured character."""
    from app.core.ai_config import ai_config
    from app.core.env import env_value
    from app.documentary import looks as LK
    from app.providers.avatar import get_avatar_provider
    from app.providers.avatar.base import AvatarProviderError

    look = _look(db, look_id)
    try:
        LK.require_approved_for_upload(look)
        cp = ST.channel_profile(look.language)
        group = env_value(cp["heygen_avatar_env"]) or ""
        if not group:
            raise ST.StudioError(f"{cp['heygen_avatar_env']} is not set (.env)")
        prov = get_avatar_provider(key_env=cp["heygen_key_env"])
        await LK.upload_to_heygen(db, look, prov, group)
        return LK.look_dict(look)
    except (LK.LookError, ST.StudioError, AvatarProviderError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


class LookDecision(BaseModel):
    by: str | None = None
    reason: str | None = None
    prompt: str | None = None


@router.post("/api/host-looks/{look_id}/approve")
def approve_host_look(look_id: int, payload: LookDecision, db: Session = Depends(get_db)):
    from app.documentary import looks as LK

    return _looks_call(lambda: LK.approve(db, _look(db, look_id), payload.by or "owner"))


@router.post("/api/host-looks/{look_id}/reject")
def reject_host_look(look_id: int, payload: LookDecision, db: Session = Depends(get_db)):
    from app.documentary import looks as LK

    return _looks_call(lambda: LK.reject(db, _look(db, look_id), payload.reason or "rejected"))


@router.post("/api/host-looks/{look_id}/regenerate")
def regenerate_host_look(look_id: int, payload: LookDecision, db: Session = Depends(get_db)):
    from app.documentary import looks as LK

    if not (payload.prompt or "").strip():
        raise HTTPException(status_code=422, detail="a prompt is required")
    return _looks_call(lambda: LK.regenerate_with_prompt(db, _look(db, look_id), payload.prompt))


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
        raise HTTPException(status_code=422, detail=str(e)) from e


class SceneRun(BaseModel):
    until: str = "voice"   # voice | avatar


async def _run_task(scene_id: int, until: str) -> None:
    db = SessionLocal()
    try:
        s = db.get(HostScene, scene_id)
        if s is not None:
            from app.agents.stage_director import StageDirector
            from app.documentary.host import HostDirector

            await run_scene(db, s, until, retimer=HostDirector().retime_scene,
                            director=StageDirector().direct)
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
