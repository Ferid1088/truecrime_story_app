"""What the voice is given for the words the owner has fixed: the word is written as its IPA between slashes, the form Eleven v4 reads as
sounds (its pronunciation dictionary, vowel marks and Latin letters do not reach the sound). Every other word stays as written."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.documentary.reading.scheme import ezafe_form, split_ezafe, split_pause, to_ipa
from app.documentary.reading.words import EDGE_PUNCT, TAG, tokenize

log = logging.getLogger(__name__)
PIECE = re.compile(r"\S+")


@dataclass(frozen=True)
class Voicing:
    fixed: dict[str, str]                       # plain word -> its reading (the owner's approvals of the language)
    readings: dict[str, list[str]]              # block -> the reading of each word (for the ezafe and the order of the words)

    def text(self, block_id: str, text: str) -> str:
        """The block's text with each fixed word as /ipa/. A block whose words are not the words of its reading is left as written."""
        words, readings = tokenize(text), self.readings.get(block_id)
        if readings is None:
            return text
        if len(readings) != len(words):
            log.warning("voicing: %s has %d words and %d readings; left as written", block_id, len(words), len(readings or []))
            return text
        chosen = {w.n: f"/{to_ipa(self.reading_of(self.fixed[w.key], readings[w.n]))}/" for w in words if w.key in self.fixed and not w.number}
        count = iter(range(len(words)))

        def piece(token: re.Match) -> str:
            core = token.group().strip(EDGE_PUNCT)
            if not any(ch.isalnum() for ch in core):
                return token.group()
            return token.group().replace(core, chosen.get(next(count), core), 1)

        return "".join(PIECE.sub(piece, part) if not TAG.fullmatch(part) else part for part in re.split(f"({TAG.pattern})", text))

    @staticmethod
    def reading_of(fixed: str, reading: str) -> str:
        """The owner's reading of the word, with the ezafe the block's reading gives it."""
        return ezafe_form(fixed) if split_ezafe(split_pause(reading)[0])[1] else fixed
