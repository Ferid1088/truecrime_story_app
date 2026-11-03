"""The Persian-with-vowel-marks form of a reading (the replacement of a dictionary alias): the Latin reading of the scheme written in Persian letters
with the short vowels as marks. Eleven v4 reads a respelling like this about as well as inline IPA, and the result is still Persian text."""

from __future__ import annotations

import re

from app.core.ai_config import ai_config
from app.documentary.reading import scheme

FATHA, KASRA, DAMMA = "َ", "ِ", "ُ"
CONSONANTS = {"b": "ب", "p": "پ", "t": "ت", "s": "س", "j": "ج", "ch": "چ", "h": "ه", "kh": "خ", "d": "د", "z": "ز", "r": "ر", "zh": "ژ", "sh": "ش",
              "gh": "ق", "f": "ف", "k": "ک", "g": "گ", "l": "ل", "m": "م", "n": "ن", "v": "و", "y": "ی", "'": "ع"}
SHORT = {"a": FATHA, "e": KASRA, "o": DAMMA}
INITIAL = {"a": "اَ", "e": "اِ", "o": "اُ", "ā": "آ", "i": "ای", "u": "او"}
LONG = {"ā": "ا", "i": "ی", "u": "و"}
DIPHTHONG = {"ey": KASRA + "ی", "ow": FATHA + "و"}


def symbols(stem: str) -> list[str]:
    cfg = ai_config.reading.transcription
    pattern = "|".join(re.escape(s) for s in sorted([*cfg.diphthongs, *cfg.consonants, *cfg.vowels, cfg.glottal], key=len, reverse=True))
    return re.findall(pattern, stem)


def to_persian(reading: str) -> str:
    """The reading without its pause and ezafe, in Persian letters with marks. Raises ValueError for a symbol the scheme has not."""
    stem, _ = scheme.split_ezafe(scheme.split_pause(reading)[0])
    stem = stem.replace("-", "")
    parts = symbols(stem)
    if "".join(parts) != stem:
        raise ValueError(f"not a reading of the scheme: {reading!r}")
    out: list[str] = []
    for i, part in enumerate(parts):
        previous = parts[i - 1] if i else ""
        if part in CONSONANTS:
            out.append(CONSONANTS[part])
        elif i == 0 or previous == "'":
            out.append(INITIAL.get(part, DIPHTHONG.get(part, "")) if part in INITIAL else "ا" + DIPHTHONG[part])
        elif part in SHORT:
            out.append(SHORT[part])
        elif part in DIPHTHONG:
            out.append(DIPHTHONG[part])
        else:
            out.append(LONG[part])
    return "".join(out)
