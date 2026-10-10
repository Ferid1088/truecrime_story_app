import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import (
    Case,
    StoryVersion,
)
from app.schemas import (
    GenerateStoryRequest,
    check_target_minutes,
)
from app.core.ai_config import ai_config
from app.agents.story import (
    MASTER_ROLES,
    StoryPipeline,
)
from app.agents.localization import LocalizationPipeline
from app.services.readiness import build_readiness


from app.api.deps import best_master, get_case_or_404
from app.api.serializers import story_full_dict, story_meta_dict
from app.api import deps

router = APIRouter()


@router.get("/api/cases/{case_id}/master-story")
def get_master_story(case_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    master = best_master(db, case_id)
    if not master:
        raise HTTPException(status_code=404, detail="No master story found")
    localizations = (
        db.query(StoryVersion)
        .filter(
            StoryVersion.case_id == case_id,
            StoryVersion.kind == "localized",
            StoryVersion.master_version_id == master.id,
        )
        .order_by(StoryVersion.version.desc())
        .all()
    )
    return {
        "master": story_full_dict(master),
        "localizations": [story_meta_dict(s) for s in localizations],
    }


@router.post("/api/cases/{case_id}/master-story/generate")
async def generate_master_story(
    case_id: int,
    payload: GenerateStoryRequest,
    db: Session = Depends(get_db),
):
    """Generate the canonical English master. All localizations derive
    from a ready master — never directly from evidence."""
    case = get_case_or_404(db, case_id)
    canonical = ai_config.multilingual.canonical_language

    # Layered preflight: provider → sources → evidence → capacity. Each
    # failure maps to its own code so the UI can say *why* generation is
    # blocked instead of showing a misleading "ready".
    provider_status = await deps.require_generation_authorized()
    report = build_readiness(
        db, case.id, payload.target_minutes,
        provider_status=provider_status or {},
    )
    master = report["master_readiness"]
    if master["status"] == "provider_blocked":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "provider_unauthorized",
                "provider_status": provider_status,
                "message": master["reason"],
            },
        )
    if master["status"] in ("incomplete_evidence", "failed"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "evidence_incomplete",
                "message": (
                    "Evidence extraction is incomplete "
                    f"({master['reason']}). Re-run research once the "
                    "provider is healthy before generating."
                ),
                "evidence_readiness": report["evidence_readiness"],
                "capacity": report["narrative_capacity"],
            },
        )
    if master["status"] == "insufficient_research":
        capacity = report["narrative_capacity"]
        raise HTTPException(
            status_code=409,
            detail={
                "code": "insufficient_research",
                "message": (
                    "Evidence supports approximately "
                    f"{capacity['estimated_supported_minutes']} minutes — "
                    "more research is recommended for the requested duration."
                ),
                "capacity": capacity,
                "source_readiness": report["source_readiness"],
            },
        )

    try:
        story = await StoryPipeline(roles=MASTER_ROLES).run(
            db=db,
            case=case,
            target_minutes=payload.target_minutes,
            language=canonical,
            tone=payload.tone,
            iterations=payload.iterations,
            kind="master",
            words_per_minute=ai_config.words_per_minute_for(canonical),
        )
        return story_full_dict(story)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/cases/{case_id}/localizations/generate")
async def generate_localization(
    case_id: int,
    language: str,
    target_minutes: int | None = None,
    db: Session = Depends(get_db),
):
    case = get_case_or_404(db, case_id)
    if target_minutes is not None:
        try:
            check_target_minutes(target_minutes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    master = best_master(db, case_id)
    if not master:
        raise HTTPException(status_code=404, detail="No master story found")
    try:
        story = await LocalizationPipeline().localize(
            db, case, master, language, target_minutes=target_minutes
        )
        return story_full_dict(story)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/cases/{case_id}/localizations")
def list_localizations(case_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    rows = (
        db.query(StoryVersion)
        .filter(
            StoryVersion.case_id == case_id,
            StoryVersion.kind == "localized",
        )
        .order_by(StoryVersion.version.desc())
        .all()
    )
    return [story_meta_dict(s) for s in rows]


@router.get("/api/localizations/{version_id}")
def get_localization(version_id: int, db: Session = Depends(get_db)):
    story = db.get(StoryVersion, version_id)
    if not story or story.kind != "localized":
        raise HTTPException(status_code=404, detail="Localization not found")
    return story_full_dict(story)


@router.post("/api/localizations/{version_id}/improve")
async def improve_localization(version_id: int, db: Session = Depends(get_db)):
    """Produce a NEW localization version from the same master — prior
    versions are never overwritten."""
    story = db.get(StoryVersion, version_id)
    if not story or story.kind != "localized":
        raise HTTPException(status_code=404, detail="Localization not found")
    case = db.get(Case, story.case_id)
    master = db.get(StoryVersion, story.master_version_id)
    if not master:
        raise HTTPException(status_code=404, detail="Source master not found")
    try:
        new_version = await LocalizationPipeline().localize(
            db, case, master, story.language
        )
        return story_full_dict(new_version)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/localizations/{version_id}/compare-master")
def compare_master(version_id: int, db: Session = Depends(get_db)):
    """Side-by-side reviewer view: master text, localized text and the
    stored semantic-consistency report (internal evidence stays internal)."""
    story = db.get(StoryVersion, version_id)
    if not story or story.kind != "localized":
        raise HTTPException(status_code=404, detail="Localization not found")
    master = db.get(StoryVersion, story.master_version_id)
    critic = {}
    if story.critic_notes:
        try:
            critic = json.loads(story.critic_notes)
        except (ValueError, TypeError):
            critic = {}
    return {
        "localization": story_meta_dict(story),
        "localized_text": story.story_text,
        "master_text": master.story_text if master else None,
        "master_version": story.derived_from_master_version,
        "semantic_consistency": critic.get("semantic_consistency") or {},
        "native_quality": critic.get("native_quality") or {},
    }
