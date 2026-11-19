"""The owner's YouTube interface: connection (OAuth), the connection test, and the private upload of one publication. Nothing is automatic and nothing becomes public here.
No response ever carries a token or the upload session address."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.tasks import spawn
from app.db.base import SessionLocal, get_db
from app.db.models import Publication, YouTubeUploadSession
from app.publishing.registry import PublishingRefused
from app.youtube_publishing.channels import ChannelError, YouTubeChannels
from app.youtube_publishing.client import YouTubeError
from app.youtube_publishing.config import youtube_config
from app.youtube_publishing.oauth import OAuthError, forget_attempt, pending_attempt
from app.youtube_publishing.uploader import (
    Refused,
    YouTubeUploader,
    confirmation_text,
    latest_session,
)

router = APIRouter(prefix="/api/youtube-publishing", tags=["youtube-publishing"])
log = logging.getLogger(__name__)


class ConnectBody(BaseModel):
    return_to: str = "/"
    discover: bool = False       # only for a language whose channel id is not registered yet: read which channel the account has, store nothing


class RegisterBody(BaseModel):
    channel_id: str


class UploadBody(BaseModel):
    confirm: str | None = None


def _safe_return(path: str) -> str:
    return path if path.startswith("/") and not path.startswith("//") else "/"


def session_view(row: YouTubeUploadSession | None) -> dict | None:
    """The upload as the interface shows it: never the session address."""
    if row is None:
        return None
    return {"id": row.id, "status": row.status, "bytes_sent": row.bytes_sent, "total_bytes": row.total_bytes,
            "percent": round(100 * row.bytes_sent / row.total_bytes) if row.total_bytes else 0, "video_id": row.remote_video_id, "privacy": row.privacy,
            "upload_status": row.upload_status, "processing_status": row.processing_status, "processing_percent": row.processing_percent, "thumbnail": row.thumbnail_status,
            "captions": row.captions_status, "error": row.error, "required_action": row.required_action, "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def uploader_for(db: Session, publication_id: int) -> YouTubeUploader:
    return YouTubeUploader.for_publication(db, publication_id)


def _refusal(exc: Refused) -> HTTPException:
    return HTTPException(status_code=409, detail={"message": str(exc), "reason": exc.reason})


def _channels(db: Session) -> YouTubeChannels:
    return YouTubeChannels(db)


def _problem(exc: OAuthError | ChannelError | YouTubeError, status: int = 409) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": str(exc), "reason": exc.reason})


@router.get("/channels")
def channels(db: Session = Depends(get_db)):
    """Every YouTube channel with its connection state (a read of the registry and the token files: no call to Google)."""
    return {"channels": _channels(db).overview(), "live_enabled": youtube_config().live_enabled, "privacy": youtube_config().upload.initial_privacy}


@router.post("/channels/{language}/register")
def register(language: str, body: RegisterBody, db: Session = Depends(get_db)):
    """Record which channel a language publishes to (publication_accounts). Credentials are only ever accepted for that channel."""
    try:
        _channels(db).register(language, body.channel_id)
        return _channels(db).status(language)
    except ChannelError as exc:
        raise _problem(exc) from exc


@router.post("/channels/{language}/connect")
def connect(language: str, body: ConnectBody, db: Session = Depends(get_db)):
    """The address to send the owner to for Google's consent screen (PKCE, offline access) for this language's channel."""
    try:
        _channels(db).require_existing(language)
        return {"authorization_url": _channels(db).oauth(language).authorization_url(_safe_return(body.return_to), discover=body.discover)}
    except (OAuthError, ChannelError) as exc:
        raise _problem(exc) from exc


@router.get("/oauth/callback")
async def callback(code: str | None = None, state: str | None = None, error: str | None = None):
    """Google's redirect (one address for every channel). The language and the page to return to come from the attempt that started here; the channel is verified before anything is
    stored, and every outcome, an error too, returns the owner to the Studio with a message."""
    base = youtube_config().ui_base_url.rstrip("/")
    attempt = pending_attempt(state) if state else None
    where = _safe_return(attempt.return_to) if attempt else "/"

    def back(**query: str) -> RedirectResponse:
        present = parse_qs(urlparse(where).query)                       # a page that already names the language keeps its own
        extra = {key: value for key, value in query.items() if key not in present}
        return RedirectResponse(f"{base}{where}{'&' if '?' in where else '?'}{urlencode(extra)}")

    if attempt is None:
        log.warning("youtube oauth callback without a known attempt (error=%s)", error)
        return back(youtube="error", reason=error or "state_invalid", detail="The connection attempt expired or did not start here. Press Connect again.")
    language = attempt.language
    if error or not code:
        forget_attempt(state)
        log.warning("youtube oauth for %s was not completed: google said %s", language, error or "no code")
        return back(youtube="error", language=language, reason=error or "denied", detail=f"Google did not authorize the connection ({error or 'no code returned'}).")
    with SessionLocal() as db:
        channels_ = _channels(db)
        try:
            outcome = await channels_.oauth(language).complete(code, state)
        except OAuthError as exc:
            log.warning("youtube oauth for %s failed: %s: %s", language, exc.reason, exc)
            return back(youtube="error", language=language, reason=exc.reason, detail=str(exc)[:300])
        finally:
            channels_.sync(language)
    if "discovered" in outcome:
        found = outcome["discovered"]
        log.info("youtube channel discovery for %s: %s", language, found["id"])
        return back(youtube="discovered", language=language, channel_id=found["id"], channel_title=found["title"])
    log.info("youtube oauth for %s connected", language)
    return back(youtube="connected", language=language)


@router.post("/channels/{language}/test")
async def test(language: str, db: Session = Depends(get_db)):
    channels_ = _channels(db)
    try:
        return {"ok": True, "language": language, **await channels_.oauth(language).test()}
    except (OAuthError, ChannelError) as exc:
        raise _problem(exc) from exc
    except YouTubeError as exc:
        raise _problem(exc, 502) from exc


@router.post("/channels/{language}/disconnect")
async def disconnect(language: str, db: Session = Depends(get_db)):
    """Revoke and remove this channel's credentials; every other channel stays connected."""
    try:
        return await _channels(db).disconnect(language)
    except ChannelError as exc:
        raise _problem(exc) from exc


