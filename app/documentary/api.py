"""Documentary production API (Parts 43–49): one-button jobs and the data
behind every tab — Blueprint, Language Versions, Voice, Visual Library,
Timeline, Music & Sound, Production Script, Critique, Render.
Secrets are never exposed."""

from __future__ import annotations

import json
import logging
import os
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.tasks import spawn
from app.db.base import get_db
from app.db.models import (
    Case, DocumentaryJob, ProductionScript, StoryVersion, VisualAsset, VisualPlan,
)
from app.documentary import jobs as J
from app.documentary import storage
from app.documentary.audio_director import audio_plan_dict, latest_audio_plan
from app.documentary.blueprint import blueprint_dict, latest_blueprint
from app.documentary.visuals import rights as R

log = logging.getLogger(__name__)
router = APIRouter(tags=["documentary"])


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (ValueError, TypeError):
        return default


def _masters(db: Session, case_id: int) -> list[StoryVersion]:
    return (db.query(StoryVersion)
            .filter(StoryVersion.case_id == case_id,
                    StoryVersion.kind.in_(("master", "direct")))
            .order_by(StoryVersion.id.desc()).all())


def _pick_master(db: Session, case_id: int, version_id: int | None) -> StoryVersion:
    if version_id:
        v = db.get(StoryVersion, version_id)
        if not v or v.case_id != case_id:
            raise HTTPException(status_code=404, detail="Story version not found")
        if v.kind != "master" or (v.language or "en") != ai_config.multilingual.canonical_language:
            raise HTTPException(status_code=409,
                                detail="A film starts from the English master story.")
        return v
    masters = [m for m in _masters(db, case_id) if (m.language or "en") == "en"]
    if not masters:
        raise HTTPException(status_code=409, detail="Write an English master story first.")
    # prefer one that already has a blueprint (work is reused)
    for m in masters:
        if latest_blueprint(db, m.id):
            return m
    return masters[0]


def asset_dict(a: VisualAsset) -> dict:
    return {
        "id": a.id, "asset_id": a.asset_code, "type": a.asset_type,
        "subject_type": a.subject_type, "title": a.title, "description": a.description,
        "caption": a.caption, "entities": _loads(a.entities_json, []),
        "role": a.asset_role, "provider": a.provider, "source_url": a.source_url,
        "page_url": a.page_url, "source_name": a.source_name, "found_for": a.found_for,
        "license": a.license, "credit": a.credit, "rights": a.rights_status,
        "rights_reason": a.rights_reason,
        "usable_preview": R.allowed(a.rights_status, "preview"),
        "usable_publish": R.allowed(a.rights_status, "publish"),
        "verification": a.verification_status,
        "verification_confidence": a.verification_confidence,
        "verification_detail": _loads(a.verification_json, None),
        "quality": a.quality_score, "reveals": _loads(a.reveals_json, []),
        "width": a.width, "height": a.height, "date": a.date_start,
        "location": a.location, "human_override": a.human_override,
        "image_url": f"/api/visuals/{a.id}/file",
        "thumbnail_url": f"/api/visuals/{a.id}/file?thumb=1",
        "created_at": a.created_at,
        "video": _video_info(a),
    }


def _video_info(a: VisualAsset) -> dict | None:
    """A kept video: its pieces or why it is not cut yet. A piece: its
    window in the video and why it starts and ends there."""
    if a.asset_type not in ("video", "video_source"):
        return None
    spec = _loads(a.spec_json, {}) or {}
    if a.asset_type == "video_source":
        return {"kind": "source", "pieces": len(spec.get("pieces") or []),
                "segment_error": spec.get("segment_error"),
                "duration": a.duration_seconds}
    return {"kind": "piece" if spec.get("parent") else "clip", "parent": spec.get("parent"),
            "window": spec.get("window") or [a.clip_start, a.clip_end],
            "cut_by": spec.get("cut_by"), "why_here": spec.get("why_here")}


def production_dict(ps: ProductionScript, full: bool = True) -> dict:
    out = {
        "id": ps.id, "story_version_id": ps.story_version_id, "language": ps.language,
        "version": ps.version, "mode": ps.mode, "status": ps.status,
        "duration_seconds": ps.duration_seconds,
        "critique": _loads(ps.critique_json, None),
        "audit": _loads(ps.audit_json, None),
        "render": _loads(ps.render_json, None),
        "video_url": f"/api/documentary/production/{ps.id}/video.mp4" if ps.render_json else None,
        "subtitles_url": f"/api/documentary/production/{ps.id}/subtitles.srt" if ps.render_json else None,
        "created_at": ps.created_at,
    }
    if full:
        out["script"] = _loads(ps.script_json, {})
    return out


