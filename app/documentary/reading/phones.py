"""A heard word (the phones of the local phoneme recogniser) against its reading (the Latin scheme): both are brought to one short alphabet of sounds
and compared. A different vowel, an ezafe that is missing or added, or a different consonant inside the word is a fault; the noise at the edges
of the word's time window and the sounds that Persian speech lets slip (a devoiced final consonant, a dropped "h" between vowels) are not."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.documentary.reading.scheme import canonical, split_ezafe, split_pause

HEARD = {"æ": "a", "a": "a", "ɛ": "e", "e": "e", "ə": "e", "ɜ": "e", "ɔ": "ā", "ɒ": "ā", "ɑ": "ā", "o": "o", "u": "u", "ʊ": "u", "i": "i", "ɪ": "i",
         "oɪ": "āy", "aɪ": "āy", "eɪ": "ey", "ej": "ey", "ow": "ow", "ɡ": "g", "g": "g", "ʒ": "zh", "ʃ": "sh", "x": "kh", "tʃ": "ch", "dʒ": "j", "j": "y",
         "ɾ": "r", "r": "r", "ɹ": "r", "ɣ": "gh", "ɢ": "gh", "ʁ": "gh", "q": "gh", "ɫ": "l", "ʔ": "", "ʕ": ""}
LATIN = re.compile("kh|gh|zh|sh|ch|ey|ow|ā|[a-z]")
VOWELS = {"a", "e", "o", "u", "i", "ā", "āy", "ey", "ow"}
SAME = {frozenset(p) for p in (("d", "t"), ("b", "p"), ("g", "k"), ("z", "s"), ("zh", "sh"), ("v", "f"), ("j", "ch"), ("gh", "kh"), ("gh", "k"), ("h", "kh"))}


@dataclass(frozen=True)
class Verdict:
    ok: bool
    expected: str
    heard: str


def expected_sounds(reading: str) -> list[str]:
    """The sounds of a reading in the short alphabet; the ezafe is a final "e"."""
    stem, ezafe = split_ezafe(split_pause(reading)[0])
    sounds = [s for s in LATIN.findall(canonical(stem).replace("'", "")) if s != "-"]
    collapsed = [s for k, s in enumerate(sounds) if not (k and s == sounds[k - 1] and s not in VOWELS)]
    return collapsed + {"": [], "e": ["e"], "ye": ["y", "e"]}[ezafe]


def heard_sounds(phones: list[str]) -> list[str]:
    out: list[str] = []
    for phone in phones:
        sound = HEARD.get(phone.replace("ː", ""), phone.replace("ː", ""))
        if sound and (not out or out[-1] != sound or sound in VOWELS):
            out.append(sound)
    return out


def vowels_of(sounds: list[str]) -> list[str]:
    """The vowels of a word, "o" and "u" as one (a final "-un" is said "-on"), a diphthong ending in a glide as its long vowel."""
    return [{"u": "o", "āy": "ā"}.get(s, s) for s in sounds if s in VOWELS]


def inner(sounds: list[str]) -> list[str]:
    """The sounds from the first vowel to the last, with the consonant before the first vowel when it is the second of a cluster ("pr" in "prat");
    the other consonants at the edges are the neighbours'."""
    at = [k for k, s in enumerate(sounds) if s in VOWELS]
    if not at:
        return []
    return sounds[max(at[0] - 1, 0) if at[0] >= 2 else at[0]:at[-1] + 1]


def compare(reading: str, phones: list[str], consonants: bool = False) -> Verdict:
    """The vowels a word is said with against the vowels of its reading. The faults a Persian narration has are vowels (a short vowel that is
    another, a syllable more or less, an ezafe that is missing or added); the consonants at the edges of the word's time window are the
    neighbours', so only the vowels are compared, and a vowel of the neighbour at either edge is not a fault (an added final "e" is: it is the
    ezafe)."""
    want, got = expected_sounds(reading), heard_sounds(phones)
    shown = (" ".join(want), " ".join(got))
    a, b = vowels_of(want), vowels_of(got)
    for tag, a0, a1, b0, b1 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert" and (b0 == 0 or (b1 == len(b) and b[b0:b1] != ["e"])):
            continue                                                       # the neighbour's vowel inside the time window
        if tag == "delete" and a0 == 0:
            continue                                                       # the window starts after the first vowel
        return Verdict(False, *shown)
    if consonants and not _consonants_agree(inner(want), inner(got)):
        return Verdict(False, *shown)
    return Verdict(True, *shown)


def _consonants_agree(want: list[str], got: list[str]) -> bool:
    for tag, a0, a1, b0, b1 in SequenceMatcher(a=want, b=got, autojunk=False).get_opcodes():
        gone, extra = want[a0:a1], got[b0:b1]
        if tag == "equal" or all(s in VOWELS for s in gone + extra):
            continue
        if tag == "replace" and len(gone) == len(extra) and all(x == y or frozenset((x, y)) in SAME or (x in VOWELS and y in VOWELS)
                                                                  for x, y in zip(gone, extra, strict=True)):
            continue
        return False
    return True


def same_heard(reading: str, heard: str, plain: str) -> bool:
    """Whether what a listener wrote in Latin letters says the written reading: the same vowels and consonants, the same ezafe; the spelling of
    the listener's punctuation, "q" and "gh", a doubled consonant and the two forms of the word "and" are not differences."""
    if plain == "و":
        return re.sub(r"[^a-zā]", "", heard.lower()) in {"o", "va", "vo"}

    def form(text: str) -> tuple[list[str], bool]:
        stem, ezafe = split_ezafe(re.sub(r"[.,:;!?،؛]+$", "", split_pause(text.strip())[0]).lstrip("-"))
        return expected_sounds(stem.replace("-", "")), bool(ezafe)

    (want, ezafe_want), (got, ezafe_got) = form(reading), form(heard)
    return want == got and ezafe_want == ezafe_got
