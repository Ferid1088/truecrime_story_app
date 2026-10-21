"""The dry-run publish queue: the exact request each platform would get and when it is scheduled.
Nothing here talks to a platform; every request carries `sent: false`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import PublishRequest

FILM_PLATFORM = "youtube"


@dataclass(frozen=True)
class FilmInput:
    language: str
    video_id: int
    file_path: str
    title: str
    description: str
    tags: list[str]


@dataclass(frozen=True)
class ShortInput:
    short_id: int
    language: str
    packages: dict[str, dict]


@dataclass(frozen=True)
class PlannedRequest:
    kind: str
    language: str
    platform: str
    short_id: int | None
    scheduled_for: datetime
    payload: dict


def _first_slot(now: datetime) -> datetime:
    slot = now.replace(hour=ai_config.autopilot_settings().schedule.first_slot_hour_utc,
                       minute=0, second=0, microsecond=0)
    return slot if slot > now else slot + timedelta(days=1)


def plan_requests(films: list[FilmInput], shorts: list[ShortInput],
                  handles: dict[tuple[str, str], str], now: datetime,
                  ) -> tuple[list[PlannedRequest], list[dict]]:
    cfg = ai_config.autopilot_settings()
    base = _first_slot(now)
    requests: list[PlannedRequest] = []
    skipped: list[dict] = []
    for f in sorted(films, key=lambda x: cfg.languages.index(x.language)):
        requests.append(PlannedRequest("film", f.language, FILM_PLATFORM, None, base, {
            "video_id": f.video_id, "file": f.file_path, "title": f.title,
            "title_confirmed": False, "description": f.description, "tags": f.tags,
            "channel_handle": handles.get((f.language, "youtube_short"), ""),
            "dry_run": True, "sent": False}))
    queue_position: dict[tuple[str, str], int] = {}
    ordered = sorted(shorts, key=lambda s: (cfg.languages.index(s.language), s.short_id))
    for s in ordered:
        for platform in cfg.platforms:
            package = s.packages.get(platform)
            if package is None:
                skipped.append({"short_id": s.short_id, "language": s.language,
                                "platform": platform, "reason": "no export package for this platform"})
                continue
            if not package.get("length_ok"):
                skipped.append({"short_id": s.short_id, "language": s.language,
                                "platform": platform,
                                "reason": f"length {package.get('duration_seconds')} s is outside "
                                          "the platform's limits"})
                continue
            slot = queue_position.get((s.language, platform), 0)
            queue_position[(s.language, platform)] = slot + 1
            when = base + timedelta(hours=cfg.schedule.film_before_shorts_hours
                                    + slot * cfg.schedule.slot_spacing_hours)
            requests.append(PlannedRequest("short", s.language, platform, s.short_id, when, {
                **package, "channel_handle": handles.get((s.language, platform), ""),
                "dry_run": True, "sent": False}))
    return requests, skipped


def store_requests(db: Session, run_id: int, case_id: int,
                   planned: list[PlannedRequest]) -> list[PublishRequest]:
    """Replace the run's queue with the planned requests (a re-plan never leaves old rows)."""
    db.query(PublishRequest).filter(PublishRequest.run_id == run_id).delete()
    rows = [PublishRequest(run_id=run_id, case_id=case_id, language=p.language,
                           platform=p.platform, kind=p.kind, short_id=p.short_id,
                           request_json=json.dumps(p.payload, ensure_ascii=False, sort_keys=True),
                           scheduled_for=p.scheduled_for, status="dry_run") for p in planned]
    db.add_all(rows)
    db.commit()
    return rows
