"""The owner's test interface of the Instagram Reels publisher (isolated: no scheduler, no pipeline calls it). The token is never returned."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.tasks import spawn
from app.db.base import SessionLocal, get_db
from app.db.models import Publication
from app.meta_publishing.config import meta_config
from app.meta_publishing.connection import (
    ConnectError,
    InstagramConnection,
    forget_attempt,
    pending_return,
)
from app.meta_publishing.graph import MetaError
from app.meta_publishing.publisher import (
    LEGACY_PLATFORM,
    PLATFORM,
    InstagramReelsPublisher,
    Mode,
    Refused,
    Report,
    confirmation_text,
    operation_dict,
)
from app.meta_publishing.token import TokenStore

router = APIRouter(prefix="/api/meta-publishing", tags=["meta-publishing"])
log = logging.getLogger(__name__)


class OperationBody(BaseModel):
    clip_key: str
    language: str = "fa"
    mode: Mode | None = None
    confirm: str | None = None


def _publisher() -> InstagramReelsPublisher:
    return InstagramReelsPublisher()


def _report(report: Report) -> dict:
    return {"mode": report.mode.value, "clip_key": report.clip_key, "language": report.language, "account": report.account,
            "steps": [{"name": s.name, "status": s.status, "detail": s.detail} for s in report.steps], "operation": report.operation, "manual_steps": report.manual_steps}


def _refusal(exc: Refused) -> HTTPException:
    return HTTPException(status_code=409, detail={"message": str(exc), "reason": exc.reason})


@router.get("/status")
def status(db: Session = Depends(get_db)):
    """The destination, the safety settings and every clip of the configured case with its Instagram preflight verdict (no external request)."""
    cfg, publisher = meta_config(), _publisher()
    clips = []
    for row in publisher.preflight.check_case(db):
        ig = row.platforms.get(LEGACY_PLATFORM)
        clips.append({"clip_key": row.clip_key, "file": row.path.name if row.path else None, "seconds": round(row.facts.duration, 1) if row.facts else None,
                      "verdict": row.verdict(LEGACY_PLATFORM).value, "error": row.error,
                      "failing": [{"name": c.name, "actual": c.actual, "expected": c.expected, "action": c.action} for c in (ig.checks if ig else []) if c.verdict.value != "PASS"]})
    operations = db.query(Publication).filter(Publication.platform == PLATFORM, Publication.container_id.isnot(None)).order_by(Publication.updated_at.desc()).limit(20).all()
    s = cfg.instagram
    return {"case_id": cfg.preflight.case_id, "language": s.language, "account": publisher.account, "default_mode": s.default_mode, "live_enabled": s.live_enabled,
            "token": {"configured": TokenStore(s).source() is not None, "source": TokenStore(s).source()}, "clips": clips,
            "operations": [operation_dict(o) for o in operations],
            "confirmation_template": confirmation_text(s.language, "<clip>", s.account.ig_account_id)}


@router.post("/authorization")
async def authorization():
    """Verify the account and the publishing permission with the configured token (read-only calls; needs live_enabled)."""
    publisher = _publisher()
    if not publisher.s.live_enabled:
        raise _refusal(Refused("live_disabled", "Live calls are off (config/meta_publishing.json: instagram.live_enabled is false)."))
    try:
        publisher.tokens.get()
        steps = await publisher.authorize()
    except Refused as exc:
        raise _refusal(exc) from exc
    except Exception as exc:
        kind = exc.kind if isinstance(exc, MetaError) else "token_missing"
        raise HTTPException(status_code=502, detail={"message": publisher.tokens.redact(str(exc)), "reason": kind}) from exc
    return {"steps": [{"name": s.name, "status": s.status, "detail": s.detail} for s in steps]}


@router.post("/operations")
async def start_operation(body: OperationBody, db: Session = Depends(get_db)):
    """DRY_RUN answers at once. UPLOAD_ONLY and PUBLISH are checked here (refusals are answered at once) and then run in the background: poll /status."""
    publisher = _publisher()
    mode = body.mode or Mode(publisher.s.default_mode)
    try:
        target = publisher.resolve(db, body.language, body.clip_key)
        if mode is Mode.DRY_RUN:
            return _report(publisher.dry_run(target))
        publisher.guard_live(target, mode, body.confirm)
    except Refused as exc:
        raise _refusal(exc) from exc

    async def work() -> None:
        with SessionLocal() as session:
            try:
                await _publisher().run(session, language=body.language, clip_key=body.clip_key, mode=mode, confirm=body.confirm)
            except Refused as exc:
                log.warning("meta publishing refused: %s", exc.reason)

    spawn(work(), name=f"meta-{mode.value}-{body.clip_key}")
    return {"mode": mode.value, "clip_key": body.clip_key, "started": True}


class ConnectBody(BaseModel):
    return_to: str = "/"


def _connection(language: str | None = None) -> InstagramConnection:
    """The connection of one language's Instagram account (the configured default when none is given); a language without an account is refused."""
    try:
        return InstagramConnection(meta_config().instagram.for_language(language) if language else None)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "reason": "no_account"}) from exc