# ---------------------------------------------------------------------------
# overview + jobs
# ---------------------------------------------------------------------------


@router.get("/api/documentary/settings")
def documentary_settings():
    d = ai_config.documentary
    return {
        "languages": d.languages,
        "film_minutes": {"min": d.min_film_minutes, "max": d.max_film_minutes},
        "pilot_seconds": d.pilot_seconds,
        "voices": {l: {"voice_id": v.voice_id, "model_id": v.model_id,
                       "language_code": v.language_code,
                       "audio_tags": v.model_id in ai_config.voice.elevenlabs.audio_tag_models}
                   for l, v in ai_config.voice.languages.items()},
        "pronunciation_check": {
            "enabled": ai_config.pronunciation.enabled,
            "languages": ai_config.pronunciation.languages,
            "max_rounds": ai_config.pronunciation.max_rounds,
        },
        "voice_performance": {
            "enabled": ai_config.voice_performance.enabled,
            "level_tags": ai_config.voice_performance.level_tags,
            "level_styles": ai_config.voice_performance.level_styles,
        },
        "concurrency": ai_config.concurrency.model_dump(),
        "styles": {k: v.model_dump() for k, v in ai_config.voice.styles.items()},
        "render": ai_config.render.model_dump(),
        "rights_profiles": ai_config.rights.allowed_for_render,
        "dynamic_eq": _dynamic_eq_settings(),
        "channels": {l: c.model_dump() for l, c in ai_config.channels.items()},
        # avatar video generation (credits) is off until enabled in config
        "avatar_generation": {"enabled": ai_config.avatar.enabled,
                              "output_format": ai_config.avatar.output_format},
        # presence only — never the values
        "credentials": {
            "elevenlabs": bool(os.getenv(ai_config.voice.elevenlabs.secret_env)),
            "avatar": {"provider": ai_config.avatar.provider, **ai_config.avatar.configured()},
        },
    }


def _dynamic_eq_settings() -> dict:
    """Studio-facing view of the dynamic EQ (full detail lives in
    config/ (connections.json, models.json, parameters/) → dynamic_eq)."""
    d = ai_config.dynamic_eq
    return {
        "enabled": d.enabled,
        "strength": d.strength,
        "attack_ms": d.attack_ms,
        "release_ms": d.release_ms,
        "bands": [{"name": b.name, "center_hz": b.center_hz,
                   "max_atten_db": b.max_atten_db,
                   "threshold_offset_db": b.threshold_offset_db}
                  for b in d.bands],
        "deesser": {"enabled": d.deesser.enabled, "strength": d.deesser.strength,
                    "center_hz": d.deesser.center_hz,
                    "max_atten_db": d.deesser.max_atten_db},
        "languages": sorted(d.languages),
    }


class DynamicEQPatch(BaseModel):
    """Studio controls; anything left out keeps its configured value."""
    enabled: bool | None = None
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    max_atten_db: float | None = Field(default=None, ge=0.0, le=24.0)
    deesser_enabled: bool | None = None
    deesser_strength: float | None = Field(default=None, ge=0.0, le=1.0)


@router.patch("/api/documentary/settings/dynamic-eq")
def update_dynamic_eq_settings(payload: DynamicEQPatch):
    """Update the dynamic EQ (persisted to config/ (connections.json, models.json, parameters/),
    effective immediately — applies to the next voice render/preview,
    never retroactively to already-rendered audio)."""
    from app.core.ai_config import save_dynamic_eq

    cfg = ai_config.dynamic_eq
    update: dict = {}
    if payload.enabled is not None:
        update["enabled"] = payload.enabled
    if payload.strength is not None:
        update["strength"] = payload.strength
    if payload.max_atten_db is not None:
        update["bands"] = [
            b.model_copy(update={"max_atten_db": payload.max_atten_db})
            for b in cfg.bands
        ]
    de: dict = {}
    if payload.deesser_enabled is not None:
        de["enabled"] = payload.deesser_enabled
    if payload.deesser_strength is not None:
        de["strength"] = payload.deesser_strength
    if de:
        update["deesser"] = cfg.deesser.model_copy(update=de)
    cfg = save_dynamic_eq(cfg.model_copy(update=update))
    return {"dynamic_eq": _dynamic_eq_settings(),
            "note": "Applies to the next narration render or EQ preview; "
                    "existing audio is unchanged."}


