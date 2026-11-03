"""What the voice is given for the words the owner has fixed: the word is written as its reading in Persian letters with vowel marks (config
reading.voice_form), every other word stays as written. Eleven v4 reads /IPA/ between slashes aloud as symbols in some blocks; the form "ipa" is
kept for a model that does not."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

from app.core.ai_config import ai_config
from app.documentary.reading.scheme import ezafe_form, split_ezafe, split_pause, to_ipa
from app.documentary.reading.spelling import final_h_for, persian
from app.documentary.reading.words import substitute, tokenize

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WordSignal:
    """What the reading stage knew about one word of a block: how sure the editor was, whether the checker and arbiter left a doubt, whether code had to
    overwrite the editor's reading (the film table)."""

    confidence: str = "high"
    doubted: bool = False
    overridden: bool = False


@dataclass(frozen=True)
class Voicing:
    fixed: dict[str, str]                       # plain word -> its reading (the owner's approvals of the language)
    readings: dict[str, list[str]]              # block -> the reading of each word (for the ezafe and the order of the words)
    signals: dict[str, list[WordSignal]] = field(default_factory=dict)      # block -> what the reading stage knew of each word
    names: frozenset[str] = frozenset()         # the plain forms of the film's people, places and foreign words

    def with_fixed(self, more: dict[str, str]) -> Voicing:
        return replace(self, fixed={**self.fixed, **more})

    def text(self, block_id: str, text: str) -> str:
        """The block's text with each fixed word as /ipa/. A block whose words are not the words of its reading is left as written."""
        words, readings = tokenize(text), self.readings.get(block_id)
        if readings is None:
            return text
        if len(readings) != len(words):
            log.warning("voicing: %s has %d words and %d readings; left as written", block_id, len(words), len(readings or []))
            return text
        chosen = {w.n: self.spell(self.reading_of(self.fixed[w.key], readings[w.n]), w.surface) for w in words if w.key in self.fixed and not w.number}
        return substitute(text, chosen)

    @staticmethod
    def spell(reading: str, surface: str = "") -> str:
        return persian(reading, final_h_for(surface)) if ai_config.reading.voice_form == "persian_marks" else f"/{to_ipa(reading)}/"

    @staticmethod
    def reading_of(fixed: str, reading: str) -> str:
        """The owner's reading of the word, with the ezafe the block's reading gives it."""
        return ezafe_form(fixed) if split_ezafe(split_pause(reading)[0])[1] else fixed