def _problem(exc: ConnectError) -> HTTPException:
    return HTTPException(status_code=409, detail={"message": str(exc), "reason": exc.reason})


def _safe_return(path: str) -> str:
    return path if path.startswith("/") and not path.startswith("//") else "/"


@router.get("/connection")
def connection(language: str | None = None, db: Session = Depends(get_db)):
    """The Instagram account's connection state (the files and the registry: no call to Meta). The registry's account row is brought in line with it."""
    conn = _connection(language)
    conn.sync(db)
    return conn.status()


@router.post("/connection/connect")
def connect(body: ConnectBody, language: str | None = None):
    """The address to send the owner to for Facebook's consent screen."""
    try:
        return {"authorization_url": _connection(language).authorization_url(_safe_return(body.return_to))}
    except ConnectError as exc:
        raise _problem(exc) from exc


@router.get("/oauth/callback")
async def callback(code: str | None = None, state: str | None = None, error: str | None = None, error_description: str | None = None):
    """Facebook's redirect. Every outcome, an error too, returns the owner to the page the attempt started on, with a message."""
    base = meta_config().instagram.connection.ui_base_url.rstrip("/")
    where = _safe_return(pending_return(state) or "/") if state else "/"

    def back(**query: str) -> RedirectResponse:
        present = parse_qs(urlparse(where).query)
        extra = {key: value for key, value in query.items() if key not in present}
        return RedirectResponse(f"{base}{where}{'&' if '?' in where else '?'}{urlencode(extra)}")

    if error or not code or not state:
        if state:
            forget_attempt(state)
        log.warning("instagram oauth was not completed: %s", error or "no code")
        return back(instagram="error", reason=error or "denied", detail=(error_description or f"Meta did not authorize the connection ({error or 'no code returned'}).")[:300])
    conn = _connection()
    try:
        await conn.complete(code, state)
    except ConnectError as exc:
        log.warning("instagram oauth failed: %s: %s", exc.reason, exc)
        return back(instagram="error", reason=exc.reason, detail=str(exc)[:300])
    finally:
        with SessionLocal() as db:
            conn.sync(db)
    log.info("instagram oauth connected")
    return back(instagram="connected")


@router.post("/connection/test")
async def test_connection(language: str | None = None, db: Session = Depends(get_db)):
    conn = _connection(language)
    try:
        return {"ok": True, **await conn.test()}
    except ConnectError as exc:
        raise _problem(exc) from exc
    finally:
        conn.sync(db)


@router.post("/connection/disconnect")
def disconnect(language: str | None = None, db: Session = Depends(get_db)):
    conn = _connection(language)
    result = conn.disconnect()
    conn.sync(db)
    return result