@router.get("/api/cases/{case_id}/documentary")
def documentary_overview(case_id: int, version_id: int | None = None,
                         db: Session = Depends(get_db)):
    _case(db, case_id)
    masters = _masters(db, case_id)
    master = None
    try:
        master = _pick_master(db, case_id, version_id)
    except HTTPException:
        pass
    out = {
        "masters": [{"id": m.id, "version": m.version, "language": m.language,
                     "status": m.status, "words": len((m.story_text or "").split()),
                     "kind": m.kind} for m in masters],
        "master_version_id": master.id if master else None,
        "film_minutes": {"min": ai_config.documentary.min_film_minutes,
                         "max": ai_config.documentary.max_film_minutes},
        "blueprint": None, "audio_plan": None, "languages": {}, "visual_plan": None,
        "jobs": [J.job_dict(j) for j in db.query(DocumentaryJob)
                 .filter(DocumentaryJob.case_id == case_id)
                 .order_by(DocumentaryJob.id.desc()).limit(10).all()],
        "visual_counts": {},
    }
    assets = db.query(VisualAsset).filter(VisualAsset.case_id == case_id).all()
    for a in assets:
        out["visual_counts"][a.verification_status] = out["visual_counts"].get(
            a.verification_status, 0) + 1
    if not master:
        return out
    bp = latest_blueprint(db, master.id)
    if bp:
        out["blueprint"] = blueprint_dict(bp)
        ap = latest_audio_plan(db, bp.id)
        out["audio_plan"] = audio_plan_dict(ap) if ap else None
        vp = J.latest_visual_plan(db, bp.id)
        if vp:
            out["visual_plan"] = {"id": vp.id, "status": vp.status, "version": vp.version,
                                  "validation": _loads(vp.validation_json, {})}
        plan = json.loads(ap.plan_json) if ap else None
        for lang in ai_config.documentary.languages:
            v = J.latest_spoken(db, master, lang, bp.id)
            if not v:
                out["languages"][lang] = None
                continue
            crit = _loads(v.critic_notes, {})
            ps = J.latest_production(db, v.id)
            from app.documentary.voice_performance import latest_performance

            vperf = latest_performance(db, v.id)
            vstats = (_loads(vperf.performance_json, {}) or {}).get("stats") if vperf else None
            out["languages"][lang] = {
                "version_id": v.id, "status": v.status,
                "voice_performance": {"id": vperf.id, "status": vperf.status,
                                      "stats": vstats} if vperf else None,
                "quality_gates": crit.get("quality_gates"),
                "storyteller_beats": (crit.get("spoken") or {}).get("storyteller_beats"),
                "beats": (crit.get("spoken") or {}).get("beats"),
                "estimated_film_minutes": J.estimate_film_minutes(v, plan),
                "production": production_dict(ps, full=False) if ps else None,
            }
    return out


class JobRequest(BaseModel):
    master_version_id: int | None = None
    languages: list[str] = Field(default_factory=lambda: list(ai_config.documentary.languages))
    mode: Literal["pilot", "full"] = "pilot"
    pilot_seconds: float | None = Field(default=None, gt=10, le=1800)
    render_profile: Literal["preview", "publish"] = "preview"
    refresh_visuals: bool = False
    # "From zero": research the case and write the master story first.
    from_zero: bool = False
    target_minutes: float | None = Field(default=None, ge=45, le=120)


def _check_languages(languages: list[str]) -> list[str]:
    langs = [l for l in languages if l in ai_config.documentary.languages]
    if not langs:
        raise HTTPException(status_code=422, detail="No supported language selected.")
    for l in langs:
        if not (ai_config.voice.languages.get(l) and ai_config.voice.languages[l].voice_id):
            raise HTTPException(status_code=409, detail=f"No narrator voice configured for {l}.")
    return langs


def _start_job(db: Session, case: Case, payload: JobRequest, langs: list[str],
               batch_id: str | None = None) -> DocumentaryJob:
    master = None
    if payload.master_version_id or not payload.from_zero:
        try:
            master = _pick_master(db, case.id, payload.master_version_id)
        except HTTPException:
            if not payload.from_zero:
                raise
    running = db.query(DocumentaryJob).filter(
        DocumentaryJob.case_id == case.id,
        DocumentaryJob.status.in_(("queued", "running", "cancelling"))).first()
    if running:
        raise HTTPException(status_code=409, detail=f"Job {running.id} is still running.")
    job = J.create_job(db, case, master, langs, payload.mode,
                       payload.pilot_seconds or (ai_config.documentary.pilot_seconds
                                                 if payload.mode == "pilot" else None),
                       payload.render_profile, payload.refresh_visuals,
                       from_zero=payload.from_zero and master is None,
                       target_minutes=payload.target_minutes, batch_id=batch_id)
    J.launch(job.id)
    return job


