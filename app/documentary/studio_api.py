"""Channel studios (registry, previews, profile edits) and host scenes
(plan, run/retry voice and avatar steps, inspect the trace)."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
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
    assert version is not None
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
    from app.documentary.outfits import OutfitError
    from app.outfits.registry import OutfitRegistry

    look = _look(db, look_id)
    try:
        reg = ST.load_registry()
        masters = [a for a in reg.assets_of(look.language) if a.approved_for_host]
        studio = ST.asset_path(reg, masters[0]) if masters else None
        if studio is None or not studio.exists():
            raise ST.StudioError(f"no studio master picture for {look.language} on disk "
                                 f"(data/studio): add it before generating looks")
        if look.host_outfit_id:           # a look of a video's outfit: the picture fitting its stance
            from app.db.models import HostOutfit
            from app.documentary import outfits as OF

            host_outfit = db.get(HostOutfit, look.host_outfit_id)
            assert host_outfit is not None
            outfit_png = OF.reference_png(host_outfit, look.stance)
        else:
            outfit_png = OutfitRegistry().resolve(look.outfit_id).reference_pose(look.stance).path.read_bytes()
        gen = ai_config.generation_provider()
        provider = LK.ApimasterImageProvider(
            gen.base_url, env_value(gen.secret_env) or "", ai_config.host_looks.image_model,
            ai_config.host_looks.image_timeout_s)
        await LK.generate(db, look, provider, studio.read_bytes(), outfit_png)
        return LK.look_dict(look)
    except (LK.LookError, ST.StudioError, KeyError, OutfitError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/api/host-looks/{look_id}/picture")
async def attach_host_look_picture(look_id: int, file: UploadFile = File(...),
                                   db: Session = Depends(get_db)):
    """The owner's own PNG as the look's picture (not generated); it still has to be approved."""
    from app.documentary import looks as LK

    look = _look(db, look_id)
    png = await file.read()
    return _looks_call(lambda: LK.attach_picture(db, look, png, file.filename or "upload"))


@router.post("/api/host-looks/{look_id}/upload")
async def upload_host_look(look_id: int, db: Session = Depends(get_db)):
    """Upload an APPROVED look to HeyGen as a look of the configured character."""
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
    return _looks_call(lambda: LK.regenerate_with_prompt(db, _look(db, look_id), payload.prompt or ""))


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


class SceneRedo(BaseModel):
    reason: str


@router.post("/api/host-scenes/{scene_id}/redo")
def redo_host_scene(scene_id: int, payload: SceneRedo, db: Session = Depends(get_db)):
    """A new direction and a new avatar video for a scene (voice kept; the old ones stay in its
    history). Run the scene afterwards."""
    from app.documentary.host_scenes import redo_video

    s = db.get(HostScene, scene_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Host scene not found")
    task = _tasks.get(scene_id)
    if task is not None and not task.done():
        raise HTTPException(status_code=409, detail="This scene is running.")
    try:
        return scene_dict(redo_video(db, s, payload.reason))
    except ST.StudioError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/api/host-scenes/{scene_id}/voice")
def host_scene_voice(scene_id: int, db: Session = Depends(get_db)):
    s = db.get(HostScene, scene_id)
    p = storage.resolve(s.voice_path) if s is not None and s.voice_path else None
    if p is None or not p.exists():
        raise HTTPException(status_code=404, detail="No voice for this scene yet")
    return FileResponse(p, media_type="audio/mpeg")

@router.get("/api/host-scenes/{scene_id}/video")
def host_scene_video(scene_id: int, db: Session = Depends(get_db)):
    """The scene's avatar video (webm or mp4) as the player needs it."""
    s = db.get(HostScene, scene_id)
    p = storage.resolve(s.avatar_video_path) if s is not None and s.avatar_video_path else None
    if p is None or not p.exists():
        raise HTTPException(status_code=404, detail="No avatar video for this scene yet")
    return FileResponse(p, media_type="video/mp4" if p.suffix == ".mp4" else "video/webm")


@router.get("/api/host-looks/{look_id}/image")
def host_look_image(look_id: int, db: Session = Depends(get_db)):
    """The generated picture of a look (a thumbnail source for the Host tab)."""
    look = _look(db, look_id)
    p = storage.resolve(look.image_path) if look.image_path else None
    if p is None or not p.exists():
        raise HTTPException(status_code=404, detail="This look has no picture yet")
    return FileResponse(p, media_type="image/png")


# ---------------------------------------------------------------------------
# outfits of a video (one case, one language) and their looks
# ---------------------------------------------------------------------------


