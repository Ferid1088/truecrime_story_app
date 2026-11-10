"""Arabic numbers: the voice is given the words of a number, never its digits (it read "911" as 9, 1, 1)."""

import pytest

from app.core.ai_config import ai_config
from app.documentary import arabic_numbers as AN
from app.documentary.spoken_numbers.ar import Numbers

BIG = ai_config.voice.number_max


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


def test_the_speech_check_expects_the_plain_words_where_the_story_shows_digits():
    check = Numbers({}, BIG, vowelled=False)
    assert check.text("في فبراير 2026، وصل اتصال إلى 911.") == "في فبراير ألفين وستة وعشرين، وصل اتصال إلى تسعمئة وأحد عشر."


def test_the_speech_check_does_not_strip_the_vowel_marks_of_the_story_words():
    assert Numbers({}, BIG, vowelled=False).text("كَتَبَ 911") == "كَتَبَ تسعمئة وأحد عشر"


@pytest.mark.parametrize("n, plain", [(102000, "مئة ألف وألفان"), (101000, "مئة ألف وألف"), (103000, "مئة وثلاثة آلاف"), (234000, "مئتان وأربعة وثلاثون ألف"),
                                       (1234567, "مليون ومئتان وأربعة وثلاثون ألف وخمسمئة وسبعة وستون")])
def test_a_count_of_a_hundred_or_more_before_a_scale_follows_its_last_two_digits(n, plain):
    assert AN.bare(AN.to_words(n)) == plain


@pytest.mark.parametrize("n, feminine, oblique, plain", [(15, True, False, "خمس عشرة"), (21, True, False, "إحدى وعشرون"), (2007, False, True, "ألفين وسبعة"),
                                                           (12, False, True, "اثني عشر"), (12, True, False, "اثنتا عشرة"), (200, False, True, "مئتين")])
def test_a_cardinal_takes_the_gender_and_the_case_it_is_given(n, feminine, oblique, plain):
    assert AN.bare(AN.to_words(n, AN.Form(feminine=feminine, oblique=oblique))) == plain
