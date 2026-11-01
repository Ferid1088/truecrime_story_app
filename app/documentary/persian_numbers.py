"""Whole numbers in Persian words ("911" -> "نهصد و یازده"), for the pronunciation dictionary: a voice reads bare digits one by one."""

from __future__ import annotations

import re

ONES = ["صفر", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه", "ده", "یازده", "دوازده", "سیزده", "چهارده", "پانزده", "شانزده",
        "هفده", "هجده", "نوزده"]
TENS = ["", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"]
HUNDREDS = ["", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد", "هشتصد", "نهصد"]
SCALES = [(10**9, "میلیارد"), (10**6, "میلیون"), (10**3, "هزار")]
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
NUMBER = re.compile(r"[0-9۰-۹٠-٩]+")


def to_words(n: int) -> str:
    if n < 0:
        raise ValueError("only whole numbers of zero and more")
    if n < 20:
        return ONES[n]
    parts: list[str] = []
    for size, name in SCALES:
        if n >= size:
            count, n = divmod(n, size)
            parts.append(name if (size == 10**3 and count == 1) else f"{to_words(count)} {name}")
    if n >= 100:
        parts.append(HUNDREDS[n // 100])
        n %= 100
    if n >= 20:
        parts.append(TENS[n // 10])
        n %= 10
        if n:
            parts.append(ONES[n])
    elif n:
        parts.append(ONES[n])
    return " و ".join(parts)


def value_of(token: str) -> int:
    return int(token.translate(DIGITS))
