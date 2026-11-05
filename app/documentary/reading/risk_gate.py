"""Which blocks the independent (blind) checker reads: only a block where the editor is itself unsure of a word. The classifier can miss an error, so a block that
is not checked is not taken as right: code validates every word and the voiced narration is listened to afterwards."""

from __future__ import annotations

from app.documentary.reading.answer import ReadingWord
from app.documentary.reading.block import BlockInput

LOW = ("low", "medium")          # the labels an agent gives its own ezafe judgement when it is not sure


def unsure_words(item: BlockInput, readings: list[ReadingWord], fixed: dict[int, str]) -> list[int]:
    """The words of the block the editor is unsure of (not a number: code reads those; not a name of the film table: code fixes those)."""
    out = []
    for r in readings:
        if r.n in fixed or item.words[r.n].number:
            continue
        ezafe = r.ezafe or {}
        unsure_ezafe = any(isinstance(v, dict) and v.get("confidence") in LOW for v in ezafe.values())
        if r.confidence != "high" or r.question or unsure_ezafe:
            out.append(r.n)
    return out
