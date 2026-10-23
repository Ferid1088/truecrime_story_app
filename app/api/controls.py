"""Case control endpoints: pause / resume / stop (keep|delete) / restore, the
audit trail, and the global emergency stop. Every action is idempotent and
persisted in the database — a pause survives an API or worker restart."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_case_or_404
from app.db.base import get_db
from app.db.models import CaseEvent
from app.services import case_control

router = APIRouter()


def _err(exc: case_control.ControlError) -> HTTPException:
    status = 404 if exc.code in ("case_trashed", "not_trashed") else 409
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


class PauseRequest(BaseModel):
    languages: list[str] | None = None
    reason: str | None = None
    actor: str = "owner"


class ActorRequest(BaseModel):
    actor: str = "owner"


class StopRequest(BaseModel):
    mode: str                          # "keep" | "delete"
    confirm_name: str | None = None    # required for mode="delete"
    reason: str | None = None
    actor: str = "owner"


class GlobalStopRequest(BaseModel):
    active: bool
    actor: str = "owner"


@router.get("/api/cases/{case_id}/control")
def case_control_state(case_id: int, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    return case_control.control_view(db, case)


@router.get("/api/cases/{case_id}/control/trash-preview")
def trash_preview(case_id: int, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    return case_control.trash_preview(db, case)


@router.post("/api/cases/{case_id}/pause")
def pause_case(case_id: int, payload: PauseRequest, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    try:
        return case_control.pause_case(db, case, actor=payload.actor,
                                       languages=payload.languages, reason=payload.reason)
    except case_control.ControlError as exc:
        raise _err(exc) from exc


@router.post("/api/cases/{case_id}/resume")
def resume_case(case_id: int, payload: ActorRequest, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    try:
        return case_control.resume_case(db, case, actor=payload.actor)
    except case_control.ControlError as exc:
        raise _err(exc) from exc


@router.post("/api/cases/{case_id}/stop")
def stop_case(case_id: int, payload: StopRequest, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    try:
        return case_control.stop_case(db, case, mode=payload.mode, actor=payload.actor,
                                      reason=payload.reason, confirm_name=payload.confirm_name)
    except case_control.ControlError as exc:
        raise _err(exc) from exc


@router.post("/api/cases/{case_id}/restore")
def restore_case(case_id: int, payload: ActorRequest, db: Session = Depends(get_db)):
    case = get_case_or_404(db, case_id)
    try:
        return case_control.restore_case(db, case, actor=payload.actor)
    except case_control.ControlError as exc:
        raise _err(exc) from exc


@router.get("/api/cases/{case_id}/control/events")
def case_events(case_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    rows = (db.query(CaseEvent).filter(CaseEvent.case_id == case_id)
            .order_by(CaseEvent.id.desc()).limit(100).all())
    return [{"id": r.id, "action": r.action, "actor": r.actor, "stage": r.stage,
             "reason": r.reason, "state_before": r.state_before, "state_after": r.state_after,
             "cost_eur_at_event": r.cost_eur_at_event, "created_at": r.created_at,
             "detail": json.loads(r.detail_json or "{}")} for r in rows]


@router.get("/api/control/global")
def global_stop_state(db: Session = Depends(get_db)):
    return case_control.global_stop_status(db)


@router.post("/api/control/global")
def set_global_stop(payload: GlobalStopRequest, db: Session = Depends(get_db)):
    return case_control.set_global_stop(db, payload.active, payload.actor)


@router.get("/api/control/tombstones")
def tombstones(db: Session = Depends(get_db)):
    """Deleted cases' permanent records — the cost history stays exportable."""
    from app.db.models import CaseTombstone
    rows = db.query(CaseTombstone).order_by(CaseTombstone.id.desc()).limit(200).all()
    return [{"case_id": t.case_id, "name": t.name, "slug": t.slug, "who": t.who,
             "total_cost_eur": t.total_cost_eur, "deleted_at": t.deleted_at,
             "restore_until": t.restore_until, "restored_at": t.restored_at,
             "purged_at": t.purged_at} for t in rows]


@router.get("/api/cases/{case_id}/costs-ledger")
def deleted_case_ledger(case_id: int, db: Session = Depends(get_db)):
    """The cost ledger of a deleted case stays readable: rows marked case_deleted."""
    from app.db.models import ProviderCall
    rows = (db.query(ProviderCall).filter(ProviderCall.case_id == case_id)
            .order_by(ProviderCall.id).all())
    return [{"id": r.id, "stage": r.stage, "model": r.model, "units": r.units,
             "cost_eur": r.cost_eur, "estimated": r.estimated,
             "case_deleted": r.case_deleted, "request_id": r.request_id,
             "created_at": r.created_at} for r in rows]
