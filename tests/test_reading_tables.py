"""The reading review's storage and settings: a reading kept across films is unique for its word, kind and sense; a doubt is unique for its word
in a film's block and is answered once. Real PostgreSQL (the language code `zz` is the test's own)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.ai_config import ai_config
from app.db.models import Case, ReadingApproval, ReadingDoubt, StoryVersion

LANG = "zz"


@pytest.fixture(autouse=True)
def clean(db_session):
    db_session.query(ReadingApproval).filter_by(language=LANG).delete()
    db_session.commit()
    yield
    db_session.rollback()
    db_session.query(ReadingApproval).filter_by(language=LANG).delete()
    db_session.commit()


def test_the_reading_loop_is_set_for_persian_only():
    cfg = ai_config.reading
    assert cfg.languages == ["fa"] and cfg.max_rounds == 3 and cfg.pause_between_names and cfg.chunk_blocks >= 1


def test_one_word_has_one_reading_per_kind_and_sense(db_session):
    db_session.add_all([ReadingApproval(language=LANG, surface="رد", kind="homograph", sense="trace", latin="rad-e"),
                        ReadingApproval(language=LANG, surface="رد", kind="homograph", sense="reject", latin="rad")])
    db_session.commit()
    db_session.add(ReadingApproval(language=LANG, surface="رد", kind="homograph", sense="trace", latin="rade"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_a_doubt_is_unique_for_its_word_and_starts_open(db_session):
    case = Case(canonical_title="Reading", slug=f"rd-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.flush()
    version = StoryVersion(case_id=case.id, version=1, kind="spoken", language="fa", story_text="t", narrative_angle="a", status="ready")
    db_session.add(version)
    db_session.flush()
    try:
        doubt = ReadingDoubt(case_id=case.id, story_version_id=version.id, block_id="FA_B01_g01_01", word_n=3, surface="ملک", question="estate or king?")
        db_session.add(doubt)
        db_session.commit()
        assert doubt.status == "open" and doubt.answer_latin is None and doubt.answered_at is None
        db_session.add(ReadingDoubt(case_id=case.id, story_version_id=version.id, block_id="FA_B01_g01_01", word_n=3, surface="ملک"))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
    finally:
        db_session.rollback()
        db_session.query(ReadingDoubt).filter_by(case_id=case.id).delete()
        db_session.query(StoryVersion).filter_by(case_id=case.id).delete()
        db_session.query(Case).filter_by(id=case.id).delete()
        db_session.commit()
