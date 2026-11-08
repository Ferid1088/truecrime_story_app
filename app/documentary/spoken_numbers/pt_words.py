"""The word tables of Brazilian Portuguese numbers: cardinals with gender and the "e" rule, ordinals, digit and letter names, months, Roman numerals.
Brazilian forms only: short scale (bilhão is 10^9), "dezesseis/dezessete/dezenove", "catorze"."""

from __future__ import annotations

import re

UNITS = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez", "onze", "doze", "treze", "catorze", "quinze",
         "dezesseis", "dezessete", "dezoito", "dezenove"]
FEMININE_UNITS = {1: "uma", 2: "duas"}
TENS = {2: "vinte", 3: "trinta", 4: "quarenta", 5: "cinquenta", 6: "sessenta", 7: "setenta", 8: "oitenta", 9: "noventa"}
HUNDREDS = {1: "cento", 2: "duzentos", 3: "trezentos", 4: "quatrocentos", 5: "quinhentos", 6: "seiscentos", 7: "setecentos", 8: "oitocentos",
            9: "novecentos"}
# (value, singular, plural) from the largest; "mil" is handled apart because it takes no "um" and agrees in gender.
SCALES = [(10 ** 12, "trilhão", "trilhões"), (10 ** 9, "bilhão", "bilhões"), (10 ** 6, "milhão", "milhões")]
SCALE_WORDS = {word: value for value, singular, plural in SCALES for word in (singular, plural)} | {"mil": 1000}

ORDINAL_UNITS = ["", "primeiro", "segundo", "terceiro", "quarto", "quinto", "sexto", "sétimo", "oitavo", "nono"]
ORDINAL_TENS = {1: "décimo", 2: "vigésimo", 3: "trigésimo", 4: "quadragésimo", 5: "quinquagésimo", 6: "sexagésimo", 7: "septuagésimo",
                8: "octogésimo", 9: "nonagésimo"}
MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
LETTERS = {"A": "a", "B": "bê", "C": "cê", "D": "dê", "E": "é", "F": "efe", "G": "gê", "H": "agá", "I": "i", "J": "jota", "K": "cá", "L": "ele",
           "M": "eme", "N": "ene", "O": "ó", "P": "pê", "Q": "quê", "R": "erre", "S": "esse", "T": "tê", "U": "u", "V": "vê", "W": "dáblio",
           "X": "xis", "Y": "ípsilon", "Z": "zê"}
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
ROMAN = r"(?=[IVXLCDM])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"


def roman_value(numeral: str) -> int:
    total = 0
    for symbol, following in zip(numeral, numeral[1:] + " ", strict=True):
        value = ROMAN_VALUES[symbol]
        total += -value if ROMAN_VALUES.get(following, 0) > value else value
    return total


def digit_words(digits: str, six_as_meia: bool = False) -> str:
    """Each digit as its own word ("190" as "um nove zero"), for numbers a narrator spells out."""
    return " ".join("meia" if six_as_meia and digit == "6" else UNITS[int(digit)] for digit in digits)


def _hundreds_and_below(value: int, feminine: bool) -> str:
    """One group of three digits (1-999) in words; 100 alone is "cem", 101-199 "cento e ..."."""
    hundreds, rest = divmod(value, 100)
    parts: list[str] = []
    if hundreds:
        word = "cem" if value == 100 else HUNDREDS[hundreds]
        parts.append(word[:-2] + "as" if feminine and hundreds > 1 else word)
    if rest:
        if rest < 20:
            parts.append(FEMININE_UNITS.get(rest, UNITS[rest]) if feminine else UNITS[rest])
        else:
            tens, unit = divmod(rest, 10)
            parts.append(TENS[tens] + (" e " + (FEMININE_UNITS.get(unit, UNITS[unit]) if feminine else UNITS[unit]) if unit else ""))
    return " e ".join(parts)


def cardinal(value: int, feminine: bool = False, feminine_mil: bool = True) -> str:
    """A whole number in words. The groups are joined without "e" except before the last non-zero group when it is under 100 or a round hundred
    ("mil e cem", "dois mil e sete", "um milhão e duzentos mil"); milhão/bilhão never change gender; the thousands agree if `feminine_mil`."""
    if value == 0:
        return "zero"
    groups: list[tuple[int, int]] = []
    for scale in (10 ** 12, 10 ** 9, 10 ** 6, 1000, 1):
        group, value = divmod(value, scale)
        if group:
            groups.append((group, scale))
    words: list[str] = []
    for group, scale in groups:
        if scale == 1:
            words.append(_hundreds_and_below(group, feminine))
        elif scale == 1000:
            words.append("mil" if group == 1 else _hundreds_and_below(group, feminine and feminine_mil) + " mil")
        else:
            _, singular, plural = next(entry for entry in SCALES if entry[0] == scale)
            words.append(_hundreds_and_below(group, False) + " " + (singular if group == 1 else plural))
    last_group = groups[-1][0]
    if len(words) > 1 and (last_group < 100 or last_group % 100 == 0):
        words[-1] = "e " + words[-1]
    return " ".join(words)


def ordinal(value: int, feminine: bool = False) -> str:
    """1-99 and 100 as an ordinal ("vigésimo primeiro"); no "e", no hyphen. The gender is the mark's (º masculine, ª feminine)."""
    tens, unit = divmod(value, 10)
    if value == 100:
        word = "centésimo"
    else:
        word = " ".join(part for part in (ORDINAL_TENS.get(tens, ""), ORDINAL_UNITS[unit]) if part)
    return re.sub(r"o\b", "a", word) if feminine else word


def feminine_form(word: str) -> str:
    return re.sub(r"o\b", "a", word)
