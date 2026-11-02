"""The listening part of the reading loop. A voiced block is listened to twice: a model writes in Latin letters what it heard (blind: it only has the
plain words), and the local phoneme recogniser (no language-model habit) writes the phones. A word the blind listener heard differently from its
reading is a candidate; it is a finding when the recogniser, in the word's own time window, also hears it differently. A listener that guesses and
a recogniser that is noisy rarely make the same mistake on the same word."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from app.core.ai_config import ai_config
from app.documentary.reading import phones as PH
from app.documentary.reading.listener import BlindListener
from app.documentary.reading.words import EDGE_PUNCT, Word, key_of, tokenize


@dataclass(frozen=True)
class Finding:
    block_id: str
    n: int
    surface: str
    expected: str           # the written reading
    heard: str              # what the blind listener wrote
    phones: str             # the sounds the recogniser found in the word's window, as the short alphabet


def windows(spoken: list[dict], words: list[Word]) -> dict[int, tuple[float, float]]:
    """The time window of each word of the text: by place when the voice said as many words as the text has, else by the words that match."""
    if len(spoken) == len(words):
        return {w.n: (s["start"], s["end"]) for w, s in zip(words, spoken, strict=True)}
    keys = [key_of(s["word"].strip(EDGE_PUNCT)) for s in spoken]
    found: dict[int, tuple[float, float]] = {}
    for tag, a0, a1, b0, _ in SequenceMatcher(a=[w.key for w in words], b=keys, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(a1 - a0):
                found[words[a0 + k].n] = (spoken[b0 + k]["start"], spoken[b0 + k]["end"])
    return found


def candidates(words: list[Word], readings: list[str], heard: dict[int, str]) -> list[int]:
    """The words the blind listener heard differently from their readings (digits are read by code; a word it did not write down is no candidate)."""
    return [w.n for w in words if not w.number and w.n in heard and not PH.same_heard(readings[w.n], heard[w.n], w.plain)]


def confirm(block_id: str, words: list[Word], readings: list[str], heard: dict[int, str], spoken: list[dict],
            frames: list[tuple[str, float, float]]) -> tuple[list[Finding], list[Finding]]:
    """(the findings, the weak flags). A finding is a candidate that the recogniser also hears differently (vowels and the consonants inside the
    word); a weak flag is a candidate that the recogniser hears right: one listener doubts it, the other does not, so it is only reported."""
    if len(readings) != len(words):
        raise ValueError(f"{block_id}: {len(words)} words and {len(readings)} readings")
    edge, where, found, weak = ai_config.reading.window_edge_s, windows(spoken, words), [], []
    for n in candidates(words, readings, heard):
        if n not in where:
            continue
        start, end = where[n]
        verdict = PH.compare(readings[n], [p for p, s, e in frames if e > start - edge and s < end + edge], consonants=True)
        (weak if verdict.ok else found).append(Finding(block_id, n, words[n].plain, readings[n], heard[n], verdict.heard))
    return found, weak


class Inspector:
    def __init__(self, blind: BlindListener, recogniser=None):
        from app.documentary.pronunciation import get_listener

        self.blind, self.recogniser = blind, recogniser or get_listener()

    async def inspect(self, block_id: str, text: str, readings: list[str], wav: Path, spoken: list[dict]) -> tuple[list[Finding], list[Finding]]:
        """(the findings, the weak flags) of one voiced block: `spoken` are the voice's words with their times in this wav."""
        words = tokenize(text)
        heard, frames = await asyncio.gather(self.blind.hear(wav, words, block_id), asyncio.to_thread(self.recogniser.frames, str(wav)))
        return confirm(block_id, words, readings, heard, spoken, frames)
