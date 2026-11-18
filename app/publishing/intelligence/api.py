"""The routes of the AI publishing plan in the Publishing Studio. Reading never calls a model; generating is an explicit request (the only paid step); the one-click approval
creates the individual approvals of the publications that may have one and says which it left out and why."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.tasks import spawn
from app.db.base import get_db
from app.db.models import Case, PublishingPlanRun
from app.publishing.intelligence import service as S
from app.publishing.registry import PublicationRegistry, PublishingRefused
from app.services.budget import BudgetStopped

router = APIRouter(tags=["publishing-intelligence"])


class GenerateBody(BaseModel):
    force: bool = False


class OrderBody(BaseModel):
    platform: str
    order: list[str]


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _refuse(exc: Exception) -> HTTPException:
    reasons = getattr(exc, "reasons", [])
    return HTTPException(status_code=409, detail={"message": str(exc), "reasons": reasons})


@router.get("/api/cases/{case_id}/publishing/{language}/ai")
def ai_state(case_id: int, language: str, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    return S.state(db, PublicationRegistry(db), case, language)


@router.post("/api/cases/{case_id}/publishing/{language}/ai/generate")
async def generate(case_id: int, language: str, body: GenerateBody, db: Session = Depends(get_db)):
    """Start the run (the owner's click). It goes on in the background; the Studio polls the run."""
    case = _case(db, case_id)
    registry = PublicationRegistry(db)
    try:
        run = S.start(db, registry, case, language, force=body.force)
    except (S.PlanRunError, PublishingRefused, BudgetStopped) as exc:
        raise _refuse(exc) from exc
    spawn(S.execute(run.id), name=f"publishing-plan-{run.id}")
    return S.run_view(run, registry, with_result=False)


@router.post("/api/cases/{case_id}/publishing/{language}/proofread")
async def proofread(case_id: int, language: str, db: Session = Depends(get_db)):
    """Proofread the texts the publications hold now with the proofreader (paid; the owner's click). Corrections are saved as edits; the answer says what changed, what was refused and the cost."""
    case = _case(db, case_id)
    try:
        return await S.proofread_existing(db, PublicationRegistry(db), case, language)
    except (S.PlanRunError, PublishingRefused, BudgetStopped) as exc:
        raise _refuse(exc) from exc


@router.get("/api/cases/{case_id}/publishing/{language}/ai/runs/{run_id}")
def ai_run(case_id: int, language: str, run_id: int, db: Session = Depends(get_db)):
    run = db.get(PublishingPlanRun, run_id)
    if run is None or run.case_id != case_id or run.language != language:
        raise HTTPException(status_code=404, detail="Run not found")
    return S.run_view(run, PublicationRegistry(db))


@router.post("/api/cases/{case_id}/publishing/{language}/order")
def set_order(case_id: int, language: str, body: OrderBody, db: Session = Depends(get_db)):
    _case(db, case_id)
    registry = PublicationRegistry(db)
    try:
        registry.reorder(case_id, language, body.platform, body.order)
    except PublishingRefused as exc:
        raise _refuse(exc) from exc
    return registry.snapshot(case_id, language)


@router.get("/api/cases/{case_id}/publishing/{language}/approve-plan")
def approve_plan_preview(case_id: int, language: str, db: Session = Depends(get_db)):
    _case(db, case_id)
    return PublicationRegistry(db).plan_approval(case_id, language)


@router.post("/api/cases/{case_id}/publishing/{language}/approve-plan")
def approve_plan(case_id: int, language: str, db: Session = Depends(get_db)):
    _case(db, case_id)
    try:
        return PublicationRegistry(db).approve_plan(case_id, language)
    except PublishingRefused as exc:
        raise _refuse(exc) from exc


@router.post("/api/publications/{publication_id}/use-suggestion")
def use_suggestion(publication_id: int, db: Session = Depends(get_db)):
    registry = PublicationRegistry(db)
    try:
        return registry.view(registry.use_suggestion(publication_id), registry.suggestions(*_ids(db, publication_id)))
    except PublishingRefused as exc:
        raise _refuse(exc) from exc


def _ids(db: Session, publication_id: int) -> tuple[int, str]:
    pub = PublicationRegistry(db).get(publication_id)
    return pub.case_id, pub.language
