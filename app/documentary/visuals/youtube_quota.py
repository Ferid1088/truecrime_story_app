"""The YouTube Data API quota ledger (P15d): every call counted (time, endpoint, units, stage,
case); the day is the PACIFIC calendar day (api.youtube quota resets at midnight Pacific Time,
09:00 in Berlin during European winter, 08:00 during European summer — the two DST calendars
do not move together, so this uses the real IANA zone, never a fixed UTC offset); the discovery
stage sees only its reserved share of the daily pool and never the share reserved for uploads.

A ledger row can also serve as a cache entry (query_key/result_json/expires_at): the same
query is never run twice within its expiry, independent of its role in the quota count."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import AppSetting, YoutubeQuotaLedger
from app.utils import utc_now

SETTINGS_KEY = "youtube_quota_settings"
OVERRIDABLE = ("daily_quota_units", "discovery_share_percent", "uploads_reserved_percent")


def _zone() -> ZoneInfo:
    return ZoneInfo(ai_config.youtube_data_api.reset_timezone)


def settings_overrides(db: Session) -> dict:
    """The owner's own Settings-page values (daily quota total, discovery/uploads shares), on
    top of the config defaults; empty until the owner changes one."""
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None:
        return {}
    try:
        return {k: v for k, v in json.loads(row.value_json).items() if k in OVERRIDABLE}
    except (ValueError, AttributeError):
        return {}


def set_settings(db: Session, **values) -> dict:
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None:
        row = AppSetting(key=SETTINGS_KEY)
        db.add(row)
    current = settings_overrides(db)
    current.update({k: v for k, v in values.items() if v is not None and k in OVERRIDABLE})
    row.value_json = json.dumps(current)
    row.updated_at = utc_now()
    db.commit()
    return current


def _value(db: Session, key: str):
    return settings_overrides(db).get(key, getattr(ai_config.youtube_data_api, key))


def pacific_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """(start, end) of the current Pacific calendar day, as timezone-aware instants (directly
    comparable with any other aware datetime, including a plain UTC one)."""
    now = now or utc_now()
    local = now.astimezone(_zone())
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local, start_local + timedelta(days=1)


def next_reset_at(now: datetime | None = None) -> datetime:
    """The next midnight Pacific Time, as a UTC-aware instant."""
    return pacific_day_bounds(now)[1]


def _cost(endpoint: str, units: int | None) -> int:
    if units is not None:
        return units
    return ai_config.youtube_data_api.cost_units.get(endpoint, 1)


def record_call(db: Session, stage: str, endpoint: str, case_id: int | None = None,
               units: int | None = None) -> YoutubeQuotaLedger:
    row = YoutubeQuotaLedger(case_id=case_id, endpoint=endpoint, units=_cost(endpoint, units),
                            stage=stage)
    db.add(row)
    db.commit()
    return row


def _today_rows(db: Session):
    start, end = pacific_day_bounds()
    return db.query(YoutubeQuotaLedger).filter(
        YoutubeQuotaLedger.created_at >= start, YoutubeQuotaLedger.created_at < end)


def units_used_today(db: Session, stage: str | None = None) -> int:
    q = _today_rows(db)
    if stage is not None:
        q = q.filter(YoutubeQuotaLedger.stage == stage)
    return sum(r.units for r in q.all())


def search_calls_used_today(db: Session, stage: str | None = None) -> int:
    q = _today_rows(db).filter(YoutubeQuotaLedger.endpoint == "search.list")
    if stage is not None:
        q = q.filter(YoutubeQuotaLedger.stage == stage)
    return q.count()


def discovery_pool_units(db: Session | None = None) -> int:
    if db is None:
        cfg = ai_config.youtube_data_api
        return int(cfg.daily_quota_units * cfg.discovery_share_percent / 100)
    return int(_value(db, "daily_quota_units") * _value(db, "discovery_share_percent") / 100)


def discovery_status(db: Session) -> dict:
    """P15d: what the DISCOVERY stage alone may still spend today — never the share reserved
    for uploads, never the rest of the pool. Visible status for "continues after the reset"."""
    daily_quota_units = _value(db, "daily_quota_units")
    discovery_share_percent = _value(db, "discovery_share_percent")
    uploads_reserved_percent = _value(db, "uploads_reserved_percent")
    pool = discovery_pool_units(db)
    used = units_used_today(db, stage="discovery")
    remaining = max(0, pool - used)
    reset = next_reset_at()
    available = remaining > 0
    return {
        "pool_units": pool, "used_units": used, "remaining_units": remaining,
        "daily_quota_units": daily_quota_units,
        "discovery_share_percent": discovery_share_percent,
        "uploads_reserved_percent": uploads_reserved_percent,
        "reset_at": reset.isoformat(),
        "available": available,
        "status_message": ("discovery share available" if available else
                          f"continues after the reset at {reset.isoformat()}"),
    }


def store_cache(db: Session, stage: str, endpoint: str, query_key: str, result: dict,
               case_id: int | None = None) -> YoutubeQuotaLedger:
    row = YoutubeQuotaLedger(
        case_id=case_id, endpoint=endpoint, units=0, stage=stage, query_key=query_key,
        result_json=json.dumps(result, ensure_ascii=False),
        expires_at=utc_now() + timedelta(hours=ai_config.youtube_data_api.cache_expiry_hours))
    db.add(row)
    db.commit()
    return row


def cached_result(db: Session, query_key: str) -> dict | None:
    row = (db.query(YoutubeQuotaLedger)
          .filter(YoutubeQuotaLedger.query_key == query_key,
                  YoutubeQuotaLedger.result_json.isnot(None))
          .order_by(YoutubeQuotaLedger.created_at.desc()).first())
    if row is None or row.expires_at is None or row.expires_at <= utc_now():
        return None
    return json.loads(row.result_json or "{}")
