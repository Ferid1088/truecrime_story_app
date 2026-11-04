"""The readings a word is scored against on the audio: the expected one, the other readings the reading agent named, and the errors the voice makes
in Persian, made from the expected reading by rule: one short vowel heard as another (config reading.vowel_confusions), a vowel inserted into a
final consonant cluster (omr / omer), an ezafe added or dropped, -e said as -ye (dar-e / dare-ye). Each candidate keeps what kind of error it is."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from app.core.ai_config import ai_config
from app.documentary.reading import scheme

CONSONANT_FREE = set(scheme.VOWELS) | {"-", "'", "|"}
MAX_CANDIDATES = 10


@dataclass(frozen=True)
class Candidate:
    reading: str
    kind: str              # expected | alternative | vowel | epenthesis | ezafe_added | ezafe_dropped | ezafe_ye
    ipa: str


def made(reading: str, kind: str) -> Candidate:
    return Candidate(reading, kind, scheme.to_ipa(reading))


def vowel_swaps(stem: str) -> list[str]:
    confusions = ai_config.reading.vowel_confusions
    out = []
    for i, ch in enumerate(stem):
        out += [stem[:i] + other + stem[i + 1:] for other in confusions.get(ch, [])]
    return out


def epenthesis(stem: str) -> list[str]:
    """A vowel the voice may put into the word's final two consonants (omr -> omer, sabr -> saber)."""
    if len(stem) >= 3 and stem[-1] not in CONSONANT_FREE and stem[-2] not in CONSONANT_FREE and stem[-2:] not in ("sh", "kh", "gh", "ch", "zh"):
        return [stem[:-1] + "e" + stem[-1]]
    return []


def for_word(expected: str, alternatives: list[str] | None = None) -> list[Candidate]:
    """The candidates of one word, the expected reading first, each reading once."""
    stem, ezafe = scheme.split_ezafe(scheme.split_pause(expected)[0])
    found: dict[str, Candidate] = {}

    def add(reading: str, kind: str) -> None:
        if reading and reading not in found and len(found) < MAX_CANDIDATES:
            with contextlib.suppress(KeyError, ValueError):
                found[reading] = made(reading, kind)

    add(f"{stem}-{ezafe}" if ezafe else stem, "expected")
    for alt in alternatives or []:
        add(scheme.split_pause(alt)[0], "alternative")
    tail = f"-{ezafe}" if ezafe else ""
    if ezafe == "e":
        add(f"{stem}e-ye", "ezafe_ye")
    if ezafe:
        add(stem, "ezafe_dropped")
    else:
        add(scheme.ezafe_form(stem), "ezafe_added")
    for reading in epenthesis(stem):
        add(reading + tail, "epenthesis")
    for reading in vowel_swaps(stem):
        add(reading + tail, "vowel")
    return list(found.values())
