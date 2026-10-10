from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import EditorialBlueprint
from app.core.ai_config import ShortFormPlatformConfig
from app.shortform.director import ShortFormDirectorAgent, candidate_table

router = APIRouter(tags=["short-form"])


class ShortFormSettingsPatch(BaseModel):
    distribution: dict[str, ShortFormPlatformConfig]
    language_mode: str = "episode"


@router.get("/api/short-form/settings")
def short_form_settings():
    from app.core.ai_config import ai_config

    return ai_config.short_form.model_dump(mode="json") | {
        "candidate_count": ai_config.short_form.candidate_count()
    }


@router.patch("/api/short-form/settings")
def update_short_form_settings(payload: ShortFormSettingsPatch):
    from app.core.ai_config import ai_config

    cfg = ai_config.short_form.model_copy(update={
        "distribution": payload.distribution,
    })
    # Pydantic validates the copied model only when explicitly revalidated.
    cfg = type(ai_config.short_form).model_validate(cfg.model_dump())
    return cfg.model_dump(mode="json") | {
        "candidate_count": cfg.candidate_count(),
        "language_mode": payload.language_mode,
        "level": "episode_override",
    }


@router.get("/api/short-form/blueprints/{blueprint_id}/candidates")
def short_form_candidates(blueprint_id: int, db: Session = Depends(get_db)):
    row = db.get(EditorialBlueprint, blueprint_id)
    if not row:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    try:
        candidates = ShortFormDirectorAgent().generate_for_blueprint_row(db, row)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "blueprint_id": blueprint_id,
        "count": len(candidates),
        "candidates": candidates,
        "table": candidate_table(candidates),
    }
