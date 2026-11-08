"""Turkish suffixes after a number: how the ending written after the apostrophe (2007'de, 911'i, 4'e, 1990'lar, 3'üncü) is put on the last word the
narrator SPEAKS. The written ending follows the sound of that word ("dört" ends in t, so 4'te; "üç" ends in ç, so 3'te) and some stems change before a
vowel ("dört" becomes "dörd-": dörde, dördüncü; "buçuk" becomes "buçuğ-"), so the ending is parsed into morphemes and built again from the word."""

from __future__ import annotations

import re
from enum import Enum

VOWELS = "aeıioöuü"
VOICELESS = "çfhkpsşt"
HARMONY_FOUR = {"a": "ı", "ı": "ı", "e": "i", "i": "i", "o": "u", "u": "u", "ö": "ü", "ü": "ü"}
STEM_CHANGES = {"dört": "dörd", "buçuk": "buçuğ"}


class Morpheme(Enum):
    DAT = "dative"
    ACC = "accusative"
    POSS = "possessive of the third person"
    GEN = "genitive"
    LOC = "locative"
    ABL = "ablative"
    INS = "instrumental"
    EQU = "equative"
    ORD = "ordinal"
    PL = "plural"
    LI = "with (1990'lı yıllar)"
    LIK = "measure (5'lik)"


CASES = [
    (Morpheme.DAT, r"[yn]?[ae]"), (Morpheme.ACC, r"[yn]?[ıiuü]"), (Morpheme.LOC, r"n?[dt][ae]"), (Morpheme.ABL, r"n?[dt][ae]n"),
    (Morpheme.GEN, r"n?[ıiuü]n"), (Morpheme.INS, r"y?l[ae]"), (Morpheme.EQU, r"[cç][ae]")]
POSSESSIVE = [(Morpheme.POSS, r"s?[ıiuü]")]
OPENING = [(Morpheme.ORD, r"[ıiuü]?nc[ıiuü]"), (Morpheme.LIK, r"l[ıiuü]k"), (Morpheme.LI, r"l[ıiuü]"), (Morpheme.PL, r"l[ae]r")]
# What may follow each morpheme: the endings are glued in this order (plural, possessive, case) and a measure or "with" ending closes the word.
FOLLOWING = {
    None: OPENING + POSSESSIVE + CASES,
    Morpheme.ORD: CASES, Morpheme.PL: POSSESSIVE + CASES, Morpheme.POSS: CASES,
    **{case: [] for case, _ in CASES}, Morpheme.LI: [], Morpheme.LIK: []}


def lower(text: str) -> str:
    """Turkish lower case: İ -> i and I -> ı (Python's default would give i̇ and i)."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def parse(suffix: str, previous: Morpheme | None = None) -> list[Morpheme] | None:
    """The morphemes of the written ending (the longest reading first), or None when it is not an ending the number can take."""
    if not suffix:
        return []
    for length in range(len(suffix), 0, -1):
        for morpheme, pattern in FOLLOWING[previous]:
            if re.fullmatch(pattern, suffix[:length]):
                rest = parse(suffix[length:], morpheme)
                if rest is not None:
                    return [morpheme, *rest]
    return None


def attach(word: str, morpheme: Morpheme, after_possessive: bool = False) -> str:
    """The word with one morpheme: vowel harmony from its last vowel, buffer letters y/n/s after a vowel (and n after a possessive), d/t and c/ç
    by the last sound, and the stem change of "dört" and "buçuk" before a vowel."""
    ends_in_vowel = word[-1] in VOWELS
    vowel = next(char for char in reversed(word) if char in VOWELS)
    two = "a" if vowel in "aıou" else "e"
    four = HARMONY_FOUR[vowel]
    voiceless = word[-1] in VOICELESS
    before_vowel = word if ends_in_vowel else STEM_CHANGES.get(word, word)
    buffer = "n" if after_possessive else "y" if ends_in_vowel else ""
    match morpheme:
        case Morpheme.DAT:
            return before_vowel + buffer + two
        case Morpheme.ACC:
            return before_vowel + buffer + four
        case Morpheme.POSS:
            return before_vowel + ("s" if ends_in_vowel else "") + four
        case Morpheme.GEN:
            return before_vowel + ("n" if ends_in_vowel or after_possessive else "") + four + "n"
        case Morpheme.LOC:
            return word + ("nd" if after_possessive else "t" if voiceless else "d") + two
        case Morpheme.ABL:
            return attach(word, Morpheme.LOC, after_possessive) + "n"
        case Morpheme.INS:
            return word + ("y" if ends_in_vowel else "") + "l" + two
        case Morpheme.EQU:
            return word + ("ç" if voiceless else "c") + two
        case Morpheme.ORD:
            return before_vowel + ("" if ends_in_vowel else four) + "nc" + four
        case Morpheme.PL:
            return word + "l" + two + "r"
        case Morpheme.LI:
            return word + "l" + four
        case Morpheme.LIK:
            return word + "l" + four + "k"


def apply(words: list[str], suffix: str) -> list[str]:
    """The spoken words with the written ending on the last one. An ending that cannot be read is dropped: the number is still said."""
    morphemes = parse(lower(suffix))
    if not morphemes:
        return words
    last, previous = words[-1], None
    for morpheme in morphemes:
        last = attach(last, morpheme, previous is Morpheme.POSS)
        previous = morpheme
    return [*words[:-1], last]
