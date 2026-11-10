"""The Arabic cardinal engine (arabic_numbers.py): the plain words of a whole number, as a news script writes them (no vowel marks, no case endings)."""

import pytest

from app.documentary import arabic_numbers as AN

BIG = 10**15 - 1
VOWEL_MARKS = "ًٌٍَُِّْٰ"


@pytest.mark.parametrize("n, plain", [
    (0, "صفر"), (1, "واحد"), (2, "اثنان"), (10, "عشرة"), (11, "أحد عشر"), (12, "اثنا عشر"), (19, "تسعة عشر"), (20, "عشرون"),
    (21, "واحد وعشرون"), (99, "تسعة وتسعون"), (100, "مئة"), (101, "مئة وواحد"), (200, "مئتان"), (800, "ثمانمئة"), (911, "تسعمئة وأحد عشر"),
    (1000, "ألف"), (2000, "ألفان"), (2026, "ألفان وستة وعشرون"), (3000, "ثلاثة آلاف"), (10000, "عشرة آلاف"),
    (11000, "أحد عشر ألف"), (200000, "مئتا ألف"), (1000000, "مليون"), (2500000, "مليونان وخمسمئة ألف"),
    (3000000000, "ثلاثة مليارات"), (10**12, "تريليون"), (2 * 10**12, "تريليونان"),
])
def test_a_number_is_said_as_the_arabic_words_of_its_value(n, plain):
    assert AN.to_words(n) == plain


def test_the_largest_number_has_every_scale_once():
    words = AN.to_words(BIG)
    assert all(scale in words for scale in ("تريليون", "مليار", "مليون", "ألف"))


@pytest.mark.parametrize("n", [0, 7, 12, 21, 99, 100, 911, 2026, 123456, 1234567, BIG])
def test_the_words_of_a_number_carry_no_vowel_marks(n):
    assert not any(c in VOWEL_MARKS for c in AN.to_words(n) + AN.to_words(n, AN.Form(feminine=True, oblique=True)))


@pytest.mark.parametrize("token", ["٩١١", "911", "۹۱۱"])
def test_the_three_digit_systems_read_alike(token):
    assert AN.read(token, BIG) == "تسعمئة وأحد عشر"


def test_a_code_with_a_leading_zero_and_a_number_above_the_limit_are_said_digit_by_digit():
    assert AN.read("007", BIG) == "صفر صفر سبعة"
    assert AN.read("1" + "0" * 16, BIG).split() == ["واحد"] + ["صفر"] * 16
    assert AN.read("123", 99) == "واحد اثنان ثلاثة"


@pytest.mark.parametrize("n, plain", [(102000, "مئة ألف وألفان"), (101000, "مئة ألف وألف"), (103000, "مئة وثلاثة آلاف"), (234000, "مئتان وأربعة وثلاثون ألف"),
                                       (1234567, "مليون ومئتان وأربعة وثلاثون ألف وخمسمئة وسبعة وستون")])
def test_a_count_of_a_hundred_or_more_before_a_scale_follows_its_last_two_digits(n, plain):
    assert AN.to_words(n) == plain


@pytest.mark.parametrize("n, feminine, oblique, plain", [(15, True, False, "خمس عشرة"), (21, True, False, "إحدى وعشرون"), (2007, False, True, "ألفين وسبعة"),
                                                           (12, False, True, "اثني عشر"), (12, True, False, "اثنتا عشرة"), (200, False, True, "مئتين"),
                                                           (13, False, False, "ثلاثة عشر"), (13, True, False, "ثلاث عشرة"), (8, True, False, "ثماني"),
                                                           (22, True, True, "اثنتين وعشرين")])
def test_a_cardinal_takes_the_gender_and_the_case_it_is_given(n, feminine, oblique, plain):
    assert AN.to_words(n, AN.Form(feminine=feminine, oblique=oblique)) == plain
