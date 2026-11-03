"""What the voice is given for the words the owner has fixed: the word is written as its reading in Persian letters with vowel marks (config
reading.voice_form), every other word stays as written. Eleven v4 reads /IPA/ between slashes aloud as symbols in some blocks; the form "ipa" is
kept for a model that does not."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.ai_config import ai_config
from app.documentary.reading.scheme import ezafe_form, split_ezafe, split_pause, to_ipa
from app.documentary.reading.spelling import persian
from app.documentary.reading.words import substitute, tokenize

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Voicing:
    fixed: dict[str, str]                       # plain word -> its reading (the owner's approvals of the language)
    readings: dict[str, list[str]]              # block -> the reading of each word (for the ezafe and the order of the words)

    def with_fixed(self, more: dict[str, str]) -> Voicing:
        return Voicing({**self.fixed, **more}, self.readings)

    def text(self, block_id: str, text: str) -> str:
        """The block's text with each fixed word as /ipa/. A block whose words are not the words of its reading is left as written."""
        words, readings = tokenize(text), self.readings.get(block_id)
        if readings is None:
            return text
        if len(readings) != len(words):
            log.warning("voicing: %s has %d words and %d readings; left as written", block_id, len(words), len(readings or []))
            return text
        chosen = {w.n: self.spell(self.reading_of(self.fixed[w.key], readings[w.n])) for w in words if w.key in self.fixed and not w.number}
        return substitute(text, chosen)

    @staticmethod
    def spell(reading: str) -> str:
        return persian(reading) if ai_config.reading.voice_form == "persian_marks" else f"/{to_ipa(reading)}/"

    @staticmethod
    def reading_of(fixed: str, reading: str) -> str:
        """The owner's reading of the word, with the ezafe the block's reading gives it."""
        return ezafe_form(fixed) if split_ezafe(split_pause(reading)[0])[1] else fixed
