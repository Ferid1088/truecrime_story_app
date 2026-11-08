"""Italian number words: cardinals, ordinals, roman numerals and the agreement of "un/uno/una" and "ventun/ventuno" with the noun they count.

Written here instead of taken from num2words because the narrator's rules differ from its output: a number below a million is one word with no
"e" ("un milione duecentomila", num2words says "un milione e duecentomila"), and only a tens word loses its vowel before uno/otto (centootto,
not centotto). Units the narrator says as nouns (milione, miliardo) stay separate words."""

from __future__ import annotations

import re

UNITS = ("zero", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove")
TEENS = ("dieci", "undici", "dodici", "tredici", "quattordici", "quindici", "sedici", "diciassette", "diciotto", "diciannove")
TENS = (None, None, "venti", "trenta", "quaranta", "cinquanta", "sessanta", "settanta", "ottanta", "novanta")
ORDINALS_TO_TEN = (None, "primo", "secondo", "terzo", "quarto", "quinto", "sesto", "settimo", "ottavo", "nono", "decimo")
SCALES = ((10 ** 9, "miliardo", "miliardi"), (10 ** 6, "milione", "milioni"))
ELIDING_ONES = frozenset(range(21, 100, 10))      # ventuno ... novantuno: the only forms that lose their o before a counted noun

# Words after a number that are not the thing counted ("1 e mezzo", "2 di loro"): the number keeps its plain form before them.
FUNCTION_WORDS = frozenset(
    ["e", "ed", "o", "a", "ad", "da", "di", "in", "su", "con", "per", "tra", "fra", "ma", "il", "lo", "la", "i", "gli", "le", "l", "un", "uno", "una", "che", "come", "se", "non", "poi", "sono", "era", "è", "del", "della", "dei", "delle", "al", "alla", "ai", "alle", "dal", "dalla", "nel", "nella", "sul", "sulla", "si", "ha", "hanno", "ho"])
# Feminine nouns the ending does not show; the ones in -a that are masculine.
FEMININE_WORDS = frozenset(
    ["classe", "serie", "specie", "notte", "fine", "mano", "moto", "foto", "auto", "legge", "chiave", "croce", "sede", "luce", "parte", "morte", "gente", "lite", "internazionale", "mani"])
MASCULINE_A_WORDS = frozenset(
    ["problema", "sistema", "programma", "poeta", "pilota", "collega", "cinema", "tema", "dilemma", "schema", "diploma", "clima", "panorama", "pianeta", "profeta", "atleta", "papa", "omicida"])
FEMININE_ENDINGS = ("zione", "zioni", "sione", "sioni", "gione", "gioni", "tà", "tù")
IMPURE_S = re.compile(r"(?:z|gn|ps|pn|x|y|s[b-df-hj-np-tv-z])")
VOWEL_START = re.compile(r"[aeiouàèéìòù]")
ROMAN = re.compile(r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})")
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _below_hundred(n: int) -> str:
    if n < 10:
        return UNITS[n]
    if n < 20:
        return TEENS[n - 10]
    tens, unit = divmod(n, 10)
    word = TENS[tens]
    if unit in (1, 8):
        word = word[:-1]
    return word + (UNITS[unit] if unit else "")


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    word = "" if not hundreds else ("cento" if hundreds == 1 else UNITS[hundreds] + "cento")
    return word + (_below_hundred(rest) if rest else "")


def _below_million(n: int) -> str:
    thousands, rest = divmod(n, 1000)
    word = "" if not thousands else ("mille" if thousands == 1 else _below_thousand(thousands) + "mila")
    return word + (_below_thousand(rest) if rest else "")


def _accented(word: str) -> str:
    """ventitré, centotré, milletré: "tre" ends a compound with a stressed, written accent (the bare 3 stays "tre")."""
    return word[:-3] + "tré" if word.endswith("tre") and word != "tre" else word


def cardinal(n: int, accent: bool = True) -> str:
    if n == 0:
        return "zero"
    groups = []
    for size, singular, plural in SCALES:
        count, n = divmod(n, size)
        if count == 1:
            groups.append(f"un {singular}")
        elif count:
            word = _below_million(count)
            if count % 100 in ELIDING_ONES:
                word = word[:-1]
            groups.append(f"{_accented(word) if accent else word} {plural}")
    if n:
        word = _below_million(n)
        groups.append(_accented(word) if accent else word)
    return " ".join(groups)