@router.post("/api/cases/{case_id}/documentary/jobs")
async def start_documentary_job(case_id: int, payload: JobRequest,
                                db: Session = Depends(get_db)):
    case = _case(db, case_id)
    langs = _check_languages(payload.languages)
    return J.job_dict(_start_job(db, case, payload, langs))


class BatchItem(BaseModel):
    case_id: int
    master_version_id: int | None = None
    from_zero: bool = False
    target_minutes: float | None = Field(default=None, ge=45, le=120)


class BatchRequest(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=50)
    languages: list[str] = Field(default_factory=lambda: list(ai_config.documentary.languages))
    mode: Literal["pilot", "full"] = "pilot"
    pilot_seconds: float | None = Field(default=None, gt=10, le=1800)
    render_profile: Literal["preview", "publish"] = "preview"
    refresh_visuals: bool = False


@router.post("/api/documentary/batch")
async def start_documentary_batch(payload: BatchRequest, db: Session = Depends(get_db)):
    """Several documentaries at once: one job per case, run in parallel up
    to concurrency.jobs (the rest wait in "queued")."""
    langs = _check_languages(payload.languages)
    batch_id = J.new_batch_id()
    started, rejected = [], []
    for item in payload.items:
        case = db.get(Case, item.case_id)
        if not case:
            rejected.append({"case_id": item.case_id, "reason": "case not found"})
            continue
        req = JobRequest(master_version_id=item.master_version_id, languages=langs,
                         mode=payload.mode, pilot_seconds=payload.pilot_seconds,
                         render_profile=payload.render_profile,
                         refresh_visuals=payload.refresh_visuals,
                         from_zero=item.from_zero, target_minutes=item.target_minutes)
        try:
            started.append(J.job_dict(_start_job(db, case, req, langs, batch_id)))
        except HTTPException as e:
            rejected.append({"case_id": item.case_id, "reason": str(e.detail)})
    if not started:
        raise HTTPException(status_code=409, detail={"message": "No job could start.",
                                                     "rejected": rejected})
    return {"batch_id": batch_id, "jobs": started, "rejected": rejected,
            "max_parallel_jobs": ai_config.concurrency.jobs}


@router.get("/api/documentary/batches/{batch_id}")
def get_documentary_batch(batch_id: str, db: Session = Depends(get_db)):
    rows = (db.query(DocumentaryJob).filter(DocumentaryJob.batch_id == batch_id)
            .order_by(DocumentaryJob.id).all())
    if not rows:
        raise HTTPException(status_code=404, detail="Batch not found")
    jobs = [J.job_dict(j) for j in rows]
    return {"batch_id": batch_id, "jobs": jobs,
            "progress": round(sum(j["progress"] for j in jobs) / len(jobs), 3),
            "statuses": {s: sum(1 for j in jobs if j["status"] == s)
                         for s in {j["status"] for j in jobs}}}


@router.get("/api/documentary/jobs")
def list_all_documentary_jobs(status: str | None = None, limit: int = 50,
                              db: Session = Depends(get_db)):
    q = db.query(DocumentaryJob)
    if status:
        q = q.filter(DocumentaryJob.status == status)
    rows = q.order_by(DocumentaryJob.id.desc()).limit(min(max(limit, 1), 200)).all()
    titles = {c.id: c.canonical_title for c in db.query(Case).filter(
        Case.id.in_({j.case_id for j in rows})).all()} if rows else {}
    return [{**J.job_dict(j), "case_title": titles.get(j.case_id)} for j in rows]


@router.get("/api/documentary/scheduler")
async def documentary_scheduler(db: Session = Depends(get_db)):
    return J.scheduler_status(db)


# ---------------------------------------------------------------------------
# voice performance (narrator arc + audio tags) and Persian display text
# ---------------------------------------------------------------------------


@router.get("/api/documentary/versions/{version_id}/voice-performance")
def get_voice_performance(version_id: int, db: Session = Depends(get_db)):
    from app.documentary.voice_performance import latest_performance, performance_dict

    row = latest_performance(db, version_id)
    if not row:
        raise HTTPException(status_code=404, detail="No voice performance yet.")
    return performance_dict(row)


class PerformanceRequest(BaseModel):
    beat_ids: list[str] | None = None


