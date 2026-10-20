"""Settings page API of the host's closing clips: the two switches, the library per language,
and "create missing variants now" (the estimate first, rendering only on confirmation)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.base import get_db
from app.db.models import Case
from app.providers.avatar.base import AvatarProviderError
from app.services.budget import BudgetStopped
from app.shortform import closing_library as CL
from app.shortform.closing_render import HeyGenClosingRenderer

router = APIRouter(tags=["closing-clips"])


def get_renderer() -> CL.ClosingRenderer:
    return HeyGenClosingRenderer()


class SettingsBody(BaseModel):
    use_stored: bool | None = None
    create_new: bool | None = None


class CreateMissingBody(BaseModel):
    language: str
    case_id: int
    confirm: bool = False


def _estimate_dict(e: CL.CostEstimate) -> dict:
    return {"characters": e.characters, "voice_eur": e.voice_eur, "seconds": e.seconds,
            "avatar_eur": e.avatar_eur, "total_eur": e.total_eur}


@router.get("/api/closing-clips")
def closing_overview(db: Session = Depends(get_db)):
    languages = list(ai_config.channels)
    table = CL.library_table(db, languages)
    renderer = get_renderer()
    for language in languages:
        looks = CL.approved_looks(db, language)
        table[language].update({
            "approved_looks": len(looks),
            "missing": len(CL.missing_looks(db, language, CL.KIND_SHORTS, looks)),
            "sentences": CL.closing_sentences(language, CL.KIND_SHORTS),
            "estimate_per_variant": _estimate_dict(renderer.estimate(
                language, CL.closing_sentences(language, CL.KIND_SHORTS)))})
    return {"settings": CL.ClosingSettings(db).as_dict(), "languages": table,
            "closing_order": ai_config.shorts_host_led.closing_order}


@router.put("/api/closing-clips/settings")
def set_closing_settings(body: SettingsBody, db: Session = Depends(get_db)):
    return CL.ClosingSettings(db).update(use_stored=body.use_stored, create_new=body.create_new)


@router.post("/api/closing-clips/create-missing")
async def create_missing(body: CreateMissingBody, db: Session = Depends(get_db)):
    if body.language not in ai_config.channels:
        raise HTTPException(status_code=422, detail=f"no channel for language {body.language!r}")
    if db.get(Case, body.case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")
    if not CL.ClosingSettings(db).create_new:
        raise HTTPException(status_code=409,
                            detail="Create new closing clips is off: no closing clip is rendered")
    looks = CL.approved_looks(db, body.language)
    wanted = CL.missing_looks(db, body.language, CL.KIND_SHORTS, looks)
    renderer = get_renderer()
    sentences = CL.closing_sentences(body.language, CL.KIND_SHORTS)
    per = renderer.estimate(body.language, sentences)
    estimate = {"variants": len(wanted), "per_variant": _estimate_dict(per),
                "total_eur": round(per.total_eur * len(wanted), 4)}
    if not body.confirm:
        return {"estimate": estimate, "created": []}
    created = []
    actual = 0.0
    try:
        for look in wanted:
            clip = await CL.create_variant(db, body.language, CL.KIND_SHORTS, look, renderer,
                                           case_id=body.case_id)
            created.append({"id": clip.id, "outfit": clip.outfit_label, "position": clip.position,
                            "seconds": clip.duration_seconds, "cost_eur": clip.cost_eur})
            actual += clip.cost_eur
    except BudgetStopped as e:
        raise HTTPException(status_code=402, detail=str(e)) from e
    except AvatarProviderError as e:
        raise HTTPException(status_code=502, detail=f"HeyGen: {e}") from e
    return {"estimate": estimate, "created": created,
            "actual_eur": round(actual, 4)}