@router.get("/cases/{case_id}/{language}/uploads")
def uploads(case_id: int, language: str, db: Session = Depends(get_db)):
    """The upload state of every youtube publication of a case and language (a read of the database: no call to Google)."""
    try:
        channel_id = _channels(db).channel_id(language)
    except ChannelError as exc:
        raise _problem(exc) from exc
    out = {}
    for pub in db.query(Publication).filter_by(case_id=case_id, language=language, platform="youtube").all():
        out[str(pub.id)] = {"publication_id": pub.id, "asset_key": pub.asset_key, "kind": pub.kind, "confirmation": confirmation_text(pub.id, channel_id) if channel_id else None,
                            "session": session_view(latest_session(db, pub.id)), "remote_url": pub.remote_url,
                            "studio_url": youtube_config().upload.studio_url.format(video_id=pub.remote_id) if pub.remote_id else None}
    return {"channel_id": channel_id, "live_enabled": youtube_config().live_enabled, "uploads": out}


@router.post("/publications/{publication_id}/upload", status_code=202)
async def upload(publication_id: int, body: UploadBody, db: Session = Depends(get_db)):
    """Check everything that needs no call to Google (refusals answer at once), then upload privately in the background: poll /uploads."""
    try:
        uploader = uploader_for(db, publication_id)
        uploader.prepare(publication_id, body.confirm)
    except PublishingRefused as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail={"message": str(exc), "reason": "not_found"}) from exc
    except Refused as exc:
        raise _refusal(exc) from exc

    async def work() -> None:
        with SessionLocal() as session:
            try:
                await uploader_for(session, publication_id).run(publication_id, body.confirm)
            except Refused as exc:
                log.warning("youtube upload refused: %s", exc.reason)

    spawn(work(), name=f"youtube-upload-{publication_id}")
    return {"started": True, "publication_id": publication_id, "privacy": youtube_config().upload.initial_privacy}


@router.post("/publications/{publication_id}/thumbnail")
async def replace_thumbnail(publication_id: int, db: Session = Depends(get_db)):
    """Put the publication's current approved cover on its video on YouTube (about 50 quota units); the video is not touched."""
    try:
        return {"session": session_view(await uploader_for(db, publication_id).replace_thumbnail(publication_id))}
    except Refused as exc:
        raise _refusal(exc) from exc
    except (OAuthError, YouTubeError) as exc:
        raise HTTPException(status_code=502, detail={"message": str(exc), "reason": exc.reason}) from exc


@router.post("/cases/{case_id}/{language}/film-addresses")
async def film_addresses(case_id: int, language: str, db: Session = Depends(get_db)):
    """Put the film addresses into the first rows of the description of the language's YouTube Shorts that are public (the rest of each video is untouched)."""
    try:
        return {"youtube": await YouTubeUploader(db, _channels(db).oauth(language)).add_film_addresses(case_id, language)}
    except ChannelError as exc:
        raise _problem(exc) from exc


@router.post("/publications/{publication_id}/sync")
async def sync_visibility(publication_id: int, db: Session = Depends(get_db)):
    """Read the video's visibility from YouTube (a read) and bring the application's record in line, for a video that was made public or unlisted in Studio."""
    try:
        return {"privacy": await uploader_for(db, publication_id).sync_visibility(publication_id)}
    except Refused as exc:
        raise _refusal(exc) from exc


@router.post("/publications/{publication_id}/category")
async def set_category(publication_id: int, db: Session = Depends(get_db)):
    """Give the video on YouTube the configured category (about 50 quota units); only the category changes."""
    try:
        return {"changed": await uploader_for(db, publication_id).set_category(publication_id)}
    except Refused as exc:
        raise _refusal(exc) from exc

@router.post("/publications/{publication_id}/check-processing")
async def check_processing(publication_id: int, db: Session = Depends(get_db)):
    try:
        return {"session": session_view(await uploader_for(db, publication_id).check_processing(publication_id))}
    except Refused as exc:
        raise _refusal(exc) from exc
    except (OAuthError, YouTubeError) as exc:
        raise HTTPException(status_code=502, detail={"message": str(exc), "reason": exc.reason}) from exc
