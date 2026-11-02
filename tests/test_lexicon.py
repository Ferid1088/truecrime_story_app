"""The pronunciation lexicon: a respelling the listening review proved is kept for every film, an ezafe never is, an owner's reading stays, and
the film's dictionary starts from it. Real PostgreSQL (the language code `zz` is the test's own)."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, PronunciationLexicon, StoryVersion
from app.documentary import lexicon as LEX
from app.documentary import semantic_review as SR
from app.documentary import voice_prepare as VP

LANG = "zz"


@pytest.fixture(autouse=True)
def clean(db_session):
    db_session.query(PronunciationLexicon).filter_by(language=LANG).delete()
    db_session.commit()
    yield
    db_session.query(PronunciationLexicon).filter_by(language=LANG).delete()
    db_session.commit()


def rows(db):
    return {r.surface: (r.kind, r.reading, r.source, r.hits) for r in db.query(PronunciationLexicon).filter_by(language=LANG)}


def test_an_ezafe_a_single_mark_free_change_or_several_words_are_not_kept():
    assert not LEX.learnable("شانزدهم", "شانزدهمِ"), "an ezafe belongs to one sentence"
    assert not LEX.learnable("کالب", "کالب") and not LEX.learnable("تیپ سیتی", "تیپ‌سیتی"), "no change, or more than one word"
    assert not LEX.learnable("درِ", "دَرِ") and not LEX.learnable("فوریهٔ", "فِوْریه"), "a word that carries the ezafe already"
    assert LEX.learnable("اشلی", "اَشْلی") and LEX.learnable("کالب", "کِیلِب")


def test_a_proven_respelling_is_kept_counted_again_and_never_replaces_an_owners_reading(db_session):
    kept = LEX.learn(db_session, LANG, [{"surface": "اشلی", "replacement": "اَشْلی"}, {"surface": "شانزدهم", "replacement": "شانزدهمِ"}], None)
    assert kept == [{"surface": "اشلی", "replacement": "اَشْلی"}] and rows(db_session) == {"اشلی": ("alias", "اَشْلی", "reviewer", 1)}
    LEX.learn(db_session, LANG, [{"surface": "اشلی", "replacement": "اَشْلی"}], None)
    assert rows(db_session)["اشلی"][3] == 2
    row = db_session.query(PronunciationLexicon).filter_by(language=LANG, surface="اشلی").one()
    row.source = "owner"
    db_session.commit()
    LEX.learn(db_session, LANG, [{"surface": "اشلی", "replacement": "اِشْلی"}], None)
    assert rows(db_session)["اشلی"][1] == "اَشْلی", "the owner's reading stays"


def test_the_dictionary_rules_of_a_text_are_those_of_the_lexicon_words_in_it(db_session):
    LEX.learn(db_session, LANG, [{"surface": "اشلی", "replacement": "اَشْلی"}, {"surface": "کالب", "replacement": "کِیلِب"}], None)
    assert LEX.rules_for(db_session, LANG, "اشلی آمد.") == [{"string_to_replace": "اشلی", "type": "alias", "alias": "اَشْلی"}]
    assert LEX.rules_for(db_session, LANG, "اشلیها آمدند.") == [], "a whole word only"


def test_a_rule_that_was_in_an_approved_block_is_kept_and_numbers_never(db_session):
    rules = [{"string_to_replace": "کالب", "type": "phoneme", "alphabet": "ipa", "phoneme": "kɑːleb"},
             {"string_to_replace": "۹۱۱", "type": "alias", "alias": "نهصد و یازده"},
             {"string_to_replace": "گاراژ", "type": "alias", "alias": "گاراج"}]
    rules.append({"string_to_replace": "درِ", "type": "phoneme", "alphabet": "ipa", "phoneme": "dær-e"})
    kept = LEX.confirm(db_session, LANG, rules, "کالب با ۹۱۱ تماس گرفت. درِ گاراژ.", None)
    assert [k["string_to_replace"] for k in kept] == ["کالب", "گاراژ"] and rows(db_session)["کالب"] == ("phoneme", "kɑːleb", "reviewer", 1)
    LEX.learn(db_session, LANG, [{"surface": "کالب", "replacement": "کِیلِب"}], None)
    assert rows(db_session)["کالب"][:2] == ("alias", "کِیلِب"), "a respelling the review proved replaces an editor's phoneme and the kind follows"


def test_the_films_dictionary_starts_from_the_lexicon_and_the_lexicon_beats_the_editors_reading(db_session):
    LEX.learn(db_session, LANG, [{"surface": "کالب", "replacement": "کِیلِب"}], None)
    case = Case(canonical_title="Lexicon", slug=f"lx-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.flush()
    prepared = {"rules": [{"string_to_replace": "کالب", "type": "alias", "alias": "کَلِب"}, {"string_to_replace": "گاراژ", "type": "alias", "alias": "گاراج"}]}
    version = StoryVersion(case_id=case.id, version=1, kind="spoken", language=LANG, story_text="کالب در گاراژ بود.", narrative_angle="a", status="ready",
                           narrative_structure=json.dumps({"prepared": prepared}))
    db_session.add(version)
    db_session.commit()
    made = []

    class Provider:
        async def create_pronunciation_dictionary(self, name, rules):
            made.append(rules)
            return {"id": "D", "version_id": "V"}

    try:
        asyncio.run(VP.ensure_dictionary(db_session, version, LANG, Provider()))
        assert {r["string_to_replace"]: r["alias"] for r in made[0]} == {"گاراژ": "گاراج", "کالب": "کِیلِب"}
    finally:
        db_session.delete(version)
        db_session.delete(case)
        db_session.commit()


def test_a_respelling_that_makes_a_block_pass_is_learned_and_an_ezafe_fix_is_not(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.semantic_review, "learn_types", ["name", "pronunciation"])
    monkeypatch.setattr(SR, "track_run", lambda *a, **k: __import__("contextlib").nullcontext(object()))
    monkeypatch.setattr(SR, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(SR, "get_generation_provider", lambda: object())
    from test_semantic_review import Scenario, block, error, raw

    fail = raw("fail", [error()], corrected_pronunciation_rules=[{"surface": "اشلی", "replacement": "اَشْلی"}, {"surface": "رفت", "replacement": "رفتِ"}])
    s = Scenario(tmp_path, [fail, raw()])
    s.reviewer = SR.Reviewer(db_session, None, {"B01": "x"}, {}, runner=s.runner)
    first = asyncio.run(s.speak(block(), 1))
    s.spoken.clear()
    result = asyncio.run(s.reviewer.run(first, s.speak, 1, LANG))
    assert [x["surface"] for x in result["semantic_review"]["learned"]] == ["اشلی"], "the ezafe on رفت is not kept"
    assert rows(db_session) == {"اشلی": ("alias", "اَشْلی", "reviewer", 1)}


def test_a_reading_never_carries_a_hyphen_in_the_dictionary_or_the_lexicon(db_session):
    assert LEX.plain_reading("dær-e") == "dære" and LEX.plain_reading("zæbɑːne‑ye") == "zæbɑːneye" and LEX.plain_reading("tʃæt dʒi pi ti") == "tʃæt dʒi pi ti"
    rules = [{"string_to_replace": "درِ", "type": "phoneme", "alphabet": "ipa", "phoneme": "dær-e"}, {"string_to_replace": "ب", "type": "alias", "alias": "x-y"}]
    assert [r.get("phoneme") or r.get("alias") for r in VP.plain_rules(rules)] == ["dære", "xy"]
    kept = LEX.confirm(db_session, LANG, [{"string_to_replace": "حالت", "type": "phoneme", "alphabet": "ipa", "phoneme": "hɑːlæt-e"}], "حالت", None)
    assert kept and rows(db_session)["حالت"][1] == "hɑːlæte"


def test_the_reading_of_a_name_of_several_words_has_no_space_because_a_space_is_a_pause():
    rules = [{"string_to_replace": "کانینگهام کورت", "type": "phoneme", "alphabet": "ipa", "phoneme": "kɑːniŋhɑːm koːrt"},
             {"string_to_replace": "کالب", "type": "phoneme", "alphabet": "ipa", "phoneme": "kɑːleb"}]
    assert [r["phoneme"] for r in VP.plain_rules(rules)] == ["kɑːniŋhɑːmkoːrt", "kɑːleb"]
