"""The words of French numbers: cardinals, ordinals, Roman numerals, letters and the small word tables the French reader needs.

Spelling follows the traditional form (hyphens inside the tens and units, "et" without hyphens: "vingt et un", "quatre-vingt-dix-neuf"): a voice says
it the same as the 1990 rectified spelling, and the traditional one is what engines tokenize best. "vingt" and "cent" take an s only when they end the
number or stand before a noun scale (Academie, Questions de langue 57): "quatre-vingts", "deux cents", "deux cent un", "deux cents millions"."""

from __future__ import annotations

import re

SMALL = ("zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize")
TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}
SCALES = ((10**12, "billion"), (10**9, "milliard"), (10**6, "million"))
LETTER_NAMES = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", (
    "a", "bé", "cé", "dé", "e", "effe", "gé", "hache", "i", "ji", "ka", "elle", "emme", "enne", "o", "pé", "ku", "erre", "esse", "té", "u", "vé",
    "double vé", "iks", "i grec", "zède"), strict=True))
MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")
# Nouns that make a final "un" feminine ("vingt et une heures", "une victime"); anything else is masculine.
FEMININE_NOUNS = frozenset((
    "heure", "heures", "minute", "minutes", "seconde", "secondes", "semaine", "semaines", "année", "années", "journée", "journées", "matinée", "soirée",
    "fois", "personne", "personnes", "femme", "femmes", "fille", "filles", "victime", "victimes", "page", "pages", "ligne", "lignes", "balle", "balles",
    "arme", "armes", "voiture", "voitures", "rue", "rues", "nuit", "nuits", "plaie", "plaies", "trace", "traces", "blessure", "blessures", "pièce", "pièces",
    "cartouche", "cartouches", "douille", "douilles", "empreinte", "empreintes", "lettre", "lettres", "photo", "photos", "chambre", "chambres",
    "livre", "livres", "étape", "étapes", "dizaine", "dizaines", "centaine", "centaines"))
_ROMAN = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_LAST_WORD = re.compile(r"^(.*[ -])?([^ -]+)$")
_ORDINAL_STEMS = {"un": "unième", "cinq": "cinquième", "neuf": "neuvième", "trois": "troisième"}


def digits_words(digits: str) -> str:
    """Digit by digit ("un un deux"), for codes that are said one digit at a time."""
    return " ".join(SMALL[int(d)] for d in digits)


def _under_100(n: int, feminine: bool, plural_end: bool) -> str:
    if n < 17:
        return "une" if n == 1 and feminine else SMALL[n]
    if n < 20:
        return f"dix-{SMALL[n - 10]}"
    if n < 70:
        tens, unit = divmod(n, 10)
        if unit == 0:
            return TENS[tens]
        return f"{TENS[tens]} et {'une' if feminine else 'un'}" if unit == 1 else f"{TENS[tens]}-{SMALL[unit]}"
    if n < 80:
        return "soixante et onze" if n == 71 else f"soixante-{_under_100(n - 60, False, False)}"
    if n == 80:
        return "quatre-vingts" if plural_end else "quatre-vingt"
    return f"quatre-vingt-{_under_100(n - 80, feminine and n == 81, False)}"


def _under_1000(n: int, feminine: bool, plural_end: bool) -> str:
    hundreds, rest = divmod(n, 100)
    if not hundreds:
        return _under_100(rest, feminine, plural_end)
    head = "cent" if hundreds == 1 else f"{SMALL[hundreds]} cent"
    if not rest:
        return head + ("s" if hundreds > 1 and plural_end else "")
    return f"{head} {_under_100(rest, feminine, plural_end)}"


def cardinal(n: int, feminine: bool = False, plural_end: bool = True) -> str:
    """`n` in words. `plural_end` is whether a final "vingt"/"cent" takes its s (false for a year, whose "cent" never does)."""
    if n == 0:
        return SMALL[0]
    parts = []
    for scale, name in SCALES:
        count, n = divmod(n, scale)
        if count:
            parts.append(f"{_under_1000(count, False, True)} {name}{'s' if count > 1 else ''}")
    thousands, n = divmod(n, 1000)
    if thousands:
        parts.append("mille" if thousands == 1 else f"{_under_1000(thousands, False, False)} mille")
    if n:
        parts.append(_under_1000(n, feminine, plural_end))
    return " ".join(parts)


def ordinal(n: int, feminine: bool = False, plural: bool = False) -> str:
    """"premier"/"première" for 1, else the cardinal's last word with "-ième" ("vingt et unième", "cinquième", "neuvième", "quatre-vingtième")."""
    if n == 1:
        return ("première" if feminine else "premier") + ("s" if plural else "")
    head, last = _LAST_WORD.match(cardinal(n, plural_end=False)).groups()
    if last in _ORDINAL_STEMS:
        last = _ORDINAL_STEMS[last]
    elif last.endswith("e"):
        last = f"{last[:-1]}ième"
    else:
        last = f"{last.rstrip('s')}ième"
    return f"{head or ''}{last}" + ("s" if plural else "")


def roman_value(text: str) -> int | None:
    """The value of a canonical upper-case Roman numeral, or None when `text` is not one."""
    if not text or not _ROMAN.match(text):
        return None
    total = 0
    for char, following in zip(text, text[1:] + " ", strict=True):
        value = _ROMAN_VALUES[char]
        total += -value if following != " " and _ROMAN_VALUES[following] > value else value
    return total