@router.post("/api/documentary/versions/{version_id}/voice-performance")
async def create_voice_performance(version_id: int, payload: PerformanceRequest,
                                   db: Session = Depends(get_db)):
    from app.documentary.spoken import spoken_blueprint
    from app.documentary.voice_performance import VoicePerformanceDirector, performance_dict

    v = db.get(StoryVersion, version_id)
    if not v or v.kind != "spoken":
        raise HTTPException(status_code=404, detail="Spoken version not found")
    bp = spoken_blueprint(db, v)
    if not bp:
        raise HTTPException(status_code=409, detail="This version has no usable blueprint.")
    row = await VoicePerformanceDirector().create(db, db.get(Case, v.case_id), v, bp,
                                                  payload.beat_ids)
    return performance_dict(row)


@router.get("/api/documentary/versions/{version_id}/speech")
def get_speech_structure(version_id: int, db: Session = Depends(get_db)):
    """Sentence by sentence: what the narrator reads and what people read."""
    from app.documentary.voice_performance import speech_structure

    v = db.get(StoryVersion, version_id)
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    return {"version_id": v.id, "language": v.language, "beats": speech_structure(v)}


@router.get("/api/cases/{case_id}/documentary/jobs")
def list_documentary_jobs(case_id: int, db: Session = Depends(get_db)):
    _case(db, case_id)
    rows = (db.query(DocumentaryJob).filter(DocumentaryJob.case_id == case_id)
            .order_by(DocumentaryJob.id.desc()).limit(50).all())
    return [J.job_dict(j) for j in rows]


