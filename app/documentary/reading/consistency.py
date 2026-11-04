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
    if "".join(spelling.symbols(stem)) != stem:
        return stem                                       # not a reading of the scheme: compared as written
    sounds = ["o" if p == "ow" else p for p in sounds_of(stem)]          # ow / o: the Tehran speech says o (روشن rowshan = roshan)
    out: list[str] = []
    for k, p in enumerate(sounds):
        nxt = sounds[k + 1] if k + 1 < len(sounds) else ""
        if p == "y" and out and out[-1] in ("i", "y", "u") and (nxt in VOWELS or nxt == "y"):
            continue                                      # one yeh writes i and y (بقیه baghiye = baghiyye); a glide between u and a vowel (masnuyi = masnu'i)
        if p == "y" and not nxt and out and out[-1] in ("ā", "u", "o"):
            continue                                      # the carrier yeh of an ezafe left in the stem (جای jāy = jā; never the y of sa'y or key)
        if p == "y" and nxt == "e" and k + 2 == len(sounds) and out and out[-1] in VOWELS:
            break                                         # an ezafe -ye written without its hyphen (khuneye = khune-ye)
        if p == "h" and (not nxt and out[-1:] == ["e"] or nxt == "ā" and out and out[-1] not in VOWELS):
            continue
        if p == "m" and nxt == "b":
            p = "n"                                       # n said m before b is one phoneme /n/ (دنبال donbāl = dombāl)
        out.append(p)
    return "".join(out)


DIGRAPH_FIRST = {"kh": "k", "gh": "g", "sh": "s", "zh": "z"}


def sounds_of(stem: str) -> list[str]:
    """The scheme's symbols of a reading with the diphthong ey as its two sounds (the scheme cannot tell khuneye's e+y from a diphthong)."""
    return [x for p in spelling.symbols(stem) for x in (("e", "y") if p == "ey" else (p,))]


def ends_on(letter: str, sound: str) -> bool:
    """Whether a stem whose last written letter is `letter` can end in `sound`."""
    from app.documentary.reading.reference import LETTERS

    if sound in LETTERS:
        return letter in LETTERS[sound]
    return (letter == "ه" and sound == "e") or (letter in "اآ" and sound == "ā") or (letter == "ی" and sound == "i") or (letter == "و" and sound in ("u", "o"))


def stem_of(word: Word, reading: str) -> tuple[str, str] | None:
    """(the stem's key, the stem's reading) of an inflected form written with a half-space (گلوله‌ها), when the suffix's sounds can be cut off the
    reading so that what is left ends in a sound the stem's last letter writes; None otherwise. A stem written with a silent ه keeps its e, which the
    vowel of the suffix takes in speech (خونه‌اش khunash: khune), and a two-letter symbol the scheme wrote across the cut is split (شلیک‌ها shelikhā:
    shelik)."""
    pieces = word.plain.split("‌")
    if len(pieces) < 2 or not pieces[0]:
        return None
    said = sounds_of(scheme.canonical(scheme.split_ezafe(scheme.split_pause(reading)[0])[0].replace("-", "").replace(" ", "").replace("'", "")))
    last = pieces[0][-1]
    for ending in sorted(ai_config.morphology.suffix_readings.get("".join(pieces[1:]), []), key=len, reverse=True):
        end = sounds_of(ending)
        if not end or len(said) <= len(end) or said[-len(end):] != end:
            continue
        stem = list(said[: -len(end)])
        if stem[-1] in DIGRAPH_FIRST and not ends_on(last, stem[-1]) and ends_on(last, DIGRAPH_FIRST[stem[-1]]):
            stem[-1] = DIGRAPH_FIRST[stem[-1]]
        if last == "ه" and end[0] in VOWELS and stem[-1] != "e" and not ends_on(last, stem[-1]):
            stem.append("e")
        if ends_on(last, stem[-1]):
            return key_of(pieces[0]), normal("".join(stem))
    return None


def variants(blocks: dict[str, list[tuple[Word, str]]], names: frozenset[str]) -> list[Variant]:
    """Every word read in more than one way across the blocks (numbers are read by code and left out). An inflected form written with a half-space
    also counts for its stem (گلوله‌ها for گلوله): the stem is read the same in all its forms."""
    homographs = {key_of(w) for w in ai_config.homographs.words} | {key_of(w) for w in ai_config.homographs.free_variants}
    seen: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for block_id, words in blocks.items():
        for word, reading in words:
            if word.number or reading == "NUM":
                continue
            seen[word.key][normal(reading)].append(block_id)
            stem = stem_of(word, reading)
            if stem is not None and stem[0] != word.key:
                seen[stem[0]][stem[1]].append(block_id)
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


def unresolved_names(found: list[Variant], table, fixed: dict[str, str]) -> dict[str, str]:
    """The names whose stored readings disagree (with each other or with the film table) and that neither the owner's approval nor a resolution of the
    case (`fixed` holds both) settles: their reading is unresolved. The film table's reading is the reading agent's, so it does not settle a disagreement by itself, and neither does how often a reading
    occurs."""
    out: dict[str, str] = {}
    for v in found:
        if v.kind == "name" and v.key not in fixed:
            table_reading = next((normal(r) for e in table.entries if len(e.keys) == 1 and e.keys[0] == v.key for r in e.readings), "")
            readings = " / ".join(f"{r} ({len(b)} blocks)" for r, b in v.readings.items())
            out[v.key] = f"the name was read {readings}; the film table says {table_reading or 'nothing'}, and the owner has not approved a reading"
    return out


def family(word: Word, table: dict[str, object]) -> str | None:
    """The key under which `table` (variants or unresolved readings) knows the word: its own, or its stem's for a form written with a half-space."""
    for key in (word.key, key_of(word.plain.split("‌")[0])):
        if key in table:
            return key
    return None
