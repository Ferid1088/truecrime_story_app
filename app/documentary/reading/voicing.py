"""What the voice is given for the words the owner has fixed: the word is written as its reading in Persian letters with vowel marks (config
reading.voice_form), every other word stays as written. Eleven v4 reads /IPA/ between slashes aloud as symbols in some blocks; the form "ipa" is
kept for a model that does not."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

from app.core.ai_config import ai_config
from app.documentary.reading.ezafe import Opinion
from app.documentary.reading.scheme import ezafe_form, split_ezafe, split_pause, to_ipa
from app.documentary.reading.spelling import final_h_for, persian
from app.documentary.reading.words import VOWEL_MARKS, substitute, tokenize

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
    opinions: dict[str, dict[int, list[Opinion]]] = field(default_factory=dict)     # block -> word -> the reading agents' own ezafe judgements
    variants: dict[str, tuple[str, ...]] = field(default_factory=dict)           # word -> its readings, for a word the film reads in more than one way
    unresolved: dict[str, str] = field(default_factory=dict)                     # word -> why its reading is unresolved (a name read two ways, no owner approval)
    boundaries: dict[str, dict[int, dict]] = field(default_factory=dict)        # block -> word -> the punctuation stage's verified ezafe boundary after it

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
        chosen = {w.n: self.spell(self.reading_of(self.fixed[w.key], readings[w.n], w.surface), w.surface) for w in words if w.key in self.fixed and not w.number}
        return substitute(text, chosen)

    @staticmethod
    def spell(reading: str, surface: str = "") -> str:
        """The word as the voice is given an approved reading: the reading's marks on the word's own letters when they can carry it (the validated
        REFERENCE_IRAB without its sukuns: no letter, half-space or ه of the word is lost), else the reading written in Persian letters (a spoken form the
        letters do not show: پرت said pat; and a word that starts with ayn, which the voice read "ohd" with marks and said right as اَهد)."""
        if ai_config.reading.voice_form != "persian_marks":
            return f"/{to_ipa(reading)}/"
        if surface and not VOWEL_MARKS.sub("", surface).startswith("ع"):      # an initial ayn: the owner heard the respelling with alef work (اَهد)
            from app.documentary.reading.reference import minimal, reference_of

            ref = reference_of(tokenize(surface)[0], reading)
            if ref is not None and ref.valid and not ref.added_carrier and ref.irab:
                return minimal(ref.irab)
        return persian(reading, final_h_for(surface))

    @staticmethod
    def reading_of(fixed: str, reading: str, surface: str = "") -> str:
        """The owner's reading of the word without the ezafe it may have been answered with (an ezafe belongs to its sentence: an approval of
        fevriye-ye does not put one after every فوریه), with the ezafe this block's reading gives it."""
        from app.documentary.reading.table import hyphenated

        base = split_ezafe(split_pause(hyphenated(VOWEL_MARKS.sub("", surface), fixed) if surface else fixed)[0])[0]
        return ezafe_form(base) if split_ezafe(split_pause(reading)[0])[1] else base