@router.get("/api/documentary/jobs/{job_id}")
def get_documentary_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(DocumentaryJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return J.job_dict(job)


@router.post("/api/documentary/jobs/{job_id}/cancel")
def cancel_documentary_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(DocumentaryJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("queued", "running"):
        job.status = "cancelling"
        db.commit()
    return J.job_dict(job)


@router.post("/api/documentary/jobs/{job_id}/resume")
async def resume_documentary_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(DocumentaryJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("failed", "cancelled", "partial", "interrupted"):
        raise HTTPException(status_code=409, detail=(
            "Only failed, partial, interrupted or cancelled jobs resume."))
    # finished stages keep their saved results and are skipped — degraded
    # ones too (later stages already built on them; a new run redoes
    # them); failed, interrupted and blocked ones run again, their
    # attempt and error history stays on the stage
    stages = json.loads(job.stages_json)
    for s in stages:
        if s["status"] in ("failed", "running", "blocked"):
            s["status"] = "pending"
    job.stages_json = json.dumps(stages)
    job.status = "queued"
    db.commit()
    J.launch(job.id)
    return J.job_dict(job)


# ---------------------------------------------------------------------------
# visual library
# ---------------------------------------------------------------------------


@router.get("/api/cases/{case_id}/visuals")
def list_visuals(case_id: int, type: str | None = None, role: str | None = None,
                 rights: str | None = None, verification: str | None = None,
                 q: str | None = None, db: Session = Depends(get_db)):
    _case(db, case_id)
    query = db.query(VisualAsset).filter(VisualAsset.case_id == case_id)
    if type:
        query = query.filter(VisualAsset.asset_type == type)
    if role:
        query = query.filter(VisualAsset.asset_role == role)
    if rights:
        query = query.filter(VisualAsset.rights_status == rights)
    if verification:
        query = query.filter(VisualAsset.verification_status == verification)
    rows = query.order_by(VisualAsset.id.desc()).all()
    if q:
        ql = q.lower()
        rows = [a for a in rows if ql in " ".join(filter(None, [
            a.title, a.caption, a.description, a.entities_json, a.found_for])).lower()]
    return [asset_dict(a) for a in rows]


class AssetUpdate(BaseModel):
    role: Literal["evidence", "context", "illustration"] | None = None
    rights: str | None = None
    verification: Literal["verified", "needs_review", "rejected", "unverified"] | None = None
    description: str | None = None
    entities: list[str] | None = None
    reveals: list[str] | None = None


@router.patch("/api/visuals/{asset_id}")
def update_visual(asset_id: int, payload: AssetUpdate, db: Session = Depends(get_db)):
    a = db.get(VisualAsset, asset_id)
    if not a:
        raise HTTPException(status_code=404, detail="Visual not found")
    if payload.rights is not None:
        if payload.rights not in R.RIGHTS:
            raise HTTPException(status_code=422, detail="Unknown rights status")
        a.rights_status = payload.rights
        a.rights_reason = "set by a human reviewer"
    if payload.role:
        a.asset_role = payload.role
    if payload.verification:
        a.verification_status = payload.verification
    if payload.description is not None:
        a.description = payload.description
    if payload.entities is not None:
        a.entities_json = json.dumps(payload.entities)
    if payload.reveals is not None:
        a.reveals_json = json.dumps(payload.reveals)
    a.human_override = True
    db.commit()
    return asset_dict(a)


@router.get("/api/visuals/{asset_id}/file")
def visual_file(asset_id: int, thumb: int = 0, db: Session = Depends(get_db)):
    a = db.get(VisualAsset, asset_id)
    if not a:
        raise HTTPException(status_code=404, detail="Visual not found")
    path = storage.resolve(a.thumbnail_path if thumb else a.local_path)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="File missing")
    video = (not thumb and a.asset_type in ("video", "video_source")
             and path.suffix.lower() == ".mp4")
    return FileResponse(path, media_type="video/mp4" if video else "image/jpeg")


@router.post("/api/visuals/{asset_id}/cut-again")
async def cut_video_again(asset_id: int, db: Session = Depends(get_db)):
    """Cut a kept video by meaning when the segmenter failed before. Its
    pieces then go to the video auditor like any other."""
    from app.documentary.visuals.pieces import cut_video, is_source

    a = db.get(VisualAsset, asset_id)
    if not a:
        raise HTTPException(status_code=404, detail="Visual not found")
    if not is_source(a):
        raise HTTPException(status_code=400, detail="Only a kept video can be cut")
    case = db.get(Case, a.case_id)
    pieces = await cut_video(db, case, a)
    if not pieces:
        err = (_loads(a.spec_json, {}) or {}).get("segment_error") or "no meaningful piece"
        raise HTTPException(status_code=502, detail=f"Not cut: {err}")
    _verify_later(case.id, a.id)
    return {**asset_dict(a), "pieces": len(pieces)}


@router.post("/api/cases/{case_id}/visuals/upload")
async def upload_visual(case_id: int, file: UploadFile = File(...),
                        title: str | None = Form(None), caption: str | None = Form(None),
                        role: str = Form("evidence"), rights: str = Form("owned"),
                        start: float = Form(0.0),
                        db: Session = Depends(get_db)):
    """A photo or a video supplied by the production. Either starts
    unverified and is vision-checked right away (in the background):
    nothing uploaded goes on screen unchecked. A video is stored muted,
    at most footage.upload_max_seconds from `start`."""
    import shutil
    import tempfile
    from pathlib import Path

    from app.documentary.visuals import footage as FT
    from app.documentary.visuals.images import ImageError
    from app.documentary.visuals.research import add_uploaded_image

    case = _case(db, case_id)
    role = role if role in ("evidence", "context", "illustration") else "evidence"
    name = file.filename or "upload"
    if FT.is_video_upload(name, file.content_type):
        cap = int(ai_config.footage.max_download_mb * 1024 * 1024)
        base = storage.case_dir(case.id)
        base.mkdir(parents=True, exist_ok=True)
        tmpdir = Path(tempfile.mkdtemp(prefix=".upload_", dir=base))
        src = tmpdir / ("source" + (Path(name).suffix.lower() or ".mp4"))
        try:
            n = 0
            with open(src, "wb") as fh:  # noqa: ASYNC230 — chunked, size-capped
                while chunk := await file.read(1024 * 1024):
                    n += len(chunk)
                    if n > cap:
                        raise HTTPException(status_code=413, detail="Video too large")
                    fh.write(chunk)
            try:
                a = await FT.add_uploaded_video(db, case, src, name, title, caption, role,
                                                rights, start)
            except FT.FootageError as e:
                raise HTTPException(status_code=422, detail=str(e))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    else:
        data = await file.read()
        if len(data) > ai_config.visual_search.max_download_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File too large")
        try:
            a = add_uploaded_image(db, case, data, name, title, caption, role, rights)
        except ImageError as e:
            raise HTTPException(status_code=422, detail=str(e))
    _verify_later(case.id, a.id)
    return asset_dict(a)


def _verify_later(case_id: int, asset_id: int) -> None:
    """Vision-check an uploaded asset in the background (entities from
    the case's latest visual plan, so the director can match it)."""

    async def run():
        from app.db.base import SessionLocal

        bg = SessionLocal()
        try:
            case, a = bg.get(Case, case_id), bg.get(VisualAsset, asset_id)
            if case is None or a is None:
                return
            vp = (bg.query(VisualPlan).filter(VisualPlan.case_id == case_id)
                  .order_by(VisualPlan.id.desc()).first())
            ents = (_loads(vp.requirements_json, {}) if vp else {}).get("entities") or []
            todo = [a]
            if a.asset_type == "video_source":  # a video: its pieces are checked
                from app.documentary.visuals.pieces import piece_info

                todo = [r for r in bg.query(VisualAsset).filter(
                    VisualAsset.case_id == case_id, VisualAsset.asset_type == "video").all()
                        if piece_info(r).get("parent") == a.asset_code]
            await J.verify_assets(bg, case, todo, ents)
        except Exception as e:  # noqa: BLE001 — the asset stays unverified (audited later)
            log.warning("upload %s: verification failed: %s", asset_id, e)
        finally:
            bg.close()

    try:
        spawn(run(), name="upload_verification")
    except RuntimeError:  # no loop (tests calling the function directly)
        pass


# ---------------------------------------------------------------------------
# plans, production scripts, renders
# ---------------------------------------------------------------------------


@router.get("/api/cases/{case_id}/documentary/visual-plan")
def get_visual_plan(case_id: int, version_id: int | None = None, db: Session = Depends(get_db)):
    master = _pick_master(db, case_id, version_id)
    bp = latest_blueprint(db, master.id)
    vp = J.latest_visual_plan(db, bp.id) if bp else None
    if not vp:
        raise HTTPException(status_code=404, detail="No visual plan yet")
    return {"id": vp.id, "blueprint_id": vp.blueprint_id, "version": vp.version,
            "status": vp.status, "requirements": _loads(vp.requirements_json, {}),
            "plan": _loads(vp.plan_json, {}), "validation": _loads(vp.validation_json, {}),
            "model": vp.generation_model}


@router.get("/api/cases/{case_id}/documentary/production/{language}")
def get_production(case_id: int, language: str, version_id: int | None = None,
                   db: Session = Depends(get_db)):
    master = _pick_master(db, case_id, version_id)
    bp = latest_blueprint(db, master.id)
    v = J.latest_spoken(db, master, language, bp.id if bp else None)
    ps = J.latest_production(db, v.id) if v else None
    if not ps:
        raise HTTPException(status_code=404, detail="No production script for this language")
    return production_dict(ps)


def _ps(db: Session, ps_id: int) -> ProductionScript:
    ps = db.get(ProductionScript, ps_id)
    if not ps:
        raise HTTPException(status_code=404, detail="Production script not found")
    return ps


@router.get("/api/documentary/production/{ps_id}/video.mp4")
def production_video(ps_id: int, db: Session = Depends(get_db)):
    info = _loads(_ps(db, ps_id).render_json, None)
    path = storage.resolve((info or {}).get("path"))
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Not rendered yet")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/api/documentary/production/{ps_id}/subtitles.srt")
def production_subtitles(ps_id: int, db: Session = Depends(get_db)):
    info = _loads(_ps(db, ps_id).render_json, None)
    path = storage.resolve((info or {}).get("srt"))
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Not rendered yet")
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=path.name)