class OutfitRequest(BaseModel):
    language: str
    name: str
    outfit_ref: str | None = None


def _outfit(db: Session, outfit_id: int):
    from app.db.models import HostOutfit

    outfit = db.get(HostOutfit, outfit_id)
    if outfit is None:
        raise HTTPException(status_code=404, detail="Outfit not found")
    return outfit


def _outfit_view(db: Session, outfit) -> dict:
    from app.documentary import look_selection as LS
    from app.documentary import looks as LK
    from app.documentary import outfits as OF

    looks = [LK.look_dict(x) for x in OF.outfit_looks(db, outfit)]
    return {"id": outfit.id, "case_id": outfit.case_id, "language": outfit.language,
            "name": outfit.name, "outfit_ref": outfit.outfit_ref, "status": outfit.status,
            "selected_at": outfit.selected_at, "looks": looks,
            "approved": sum(1 for x in looks if x["status"] == "approved"),
            "to_approve": LS.missing_looks(db, outfit),
            "studio": {stance: OF.studio_supports(outfit.language, stance)
                       for stance in OF.STANCES}}


@router.get("/api/cases/{case_id}/outfits")
def list_outfits(case_id: int, language: str | None = None, db: Session = Depends(get_db)):
    from app.db.models import HostOutfit

    q = db.query(HostOutfit).filter(HostOutfit.case_id == case_id, HostOutfit.status != "retired")
    if language:
        q = q.filter(HostOutfit.language == language)
    return [_outfit_view(db, o) for o in q.order_by(HostOutfit.id)]


@router.get("/api/outfit-registry")
def outfit_registry():
    """The app's outfits an outfit of a video can be made from."""
    from app.outfits.registry import OutfitRegistry

    return [{"id": o.outfit_id, "label": o.label, "languages": [o.language]}
            for o in OutfitRegistry().all()]


