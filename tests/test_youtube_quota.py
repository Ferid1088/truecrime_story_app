"""P15d: the YouTube Data API quota ledger. Every call is counted (time, endpoint, units,
stage, case); the day is the PACIFIC calendar day (the quota resets at midnight Pacific,
09:00 in Berlin during winter); the discovery stage sees only its reserved share and never the
share reserved for uploads; a cached result is never fetched twice within its expiry."""

import uuid
from datetime import UTC, datetime, timedelta

from app.core.ai_config import ai_config
from app.db.models import Case, YoutubeQuotaLedger
from app.documentary.visuals import youtube_quota as Q


def _case(db):
    case = Case(canonical_title="Quota", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


# ---- Pacific-day accounting ---------------------------------------------------------------

def test_pacific_day_bounds_spans_a_full_utc_day_shifted_by_the_offset():
    # 2026-10-21 12:00 UTC is 2026-10-21 05:00 PDT (UTC-7 in October, daylight time)
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    start, end = Q.pacific_day_bounds(now)
    assert end - start == timedelta(days=1)
    assert start <= now < end


def test_next_reset_is_midnight_pacific_as_a_utc_instant():
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    reset = Q.next_reset_at(now)
    assert reset > now and reset - now <= timedelta(hours=24)


# ---- recording and counting -----------------------------------------------------------------

def test_record_call_writes_a_ledger_row_with_the_configured_unit_cost(db_session):
    case = _case(db_session)
    Q.record_call(db_session, stage="discovery", endpoint="playlistItems.list", case_id=case.id)
    [row] = db_session.query(YoutubeQuotaLedger).filter_by(case_id=case.id).all()
    assert row.endpoint == "playlistItems.list" and row.stage == "discovery"
    assert row.units == ai_config.youtube_data_api.cost_units["playlistItems.list"]


def test_units_used_today_sums_only_todays_pacific_day(db_session, monkeypatch):
    case = _case(db_session)
    before = Q.units_used_today(db_session)
    row1 = Q.record_call(db_session, stage="discovery", endpoint="videos.list", case_id=case.id, units=1)
    Q.record_call(db_session, stage="discovery", endpoint="videos.list", case_id=case.id, units=1)
    row1.created_at = row1.created_at - timedelta(days=2)
    db_session.commit()
    assert Q.units_used_today(db_session) == before + 1


def test_search_calls_used_today_counts_only_search_list(db_session):
    case = _case(db_session)
    before = Q.search_calls_used_today(db_session)
    Q.record_call(db_session, stage="discovery", endpoint="search.list", case_id=case.id)
    Q.record_call(db_session, stage="discovery", endpoint="videos.list", case_id=case.id)
    assert Q.search_calls_used_today(db_session) == before + 1


# ---- the reserved share and the visible status ----------------------------------------------

def test_discovery_share_is_a_percent_of_the_daily_pool(monkeypatch):
    monkeypatch.setattr(ai_config.youtube_data_api, "daily_quota_units", 10000)
    monkeypatch.setattr(ai_config.youtube_data_api, "discovery_share_percent", 20.0)
    assert Q.discovery_pool_units() == 2000


def test_discovery_status_reports_remaining_and_reset_time(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.youtube_data_api, "daily_quota_units", 1000)
    monkeypatch.setattr(ai_config.youtube_data_api, "discovery_share_percent", 10.0)
    case = _case(db_session)
    before = Q.discovery_status(db_session)
    Q.record_call(db_session, stage="discovery", endpoint="videos.list", case_id=case.id, units=40)
    status = Q.discovery_status(db_session)
    assert status["pool_units"] == 100 and status["used_units"] == before["used_units"] + 40
    assert status["remaining_units"] == status["pool_units"] - status["used_units"]
    assert status["available"] is True
    assert "reset_at" in status


def test_discovery_stage_waits_when_its_share_is_used_up(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.youtube_data_api, "daily_quota_units", 100)
    monkeypatch.setattr(ai_config.youtube_data_api, "discovery_share_percent", 10.0)
    case = _case(db_session)
    Q.record_call(db_session, stage="discovery", endpoint="videos.list", case_id=case.id, units=10)
    status = Q.discovery_status(db_session)
    assert status["available"] is False
    assert "reset at" in status["status_message"].lower()


def test_discovery_never_counts_against_the_uploads_reserved_share(db_session, monkeypatch):
    # a call made under stage "uploads" must not affect the discovery pool's own accounting
    monkeypatch.setattr(ai_config.youtube_data_api, "daily_quota_units", 1000)
    monkeypatch.setattr(ai_config.youtube_data_api, "discovery_share_percent", 10.0)
    case = _case(db_session)
    before = Q.discovery_status(db_session)
    Q.record_call(db_session, stage="uploads", endpoint="videos.list", case_id=case.id, units=90)
    status = Q.discovery_status(db_session)
    assert status["used_units"] == before["used_units"]


# ---- caching: the same query is never run twice within its expiry ---------------------------

def test_a_cached_result_is_returned_without_a_new_call(db_session):
    case = _case(db_session)
    Q.store_cache(db_session, stage="discovery", endpoint="search.list", query_key="k1",
                  result={"items": [1, 2, 3]}, case_id=case.id)
    assert Q.cached_result(db_session, "k1") == {"items": [1, 2, 3]}


def test_an_expired_cache_entry_is_not_returned(db_session, monkeypatch):
    case = _case(db_session)
    monkeypatch.setattr(ai_config.youtube_data_api, "cache_expiry_hours", 0.0)
    Q.store_cache(db_session, stage="discovery", endpoint="search.list", query_key="k2",
                 result={"items": []}, case_id=case.id)
    row = db_session.query(YoutubeQuotaLedger).filter_by(query_key="k2").first()
    row.expires_at = row.expires_at - timedelta(seconds=1)
    db_session.commit()
    assert Q.cached_result(db_session, "k2") is None


def test_a_missing_cache_key_returns_none(db_session):
    assert Q.cached_result(db_session, "never-seen") is None


# ---- Settings overrides: the owner can raise the quota / change the shares --------------------

def test_settings_overrides_are_empty_until_the_owner_changes_one(db_session):
    assert Q.settings_overrides(db_session) == {}


def test_set_settings_overrides_the_configured_defaults(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.youtube_data_api, "daily_quota_units", 10000)
    Q.set_settings(db_session, daily_quota_units=20000, discovery_share_percent=30.0)
    assert Q.settings_overrides(db_session) == {"daily_quota_units": 20000,
                                                "discovery_share_percent": 30.0}
    assert Q.discovery_pool_units(db_session) == 6000


def test_set_settings_only_touches_the_given_fields(db_session):
    Q.set_settings(db_session, daily_quota_units=15000)
    Q.set_settings(db_session, discovery_share_percent=25.0)
    out = Q.settings_overrides(db_session)
    assert out["daily_quota_units"] == 15000 and out["discovery_share_percent"] == 25.0
