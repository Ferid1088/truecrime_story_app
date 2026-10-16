"""Story plan endpoints (Part 5a): plan before writing, preview then edit or
redo, approve."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.api.deps import get_case_or_404
from app.core.ai_config import ai_config
from app.db.base import get_db
from app.db.models import StoryPlan
from app.services.story_plan_service import StoryPlanError, StoryPlanService, plan_dict

router = APIRouter()


class CreatePlan(BaseModel):
    target_minutes: float = Field(gt=0)
    language: str | None = None
    instruction: str | None = None


class PlanPreview(BaseModel):
    action: str
    instruction: str | None = None
    beats: list[dict] | None = None


class PlanEdit(BaseModel):
    token: str
    beats: list[dict]


class PlanRedo(BaseModel):
    token: str
    instruction: str = Field(min_length=1)


class PlanApproval(BaseModel):
    approved_by: str = Field(min_length=1, max_length=120)


def _plan(db: Session, plan_id: int) -> StoryPlan:
    plan = db.get(StoryPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Story plan not found")
    return plan


def _guard(call):
    try:
        return call()
    except StoryPlanError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/api/cases/{case_id}/story-plan")
def get_story_plan(case_id: int, language: str | None = None, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    plan = StoryPlanService(db).latest(case_id, language)
    if plan is None:
        raise HTTPException(status_code=404, detail="No story plan yet")
    return plan_dict(plan)


@router.post("/api/cases/{case_id}/story-plan")
async def create_story_plan(case_id: int, payload: CreatePlan, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    await deps.require_generation_authorized()
    language = payload.language or ai_config.multilingual.canonical_language
    service = StoryPlanService(db)
    previous = service.latest(case_id, language)
    try:
        plan = await service.create(
            case, payload.target_minutes, language, instruction=payload.instruction,
            supersedes=previous if previous and previous.status == "draft" else None)
    except StoryPlanError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return plan_dict(plan)


@router.post("/api/story-plans/{plan_id}/preview")
def preview_story_plan(plan_id: int, payload: PlanPreview, db: Session = Depends(get_db)):
    return _guard(lambda: StoryPlanService(db).preview(
        _plan(db, plan_id), payload.action, payload.instruction, payload.beats))


@router.post("/api/story-plans/{plan_id}/edit")
def edit_story_plan(plan_id: int, payload: PlanEdit, db: Session = Depends(get_db)):
    service = StoryPlanService(db)
    return plan_dict(_guard(lambda: service.edit(_plan(db, plan_id), payload.beats, payload.token)))


@router.post("/api/story-plans/{plan_id}/redo")
async def redo_story_plan(plan_id: int, payload: PlanRedo, db: Session = Depends(get_db)):
    await deps.require_generation_authorized()
    try:
        plan = await StoryPlanService(db).redo(_plan(db, plan_id), payload.instruction, payload.token)
    except StoryPlanError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return plan_dict(plan)


@router.post("/api/story-plans/{plan_id}/approve")
def approve_story_plan(plan_id: int, payload: PlanApproval, db: Session = Depends(get_db)):
    service = StoryPlanService(db)
    return plan_dict(_guard(lambda: service.approve(_plan(db, plan_id), payload.approved_by)))
