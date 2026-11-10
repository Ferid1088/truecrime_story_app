"""A number in the film's text is read as a number, not digit by digit: whole numbers in Persian words."""

import pytest

from app.documentary.persian_numbers import to_words, value_of


@pytest.mark.parametrize("n, words", [(0, "صفر"), (11, "یازده"), (31, "سی و یک"), (100, "صد"), (911, "نهصد و یازده"), (1000, "هزار"),
                                      (2026, "دو هزار و بیست و شش"), (21000, "بیست و یک هزار"), (1500000, "یک میلیون و پانصد هزار")])
def test_whole_numbers_in_persian_words(n, words):
    assert to_words(n) == words


def test_persian_arabic_and_ascii_digits_have_the_same_value():
    assert value_of("۹۱۱") == value_of("٩١١") == value_of("911") == 911
