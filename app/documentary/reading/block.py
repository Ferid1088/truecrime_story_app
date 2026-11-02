"""The input of the reading loop for one block: its words, the English source of its stretch of the story and the sentences around it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.documentary.reading.words import TAG, Word, tokenize


@dataclass
class BlockInput:
    block_id: str
    section: str
    text: str
    english: str = ""
    previous: str = ""
    next: str = ""
    words: list[Word] = field(init=False)

    def __post_init__(self):
        self.words = tokenize(self.text)

    def sentence(self) -> str:
        return re.sub(r"\s+", " ", TAG.sub(" ", self.text)).strip()


def block_inputs(blocks: list[dict], english: dict[str, str], neighbour_chars: int) -> list[BlockInput]:
    """The blocks of a film's performance plan as inputs, each with the end of the block before and the start of the block after it."""
    plain = [re.sub(r"\s+", " ", TAG.sub(" ", b.get("tts_text") or b["text"])).strip() for b in blocks]
    return [BlockInput(b["block_id"], b.get("section_id") or "", b.get("tts_text") or b["text"], english.get(b.get("section_id") or "", ""),
                       plain[i - 1][-neighbour_chars:] if i else "", plain[i + 1][:neighbour_chars] if i + 1 < len(blocks) else "")
            for i, b in enumerate(blocks)]
