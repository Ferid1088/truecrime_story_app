"""The Latin scheme of a reading (one symbol for one sound) and the comparison of two readings of one word. A reading is written without capitals;
an ezafe is a hyphen and "e" (or "ye" after a vowel); an audible pause after the word is a trailing "|"."""

from __future__ import annotations

import re

VOWELS = "aāeiou"
PAUSE = "|"
ALIASES = {"x": "kh", "q": "gh", "’": "'", "ʼ": "'", "ʿ": "'", "`": "'", "ou": "ow", "ei": "ey", "oo": "u", "â": "ā", "ī": "i", "ū": "u", "ē": "e", "ō": "o"}
ALIAS = re.compile("|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True)))


def ezafe_form(reading: str) -> str:
    """The reading with its ezafe: "ye" after a vowel, "e" after a consonant."""
    stem, _ = split_ezafe(reading)
    return f"{stem}-{'ye' if stem[-1:] in VOWELS else 'e'}"


def split_ezafe(reading: str) -> tuple[str, str]:
    """(stem, ezafe) of one word's reading: ezafe is "", "e" or "ye"."""
    stem, dash, tail = reading.rpartition("-")
    if dash and tail in ("e", "ye") and stem:
        return stem, tail
    return reading, ""


def split_pause(reading: str) -> tuple[str, bool]:
    text = reading.strip()
    if text.endswith(PAUSE):
        return text[:-1].strip(), True
    return text, False


def canonical(stem: str) -> str:
    return ALIAS.sub(lambda m: ALIASES[m.group()], stem.strip().lower())


def same_reading(a: str, b: str) -> bool:
    """True when two readings of a word say the same: the same sounds, and the same ezafe ("e" and "ye" are one ezafe after a vowel)."""
    (stem_a, ezafe_a), (stem_b, ezafe_b) = split_ezafe(split_pause(a)[0]), split_ezafe(split_pause(b)[0])
    stem_a, stem_b = canonical(stem_a), canonical(stem_b)
    if stem_a != stem_b:
        return False
    if stem_a[-1:] in VOWELS:
        return bool(ezafe_a) == bool(ezafe_b)
    return ezafe_a == ezafe_b


SOUNDS = {"kh": "x", "gh": "ɢ", "zh": "ʒ", "sh": "ʃ", "ch": "tʃ", "j": "dʒ", "y": "j", "r": "ɾ", "g": "ɡ", "'": "ʔ", "ey": "ej", "ow": "ow",
          "a": "æ", "e": "e", "o": "o", "ā": "ɑː", "i": "iː", "u": "uː"}
SOUND = re.compile("|".join(re.escape(k) for k in sorted(SOUNDS, key=len, reverse=True)))


def to_ipa(reading: str) -> str:
    """The IPA of one word's reading, ezafe included: the symbols Eleven v4 takes between slashes. Every other symbol of the scheme is its own IPA."""
    stem, ezafe = split_ezafe(split_pause(reading)[0])
    return SOUND.sub(lambda m: SOUNDS[m.group()], canonical(stem)) + {"": "", "e": "e", "ye": "je"}[ezafe]
