"""The autopilot's HTTP surface: estimate and Start, the run page, the dry-run publish queue and
the channel placeholders."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.autopilot import channels
from app.autopilot import production as PR
from app.autopilot import service as SV
from app.db.base import get_db
from app.db.models import AutopilotRun, Case, PublishRequest

router = APIRouter()


class HandleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handle: str = Field(max_length=100)


class ProductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    languages: list[str] = Field(min_length=1)
    # language -> ``existing:<id>`` / ``registry:<outfit>``; the outfit may be saved language by language.
    outfits: dict[str, str] | None = None
    outfit_photos: dict[str, list[int]] | None = None


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/api/cases/{case_id}/production")
def case_production(case_id: int, db: Session = Depends(get_db)):
    return PR.view(db, _case(db, case_id))


@router.put("/api/cases/{case_id}/production")
def save_case_production(case_id: int, payload: ProductionRequest, db: Session = Depends(get_db)):
    try:
        return PR.save(db, _case(db, case_id), payload.languages, payload.outfits, payload.outfit_photos)
    except SV.StartError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/cases/{case_id}/production/autopilot", status_code=201)
def start_case_autopilot(case_id: int, db: Session = Depends(get_db)):
    try:
        run = PR.start(db, _case(db, case_id))
    except SV.StartError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SV.run_view(db, run)


@router.get("/api/channel-settings")
def get_channel_settings(db: Session = Depends(get_db)):
    return {"channels": channels.list_channels(db)}


@router.put("/api/channel-settings/{language}/{platform}")
def put_channel_handle(language: str, platform: str, payload: HandleRequest,
                       db: Session = Depends(get_db)):
    try:
        return channels.set_handle(db, language, platform, payload.handle)
    except channels.UnknownChannel as exc:
        raise HTTPException(status_code=404, detail=f"No channel {exc}") from exc


def _run(db: Session, run_id: int) -> AutopilotRun:
    run = db.get(AutopilotRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/api/autopilot/runs")
def list_autopilot_runs(db: Session = Depends(get_db)):
    rows = db.query(AutopilotRun).order_by(AutopilotRun.id.desc()).limit(50).all()
    return {"runs": [SV.run_view(db, r) for r in rows]}


@router.get("/api/autopilot/runs/{run_id}")
def get_autopilot_run(run_id: int, db: Session = Depends(get_db)):
    return SV.run_view(db, _run(db, run_id))


@router.post("/api/autopilot/runs/{run_id}/cancel")
def cancel_autopilot_run(run_id: int, db: Session = Depends(get_db)):
    return SV.run_view(db, SV.cancel_run(db, _run(db, run_id)))


@router.get("/api/autopilot/runs/{run_id}/publish-requests")
def autopilot_publish_requests(run_id: int, db: Session = Depends(get_db)):
    _run(db, run_id)
    rows = (db.query(PublishRequest).filter(PublishRequest.run_id == run_id)
            .order_by(PublishRequest.scheduled_for, PublishRequest.id).all())
    return {"sent": sum(r.status != "dry_run" for r in rows),
            "requests": [{"id": r.id, "language": r.language, "platform": r.platform,
                          "kind": r.kind, "short_id": r.short_id, "status": r.status,
                          "scheduled_for": r.scheduled_for,
                          "request": json.loads(r.request_json)} for r in rows]}
