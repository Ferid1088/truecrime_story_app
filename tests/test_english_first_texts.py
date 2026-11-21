"""English first: the owner's review of the English texts gates the covers, and the other languages wait for his release."""

import json
import uuid
from pathlib import Path

import pytest

from app.core.ai_config import ai_config

from app.covers import compose as C
from app.covers import text_gate as TG
from app.db.models import Case
from app.shortform import pack as PK


@pytest.fixture
def case(db_session):
    row = Case(canonical_title="English first", slug=f"english-first-{uuid.uuid4().hex[:8]}", language="en")
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def reviewed(monkeypatch):
    monkeypatch.setattr(ai_config.covers_settings(), "text_review_languages", ["en", "fa"])           # the tests switch the owner's review off by default


def _find(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for child in node.values():
            if (found := _find(child, key)) is not None:
                return found
    return None


def test_the_configuration_makes_english_and_persian_the_languages_the_owner_reviews():
    config = json.loads((Path(__file__).resolve().parents[1] / "config/parameters/covers.json").read_text(encoding="utf-8"))
    assert _find(config, "text_review_languages") == ["en", "fa"]


def test_english_texts_that_are_not_released_are_not_handed_to_the_other_languages(db_session, case, reviewed):
    assert PK.released_english(db_session, case) is None


def test_no_cover_picture_preview_or_approval_is_made_before_the_english_release(db_session, case, reviewed):
    with pytest.raises(C.CoverError, match="wait for your review"):
        TG.guard_cover(db_session, case, "en")


def test_a_language_the_owner_does_not_review_is_not_held(db_session, case, reviewed):
    TG.guard_cover(db_session, case, "de")


def test_without_an_english_review_nothing_is_held_for_the_other_languages(db_session, case):
    assert PK.released_english(db_session, case) == {}
