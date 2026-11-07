"""Arabic gets the numbers layer Persian has: the voice is given the words of a number, never its digits (it read "911" as 9, 1, 1)."""

import pytest

from app.core.ai_config import ai_config
from app.documentary import arabic_numbers as AN
from app.documentary.reading.tashkeel import TashkeelResult, TashkeelVoicing, VocalWord
from app.documentary.reading.words import tokenize

BIG = ai_config.reading.tashkeel_number_max


@pytest.mark.parametrize("n, plain", [
    (0, "صفر"), (1, "واحد"), (2, "اثنان"), (10, "عشرة"), (11, "أحد عشر"), (12, "اثنا عشر"), (19, "تسعة عشر"), (20, "عشرون"),
    (21, "واحد وعشرون"), (99, "تسعة وتسعون"), (100, "مئة"), (101, "مئة وواحد"), (200, "مئتان"), (911, "تسعمئة وأحد عشر"),
    (1000, "ألف"), (2000, "ألفان"), (2026, "ألفان وستة وعشرون"), (3000, "ثلاثة آلاف"), (10000, "عشرة آلاف"),
    (11000, "أحد عشر ألف"), (200000, "مئتا ألف"), (1000000, "مليون"), (2500000, "مليونان وخمسمئة ألف"),
    (3000000000, "ثلاثة مليارات"), (10**12, "ترليون"),
])
def test_a_number_is_said_as_the_arabic_words_of_its_value(n, plain):
    assert AN.bare(AN.to_words(n)) == plain


def test_the_largest_number_has_every_scale_once():
    words = AN.bare(AN.to_words(10**15 - 1))
    assert all(scale in words for scale in ("ترليون", "مليار", "مليون", "ألف"))


def test_the_words_carry_the_vowel_marks_of_the_stem():
    assert AN.to_words(911) == "تِسْعُمِئَة وَأَحَدَ عَشَر"


@pytest.mark.parametrize("token, plain", [("٩١١", "تسعمئة وأحد عشر"), ("911", "تسعمئة وأحد عشر"), ("۹۱۱", "تسعمئة وأحد عشر")])
def test_the_three_digit_systems_read_alike(token, plain):
    assert AN.bare(AN.read(token, BIG)) == plain


def test_a_code_with_a_leading_zero_and_a_number_above_the_limit_are_said_digit_by_digit():
    assert AN.bare(AN.read("007", BIG)) == "صفر صفر سبعة"
    assert AN.bare(AN.read("1" + "0" * 16, BIG)).split() == ["واحد"] + ["صفر"] * 16
    assert AN.bare(AN.read("123", 99)) == "واحد اثنان ثلاثة"


def test_the_speech_check_expects_the_words_where_the_story_shows_digits():
    assert AN.spell_digits("في فبراير 2026، وصل اتصال إلى 911.", BIG) == "في فبراير ألفان وستة وعشرون، وصل اتصال إلى تسعمئة وأحد عشر."


def _voicing(text):
    words = tokenize(text)
    readings = [VocalWord(w.n, w.plain) for w in words]
    return TashkeelVoicing(TashkeelResult(readings={"B1": readings}))


def test_the_voice_is_given_the_words_of_a_number_and_the_rest_of_the_block_as_before():
    text = "اتصل برقم الطوارئ 911 في فبراير 2026."
    voiced = _voicing(text).text("B1", text)
    assert "911" not in voiced and "2026" not in voiced
    assert AN.bare(voiced) == "اتصل برقم الطوارئ تسعمئة وأحد عشر في فبراير ألفان وستة وعشرون."


def test_the_story_text_keeps_its_digits():
    text = "رقم 911"
    _voicing(text).text("B1", text)
    assert text == "رقم 911"
