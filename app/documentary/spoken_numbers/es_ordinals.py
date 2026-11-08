"""Ordinals written with a mark (1.º, 1.ª, 1.er, 3.o, 2do, 5to) and the "N.º" sign."""

from __future__ import annotations

import re

from app.documentary.spoken_numbers.es_lexicon import FUNCTION_WORDS, gender_of
from app.documentary.spoken_numbers.es_speech import CLOSE, OPEN, START, Rule
from app.documentary.spoken_numbers.es_words import ordinal

FEMININE_MARKS = frozenset({"ª", "ra", "da", "ta", "va", "ma", "era", "as", "a"})


class Ordinals(Rule):
    """A figure with an ordinal mark is an ordinal; a plain figure never is ("el piso 15" is "quince"). The gender comes from the mark (ª is
    feminine, º and "er" masculine) or, for the ambiguous "°" and a bare "o", from the next word; "primero"/"tercero" lose the "o" before a
    masculine noun. Up to `marked_ordinals_up_to` the ordinal is spoken, above it the cardinal. "90°" is degrees, so "°" is an ordinal only up to
    ten and before a lowercase word."""

    PATTERN = re.compile(rf"{START}(?P<num>\d{{1,3}})(?:\.)?(?P<mark>[º°ª]|era|ero|er|ra|ro|da|do|ta|to|va|vo|ma|mo|os|as|(?<=\.)[oa])(?![\w{CLOSE}])")

    def apply(self, text: str) -> str:
        return self.PATTERN.sub(self._ordinal, text)

    def _ordinal(self, match: re.Match) -> str:
        value, mark, text = int(match["num"]), match["mark"], match.string
        following = self.speech.next_word(text, match.end())
        if value == 0 or (mark == "°" and (value > 10 or not following or not following.islower())):
            return match.group()
        feminine = mark in FEMININE_MARKS or (mark == "°" and bool(following) and gender_of(following) == "f")
        plural = mark in ("os", "as")
        has_noun = bool(following) and following.lower() not in FUNCTION_WORDS
        if value > self.speech.options.marked_ordinals_up_to:
            return self.speech.cardinal(value, ("f" if feminine else "m") if has_noun else None)
        return ordinal(value, feminine, plural, apocope=mark == "er" or has_noun)


class NumberSign(Rule):
    """"N.º 5", "Nº 5", "núm. 5", "nro. 5", "No. 5" and "#5" as "número cinco"; the number itself is read by the generic rule."""

    PATTERN = re.compile(rf"(?<![\w{OPEN}])(?:(?P<plural>Nos\.)|[Nn]\.?\s?[º°]\.?|[Nn]úm\.|[Nn]ro\.|No\.|#)\s?(?=\d)")

    def apply(self, text: str) -> str:
        return self.PATTERN.sub(lambda m: "números " if m["plural"] else "número ", text)
