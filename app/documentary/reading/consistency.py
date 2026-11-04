"""Whether a word is read the same way everywhere in a film. A name is read one way (the film table's); any other word may legitimately differ when it is
a homograph (مرد mard / mord: the meaning decides) or when only its ezafe or pause differ, or when two spellings of the scheme say the same (miyāmi /
miāmi). Every other difference is an inconsistency: the reading of one block or the other is wrong, and the voice would say the word two ways."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.core.ai_config import ai_config
from app.documentary.reading import scheme, spelling
from app.documentary.reading.scheme import VOWELS
from app.documentary.reading.words import Word, key_of


@dataclass(frozen=True)
class Variant:
    key: str
    kind: str                                    # name | word
    readings: dict[str, tuple[str, ...]]         # the normalised reading -> the blocks that read it so
    legitimate: str = ""                         # why the difference is allowed ("" when it is not)

    def describe(self) -> dict:
        return {"word": self.key, "kind": self.kind, "readings": {r: list(b) for r, b in self.readings.items()}, "legitimate": self.legitimate}


def normal(reading: str) -> str:
    """A reading as far as what it says can differ: without its ezafe and pause, in the scheme's canonical spelling, and without what is notation or free
    colloquial variation and never a different word: hyphens and spaces inside it, the glottal stop (بعد ba'd = bad), a y between i and a vowel (one
    yeh writes both), a final h after e (سه seh = se), the h of a plural after a consonant (دخترها dokhtarhā = dokhtarā)."""
    stem = scheme.canonical(scheme.canonical(scheme.split_ezafe(scheme.split_pause(reading)[0])[0]).replace("-", "").replace(" ", "").replace("'", ""))
    sounds = ["o" if p == "ow" else p for p in spelling.symbols(stem)]       # ow / o: the Tehran speech says o (روشن rowshan = roshan)
    if "".join(sounds).replace("o", "") != stem.replace("ow", "o").replace("o", ""):
        return stem                                       # not a reading of the scheme: compared as written
    out: list[str] = []
    for k, p in enumerate(sounds):
        nxt = sounds[k + 1] if k + 1 < len(sounds) else ""
        if p == "y" and out and out[-1] in ("i", "y") and (nxt in VOWELS or nxt == "y"):
            continue                                      # one yeh writes i and y (بقیه baghiye = baghiyye)
        if p == "y" and not nxt and out and out[-1] in VOWELS:
            continue                                      # the carrier yeh of an ezafe left in the stem (جای jāy = jā)
        if p == "h" and (not nxt and out[-1:] == ["e"] or nxt == "ā" and out and out[-1] not in VOWELS):
            continue
        out.append(p)
    return "".join(out)


def variants(blocks: dict[str, list[tuple[Word, str]]], names: frozenset[str]) -> list[Variant]:
    """Every word read in more than one way across the blocks (numbers are read by code and left out)."""
    homographs = {key_of(w) for w in ai_config.homographs.words} | {key_of(w) for w in ai_config.homographs.free_variants}
    seen: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for block_id, words in blocks.items():
        for word, reading in words:
            if word.number or reading == "NUM":
                continue
            seen[word.key][normal(reading)].append(block_id)
    out = []
    for key, readings in seen.items():
        if len(readings) < 2:
            continue
        kind = "name" if key in names else "word"
        legitimate = "a homograph or a free variant: the sentence decides, both are right" if key in homographs and kind == "word" else ""
        out.append(Variant(key, kind, {r: tuple(dict.fromkeys(b)) for r, b in readings.items()}, legitimate))
    return sorted(out, key=lambda v: (v.legitimate != "", v.kind != "name", -sum(len(b) for b in v.readings.values())))


def inconsistent(found: list[Variant]) -> dict[str, tuple[str, ...]]:
    """The words read in more than one way without a legitimate reason: word -> its readings, the film's most frequent first."""
    return {v.key: tuple(sorted(v.readings, key=lambda r: -len(v.readings[r]))) for v in found if not v.legitimate}
