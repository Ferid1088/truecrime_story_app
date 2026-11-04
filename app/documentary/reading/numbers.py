"""How a number written in digits is said, decided by the words around it (config number_reading), never by how often a reading occurs: a service
number digit by digit (۹۱۱ "nine one one", not "nine hundred eleven"), an identifier after a cue word digit by digit when it is long, a year after a
cue word, an ordinal by its suffix, every other whole number as a cardinal. The words go to the voice in place of the digits (the story keeps its
digits); a number written with other characters inside it (۲:۳۱, ۱۰٫۵) is not read by code and is left for a person."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.ai_config import ai_config
from app.documentary.persian_numbers import DIGITS, NUMBER, to_words, value_of
from app.documentary.reading.words import Word, key_of

ORDINAL = re.compile(r"^([0-9۰-۹٠-٩]+)‌?(مین|ام|م)$")
ORDINAL_WORD = {"یک": "یکم", "سه": "سوم", "سی": "سی‌ام"}


@dataclass(frozen=True)
class NumberReading:
    kind: str             # service | identifier | year | ordinal | cardinal | unread
    words: str | None     # what the voice is given in place of the digits (None: not read by code)
    basis: str


def respelled(words: str) -> str:
    """The number words with each word another word is written the same as in its marked form (نه -> نُه)."""
    table = ai_config.number_reading.respelled
    return " ".join(table.get(w, w) for w in words.split(" "))


def ordinal(n: int, suffix: str) -> str:
    words = to_words(n).split(" ")
    words[-1] = ORDINAL_WORD.get(words[-1], words[-1] + ("ی‌ام" if words[-1].endswith("ی") else "م"))
    return " ".join(words) + ("ین" if suffix == "مین" else "")


def cue_before(words: list[Word], n: int, cues: list[str]) -> str | None:
    keys = {key_of(c) for c in cues}
    return words[n - 1].surface if n and words[n - 1].key in keys else None


def read(words: list[Word], n: int) -> NumberReading:
    cfg, word = ai_config.number_reading, words[n]
    if not word.number:
        found = ORDINAL.match(word.surface)
        if found:
            return NumberReading("ordinal", respelled(ordinal(value_of(found.group(1)), found.group(2))), f"an ordinal ({found.group(2)} after the digits)")
        return NumberReading("unread", None, "a number written with other characters among its digits: not read by code, left as written for a person")
    digits = word.surface.translate(DIGITS)
    if word.surface in cfg.digit_by_digit or digits in {d.translate(DIGITS) for d in cfg.digit_by_digit}:
        return NumberReading("service", " ".join(cfg.digit_words[int(d)] for d in digits), "a service number, said digit by digit")
    cue = cue_before(words, n, cfg.digit_cues)
    if cue and len(digits) >= cfg.digit_min_length:
        return NumberReading("identifier", " ".join(cfg.digit_words[int(d)] for d in digits), f"an identifier after «{cue}», said digit by digit")
    value = value_of(word.surface)
    year_cue = cue_before(words, n, cfg.year_cues)
    if year_cue and len(digits) == 4 and cfg.year_range[0] <= value <= cfg.year_range[1]:
        return NumberReading("year", respelled(to_words(value)), f"a year after «{year_cue}», said as its cardinal")
    return NumberReading("cardinal", respelled(to_words(value)), "a whole number with no cue for another reading: its cardinal")


def has_digit(word: Word) -> bool:
    return bool(NUMBER.search(word.surface))