@router.get("/api/documentary/music")
def music_library(db: Session = Depends(get_db)):
    """The shared track library (several variants per kind and mood): how
    often each track was used, when last, and in which films (film keys,
    in order of first use). `id` is the track code used in the file URL;
    `cue_id` names the config cue a variant-1 track was imported from."""
    from app.documentary.music import track_catalogue

    return track_catalogue(db)


@router.get("/api/documentary/music/{cue_id}/file")
def music_file(cue_id: str, db: Session = Depends(get_db)):
    """A track's audio by track_code (e.g. bridge-tension-v3); the old
    config cue ids (e.g. bridge_tension) still resolve."""
    from app.documentary.music import track_file

    path = track_file(db, cue_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Unknown track")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Track not generated yet")
    return FileResponse(path, media_type="audio/wav")


# ---------------------------------------------------------------------------
# on-screen host (persona_master_prompt.md): plan, dialogue, memory
# ---------------------------------------------------------------------------


@router.get("/api/cases/{case_id}/documentary/chapters")
def get_chapters(case_id: int, version_id: int | None = None, db: Session = Depends(get_db)):
    """The approved chapter titles, film title and timeline labels (all
    languages) with the auditor's rounds and what was left out."""
    from app.documentary.chapters import latest_chapter_plan

    _case(db, case_id)
    bp = latest_blueprint(db, _pick_master(db, case_id, version_id).id)
    row = latest_chapter_plan(db, bp.id) if bp else None
    if not row:
        raise HTTPException(status_code=404, detail="No chapters yet.")
    return {"id": row.id, "version": row.version, "status": row.status,
            "blueprint_id": row.blueprint_id, "languages": _loads(row.languages_json, []),
            "plan": _loads(row.plan_json, {}), "audit": _loads(row.audit_json, {}),
            "created_at": row.created_at}


