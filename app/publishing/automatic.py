"""The automatic publisher: a publication whose planned time has come is published by the application, when the owner has switched it on (config/publishing.json:
automatic_publishing.enabled; off until the owner says go). It is the same publisher as the owner's click, with the same checks (the Emergency Stop, the approval given by default, the
media rules, the file hashed afresh, one upload per file) and nothing more: it never retries a failed publication (`automatic_retries` is 0), never publishes a draft, never touches a
publication outside its window, and only publishes where a publisher is built (Facebook so far). Everything else is left for the owner, whom Monitoring tells."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.core.env import env_value
from app.db.base import SessionLocal
from app.db.models import Publication
from app.publishing.config import publishing_config
from app.publishing.registry import PublicationRegistry
from app.utils import utc_now

log = logging.getLogger(__name__)
WAITING = ("draft", "content_approved", "publication_approved", "scheduled")
_task: asyncio.Task | None = None
_reported: set[tuple[int, str]] = set()          # (publication, reason) already recorded: a refusal is written once, not at every poll


def due(db: Session, registry: PublicationRegistry | None = None) -> list[Publication]:
    """The facebook publications whose time has come and whose window is still open, in the order of their times (a read: nothing is changed)."""
    now = utc_now()
    found = (db.query(Publication).filter(Publication.platform == "facebook", Publication.status.in_(WAITING), Publication.scheduled_at.isnot(None), Publication.scheduled_at <= now,
                                          Publication.window_ends_at.isnot(None), Publication.window_ends_at >= now).order_by(Publication.scheduled_at, Publication.id).all())
    registry = registry or PublicationRegistry(db)
    return [p for p in found if (d := registry.cfg.destination(p.platform, p.kind)) is not None and d.automation_allowed]


async def publish_due(db: Session, *, publisher_factory=None) -> list[int]:
    """Publish what is due, one publication after the other. Returns the ids that were started. Does nothing while the master switch is off or the Emergency Stop is active."""
    from app.meta_publishing.facebook_publisher import FacebookPublisher, Mode, Refused

    if not publishing_config().automatic_publishing.enabled:
        return []
    registry = PublicationRegistry(db)
    if registry._stopped():
        return []
    started = []
    for pub in due(db, registry):
        try:
            publisher = (publisher_factory or FacebookPublisher)(db, language=pub.language)
            await publisher.run(pub.id, Mode.PUBLISH, None)
        except ValueError as exc:                                                    # a language with no Page of its own: nothing is published for it
            if (pub.id, "no_page") not in _reported:
                _reported.add((pub.id, "no_page"))
                registry.event(pub, "automatic_blocked", f"no_page: {exc}", "automatic")
                db.commit()
            continue
        except Refused as exc:
            if (pub.id, exc.reason) not in _reported:
                _reported.add((pub.id, exc.reason))
                log.warning("automatic publishing of %s was refused: %s (%s)", pub.id, exc, exc.reason)
                registry.event(pub, "automatic_blocked", f"{exc.reason}: {exc}", "automatic")
                db.commit()
            continue
        started.append(pub.id)
    return started


async def _loop() -> None:
    while True:
        try:
            with SessionLocal() as db:
                done = await publish_due(db)
            if done:
                log.info("automatic publishing handled publications %s", done)
        except Exception as exc:  # noqa: BLE001 - the automatic publisher never dies
            log.warning("automatic publishing tick failed: %s", exc)
        await asyncio.sleep(publishing_config().automatic_publishing.poll_seconds)


def disabled() -> bool:
    return (env_value("TRUECRIME_DISABLE_SCHEDULER", "") or "").lower() in ("1", "true", "yes") or not publishing_config().automatic_publishing.enabled


def start() -> bool:
    global _task
    if disabled() or (_task is not None and not _task.done()):
        return False
    _task = asyncio.get_event_loop().create_task(_loop())
    return True


def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
