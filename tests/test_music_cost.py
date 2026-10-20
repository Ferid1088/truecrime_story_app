import uuid

import pytest

from app.db.models import Case, MusicTrack
from app.documentary.music_cost import MusicPriceUnverified, book_track


def _case(db_session) -> Case:
    slug = f"music-cost-test-case-{uuid.uuid4().hex[:8]}"
    case = Case(canonical_title="music cost test case", slug=slug)
    db_session.add(case)
    db_session.commit()
    return case


def test_unpaid_track_books_nothing(db_session):
    case = _case(db_session)
    track = MusicTrack(track_code="bridge-tense-v1", kind="bridge", mood="tense", variant=1,
                       seconds=20.0, situation="x", tags_json="[\"x\"]")
    db_session.add(track)
    db_session.commit()
    assert book_track(db_session, track, case.id) is None


def test_paid_track_without_verified_price_raises(db_session):
    case = _case(db_session)
    track = MusicTrack(track_code="bridge-tense-v2", kind="bridge", mood="tense", variant=2,
                       seconds=20.0, situation="x", tags_json="[\"x\"]", characters_paid=500)
    db_session.add(track)
    db_session.commit()
    with pytest.raises(MusicPriceUnverified):
        book_track(db_session, track, case.id)
