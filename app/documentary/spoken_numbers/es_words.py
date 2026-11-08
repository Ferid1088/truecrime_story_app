"""Spanish number words: cardinals with the apocope and gender a narrator uses, ordinals, Roman numerals and the digit names. Pure language, no text
handling; the rules that find numbers in a text live in the other es_* modules."""

from __future__ import annotations

import re

ONES = ("cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce", "trece", "catorce", "quince",
        "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidós", "veintitrés", "veinticuatro", "veinticinco",
        "veintiséis", "veintisiete", "veintiocho", "veintinueve")
TENS = {3: "treinta", 4: "cuarenta", 5: "cincuenta", 6: "sesenta", 7: "setenta", 8: "ochenta", 9: "noventa"}
HUNDREDS = ("", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos", "ochocientos", "novecientos")
DIGITS = ONES[:10]
# Long scale: 10^6 millón, 10^12 billón, 10^18 trillón (10^9 is "mil millones").
SCALES = (("millón", "millones"), ("billón", "billones"), ("trillón", "trillones"))
MILLION = 10 ** 6

ORDINAL_ONES = ("", "primero", "segundo", "tercero", "cuarto", "quinto", "sexto", "séptimo", "octavo", "noveno")
ORDINAL_TENS = {1: "décimo", 2: "vigésimo", 3: "trigésimo", 4: "cuadragésimo", 5: "quincuagésimo", 6: "sexagésimo", 7: "septuagésimo",
                8: "octogésimo", 9: "nonagésimo"}
APOCOPE = {"primero": "primer", "tercero": "tercer"}

ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
ROMAN = re.compile(r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})")


def roman_value(numeral: str) -> int | None:
    """The value of a well-formed Roman numeral, else None (so "MIX" is a number and "XIIII" or "CIVIL" are not)."""
    if not numeral or not ROMAN.fullmatch(numeral):
        return None
    total = 0
    for char, following in zip(numeral, numeral[1:] + " ", strict=True):
        value = ROMAN_VALUES[char]
        total += -value if ROMAN_VALUES.get(following, 0) > value else value
    return total


def one_form(word: str, gender: str | None) -> str:
    """"uno"/"veintiuno" as the narrator says them before a noun: un/una, veintiún/veintiuna. gender None means no noun follows: "uno"."""
    if gender is None or not word.endswith("uno"):
        return word
    stem = word[:-3]
    if gender == "f":
        return stem + "una"
    return stem + ("ún" if stem else "un")


class Cardinals:
    """Spanish cardinals. `gender` is "m" (un, doscientos), "f" (una, doscientas) or None (a bare count: "uno"). Over "mil" agreement is optional in
    Spanish, so the feminine of "veintiún mil" and of "doscientos mil" are options."""

    def __init__(self, feminine_one_before_thousand: bool, feminine_hundreds_before_thousand: bool):
        self.feminine_one = feminine_one_before_thousand
        self.feminine_hundreds = feminine_hundreds_before_thousand

    def words(self, value: int, gender: str | None = None) -> str:
        if value == 0:
            return ONES[0]
        chunks = []
        while value:
            value, chunk = divmod(value, MILLION)
            chunks.append(chunk)
        parts = []
        for scale in reversed(range(len(chunks))):
            if chunks[scale]:
                parts.append(self._chunk(chunks[scale], gender) if scale == 0 else self._scaled(chunks[scale], scale))
        return " ".join(parts)

    def _scaled(self, chunk: int, scale: int) -> str:
        singular, plural = SCALES[scale - 1]
        if chunk == 1:
            return "un " + singular
        return self._chunk(chunk, "m") + " " + plural

    def _chunk(self, chunk: int, gender: str | None) -> str:
        thousands, rest = divmod(chunk, 1000)
        parts = []
        if thousands == 1:
            parts.append("mil")
        elif thousands:
            feminine = gender == "f"
            parts.append(self._hundreds(thousands, "f" if feminine and self.feminine_one else "m", feminine and self.feminine_hundreds) + " mil")
        if rest:
            parts.append(self._hundreds(rest, gender, gender == "f"))
        return " ".join(parts)

    def _hundreds(self, value: int, gender: str | None, feminine_hundreds: bool) -> str:
        hundreds, rest = divmod(value, 100)
        if value == 100:
            return "cien"
        parts = []
        if hundreds:
            word = HUNDREDS[hundreds]
            parts.append(word[:-2] + "as" if feminine_hundreds and hundreds > 1 else word)
        if rest:
            parts.append(self._below_hundred(rest, gender))
        return " ".join(parts)

    @staticmethod
    def _below_hundred(value: int, gender: str | None) -> str:
        if value < 30:
            return one_form(ONES[value], gender)
        tens, unit = divmod(value, 10)
        return TENS[tens] if not unit else f"{TENS[tens]} y {one_form(ONES[unit], gender)}"


def ordinal(value: int, feminine: bool = False, plural: bool = False, apocope: bool = False) -> str:
    """The ordinal word for 1..100 ("décimo primero", "vigésimo tercer" with apocope before a masculine noun)."""
    if value == 100:
        words = ["centésimo"]
    else:
        tens, unit = divmod(value, 10)
        words = ([ORDINAL_TENS[tens]] if tens else []) + ([ORDINAL_ONES[unit]] if unit else [])
    if apocope and not feminine and not plural and words[-1] in APOCOPE:
        words[-1] = APOCOPE[words[-1]]
    ending = ("a" if feminine else "o") + ("s" if plural else "")
    return " ".join(word if word in APOCOPE.values() else word[:-1] + ending for word in words)


def digits_words(digits: str) -> str:
    """Each digit by its name ("911" as "nueve uno uno")."""
    return " ".join(DIGITS[int(digit)] for digit in digits)
