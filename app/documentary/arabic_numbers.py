"""Whole numbers in Arabic words ("911" -> "تسعمئة وأحد عشر"), for the voice: a voice reads bare digits one by one. By default a number is a plain
masculine cardinal in the nominative, the way a narrator reads a figure out ("2026" is "ألفان وستة وعشرون"). A `Form` says when the counted noun is
feminine (3 to 10 and the units of 11 to 99 reverse their gender: "ثلاث سنوات") and when the number stands after a preposition (the oblique case, whose
dual and tens have another stem: "اثنين", "عشرين"). The words are plain Modern Standard Arabic as a news script writes it: no vowel marks, no case endings.

From a thousand upward the counted word follows its number: one is the scale alone, two its dual, three to ten the plural, eleven and more the singular. The
counts of a scale are always masculine (the scale word is). A count of a hundred or more follows its last two digits: 3 to 10 take the plural ("مئة وثلاثة
آلاف"), 11 to 99 the singular ("مئتان وأربعة وثلاثون ألف"), and 1 or 2 are said with their own scale word after the hundreds ("مئة ألف وألفان" is
102,000). A number above `max_value` or one written with a leading zero ("007", a code) is said digit by digit."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from app.documentary.persian_numbers import value_of

ONES = ["صفر", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة", "عشرة",
        "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر", "ستة عشر", "سبعة عشر",
        "ثمانية عشر", "تسعة عشر"]
TENS = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
HUNDREDS = ["", "مئة", "مئتان", "ثلاثمئة", "أربعمئة", "خمسمئة", "ستمئة", "سبعمئة", "ثمانمئة", "تسعمئة"]
ONES_FEMININE = ["صفر", "واحدة", "اثنتان", "ثلاث", "أربع", "خمس", "ست", "سبع", "ثماني", "تسع", "عشر",
                 "إحدى عشرة", "اثنتا عشرة", "ثلاث عشرة", "أربع عشرة", "خمس عشرة", "ست عشرة", "سبع عشرة",
                 "ثماني عشرة", "تسع عشرة"]
ONE_BEFORE_TENS_FEMININE = "إحدى"          # 21: إحدى وعشرون, where a lone one is واحدة
# The oblique case (after a preposition, in an annexation, an object) changes only the dual and the tens.
ONES_OBLIQUE = {2: "اثنين", 12: "اثني عشر"}
ONES_FEMININE_OBLIQUE = {2: "اثنتين", 12: "اثنتي عشرة"}
TENS_OBLIQUE = ["", "", "عشرين", "ثلاثين", "أربعين", "خمسين", "ستين", "سبعين", "ثمانين", "تسعين"]
HUNDRED_DUAL_OBLIQUE = "مئتين"
HUNDRED_AS_FIRST_OF_PAIR = "مئتا"          # 200 before a scale: مئتا ألف
HUNDRED_AS_FIRST_OF_PAIR_OBLIQUE = "مئتي"
# (size, singular, dual, plural for three to ten, oblique dual)
SCALES = [(10**12, "ترليون", "ترليونان", "ترليونات", "ترليونين"),
          (10**9, "مليار", "ملياران", "مليارات", "مليارين"),
          (10**6, "مليون", "مليونان", "ملايين", "مليونين"),
          (10**3, "ألف", "ألفان", "آلاف", "ألفين")]
AND = "و"


@dataclass(frozen=True)
class Form:
    """How a cardinal stands in its sentence: `feminine` for a feminine counted noun, `oblique` after a preposition or a governing noun."""
    feminine: bool = False
    oblique: bool = False


PLAIN = Form()


def unit(k: int, form: Form, before_tens: bool = False) -> str:
    if form.feminine and before_tens and k == 1:
        return ONE_BEFORE_TENS_FEMININE
    table, oblique = (ONES_FEMININE, ONES_FEMININE_OBLIQUE) if form.feminine else (ONES, ONES_OBLIQUE)
    return oblique[k] if form.oblique and k in oblique else table[k]


def below_thousand(n: int, form: Form = PLAIN) -> str:
    parts: list[str] = []
    if n >= 100:
        hundreds = n // 100
        parts.append(HUNDRED_DUAL_OBLIQUE if hundreds == 2 and form.oblique else HUNDREDS[hundreds])
        n %= 100
    if n >= 20:
        tens, ones = divmod(n, 10)
        if ones:
            parts.append(unit(ones, form, before_tens=True))
        parts.append((TENS_OBLIQUE if form.oblique else TENS)[tens])
    elif n or not parts:
        parts.append(unit(n, form))
    return joined(parts)


def joined(parts: list[str]) -> str:
    """The parts of one number, each after the first joined to the one before by a "wa" written onto it."""
    return " ".join([parts[0], *[AND + p for p in parts[1:]]])


def counted(count: int, scale: tuple[int, str, str, str, str], oblique: bool = False) -> str:
    """`count` of one scale: "a thousand", "two thousand", "three thousand", "eleven thousand", "two hundred thousand"."""
    _, singular, dual, plural, dual_oblique = scale
    if count == 1:
        return singular
    if count == 2:
        return dual_oblique if oblique else dual
    if count <= 10:
        return f"{ONES[count]} {plural}"
    if count >= 100 and count % 100 in (1, 2):
        return joined([counted(count - count % 100, scale, oblique), counted(count % 100, scale, oblique)])
    if count >= 100 and count % 100 <= 10 and count % 100:
        return f"{below_thousand(count, Form(oblique=oblique))} {plural}"
    if count == 200:
        return f"{HUNDRED_AS_FIRST_OF_PAIR_OBLIQUE if oblique else HUNDRED_AS_FIRST_OF_PAIR} {singular}"
    return f"{below_thousand(count, Form(oblique=oblique))} {singular}"


def to_words(n: int, form: Form = PLAIN) -> str:
    if n < 0:
        raise ValueError("only whole numbers of zero and more")
    if n < 1000:
        return below_thousand(n, form)
    parts: list[str] = []
    for scale in SCALES:
        if n >= scale[0]:
            count, n = divmod(n, scale[0])
            parts.append(counted(count, scale, form.oblique) if count < 1000 else f"{to_words(count, Form(oblique=form.oblique))} {scale[1]}")
    if n:
        parts.append(below_thousand(n, form))
    return joined(parts)


def digit_by_digit(token: str) -> str:
    return " ".join(ONES[int(c)] for c in unicodedata.normalize("NFKC", token) if c.isdigit())


def read(token: str, max_value: int) -> str:
    """The words for a run of digits (Western or Arabic-Indic): the number, or its digits one by one when it is a code or too large."""
    if (len(token) > 1 and token[0] in "0٠") or value_of(token) > max_value:
        return digit_by_digit(token)
    return to_words(value_of(token))
