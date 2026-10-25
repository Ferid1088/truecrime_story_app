"""The autopilot's HTTP surface: estimate and Start, the run page, the dry-run publish queue and
the channel placeholders."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.autopilot import channels
from app.autopilot import service as SV
from app.core.ai_config import ai_config
from app.db.base import get_db
from app.db.models import AutopilotRun, DiscoveryCandidate, PublishRequest

router = APIRouter()


class HandleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handle: str = Field(max_length=100)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: int
    languages: list[str] = Field(min_length=1)
    # ``existing:<id>`` selects an outfit already attached to the case;
    # ``registry:<language>/<outfit>`` creates/selects the shared outfit for a
    # newly opened case.  Kept optional for API backwards compatibility; the
    # UI always sends it for a new run.
    outfits: dict[str, str] | None = None
    # Library item ids checked by the owner for each language's chosen outfit.
    outfit_photos: dict[str, list[int]] | None = None


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


@router.get("/api/autopilot/estimate")
def autopilot_estimate(candidate_id: int, languages: str | None = None,
                       db: Session = Depends(get_db)):
    cand = db.get(DiscoveryCandidate, candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    chosen = languages.split(",") if languages else list(ai_config.autopilot_settings().languages)
    try:
        return SV.estimate_for_candidate(db, cand, chosen)
    except SV.StartError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/autopilot/runs", status_code=201)
def start_autopilot_run(payload: StartRequest, db: Session = Depends(get_db)):
    try:
        run = SV.start_run(db, payload.candidate_id, payload.languages, payload.outfits,
                           payload.outfit_photos)
    except SV.StartError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SV.run_view(db, run)


@router.get("/api/autopilot/outfit-options")
def autopilot_outfit_options(candidate_id: int, languages: str | None = None,
                             db: Session = Depends(get_db)):
    cand = db.get(DiscoveryCandidate, candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    chosen = languages.split(",") if languages else list(ai_config.autopilot_settings().languages)
    try:
        return SV.outfit_options_for_candidate(db, cand, chosen)
    except SV.StartError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
