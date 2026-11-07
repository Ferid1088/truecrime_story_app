"""Whole numbers in Arabic words ("911" -> "تِسْعُمِئَة وَأَحَدَ عَشَر"), for the voice: a voice reads bare digits one by one. Counted nouns are not in the text,
so every number is said as a plain masculine cardinal, the way a narrator reads a figure out ("2026" is "أَلْفَان وَسِتَّة وَعِشْرُون"). The words carry the vowel
marks inside the word (the stem) and no case endings: the same style the tashkeel reading gives every other word.

From a thousand upward the counted word follows its number: one is the scale alone, two its dual, three to ten the plural, eleven and more the singular.
A number above `max_value` or one written with a leading zero ("007", a code) is said digit by digit."""

from __future__ import annotations

import re
import unicodedata

from app.documentary.persian_numbers import NUMBER, value_of
from app.documentary.reading.words import VOWEL_MARKS

ONES = ["صِفْر", "وَاحِد", "اثْنَان", "ثَلَاثَة", "أَرْبَعَة", "خَمْسَة", "سِتَّة", "سَبْعَة", "ثَمَانِيَة", "تِسْعَة", "عَشَرَة",
        "أَحَدَ عَشَر", "اثْنَا عَشَر", "ثَلَاثَةَ عَشَر", "أَرْبَعَةَ عَشَر", "خَمْسَةَ عَشَر", "سِتَّةَ عَشَر", "سَبْعَةَ عَشَر",
        "ثَمَانِيَةَ عَشَر", "تِسْعَةَ عَشَر"]
TENS = ["", "", "عِشْرُون", "ثَلَاثُون", "أَرْبَعُون", "خَمْسُون", "سِتُّون", "سَبْعُون", "ثَمَانُون", "تِسْعُون"]
HUNDREDS = ["", "مِئَة", "مِئَتَان", "ثَلَاثُمِئَة", "أَرْبَعُمِئَة", "خَمْسُمِئَة", "سِتُّمِئَة", "سَبْعُمِئَة", "ثَمَانُمِئَة", "تِسْعُمِئَة"]
HUNDRED_AS_FIRST_OF_PAIR = "مِئَتَا"          # 200 before a scale: مِئَتَا أَلْف
# (size, singular, dual, plural for three to ten)
SCALES = [(10**12, "تِرِلْيُون", "تِرِلْيُونَان", "تِرِلْيُونَات"),
          (10**9, "مِلْيَار", "مِلْيَارَان", "مِلْيَارَات"),
          (10**6, "مِلْيُون", "مِلْيُونَان", "مَلَايِين"),
          (10**3, "أَلْف", "أَلْفَان", "آلَاف")]
AND = "وَ"


def below_thousand(n: int) -> str:
    parts: list[str] = []
    if n >= 100:
        parts.append(HUNDREDS[n // 100])
        n %= 100
    if n >= 20:
        tens, ones = divmod(n, 10)
        if ones:
            parts.append(ONES[ones])
        parts.append(TENS[tens])
    elif n or not parts:
        parts.append(ONES[n])
    return joined(parts)


def joined(parts: list[str]) -> str:
    """The parts of one number, each after the first joined to the one before by a "wa" written onto it."""
    return " ".join([parts[0], *[AND + p for p in parts[1:]]])


def counted(count: int, scale: tuple[int, str, str, str]) -> str:
    """`count` of one scale: "a thousand", "two thousand", "three thousand", "eleven thousand", "two hundred thousand"."""
    _, singular, dual, plural = scale
    if count == 1:
        return singular
    if count == 2:
        return dual
    if count <= 10:
        return f"{ONES[count]} {plural}"
    words = below_thousand(count)
    return f"{HUNDRED_AS_FIRST_OF_PAIR} {singular}" if count == 200 else f"{words} {singular}"


def to_words(n: int) -> str:
    if n < 0:
        raise ValueError("only whole numbers of zero and more")
    if n < 1000:
        return below_thousand(n)
    parts: list[str] = []
    for scale in SCALES:
        if n >= scale[0]:
            count, n = divmod(n, scale[0])
            parts.append(counted(count, scale) if count < 1000 else f"{to_words(count)} {scale[1]}")
    if n:
        parts.append(below_thousand(n))
    return joined(parts)


def digit_by_digit(token: str) -> str:
    return " ".join(ONES[int(c)] for c in unicodedata.normalize("NFKC", token) if c.isdigit())


def read(token: str, max_value: int) -> str:
    """The words for a run of digits (Western or Arabic-Indic): the number, or its digits one by one when it is a code or too large."""
    if (len(token) > 1 and token[0] in "0٠") or value_of(token) > max_value:
        return digit_by_digit(token)
    return to_words(value_of(token))


def bare(text: str) -> str:
    return VOWEL_MARKS.sub("", unicodedata.normalize("NFC", text))


def spell_digits(text: str, max_value: int) -> str:
    """The text with every run of digits in plain words (no vowel marks): what the speech check expects to hear where the story shows digits."""
    return re.sub(NUMBER.pattern, lambda m: bare(read(m.group(), max_value)), text)