def _outfit_call(fn):
    from app.documentary import looks as LK
    from app.documentary import outfits as OF

    try:
        return fn()
    except (OF.OutfitError, LK.LookError, ST.StudioError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/api/cases/{case_id}/outfits")
def create_outfit(case_id: int, payload: OutfitRequest, db: Session = Depends(get_db)):
    from app.core.ai_config import ai_config
    from app.documentary import outfits as OF

    allow = ai_config.stage_direction.outfit_allow_list.get(payload.language)
    if payload.outfit_ref and allow and payload.outfit_ref not in allow:
        raise HTTPException(status_code=409, detail=f"{payload.outfit_ref} is not allowed in "
                                                    f"{payload.language} (stage_direction.outfit_allow_list)")
    return _outfit_view(db, _outfit_call(lambda: OF.create_outfit(
        db, case_id, payload.language, payload.name, payload.outfit_ref)))


@router.post("/api/cases/{case_id}/outfits/upload")
async def create_outfit_from_upload(case_id: int, language: str = Form(...), name: str = Form(...),
                                    file: UploadFile = File(...), db: Session = Depends(get_db)):
    """An outfit made from the owner's own PNG picture instead of a registry outfit."""
    from app.documentary import outfits as OF

    png = await file.read()
    return _outfit_view(db, _outfit_call(lambda: OF.create_outfit_from_picture(
        db, case_id, language, name, png)))


@router.post("/api/outfits/{outfit_id}/select")
def select_outfit(outfit_id: int, db: Session = Depends(get_db)):
    from app.documentary import outfits as OF

    return _outfit_view(db, _outfit_call(lambda: OF.select_outfit(db, _outfit(db, outfit_id))))


@router.post("/api/outfits/{outfit_id}/retire")
def retire_outfit(outfit_id: int, db: Session = Depends(get_db)):
    from app.documentary import outfits as OF

    return _outfit_view(db, _outfit_call(lambda: OF.retire_outfit(db, _outfit(db, outfit_id))))


class OutfitLookRequest(BaseModel):
    camera: str
    framing: str
    stance: str
    pose: str = "P01_NEUTRAL_SEATED"
    prompt: str | None = None


@router.post("/api/outfits/{outfit_id}/looks")
def request_outfit_look(outfit_id: int, payload: OutfitLookRequest, db: Session = Depends(get_db)):
    from app.documentary import looks as LK
    from app.documentary import outfits as OF

    outfit = _outfit(db, outfit_id)
    return LK.look_dict(_outfit_call(lambda: OF.request_look(
        db, outfit, payload.camera, payload.framing, payload.stance, payload.pose, payload.prompt)))


@router.post("/api/outfits/{outfit_id}/pose-set")
def request_pose_set(outfit_id: int, db: Session = Depends(get_db)):
    """The standard set of looks of the outfit in one click (requested, not generated)."""
    from app.documentary import looks as LK
    from app.documentary import outfits as OF

    outfit = _outfit(db, outfit_id)
    done = _outfit_call(lambda: OF.request_pose_set(db, outfit))
    return {"requested": [LK.look_dict(x) for x in done["requested"]],
            "already_there": [LK.look_dict(x) for x in done["already_there"]],
            "skipped": done["skipped"], "outfit": _outfit_view(db, outfit)}


class LookOverride(BaseModel):
    look_id: int


# ---------------------------------------------------------------------------
# the Outfits panel (O2): one row per language, the outfit choice, the fixed picture
# ---------------------------------------------------------------------------


class PanelSelect(BaseModel):
    outfit_id: int


class PanelFixedLook(BaseModel):
    outfit_id: int
    look_id: int | None = None


class PanelSamePicture(BaseModel):
    outfit_id: int
    look_id: int


def _case_outfit(db: Session, case_id: int, outfit_id: int):
    outfit = _outfit(db, outfit_id)
    if outfit.case_id != case_id:
        raise HTTPException(status_code=404, detail="Outfit not found")
    return outfit


@router.get("/api/cases/{case_id}/outfit-panel")
def outfit_panel(case_id: int, db: Session = Depends(get_db)):
    from app.documentary import outfits as OF

    return OF.panel(db, case_id)


@router.post("/api/cases/{case_id}/outfit-panel/select")
def outfit_panel_select(case_id: int, payload: PanelSelect, db: Session = Depends(get_db)):
    from app.documentary import outfits as OF

    _outfit_call(lambda: OF.select_outfit(db, _case_outfit(db, case_id, payload.outfit_id)))
    return OF.panel(db, case_id)


@router.post("/api/cases/{case_id}/outfit-panel/same-outfit")
def outfit_panel_same_outfit(case_id: int, payload: PanelSelect, db: Session = Depends(get_db)):
    """Checkbox A: the same-named outfit becomes the choice of every language that has one."""
    from app.documentary import outfits as OF

    result = _outfit_call(lambda: OF.apply_same_outfit(db, case_id, payload.outfit_id))
    return {**OF.panel(db, case_id), "result": result}


@router.post("/api/cases/{case_id}/outfit-panel/fixed-look")
def outfit_panel_fixed_look(case_id: int, payload: PanelFixedLook,
                            db: Session = Depends(get_db)):
    """Checkbox C: pin one approved picture of the language's outfit for every appearance."""
    from app.documentary import outfits as OF

    _outfit_call(lambda: OF.set_fixed_look(db, _case_outfit(db, case_id, payload.outfit_id),
                                           payload.look_id))
    return OF.panel(db, case_id)


@router.post("/api/cases/{case_id}/outfit-panel/same-picture")
def outfit_panel_same_picture(case_id: int, payload: PanelSamePicture,
                              db: Session = Depends(get_db)):
    """Checkbox B: pin the same pose/camera/framing in every same-named outfit that has it."""
    from app.documentary import outfits as OF

    result = _outfit_call(lambda: OF.apply_same_picture(db, case_id, payload.outfit_id,
                                                        payload.look_id))
    return {**OF.panel(db, case_id), "result": result}


@router.post("/api/host-scenes/{scene_id}/look")
def override_scene_look(scene_id: int, payload: LookOverride, db: Session = Depends(get_db)):
    """The owner picks a look for a scene: only an approved look of the scene's own outfit."""
    from app.db.models import HostLook
    from app.documentary import look_selection as LS
    from app.documentary import outfits as OF
    from app.documentary.host_scenes import _log

    s = db.get(HostScene, scene_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Host scene not found")
    outfit = OF.selected_outfit(db, s.case_id, s.language)
    look = db.get(HostLook, payload.look_id)
    if outfit is None or look is None or look.id not in {x.id for x in LS.approved_looks(db, outfit)}:
        raise HTTPException(status_code=409, detail=(
            "Only an approved look of this video's outfit (in this language's studio) can be chosen."))
    if s.status in ("avatar_requested", "avatar_ready", "composited"):
        raise HTTPException(status_code=409, detail="The video is already made with another look.")
    s.host_outfit_id, s.look_id, s.avatar_id = outfit.id, look.id, look.heygen_look_id
    choice = {"look_id": look.id, "override": True,
              "chosen": {"stance": look.stance, "camera": look.camera, "framing": look.framing},
              "why": "chosen by the owner"}
    s.look_choice_json = json.dumps(choice)
    _log(s, "look", "override", choice)
    db.commit()
    return scene_dict(s)
