import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import (
    StoryVersion,
    EditorialBlueprint,
)
from app.schemas import (
    GenerateStoryRequest,
    ImproveStoryRequest,
    VoiceRenderRequest,
    DynamicEQPreviewRequest,
)
from app.core.ai_config import ai_config
from app.agents.story import (
    StoryPipeline,
    current_evidence_fingerprint,
    stored_evidence_fingerprint,
)
from app.documentary.audio import AudioToolError
from app.documentary.voice_blocks import plan_for_version
from app.documentary.voice_render import VoiceRenderer
from app.documentary.blueprint import (
    NarrativeDirector,
    blueprint_dict,
    latest_blueprint,
)
from app.documentary.performance import performance_for_version
from app.documentary.production.audio import render_documentary_audio
from app.documentary.spoken import SpokenNarrator
from app.documentary.audio_director import (
    AudioDirector,
    audio_plan_dict,
    latest_audio_plan,
)
from app.providers.voice import VoiceProviderError
from app.providers.generation.base import GenerationError


from app.api.deps import get_case_or_404, story_or_404
from app.api.serializers import story_full_dict, story_meta_dict
from app.api import deps

router = APIRouter()


@router.post("/api/cases/{case_id}/generate-story")
async def generate_story(case_id: int, payload: GenerateStoryRequest, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    await deps.require_generation_authorized()

    try:
        story = await StoryPipeline().run(
            db=db,
            case=case,
            target_minutes=payload.target_minutes,
            language=payload.language,
            tone=payload.tone,
            iterations=payload.iterations,
        )
        return {
            "story_version_id": story.id,
            "version": story.version,
            "engagement_score": story.engagement_score,
            "similarity_score": story.similarity_score,
            "similarity_status": story.similarity_status,
            "status": story.status,
            "story_text": story.story_text,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/cases/{case_id}/improve-story")
async def improve_story(case_id: int, payload: ImproveStoryRequest, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    story = (
        db.query(StoryVersion)
        .filter(StoryVersion.id == payload.story_version_id, StoryVersion.case_id == case_id)
        .first()
    )
    if not story:
        raise HTTPException(status_code=404, detail="Story version not found")

    try:
        new_version = await StoryPipeline().improve(
            db=db, case=case, story_version=story, instruction=payload.instruction
        )
        return story_full_dict(new_version)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/cases/{case_id}/stories")
def list_stories(case_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    rows = (
        db.query(StoryVersion)
        .filter(StoryVersion.case_id == case_id)
        .order_by(StoryVersion.version.desc())
        .all()
    )
    return [story_meta_dict(s) for s in rows]


@router.get("/api/cases/{case_id}/stories/{version_id}")
def get_story_version(case_id: int, version_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    story = (
        db.query(StoryVersion)
        .filter(StoryVersion.id == version_id, StoryVersion.case_id == case_id)
        .first()
    )
    if not story:
        raise HTTPException(status_code=404, detail="Story version not found")
    return story_full_dict(story)


@router.get("/api/cases/{case_id}/stories/{version_id}/voice-blocks")
def story_voice_blocks(case_id: int, version_id: int, db: Session = Depends(get_db)):
    """Sentence-safe TTS block plan for one story version (read-only, no
    provider cost). `evidence_current` is False when research changed
    after this version was written (its evidence IDs are then stale)."""
    get_case_or_404(db, case_id)
    story = (
        db.query(StoryVersion)
        .filter(StoryVersion.id == version_id, StoryVersion.case_id == case_id)
        .first()
    )
    if not story:
        raise HTTPException(status_code=404, detail="Story version not found")
    plan = plan_for_version(story)
    stored_fp = stored_evidence_fingerprint(story)
    plan["evidence_current"] = (
        None if stored_fp is None
        else stored_fp == current_evidence_fingerprint(db, case_id)
    )
    return plan


def voice_summary(manifest: dict) -> dict:
    """Manifest without the (long) word-level timeline."""
    timeline = manifest.get("timeline") or {}
    return {
        **{k: v for k, v in manifest.items() if k != "timeline"},
        "timeline_blocks": timeline.get("blocks") or [],
        "timeline_word_count": len(timeline.get("words") or []),
    }


@router.post("/api/cases/{case_id}/stories/{version_id}/voice/render")
async def render_story_voice(
    case_id: int, version_id: int, payload: VoiceRenderRequest,
    db: Session = Depends(get_db),
):
    """Render narration for a story version: TTS per voice block with
    word timestamps, independent speech-to-text check (with automatic
    re-take of failing blocks), loudness normalization, one narration
    track + timeline. Unchanged blocks come from cache (no cost).
    `max_seconds` renders only the opening (e.g. a 3-minute pilot).
    With a usable editorial blueprint, blocks follow its performance
    script (style per beat, dramatic pauses, silences)."""
    story = story_or_404(db, case_id, version_id)
    plan = performance_for_version(db, story)
    try:
        manifest = await render_documentary_audio(
            plan, case_id=case_id, story_version_id=version_id,
            max_seconds=payload.max_seconds, style=payload.style,
            force_block_ids=payload.force_block_ids,
            with_music=payload.with_music,
        )
    except VoiceProviderError as e:
        code = 503 if e.kind == "missing_credentials" else 502
        raise HTTPException(status_code=code, detail={"code": e.kind, "message": str(e)})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except AudioToolError as e:
        raise HTTPException(status_code=500, detail=str(e))
    summary = voice_summary(manifest)
    summary["blueprint_used"] = plan.get("blueprint_used", False)
    summary["directed"] = plan.get("directed", False)
    return summary


@router.post("/api/cases/{case_id}/stories/{version_id}/spoken")
async def create_spoken_version(
    case_id: int, version_id: int, language: str, db: Session = Depends(get_db),
):
    """Spoken storytelling version of a story (needs its blueprint): the
    text the way a person tells a true story, natively in `language`
    (en/de/fa/ar), beat by beat, facts unchanged — checked for meaning by
    one model and for storyteller tone by another."""
    story = story_or_404(db, case_id, version_id)
    await deps.require_generation_authorized()
    case = get_case_or_404(db, case_id)
    try:
        spoken = await SpokenNarrator().create(db, case, story, language)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    except GenerationError as e:
        raise HTTPException(status_code=502, detail={"code": e.kind, "message": str(e)})
    body = story_full_dict(spoken)
    body["spoken_checks"] = (json.loads(spoken.critic_notes or "{}").get("spoken"))
    return body


def blueprint_row_for(db: Session, story: StoryVersion):
    """The blueprint behind a story: its own, or — for a spoken version —
    the one its beats were written from (shared by all languages)."""
    if story.kind == "spoken":
        try:
            bp_id = json.loads(story.narrative_structure or "{}").get("blueprint_id")
        except (ValueError, TypeError):
            bp_id = None
        return db.get(EditorialBlueprint, bp_id) if bp_id else None
    return latest_blueprint(db, story.id)


@router.post("/api/cases/{case_id}/stories/{version_id}/audio-plan")
async def create_audio_plan(case_id: int, version_id: int, db: Session = Depends(get_db)):
    """Audio director's plan (breaths, music beds, bridges, emotional
    moments, stings, silences) for this story's blueprint — shared by
    every language version made from it."""
    story = story_or_404(db, case_id, version_id)
    row = blueprint_row_for(db, story)
    if not row or row.status == "invalid":
        raise HTTPException(status_code=409, detail="Create a valid blueprint first.")
    await deps.require_generation_authorized()
    case = get_case_or_404(db, case_id)
    try:
        plan = await AudioDirector().create(db, case, row)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except GenerationError as e:
        raise HTTPException(status_code=502, detail={"code": e.kind, "message": str(e)})
    return audio_plan_dict(plan)


@router.get("/api/cases/{case_id}/stories/{version_id}/audio-plan")
def get_audio_plan(case_id: int, version_id: int, db: Session = Depends(get_db)):
    story = story_or_404(db, case_id, version_id)
    row = blueprint_row_for(db, story)
    plan = latest_audio_plan(db, row.id) if row else None
    if not plan:
        raise HTTPException(status_code=404, detail="No audio plan for this story")
    return audio_plan_dict(plan)


@router.post("/api/cases/{case_id}/stories/{version_id}/blueprint")
async def create_story_blueprint(
    case_id: int, version_id: int, db: Session = Depends(get_db),
):
    """Editorial blueprint for a story: beats (paragraph ranges per act)
    with purpose, reveals, listener questions, attention/visual/audio
    intents and pauses — validated deterministically (status valid |
    needs_review | invalid)."""
    story = story_or_404(db, case_id, version_id)
    await deps.require_generation_authorized()
    case = get_case_or_404(db, case_id)
    try:
        row = await NarrativeDirector().create(db, case, story)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except GenerationError as e:
        raise HTTPException(status_code=502, detail={"code": e.kind, "message": str(e)})
    return blueprint_dict(row)


@router.get("/api/cases/{case_id}/stories/{version_id}/blueprint")
def get_story_blueprint(case_id: int, version_id: int, db: Session = Depends(get_db)):
    story_or_404(db, case_id, version_id)
    row = latest_blueprint(db, version_id)
    if not row:
        raise HTTPException(status_code=404, detail="No blueprint for this story version")
    return blueprint_dict(row)


@router.get("/api/cases/{case_id}/stories/{version_id}/performance")
def get_story_performance(case_id: int, version_id: int, db: Session = Depends(get_db)):
    """Performance script (read-only, no provider cost): voice blocks with
    style, beat ranges and the pause after each block."""
    story = story_or_404(db, case_id, version_id)
    return performance_for_version(db, story)


def voice_dir(story: StoryVersion):
    return VoiceRenderer.out_dir_for(story.case_id, story.language, story.id)


@router.get("/api/cases/{case_id}/stories/{version_id}/voice")
def story_voice_manifest(case_id: int, version_id: int, db: Session = Depends(get_db)):
    story = story_or_404(db, case_id, version_id)
    path = voice_dir(story) / "manifest.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No narration rendered yet")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/api/cases/{case_id}/stories/{version_id}/voice/narration.mp3")
def story_voice_audio(case_id: int, version_id: int, db: Session = Depends(get_db)):
    story = story_or_404(db, case_id, version_id)
    path = voice_dir(story) / "narration.mp3"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No narration rendered yet")
    return FileResponse(path, media_type="audio/mpeg")


@router.get("/api/cases/{case_id}/stories/{version_id}/voice/narration_original.mp3")
def story_voice_audio_original(case_id: int, version_id: int,
                               db: Session = Depends(get_db)):
    """The narration BEFORE dynamic EQ — the A side of the studio's
    original/enhanced comparison."""
    story = story_or_404(db, case_id, version_id)
    path = voice_dir(story) / "narration_original.mp3"
    if not path.exists():
        raise HTTPException(status_code=404,
                            detail="No pre-EQ original (enhancement was not applied)")
    return FileResponse(path, media_type="audio/mpeg")


@router.get("/api/cases/{case_id}/stories/{version_id}/voice/dynamics")
def story_voice_dynamics(case_id: int, version_id: int,
                         db: Session = Depends(get_db)):
    """Gain-reduction report: per-band thresholds, when/where/how much
    reduction was applied (events) and the GR curve over time."""
    story = story_or_404(db, case_id, version_id)
    path = voice_dir(story) / "dynamics.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No dynamic-EQ report yet")
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/api/cases/{case_id}/stories/{version_id}/voice/eq-preview")
def story_voice_eq_preview(case_id: int, version_id: int,
                           payload: DynamicEQPreviewRequest,
                           db: Session = Depends(get_db)):
    """Pre-rendered preview: process the opening of the ORIGINAL
    narration with the given overrides and return a small mp3 plus the
    full gain-reduction report. Originals are never touched."""
    from app.documentary import dynamic_eq as DEQ

    story = story_or_404(db, case_id, version_id)
    out = voice_dir(story)
    src = out / "narration.wav"
    if not src.exists():
        raise HTTPException(status_code=404, detail="No narration rendered yet")
    language = story.language or "en"
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
    cfg = cfg.model_copy(update=update)
    try:
        report = DEQ.preview_into(src, out, language, cfg.resolved(language),
                                  payload.seconds, ai_config.loudness.sample_rate)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"EQ preview failed: {type(e).__name__}: {e}")
    return {"mp3_url": f"/api/cases/{case_id}/stories/{version_id}/voice"
                       f"/preview/{report['mp3']}",
            "report": report}


@router.get("/api/cases/{case_id}/stories/{version_id}/voice/preview/{filename}")
def story_voice_eq_preview_file(case_id: int, version_id: int, filename: str,
                                db: Session = Depends(get_db)):
    import re

    story = story_or_404(db, case_id, version_id)
    if not re.fullmatch(r"eq_preview_[0-9a-f]{16}\.mp3", filename):
        raise HTTPException(status_code=404, detail="Unknown preview")
    path = voice_dir(story) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Preview not found")
    return FileResponse(path, media_type="audio/mpeg")


@router.get("/api/cases/{case_id}/story/latest")
def latest_story(case_id: int, db: Session = Depends(get_db)):
    # "Latest" for consumers means the best version, not the newest.
    story = (
        db.query(StoryVersion)
        .filter(StoryVersion.case_id == case_id)
        .order_by(StoryVersion.is_best.desc(), StoryVersion.version.desc())
        .first()
    )
    if not story:
        raise HTTPException(status_code=404, detail="No story found")

    # Intentional: only the final story text.
    return {"story_text": story.story_text}
