"""Case lifecycle API: resolution status, films (videos) and archive,
the unsolved-case monitor, follow-up approvals and the audit trail."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.ai_config import RESOLUTION_STATUSES, ai_config
from app.db.base import get_db
from app.db.models import (
    Case, CaseStatusCheck, DiscoveryCandidate, DocumentaryJob, FollowUpCandidate, MediaUsage,
    MonitorRun, MusicTrack, MusicUsage, ProductionScript, VisualAsset, VisualPlan, Video,
)
from app.lifecycle import followups as F
from app.lifecycle import scheduler as S
from app.lifecycle import videos as V
from app.lifecycle.monitor import UnsolvedCaseMonitor, check_dict, run_dict
from app.lifecycle.selection import candidate_dict
from app.lifecycle.status import history, history_dict, set_resolution

router = APIRouter(tags=["lifecycle"])
_background: set[asyncio.Task] = set()


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# resolution status
# ---------------------------------------------------------------------------


def resolution_dict(case: Case) -> dict:
    return {
        "case_uid": case.case_uid,
        "resolution_status": case.resolution_status or "UNKNOWN",
        "resolution_confidence": case.resolution_confidence,
        "resolution_summary": case.resolution_summary,
        "resolution_checked_at": case.resolution_checked_at,
    }


class StatusRequest(BaseModel):
    status: Literal["SOLVED", "UNSOLVED", "UNKNOWN", "STATUS_UNDER_REVIEW"]
    reason: str = Field(min_length=3, max_length=4000)
    sources: list[str] = []
    summary: str | None = None


@router.get("/api/cases/{case_id}/status")
def case_status(case_id: int, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    return {**resolution_dict(case), "statuses": list(RESOLUTION_STATUSES),
            "history": [history_dict(h) for h in history(db, case.id)]}


@router.put("/api/cases/{case_id}/status")
def set_case_status(case_id: int, payload: StatusRequest, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    set_resolution(db, case, payload.status, changed_by="user", reason=payload.reason,
                   sources=[{"url": u} for u in payload.sources], summary=payload.summary,
                   confidence=1.0)
    return case_status(case_id, db)


@router.get("/api/cases/{case_id}/status-checks")
def case_status_checks(case_id: int, db: Session = Depends(get_db)):
    _case(db, case_id)
    rows = (db.query(CaseStatusCheck).filter(CaseStatusCheck.case_id == case_id)
            .order_by(CaseStatusCheck.id.desc()).limit(50).all())
    return [check_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# films (videos) + archive
# ---------------------------------------------------------------------------


@router.get("/api/films")
def list_films(state: str | None = None, status: str | None = None, language: str | None = None,
               case_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(Video)
    if state:
        q = q.filter(Video.state == state)
    if language:
        q = q.filter(Video.language == language)
    if case_id:
        q = q.filter(Video.case_id == case_id)
    if status and status.upper() != "ALL":
        q = q.join(Case, Case.id == Video.case_id).filter(Case.resolution_status == status.upper())
    rows = q.order_by(Video.id.desc()).limit(500).all()
    cases = {c.id: c for c in db.query(Case).filter(Case.id.in_({r.case_id for r in rows})).all()}
    return [{**V.video_dict(r), "case_title": cases[r.case_id].canonical_title,
             "case_status": cases[r.case_id].resolution_status} for r in rows]


@router.get("/api/films/{video_id}")
def get_film(video_id: int, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(status_code=404, detail="Video not found")
    case = db.get(Case, v.case_id)
    return {**V.video_dict(v), "case_title": case.canonical_title,
            "case_status": case.resolution_status,
            "follow_ups": [V.video_dict(x) for x in
                           db.query(Video).filter(Video.original_video_id == v.id).all()]}


class PublishRequest(BaseModel):
    published_at: datetime | None = None
    youtube_url: str | None = Field(default=None, max_length=500)
    episode_number: int | None = Field(default=None, ge=1)


@router.post("/api/films/{video_id}/publish")
def publish_film(video_id: int, payload: PublishRequest, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(status_code=404, detail="Video not found")
    V.publish(db, v, published_at=payload.published_at, youtube_url=payload.youtube_url,
              episode_number=payload.episode_number)
    return get_film(video_id, db)


@router.post("/api/films/{video_id}/archive")
def archive_film(video_id: int, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(status_code=404, detail="Video not found")
    v.state = "archived"
    db.commit()
    return get_film(video_id, db)


@router.get("/api/archive")
def archive(status: str = "ALL", db: Session = Depends(get_db)):
    """Archived cases and cases with published films — solved and unsolved
    stay distinguishable (filter SOLVED | UNSOLVED | ALL)."""
    published = {cid for (cid,) in db.query(Video.case_id).filter(
        Video.state.in_(("published", "archived"))).distinct()}
    q = db.query(Case).filter(or_(Case.status == "archived", Case.id.in_(published)))
    st = (status or "ALL").upper()
    if st != "ALL":
        if st not in RESOLUTION_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be ALL or one of "
                                                        f"{', '.join(RESOLUTION_STATUSES)}")
        q = q.filter(Case.resolution_status == st)
    out = []
    for c in q.order_by(Case.id.desc()).all():
        films = db.query(Video).filter(Video.case_id == c.id).order_by(Video.id).all()
        out.append({"id": c.id, "title": c.canonical_title, "status": c.status,
                    **resolution_dict(c), "location": c.location,
                    "films": [V.video_dict(f) for f in films]})
    counts = {s: sum(1 for x in out if x["resolution_status"] == s) for s in RESOLUTION_STATUSES}
    return {"filter": st, "counts": counts, "cases": out}


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------


@router.get("/api/monitor")
def monitor_status(db: Session = Depends(get_db)):
    runs = db.query(MonitorRun).order_by(MonitorRun.id.desc()).limit(10).all()
    watched = (db.query(Case).filter(Case.resolution_status.in_(ai_config.case_monitor.statuses))
               .count())
    return {**S.status(db), "watched_cases": watched, "runs": [run_dict(r) for r in runs]}


@router.post("/api/monitor/run", status_code=202)
async def run_monitor_now():
    """Start a monitor run now (in the background)."""
    from app.db.base import SessionLocal

    async def go():
        db = SessionLocal()
        try:
            await UnsolvedCaseMonitor().run(db, trigger="manual")
        finally:
            db.close()

    task = asyncio.get_event_loop().create_task(go())
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"started": True}


@router.get("/api/monitor/runs/{run_id}")
def monitor_run(run_id: int, db: Session = Depends(get_db)):
    r = db.get(MonitorRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="Monitor run not found")
    checks = db.query(CaseStatusCheck).filter(CaseStatusCheck.monitor_run_id == run_id).all()
    return {**run_dict(r), "checks": [check_dict(c) for c in checks]}


# ---------------------------------------------------------------------------
# follow-ups
# ---------------------------------------------------------------------------


@router.get("/api/follow-ups")
def list_follow_ups(state: str | None = "pending", db: Session = Depends(get_db)):
    q = db.query(FollowUpCandidate)
    if state and state != "all":
        q = q.filter(FollowUpCandidate.state == state)
    return [F.candidate_dict(db, fu) for fu in q.order_by(FollowUpCandidate.id.desc()).all()]


class ApproveRequest(BaseModel):
    mode: Literal["pilot", "full"] = "pilot"
    languages: list[str] | None = None
    render_profile: Literal["preview", "publish"] = "preview"
    pilot_seconds: float | None = Field(default=None, gt=10, le=1800)


@router.post("/api/follow-ups/{fu_id}/approve")
def approve_follow_up(fu_id: int, payload: ApproveRequest, db: Session = Depends(get_db)):
    from app.documentary import jobs as J

    fu = db.get(FollowUpCandidate, fu_id)
    if not fu:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    running = db.query(DocumentaryJob).filter(
        DocumentaryJob.case_id == fu.case_id,
        DocumentaryJob.status.in_(("queued", "running", "cancelling"))).first()
    if running:
        raise HTTPException(status_code=409, detail=f"Job {running.id} is still running.")
    try:
        job = F.approve(db, fu, mode=payload.mode, languages=payload.languages,
                        profile=payload.render_profile,
                        pilot_seconds=payload.pilot_seconds or (
                            ai_config.documentary.pilot_seconds if payload.mode == "pilot" else None))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"follow_up": F.candidate_dict(db, fu), "job": J.job_dict(job)}


@router.post("/api/follow-ups/{fu_id}/dismiss")
def dismiss_follow_up(fu_id: int, db: Session = Depends(get_db)):
    fu = db.get(FollowUpCandidate, fu_id)
    if not fu:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    try:
        F.dismiss(db, fu)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return F.candidate_dict(db, fu)


# ---------------------------------------------------------------------------
# audit: why did the system decide what it decided?
# ---------------------------------------------------------------------------


@router.get("/api/cases/{case_id}/audit")
def case_audit(case_id: int, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    suggestions = db.query(DiscoveryCandidate).filter(
        or_(DiscoveryCandidate.case_id == case_id,
            DiscoveryCandidate.duplicate_of_case_id == case_id)).all()
    scripts = (db.query(ProductionScript).filter(ProductionScript.case_id == case_id)
               .order_by(ProductionScript.id.desc()).all())
    latest: dict[str, ProductionScript] = {}
    for ps in scripts:
        latest.setdefault(ps.language, ps)
    assets = {a.id: a for a in db.query(VisualAsset).filter(VisualAsset.case_id == case_id)}
    tracks = {t.id: t for t in db.query(MusicTrack).all()}
    productions = []
    for lang, ps in latest.items():
        script = _loads(ps.script_json, {})
        media = db.query(MediaUsage).filter(MediaUsage.production_script_id == ps.id).order_by(
            MediaUsage.start).all()
        music = db.query(MusicUsage).filter(MusicUsage.production_script_id == ps.id).order_by(
            MusicUsage.start).all()
        plan = db.get(VisualPlan, ps.visual_plan_id) if ps.visual_plan_id else None
        productions.append({
            "production_script_id": ps.id, "language": lang, "mode": ps.mode,
            "case_status": script.get("case_status"),
            "production_type": script.get("production_type"),
            "opening_strategy": script.get("opening_strategy"),
            "pictures": [{
                "start": u.start, "seconds": u.seconds, "kind": u.kind, "tier": u.tier,
                "asset": assets[u.asset_id].asset_code if u.asset_id in assets else u.asset_id,
                "title": assets[u.asset_id].title if u.asset_id in assets else None,
                "sentence": u.sentence, "reason": u.reason, "appearance": u.appearance,
                "repeat_justified": u.repeat_justified, "repeat_reason": u.repeat_reason,
            } for u in media],
            "maps": [{"start": u.start, "reason": u.reason, "sentence": u.sentence}
                     for u in media if u.kind == "map"],
            "music": [{
                "start": m.start, "end": m.end, "purpose": m.purpose, "mood": m.mood,
                "track": tracks[m.track_id].track_code if m.track_id in tracks else None,
                "why": m.why, "selection_reason": m.selection_reason,
            } for m in music],
            "search_requests": _loads(plan.validation_json, {}).get("search_requests", [])
            if plan else [],
        })
    return {
        "case": {"id": case.id, "title": case.canonical_title, "origin": case.origin,
                 **resolution_dict(case)},
        "suggestions": [candidate_dict(s) for s in suggestions],
        "status_history": [history_dict(h) for h in history(db, case.id)],
        "status_checks": case_status_checks(case_id, db)[:20],
        "productions": productions,
        "films": [V.video_dict(v) for v in db.query(Video).filter(Video.case_id == case_id)],
        "follow_ups": [F.candidate_dict(db, fu) for fu in db.query(FollowUpCandidate).filter(
            FollowUpCandidate.case_id == case_id)],
    }


# ---------------------------------------------------------------------------
# episode identity (public title, localized status, channel)
# ---------------------------------------------------------------------------

@router.get("/api/cases/{case_id}/identity")
def case_identity(case_id: int, db: Session = Depends(get_db)):
    """Per language: the episode identity (or the title it would get),
    plus where the resolution status came from."""
    from app.identity import titles as T

    case = _case(db, case_id)
    out = {}
    for lang in ai_config.channels:
        ident = T.get_identity(db, case.id, lang)
        out[lang] = (T.identity_dict(ident) if ident else {
            "language": lang, "channel_id": T.channel(lang)["id"], "case_uid": case.case_uid,
            "resolution_label": T.status_label(case.resolution_status, lang)})
        out[lang]["channel"] = T.channel(lang)["name"]
    return {"case_id": case.id, "case_uid": case.case_uid,
            "provenance": T.resolution_provenance(db, case), "languages": out}
