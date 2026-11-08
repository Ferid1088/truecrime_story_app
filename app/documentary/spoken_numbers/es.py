"""Mexican Spanish numbers for the voice: the digits of a text as the words a documentary narrator says. The rules are in the es_* modules;
`Options` (es_speech) holds the choices a project owner may flip."""

from __future__ import annotations

import re

from app.documentary.spoken_numbers.base import LanguageNumbers
from app.documentary.spoken_numbers.es_calendar import Clock, Dates, Decades, RomanNumerals
from app.documentary.spoken_numbers.es_codes import EmergencyNumbers, Phones, PostalCodes
from app.documentary.spoken_numbers.es_lexicon import SCALES
from app.documentary.spoken_numbers.es_measures import (
    Calibres,
    Coordinates,
    Fractions,
    Percentages,
    Ranges,
    Units,
)
from app.documentary.spoken_numbers.es_money import Currencies
from app.documentary.spoken_numbers.es_ordinals import NumberSign, Ordinals
from app.documentary.spoken_numbers.es_speech import CLOSE, SIGNED_NUMBER, Options, Rule, Speech


class PlainNumbers(Rule):
    """Every figure left, as a cardinal: "21 mujeres" is "veintiuna", "21 años" "veintiún", "5 millones de" "cinco millones de", "-5" "menos cinco",
    "1,250.50" "mil doscientos cincuenta punto cincuenta". A whole number of millions gets its "de" before the noun."""

    PATTERN = re.compile(rf"{SIGNED_NUMBER}(?:\s+(?P<scale>{'|'.join(sorted(SCALES, key=len, reverse=True))})(?![\w{CLOSE}]))?(?![\w{CLOSE}])(?![.,]\d)")

    def apply(self, text: str) -> str:
        return self.PATTERN.sub(self._number, text)

    def _number(self, match: re.Match) -> str:
        speech, end, sign = self.speech, match.end(), bool(match["sign"])
        gender = speech.gender_after(match.string, end)
        if match["scale"]:
            multiplier, scale_word = SCALES[match["scale"]]
            words, value = speech.scaled(match["num"], multiplier, scale_word, gender)
            return speech.with_de(match.string, end, ("menos " if sign else "") + words, value)
        words, _ = speech.number(match["num"], gender, False, sign)
        parsed = speech.parse(match["num"])
        return speech.with_de(match.string, end, words, parsed[0] if parsed and parsed[1] is None else None)


class Numbers(LanguageNumbers):
    """The numbers of a text in Mexican Spanish words. `options` is the language's block of config voice.number_words (see `Options`)."""

    def __init__(self, options: dict, max_value: int):
        super().__init__(options, max_value)
        speech = Speech(Options.model_validate(options), max_value)
        # Order matters: a rule that speaks a number removes its digits, and specific forms (dates, money, phones) run before the generic ones.
        self.rules: tuple[Rule, ...] = tuple(rule(speech) for rule in (
            RomanNumerals, EmergencyNumbers, Phones, PostalCodes, Dates, Decades, Clock, Coordinates, Ordinals, NumberSign, Currencies, Percentages,
            Calibres, Ranges, Fractions, Units, PlainNumbers))

    def convert(self, text: str) -> str:
        text = text.replace(" ", " ")
        for rule in self.rules:
            text = rule.apply(text)
        return text
