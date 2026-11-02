"""An agent's reading of a block, in a fixed shape, and the rules code enforces on it: the same words in the same order, and the film table's
readings for the names (code overwrites them, an agent cannot get them wrong)."""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.documentary.reading import scheme
from app.documentary.reading.table import FilmTable
from app.documentary.reading.words import NUMBER_WORD, Word


@dataclass(frozen=True)
class ReadingWord:
    n: int
    latin: str
    pause_after: bool = False
    why: str = ""
    confidence: str = "high"
    question: str = ""


class ReadingShapeError(ValueError):
    """The answer does not have the words of the block, once each, in their order. `problems` says what is wrong; the writer is asked again."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems[:6]))


def parse(raw: dict, words: list[Word]) -> list[ReadingWord]:
    got = raw.get("words") if isinstance(raw, dict) else None
    if not isinstance(got, list):
        raise ReadingShapeError(["the answer has no list of words"])
    problems: list[str] = []
    if len(got) != len(words):
        problems.append(f"{len(got)} words answered for the {len(words)} words of the block")
    out: list[ReadingWord] = []
    for item, word in zip(got, words, strict=False):
        if not isinstance(item, dict) or item.get("n") != word.n:
            problems.append(f"word {word.n} ({word.plain}) is not answered in its place")
            continue
        latin, pause = scheme.split_pause(str(item.get("latin") or ""))
        if not latin and not word.number:
            problems.append(f"word {word.n} ({word.plain}) has no reading")
            continue
        out.append(ReadingWord(word.n, NUMBER_WORD if word.number else latin, pause or bool(item.get("pause_after")), str(item.get("why") or ""),
                               str(item.get("confidence") or "high"), str(item.get("question") or "")))
    if problems:
        raise ReadingShapeError(problems)
    return out


def enforce(readings: list[ReadingWord], words: list[Word], table: FilmTable, pause_between_names: bool) -> tuple[list[ReadingWord], list[str]]:
    """The readings with the table applied: a name is read as the table says, and two persons side by side are separated by a pause. Returns
    the readings and a note for each word code changed."""
    fixed = table.fixed_readings(words)
    pauses = table.pauses(words) if pause_between_names else set()
    notes: list[str] = []
    out: list[ReadingWord] = []
    for r in readings:
        latin, pause = fixed.get(r.n, r.latin), r.pause_after
        if r.n in fixed and not scheme.same_reading(r.latin, latin):
            notes.append(f"word {r.n} ({words[r.n].plain}): {r.latin} -> {latin} (the film table)")
        if r.n in pauses and not pause:
            notes.append(f"word {r.n} ({words[r.n].plain}): a pause between two names")
            pause = True
        out.append(replace(r, latin=latin, pause_after=pause))
    return out, notes
