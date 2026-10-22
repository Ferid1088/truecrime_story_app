import uuid

import pytest

from app.db.models import Case, HostLedMusicTrack, MusicTrack, ProviderCall
from app.documentary.music_cost import (
    STAGE,
    STAGE_HOST_LED,
    MusicPriceUnverified,
    book_host_led,
    book_track,
)


def _case(db_session) -> Case:
    slug = f"music-cost-test-case-{uuid.uuid4().hex[:8]}"
    case = Case(canonical_title="music cost test case", slug=slug)
    db_session.add(case)
    db_session.commit()
    return case


def _track(db_session, **kw) -> MusicTrack:
    track = MusicTrack(track_code=kw.pop("track_code", f"bridge-tense-{uuid.uuid4().hex[:6]}"),
                       kind="bridge", mood="tense", variant=1, seconds=20.0,
                       situation="x", tags_json="[\"x\"]", **kw)
    db_session.add(track)
    db_session.commit()
    return track


def test_unpaid_track_books_nothing(db_session):
    case = _case(db_session)
    track = _track(db_session)
    assert book_track(db_session, track, case.id) is None


def test_paid_track_books_assumed_estimate(db_session):
    """Unverified price still books the paid call — flagged estimated with the assumed rate."""
    case = _case(db_session)
    track = _track(db_session, characters_paid=500)
    row = book_track(db_session, track, case.id)
    assert row is not None
    assert row.stage == STAGE
    assert row.case_id == case.id
    assert row.estimated is True
    assert row.cost_source == "assumed-unverified"
    assert row.request_id == f"music:{track.track_code}"
    # 20 s at the assumed 0.27 EUR/min
    assert row.cost_eur == pytest.approx(20.0 / 60 * 0.27, abs=1e-6)


def test_booking_dedupes_by_track_key(db_session):
    case = _case(db_session)
    track = _track(db_session, characters_paid=500)
    first = book_track(db_session, track, case.id)
    assert book_track(db_session, track, case.id) is first
    rows = (db_session.query(ProviderCall)
            .filter_by(stage=STAGE, request_id=f"music:{track.track_code}").all())
    assert len(rows) == 1


def test_no_price_at_all_raises(db_session, monkeypatch):
    case = _case(db_session)
    track = _track(db_session, characters_paid=500)
    import app.documentary.music_cost as mc
    monkeypatch.setattr(mc, "load_pricing", lambda: {"elevenlabs_sound": {}})
    with pytest.raises(MusicPriceUnverified):
        book_track(db_session, track, case.id)


def _host_led_track(db_session, **kw) -> HostLedMusicTrack:
    track = HostLedMusicTrack(
        channel="en", mood="waiting", prompt="x", provider="elevenlabs_music",
        model_id="music_v1", duration_seconds=30.0, file_path="music/x.mp3",
        file_sha256=uuid.uuid4().hex, licence_note="x", terms_url="x",
        terms_recorded_date="2026-10-22", **kw)
    db_session.add(track)
    db_session.commit()
    return track


def test_host_led_track_books_once_per_generation(db_session):
    case = _case(db_session)
    track = _host_led_track(db_session,
                            provider_generation_id=f"gen-{uuid.uuid4().hex[:8]}")
    row = book_host_led(db_session, track, case.id)
    assert row is not None
    assert row.stage == STAGE_HOST_LED
    assert row.estimated is True
    assert row.cost_source == "assumed-unverified"
    assert row.units == pytest.approx(30.0)
    assert book_host_led(db_session, track, case.id) is row


def test_host_led_track_without_generation_books_nothing(db_session):
    case = _case(db_session)
    track = _host_led_track(db_session)
    assert book_host_led(db_session, track, case.id) is None
