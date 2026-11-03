"""The listening part of the reading loop. The local phoneme recogniser scores every word of a voiced block: how well the audio fits the word's
reading and how well it fits the forms a vowel away from it (the sounds the voice mixes up). A word that a wrong form fits clearly better is looked
at again: a model hears the clip of that one word and chooses which form it was. The word is a finding when it chooses a wrong form (or none),
a weak flag when it chooses the reading."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from app.core.ai_config import ai_config
from app.documentary.reading.chooser import WordChooser, variants
from app.documentary.reading.scheme import split_pause, to_ipa
from app.documentary.reading.words import EDGE_PUNCT, Word, key_of, tokenize

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Finding:
    block_id: str
    n: int
    surface: str
    expected: str           # the written reading
    said: str               # the form the model chose for the clip ("none": no form fitted)
    best: str               # the form the phoneme score fitted best
    margin: float           # how much better that form fitted than the reading


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


def best_form(fit, log_probs, start: float, end: float, reading: str) -> tuple[str, float]:
    """(the form that fits the audio best, how much better it fits than the reading) among the reading and its wrong forms."""
    edge, stem = ai_config.reading.fit_edge_s, split_pause(reading)[0]
    forms = [stem] + variants(stem)
    loss = {}
    for form in forms:
        try:
            loss[form] = fit(log_probs, start - edge, end + edge, to_ipa(form))
        except ValueError:                    # the window is too short for the sounds of this form
            continue
    if stem not in loss:
        return stem, 0.0
    best = min(loss, key=loss.get)
    return best, loss[stem] - loss[best]


class Inspector:
    def __init__(self, chooser: WordChooser, recogniser=None):
        from app.documentary.pronunciation import get_listener

        self.chooser, self.recogniser = chooser, recogniser or get_listener()

    async def inspect(self, block_id: str, text: str, readings: list[str], wav: Path, spoken: list[dict]) -> tuple[list[Finding], list[Finding]]:
        """(the findings, the weak flags) of one voiced block: `spoken` are the voice's words with their times in this wav."""
        words = tokenize(text)
        if len(readings) != len(words):
            raise ValueError(f"{block_id}: {len(words)} words and {len(readings)} readings")
        log_probs = await asyncio.to_thread(self.recogniser.log_probs, str(wav))
        where, found, weak = windows(spoken, words), [], []
        for w in words:
            if w.number or w.n not in where:
                continue
            start, end = where[w.n]
            best, margin = best_form(self.recogniser.fit, log_probs, start, end, readings[w.n])
            if margin < ai_config.reading.flag_margin:
                continue
            try:
                said = (await self.chooser.choose(wav, start, end, w.plain, split_pause(readings[w.n])[0], f"{block_id}:{w.n}"))["said"]
            except ValueError as e:               # no clip can be cut at this place: the word stays unchecked
                log.warning("%s word %d: %s", block_id, w.n, e)
                continue
            finding = Finding(block_id, w.n, w.plain, readings[w.n], said, best, round(margin, 1))
            (weak if said == split_pause(readings[w.n])[0] else found).append(finding)
        return found, weak
