"""The owner's Facebook Page interface: connection status, the read-only connection test, and the upload of one publication (a Reel or a long film). Nothing is automatic and nothing becomes
public here unless the separate switch is on and the owner types the confirmation. No response ever carries a token, a Page access token or an upload address."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.tasks import spawn
from app.db.base import SessionLocal, get_db
from app.db.models import FacebookUploadSession, Publication
from app.meta_publishing.connection import ConnectError
from app.meta_publishing.facebook_connection import FacebookConnection
from app.meta_publishing.facebook_publisher import (
    FacebookPublisher,
    Mode,
    Refused,
    confirmation_text,
    latest_session,
    text_hash,
)
from app.meta_publishing.graph import MetaError
from app.meta_publishing.token import TokenError
from app.publishing.config import publishing_config
from app.publishing.registry import PublishingRefused
from app.utils import utc_now

router = APIRouter(prefix="/api/facebook-publishing", tags=["facebook-publishing"])
log = logging.getLogger(__name__)


class UploadBody(BaseModel):
    mode: Mode
    confirm: str | None = None


def session_view(row: FacebookUploadSession | None) -> dict | None:
    """The upload as the interface shows it: never the upload address or the file handle."""
    if row is None:
        return None
    return {"id": row.id, "kind": row.kind, "status": row.status, "bytes_sent": row.bytes_sent, "total_bytes": row.total_bytes,
            "percent": round(100 * row.bytes_sent / row.total_bytes) if row.total_bytes else 0, "video_id": row.remote_video_id, "video_status": row.video_status,
            "requested": {"requested_public": "public", "requested_draft": "draft"}.get(row.publish_status or ""), "error": row.error, "required_action": row.required_action,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def _refusal(exc: Refused) -> HTTPException:
    return HTTPException(status_code=409, detail={"message": str(exc), "reason": exc.reason})


def _publisher(db: Session) -> FacebookPublisher:
    return FacebookPublisher(db)


def _text_stale(publisher: FacebookPublisher, pub: Publication) -> bool:
    """The text of this publication was corrected after the video was finished on Facebook: the video there keeps the text it was finished with."""
    session = latest_session(publisher.db, pub.id)
    if session is None or not (session.status in ("drafted", "published", "uncertain") or session.publish_status in ("requested_draft", "requested_public")):
        return False                                       # not finished on Facebook yet: the finish will carry the present text
    title, description, _problems = publisher._text(pub)
    return text_hash(title, description) != session.metadata_hash


@router.get("/connection")
def connection(db: Session = Depends(get_db)):
    """The Page's connection state (the stored facts and the configuration: no call to Meta). The registry's account row is brought in line with it."""
    conn = FacebookConnection()
    conn.sync(db)
    return conn.status()


@router.post("/connection/test")
async def test_connection(db: Session = Depends(get_db)):
    conn = FacebookConnection()
    try:
        return {"ok": True, **await conn.test()}
    except ConnectError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "reason": exc.reason}) from exc
    finally:
        conn.sync(db)


@router.get("/cases/{case_id}/{language}/uploads")
def uploads(case_id: int, language: str, db: Session = Depends(get_db)):
    """The upload state, readiness and confirmation texts of every facebook publication of a case and language (a read of the database: no call to Meta)."""
    publisher = _publisher(db)
    page = publisher.page_id
    out = {}
    for pub in db.query(Publication).filter_by(case_id=case_id, language=language, platform="facebook").all():
        out[str(pub.id)] = {"publication_id": pub.id, "asset_key": pub.asset_key, "kind": pub.kind,
                            "confirmations": {mode.value: confirmation_text(mode, pub.id, page) for mode in (Mode.UPLOAD_ONLY, Mode.SAVE_DRAFT, Mode.PUBLISH)},
                            "readiness": publisher.readiness(pub.id) if language == publisher.f.language else [f"Only the {publisher.f.language} Page is connected."],
                            "session": session_view(latest_session(db, pub.id)), "remote_url": pub.remote_url, "text_stale": _text_stale(publisher, pub)}
    return {"page_id": page, "live_enabled": publisher.f.live_enabled, "allow_public_publish": publisher.f.allow_public_publish,
            "automatic_publishing": publishing_config().automatic_publishing.enabled, "uploads": out}


@router.get("/publications/{publication_id}/plan")
def plan(publication_id: int, db: Session = Depends(get_db)):
    """A dry run: no request to Meta."""
    try:
        return {"steps": [step.__dict__ for step in _publisher(db).plan(publication_id)]}
    except PublishingRefused as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc), "reason": "not_found"}) from exc


@router.post("/publications/{publication_id}/upload", status_code=202)
async def upload(publication_id: int, body: UploadBody, db: Session = Depends(get_db)):
    """Check everything that needs no call to Meta (refusals answer at once), then upload in the background: poll /uploads."""
    if body.mode is Mode.DRY_RUN:
        raise HTTPException(status_code=422, detail={"message": "A dry run is GET /plan.", "reason": "dry_run"})
    try:
        _publisher(db).prepare(publication_id, body.mode, body.confirm)
    except PublishingRefused as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc), "reason": "not_found"}) from exc
    except Refused as exc:
        raise _refusal(exc) from exc

    async def work() -> None:
        with SessionLocal() as session:
            try:
                await _publisher(session).run(publication_id, body.mode, body.confirm)
            except Refused as exc:
                log.warning("facebook upload refused: %s", exc.reason)

    spawn(work(), name=f"facebook-{body.mode.value}-{publication_id}")
    return {"started": True, "publication_id": publication_id, "mode": body.mode.value}


@router.post("/publications/{publication_id}/check-processing")
async def check_processing(publication_id: int, db: Session = Depends(get_db)):
    try:
        return {"session": session_view(await _publisher(db).check_processing(publication_id))}
    except Refused as exc:
        raise _refusal(exc) from exc


@router.get("/publications/{publication_id}/thumbnails")
async def thumbnails(publication_id: int, db: Session = Depends(get_db)):
    """Which thumbnail Meta holds and uses for the uploaded Reel (a read-only call to Meta: nothing is sent or changed). It never carries a token."""
    session = latest_session(db, publication_id)
    if session is None or not session.remote_video_id:
        raise HTTPException(status_code=409, detail={"message": "This publication has no upload on Facebook yet.", "reason": "no_upload"})
    publisher = _publisher(db)
    try:
        rows = await publisher.client.thumbnails(session.remote_video_id)
    except (MetaError, TokenError) as exc:
        raise HTTPException(status_code=502, detail={"message": publisher.tokens.redact(str(exc)), "reason": getattr(exc, "kind", "token")}) from exc
    return {"video_id": session.remote_video_id, "checked_at": utc_now().isoformat(), "thumbnails": rows, "preferred_id": next((t["id"] for t in rows if t["is_preferred"]), None)}


class CoverBody(BaseModel):
    variant_id: int


@router.post("/publications/{publication_id}/cover")
async def set_cover(publication_id: int, body: CoverBody, db: Session = Depends(get_db)):
    """Sets a reel cover picture of this Short as the cover of the video already uploaded to Facebook (needs the cover permissions; never carries a token). Refusals say what is missing."""
    try:
        return await _publisher(db).set_cover(publication_id, body.variant_id)
    except Refused as exc:
        raise _refusal(exc) from exc


@router.post("/publications/{publication_id}/cancel")
def cancel(publication_id: int, db: Session = Depends(get_db)):
    try:
        return {"session": session_view(_publisher(db).cancel(publication_id))}
    except Refused as exc:
        raise _refusal(exc) from exc