@router.get("/api/cases/{case_id}/documentary/host-plan")
def get_host_plan(case_id: int, version_id: int | None = None, db: Session = Depends(get_db)):
    from app.documentary.host import host_plan_dict, latest_host_plan

    _case(db, case_id)
    bp = latest_blueprint(db, _pick_master(db, case_id, version_id).id)
    row = latest_host_plan(db, bp.id) if bp else None
    if not row:
        raise HTTPException(status_code=404, detail="No host plan yet.")
    return host_plan_dict(row)


@router.post("/api/cases/{case_id}/documentary/host-plan")
async def create_host_plan(case_id: int, version_id: int | None = None,
                           db: Session = Depends(get_db)):
    from app.documentary.host import HostDirector, host_plan_dict

    case = _case(db, case_id)
    master = _pick_master(db, case_id, version_id)
    bp = latest_blueprint(db, master.id)
    if not bp or bp.status == "invalid":
        raise HTTPException(status_code=409, detail="This story needs a valid blueprint first.")
    row = await HostDirector().create_plan(db, case, bp, master)
    return host_plan_dict(row)


@router.get("/api/documentary/versions/{version_id}/host")
def get_host_segments(version_id: int, db: Session = Depends(get_db)):
    from app.documentary.host import host_segments_dict, latest_host_segments

    row = latest_host_segments(db, version_id)
    if not row:
        raise HTTPException(status_code=404, detail="No host segments yet.")
    return host_segments_dict(row)


@router.post("/api/documentary/versions/{version_id}/host")
async def create_host_segments(version_id: int, db: Session = Depends(get_db)):
    """The host's dialogue for one spoken version (its language), from the
    newest host plan of its blueprint."""
    from app.documentary.host import HostDirector, host_segments_dict, latest_host_plan

    v = db.get(StoryVersion, version_id)
    if not v or v.kind != "spoken":
        raise HTTPException(status_code=404, detail="Spoken version not found")
    bp_id = _loads(v.narrative_structure, {}).get("blueprint_id")
    plan = latest_host_plan(db, bp_id) if bp_id else None
    if not plan:
        raise HTTPException(status_code=409, detail="Create the host plan first.")
    row = await HostDirector().write(db, db.get(Case, v.case_id), v, plan)
    return host_segments_dict(row)


@router.get("/api/documentary/host/memory")
def list_host_memory(case_id: int | None = None, db: Session = Depends(get_db)):
    from app.db.models import HostMemory
    from app.documentary.host import memory_dict

    q = db.query(HostMemory)
    if case_id:
        q = q.filter(HostMemory.case_id == case_id)
    return [memory_dict(m) for m in q.order_by(HostMemory.id.desc()).limit(500)]


class HostMemoryRequest(BaseModel):
    case_id: int
    kind: Literal["opinion", "reaction", "correction", "open_question", "theme"]
    text: str = Field(min_length=3, max_length=500)


@router.post("/api/documentary/host/memory")
def add_host_memory(payload: HostMemoryRequest, db: Session = Depends(get_db)):
    """A memory confirmed by an editor (kept when plans are regenerated)."""
    from app.db.models import HostMemory
    from app.documentary.host import memory_dict

    _case(db, payload.case_id)
    m = HostMemory(case_id=payload.case_id, kind=payload.kind, text=payload.text.strip(),
                   origin="editor")
    db.add(m)
    db.commit()
    db.refresh(m)
    return memory_dict(m)


class HostMemoryPatch(BaseModel):
    active: bool | None = None
    text: str | None = Field(default=None, min_length=3, max_length=500)


@router.patch("/api/documentary/host/memory/{memory_id}")
def update_host_memory(memory_id: int, payload: HostMemoryPatch, db: Session = Depends(get_db)):
    """Retire a wrong memory (active=false) or correct its text; an edited
    memory becomes editor-owned."""
    from app.db.models import HostMemory
    from app.documentary.host import memory_dict

    m = db.get(HostMemory, memory_id)
    if not m:
        raise HTTPException(status_code=404, detail="Memory not found")
    if payload.active is not None:
        m.active = payload.active
    if payload.text is not None:
        m.text = payload.text.strip()
        m.origin = "editor"
    db.commit()
    return memory_dict(m)
