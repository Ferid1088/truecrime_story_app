"""The Publishing Studio API: the plan of a case and language, the edits and approvals of its publications, and their routing links. Nothing here uploads or publishes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Case, RoutingLink
from app.publishing.registry import PublicationRegistry, PublishingRefused

router = APIRouter(tags=["publishing"])


class PlanBody(BaseModel):
    start_date: date | None = None
    reset_schedule: bool = False


class EditBody(BaseModel):
    caption: str | None = None
    scheduled_local: str | None = None
    clear_schedule: bool = False
    visibility: str | None = None
    metadata: dict | None = None


class RoutingBody(BaseModel):
    status: str
    evidence_note: str | None = None


def _refuse(exc: PublishingRefused) -> HTTPException:
    """409 with the message and, one by one, the reasons (the Studio shows each on its own line)."""
    return HTTPException(status_code=409, detail={"message": str(exc), "reasons": exc.reasons})


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/api/cases/{case_id}/publishing/{language}")
def studio(case_id: int, language: str, db: Session = Depends(get_db)):
    _case(db, case_id)
    return PublicationRegistry(db).snapshot(case_id, language)


@router.get("/api/cases/{case_id}/publishing-overview")
def overview(case_id: int, db: Session = Depends(get_db)):
    _case(db, case_id)
    return {"publications": PublicationRegistry(db).overview(case_id)}


@router.post("/api/cases/{case_id}/publishing/{language}/plan")
def make_plan(case_id: int, language: str, body: PlanBody, db: Session = Depends(get_db)):
    _case(db, case_id)
    registry = PublicationRegistry(db)
    try:
        registry.plan(case_id, language, start_date=body.start_date, reset_schedule=body.reset_schedule)
    except PublishingRefused as exc:
        raise _refuse(exc) from exc
    return registry.snapshot(case_id, language)


def _act(db: Session, publication_id: int, action: str):
    registry = PublicationRegistry(db)
    try:
        pub = getattr(registry, action)(publication_id)
    except PublishingRefused as exc:
        raise _refuse(exc) from exc
    return registry.view(pub)


@router.patch("/api/publications/{publication_id}")
def edit_publication(publication_id: int, body: EditBody, db: Session = Depends(get_db)):
    registry = PublicationRegistry(db)
    try:
        pub = registry.update(publication_id, caption=body.caption, scheduled_local=body.scheduled_local, clear_schedule=body.clear_schedule, visibility=body.visibility,
                                 metadata=body.metadata)
    except PublishingRefused as exc:
        raise _refuse(exc) from exc
    return registry.view(pub)


@router.post("/api/publications/{publication_id}/content-approval")
def approve_content(publication_id: int, db: Session = Depends(get_db)):
    return _act(db, publication_id, "approve_content")


@router.post("/api/publications/{publication_id}/publication-approval")
def approve_publication(publication_id: int, db: Session = Depends(get_db)):
    return _act(db, publication_id, "approve_publication")


@router.post("/api/publications/{publication_id}/revoke")
def revoke_approval(publication_id: int, db: Session = Depends(get_db)):
    return _act(db, publication_id, "revoke")


@router.post("/api/publications/{publication_id}/schedule")
def schedule_publication(publication_id: int, db: Session = Depends(get_db)):
    return _act(db, publication_id, "schedule")


@router.patch("/api/routing-links/{link_id}")
def edit_routing_link(link_id: int, body: RoutingBody, db: Session = Depends(get_db)):
    registry = PublicationRegistry(db)
    try:
        link = registry.update_routing(link_id, status=body.status, evidence_note=body.evidence_note)
    except PublishingRefused as exc:
        raise _refuse(exc) from exc
    row = db.get(RoutingLink, link.id)
    return registry.view(registry.get(row.publication_id))