def ordinal(n: int, feminine: bool = False) -> str | None:
    """primo ... decimo, then the cardinal without its last vowel plus -esimo (tre and sei keep theirs, without the accent); None above 999,999."""
    if n < 1 or n >= 10 ** 6:
        return None
    if n <= 10:
        word = ORDINALS_TO_TEN[n]
    else:
        base = cardinal(n, accent=False)
        if base == "mille":
            word = "millesimo"
        elif base.endswith("mila"):
            word = base[:-4] + "millesimo"
        else:
            word = (base if base.endswith(("tre", "sei")) else base[:-1]) + "esimo"
    return word[:-1] + "a" if feminine else word


def roman_value(token: str) -> int | None:
    if not token or not ROMAN.fullmatch(token):
        return None
    total, highest = 0, 0
    for char in reversed(token):
        value = ROMAN_VALUES[char]
        total += value if value >= highest else -value
        highest = max(highest, value)
    return total


def split_amount(text: str) -> tuple[int, str]:
    """"1.500,50" -> (1500, "50"); "12.5" -> (12, "5"); "1.500" -> (1500, ""): a dot with 1-2 digits after it is a decimal mark, with 3 it is a
    thousands mark."""
    if "," in text:
        whole, frac = text.split(",")
    elif re.search(r"\.\d{1,2}$", text):
        whole, frac = text.rsplit(".", 1)
    else:
        whole, frac = text, ""
    return int(whole.replace(".", "")), frac


def is_feminine(noun: str, plural: bool) -> bool:
    word = noun.lower()
    if word in FEMININE_WORDS:
        return True
    if word in MASCULINE_A_WORDS:
        return False
    if word.endswith(FEMININE_ENDINGS):
        return True
    return word.endswith("e" if plural else "a")


def counted(n: int, noun: str) -> str:
    """The number with the noun it counts, as the noun asks: un anno, uno studente, una donna, un'ora, ventun anni, ventuno coltellate (feminine
    nouns keep the full -uno), tre colpi. Before a word that is not a noun (e, di, ...) the plain cardinal."""
    word = noun.split()[0].lower()
    if word in FUNCTION_WORDS:
        return f"{cardinal(n)} {noun}"
    feminine = is_feminine(word, n != 1)
    impure = bool(IMPURE_S.match(word))
    if n == 1:
        if feminine:
            return f"un'{noun}" if VOWEL_START.match(word) else f"una {noun}"
        return f"{'uno' if impure else 'un'} {noun}"
    if n % 100 in ELIDING_ONES and not feminine and not impure:
        return f"{cardinal(n)[:-1]} {noun}"
    return f"{cardinal(n)} {noun}"


class Speaker:
    """The number words bound to the configured maximum: a number above it is said digit by digit rather than guessed."""

    def __init__(self, max_value: int):
        self.max_value = max_value

    def digits(self, text: str) -> str:
        return " ".join(UNITS[int(char)] for char in text if char.isdigit())

    def integer(self, value: int) -> str:
        return cardinal(value) if value <= self.max_value else self.digits(str(value))

    def decimal(self, whole: int, frac: str, as_number_up_to: int) -> str:
        """"3,5" tre virgola cinque; "0,05" zero virgola zero cinque; "3,50" tre virgola cinquanta; more decimals than `as_number_up_to`: digit by digit."""
        if not frac:
            return self.integer(whole)
        zeros = len(frac) - len(frac.lstrip("0"))
        rest = frac[zeros:]
        if rest and len(frac) <= as_number_up_to:
            spoken = " ".join(["zero"] * zeros + [cardinal(int(rest))])
        else:
            spoken = self.digits(frac)
        return f"{self.integer(whole)} virgola {spoken}"

    def age(self, years: int, ending: str) -> str:
        """35 + "e": trentacinquenne; the cardinal without its last vowel plus -enne/-enni (tre and sei keep theirs: treenne, trentaseienne)."""
        base = cardinal(years, accent=False)
        return (base if base.endswith(("tre", "sei")) else base[:-1]) + "enn" + ending
