"""What every French rule shares: the options, the shape of a written number and the way one number is said."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.documentary.spoken_numbers import fr_words as W

SP = "[    ]"
# A written number: space / NBSP / narrow NBSP grouping by threes, a dot grouping by threes ("1.500"), English commas ("1,500,000", "1,200.50"), a
# decimal comma or point. A space joins only a 1-3 digit head to groups of exactly three, so "2007 100" stays two numbers.
NUM = (r"(?<!\d)(?<!\d[.,])(?:\d{1,3}(?:,\d{3})+\.\d+|\d{1,3}(?:,\d{3}){2,}|\d{1,3}(?:" + SP + r"\d{3})+|\d{1,3}(?:\.\d{3})+|\d+)(?:[,.]\d+)?(?!\d)")
NUM_ENGLISH = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"
_ENGLISH_GROUPS = re.compile(r"\d{1,3}(?:,\d{3})+\.\d+|\d{1,3}(?:,\d{3}){2,}")
_DOT_GROUPS = re.compile(r"\d{1,3}(?:\.\d{3})+")
_NEXT_WORD = re.compile(r"[   ]+([^\W\d_]+)")


class Options(BaseModel):
    """What a project owner may reasonably flip; the defaults are the researched ones (scratchpad/numbers/fr.md)."""

    year_style: Literal["hundreds_before_1700", "mille", "hundreds"] = "hundreds_before_1700"  # "quinze cent quinze" but "mille neuf cent quatre-vingt-dix"
    emergency_numbers: Literal["digits_in_context", "digits_always", "number_always"] = "number_always"  # 112 = "cent douze"
    time_style: Literal["digits", "colloquial"] = "digits"  # "dix heures trente" or "dix heures et demie"
    three_decimals: Literal["number", "digits"] = "number"  # 0,125 = "zéro virgule cent vingt-cinq" or digit by digit
    dollar_singular: str = "dollar"  # what "$" and "USD" are called ("dollar américain" if the owner wants it said)
    dollar_plural: str = "dollars"
    mass_word: str = "kilo"  # "kg" is said "kilos" (alternative "kilogramme")
    say_celsius: bool = False  # "35 °C" = "trente-cinq degrés", or "degrés Celsius"
    second_in_fixed_phrases: bool = True  # "Second Empire", "Seconde Guerre mondiale" instead of "Deuxième"
    age_in_parentheses: bool = False  # "Martin (35)" = "Martin (trente-cinq ans)"
    postal_codes: bool = True  # "75001 Paris" = "soixante-quinze zéro zéro un Paris"


class Speaker:
    """Reads one written number as words. Subclasses add the rules of a context (dates, money, units...); `opts` and `max_value` come from Numbers."""

    opts: Options
    max_value: int

    def split(self, token: str, english: bool = False) -> tuple[str, str | None]:
        """The integer digits and the decimal digits (or None) of a written number."""
        compact = re.sub(SP, "", token)
        if _ENGLISH_GROUPS.fullmatch(compact) or (english and re.fullmatch(NUM_ENGLISH, compact)):
            integer, _, decimal = compact.replace(",", "").partition(".")
            return integer, decimal or None
        if _DOT_GROUPS.fullmatch(compact) and not compact.startswith("0"):
            return compact.replace(".", ""), None
        integer, _, decimal = compact.partition("," if "," in compact else ".")
        return integer, decimal or None

    def say_int(self, digits: str, feminine: bool = False, plural_end: bool = True) -> str:
        """Leading zeros are said ("007" = "zéro zéro sept"); a number above the configured maximum is said digit by digit."""
        stripped = digits.lstrip("0")
        zeros = len(digits) - len(stripped)
        if len(stripped) > len(str(self.max_value)) or (stripped and int(stripped) > self.max_value):
            return W.digits_words(digits)
        if not stripped:
            return " ".join([W.SMALL[0]] * len(digits))
        return " ".join([W.SMALL[0]] * zeros + [W.cardinal(int(stripped), feminine, plural_end)])

    def say_decimal(self, digits: str) -> str:
        """1-2 decimals are a number (zeros kept: "zéro cinq"), 3 are a number unless a zero shows, more are said digit by digit."""
        if len(digits) <= 2 or (len(digits) == 3 and self.opts.three_decimals == "number" and digits[0] != "0" and digits[-1] != "0"):
            return self.say_int(digits)
        return W.digits_words(digits)

    def say_number(self, token: str, feminine: bool = False, plural_end: bool = True, english: bool = False) -> str:
        integer, decimal = self.split(token, english)
        said = self.say_int(integer, feminine and decimal is None, plural_end and decimal is None)
        return said if decimal is None else f"{said} virgule {self.say_decimal(decimal)}"

    @staticmethod
    def next_word(match: re.Match) -> str:
        found = _NEXT_WORD.match(match.string, match.end())
        return found.group(1).lower() if found else ""

    @staticmethod
    def starts_letter(match: re.Match) -> bool:
        return match.end() < len(match.string) and match.string[match.end()].isalpha()
