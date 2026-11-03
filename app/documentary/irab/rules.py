"""What code enforces on the vowelized words an editor answers with: the letters of a word never change (only marks are added), a spoken form is
Persian letters, and a number always has its spoken form in words. An answer that breaks a rule is refused with the reason and asked again."""

from __future__ import annotations

import re

from app.documentary.reading.words import Word, key_of

MARKS = "ًٌٍَُِّْٰٔ"
SPOKEN = re.compile(r"^[ء-غف-يپچژکگیآ‌ " + MARKS + r"]+$")


def problem_with(word: Word, irab: str, said: str) -> str:
    """Why this answer for the word is refused ("" when it is fine)."""
    if not irab.strip():
        return f"word {word.n} ({word.plain}) has no irab"
    if key_of(irab) != word.key:
        bare = "".join(ch for ch in irab if ch not in MARKS)
        return (f"word {word.n} ({word.plain}): irab {irab!r} changes the letters of the word (without its marks it is {bare!r}); only marks may be added. "
                "A sound that has no letter in the written word, such as an ezafe, goes in \"said\" and not in \"irab\"")
    if word.number and not said:
        return f"word {word.n} ({word.plain}) is a number and needs its spoken form in words"
    if said and not SPOKEN.match(said):
        return f"word {word.n} ({word.plain}): the spoken form {said!r} must be Persian letters and marks only (no digits, no Latin letters)"
    return ""


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


REDUNDANT = re.compile("[\u064e\u064f\u0650](?=[\u0627\u0622])|\u0652")


def canonical(irab: str) -> str:
    """The vowelization without marks that add no sound: a short vowel mark before an alef (the alef is the long vowel) and the sukun (a letter
    without a vowel mark has none). Two editors that differ only in these marks do not differ."""
    return REDUNDANT.sub("", irab)
