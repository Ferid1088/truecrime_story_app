"""A number in the film's text is read as a number, not digit by digit: whole numbers in Persian words and the dictionary's rules for them."""

import pytest

from app.documentary import voice_prepare as VP
from app.documentary.persian_numbers import to_words, value_of


@pytest.mark.parametrize("n, words", [(0, "صفر"), (11, "یازده"), (31, "سی و یک"), (100, "صد"), (911, "نهصد و یازده"), (1000, "هزار"),
                                      (2026, "دو هزار و بیست و شش"), (21000, "بیست و یک هزار"), (1500000, "یک میلیون و پانصد هزار")])
def test_whole_numbers_in_persian_words(n, words):
    assert to_words(n) == words


def test_persian_arabic_and_ascii_digits_have_the_same_value():
    assert value_of("۹۱۱") == value_of("٩١١") == value_of("911") == 911


def test_every_whole_number_gets_an_alias_rule_unless_it_is_part_of_a_longer_number_or_already_has_a_rule():
    text = "شماره ۹۱۱ بود. در ۲۰۱۱ و ۱۱ نفر. سال ۲۰۲۶. ۴۲"
    rules = VP.number_rules(text, {"۴۲"})
    assert {r["string_to_replace"] for r in rules} == {"۹۱۱", "۲۰۱۱", "۲۰۲۶"}, "۱۱ is inside ۲۰۱۱, ۴۲ has its own rule"
    assert {r["string_to_replace"]: r["alias"] for r in rules}["۹۱۱"] == "نهصد و یازده"
    assert all(r["type"] == "alias" for r in rules)


def test_the_editors_own_reading_of_a_number_is_replaced_by_the_number_in_words(db_session):
    """The editor read ۹۱۱ as the emergency number, digit by digit; the film's dictionary says it as a number."""
    import asyncio
    import json

    from app.db.models import Case, StoryVersion

    case = Case(canonical_title="Numbers", slug="num-test-" + "x" * 6, language="en")
    db_session.add(case)
    db_session.flush()
    rules = [{"string_to_replace": "۹۱۱", "type": "alias", "alias": "نُه یک یک"}, {"string_to_replace": "۹", "type": "alias", "alias": "نُه"},
             {"string_to_replace": "کالب", "type": "alias", "alias": "کِیلِب"}]
    version = StoryVersion(case_id=case.id, version=1, kind="spoken", language="fa", story_text="شماره ۹۱۱ بود.", narrative_angle="a", status="ready",
                           narrative_structure=json.dumps({"prepared": {"rules": rules}}))
    db_session.add(version)
    db_session.commit()
    made = []

    class Provider:
        async def create_pronunciation_dictionary(self, name, rules):
            made.append(rules)
            return {"id": "D", "version_id": "V"}

    try:
        asyncio.run(VP.ensure_dictionary(db_session, version, "fa", Provider()))
        assert {r["string_to_replace"]: r["alias"] for r in made[0]} == {"کالب": "کِیلِب", "۹۱۱": "نهصد و یازده"}
    finally:
        db_session.delete(version)
        db_session.delete(case)
        db_session.commit()
