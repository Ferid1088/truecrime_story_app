"""A reading written in Persian letters with the short vowels as marks: what the voice is given for a word whose reading is fixed. Eleven v4 reads
inline IPA (and a dictionary phoneme rule) as symbols aloud in some blocks ("slash b a h m slash"); a respelling in Persian letters is still Persian
text and cannot be read as notation. The owner heard it say the fixed words right (ahd, beham)."""

from __future__ import annotations

import re

from app.documentary.reading import scheme

FATHA, KASRA, DAMMA, HAMZA_ABOVE = "َ", "ِ", "ُ", "ٔ"
CONSONANTS = {"b": "ب", "p": "پ", "t": "ت", "s": "س", "j": "ج", "ch": "چ", "h": "ه", "kh": "خ", "d": "د", "z": "ز", "r": "ر", "zh": "ژ", "sh": "ش",
              "gh": "ق", "f": "ف", "k": "ک", "g": "گ", "l": "ل", "m": "م", "n": "ن", "v": "و", "y": "ی", "'": "ع"}
SHORT = {"a": FATHA, "e": KASRA, "o": DAMMA}
INITIAL = {"a": "اَ", "e": "اِ", "o": "اُ", "ā": "آ", "i": "ای", "u": "او", "ey": "اِی", "ow": "اَو"}
LONG = {"ā": "ا", "i": "ی", "u": "و"}
DIPHTHONG = {"ey": KASRA + "ی", "ow": FATHA + "و"}
FINAL = {"e": "ه", "o": "و"}                       # a short e or o that ends a word is written with a letter, not a mark
FINAL_H = "ح"                                      # a consonant h that ends a word and is written ح in the word itself (mosallah), or has evidence (config)


SYMBOL = re.compile(r"ey|ow|kh|gh|zh|sh|ch|[aeoāiu]|[bptsjhdzrfkglmnvy']")


def symbols(stem: str) -> list[str]:
    return SYMBOL.findall(stem)


def persian(reading: str, final_h: bool = False) -> str:
    """The reading in Persian letters with marks, with its ezafe. A consonant h that ends the word is written ه, or ح when `final_h` (see `final_h_for`: the
    word's own letter or word-specific evidence; never a rule for every such word). A hyphen inside the reading only keeps letters apart (sag-hā is not saghā) and is
    not written. Raises ValueError for a symbol the scheme has not."""
    body, _ = scheme.split_pause(reading)
    stem, ezafe = scheme.split_ezafe(body)
    out: list[str] = []
    for index, piece in enumerate(stem.split("-")):
        parts = symbols(piece)
        if "".join(parts) != piece:
            raise ValueError(f"not a reading of the scheme: {reading!r}")
        for i, part in enumerate(parts):
            first = i == 0 and index == 0
            if part == "'" and first:
                continue                                    # the stop before a first vowel is the initial form of that vowel
            if part == "y" and i and parts[i - 1] == "i":
                continue                                    # i + y is one yeh (miyāmi is میامی)
            starts = first or (i == 1 and index == 0 and parts[0] == "'")
            last = i == len(parts) - 1 and index == len(stem.split("-")) - 1
            if part == "h" and last and i and final_h:
                out.append(FINAL_H)
            elif part in CONSONANTS:
                out.append(CONSONANTS[part])
            elif starts:
                out.append(INITIAL[part])
            elif last and part in FINAL:
                out.append(FINAL[part])
            elif part in SHORT:
                out.append(SHORT[part])
            elif part in DIPHTHONG:
                out.append(DIPHTHONG[part])
            else:
                out.append(LONG[part])
    word = "".join(out)
    return word + ezafe_mark(stem, word) if ezafe else word


def ezafe_mark(stem: str, written: str) -> str:
    """What writes the ezafe after a word, by its spoken final sound (never by its last letter alone): a hamza on a ه said as the vowel e (خانهٔ), a
    kasra after a consonant (a ه said h too: دادگاهِ) and after a final i on its own yeh (صندلیِ), a yeh after the other vowels (صدای، بوی)."""
    last = symbols(stem.split("-")[-1])[-1:] or [""]
    if last[0] == "e" and written.endswith("ه"):
        return HAMZA_ABOVE
    if last[0] in ("ā", "u", "o", "e"):
        return "ی"
    return KASRA


def final_h_for(surface: str) -> bool:
    """Whether a consonant h that ends this word is written ح in its respelling: when the word itself is written with ح (مسلح, سلاح: the respelling keeps the
    word's own letter, it converts nothing), or when config reading.final_h_evidence lists the word (evidence that heh is read as a vowel there and hah
    is not). A word written with ه (دادگاه, شاه) keeps ه."""
    from app.core.ai_config import ai_config
    from app.documentary.reading.words import VOWEL_MARKS, key_of

    plain = VOWEL_MARKS.sub("", surface).strip()
    return plain.endswith(FINAL_H) or key_of(plain) in ai_config.reading.final_h_evidence
