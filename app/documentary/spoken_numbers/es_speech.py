"""The shared core of the Spanish number rules: the options a project owner may flip, how a digit token with Mexican separators ("1,250.50") is
understood, and how a number or its noun is spoken. Every rule class of the es_* modules is built with one `Speech`."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.documentary.spoken_numbers.es_lexicon import FUNCTION_WORDS, gender_of
from app.documentary.spoken_numbers.es_words import MILLION, Cardinals, digits_words

# Digit runs inside a protected span's placeholder (see base.LanguageNumbers) are never numbers: they sit between these two private characters.
OPEN, CLOSE = "\ue000", "\ue001"
START = rf"(?<![\w{OPEN}])(?<!\d[.,])"
END = rf"(?![\w{CLOSE}])(?![.,]\d)"
NUMBER = r"\d{1,3}(?:[ ]\d{3})+(?!\d)|\d+(?:[.,]\d+)*"
SIGNED_NUMBER = rf"(?:(?<![\w{OPEN}.,])(?P<sign>[−-])|{START})(?P<num>{NUMBER})"
THOUSANDS_COMMAS = re.compile(r"\d{1,3}(?:,\d{3})+")
THOUSANDS_DOTS = re.compile(r"\d{1,3}(?:\.\d{3}){2,}")
NEXT_WORD = re.compile(r"\s+([^\W\d_]+)")


class Options(BaseModel):
    """What a project owner may reasonably flip; the defaults are the researched Mexican narration."""

    model_config = ConfigDict(extra="forbid")

    # 911/089/066/065: said digit by digit when the sentence says it is an emergency number, "digits_always", or as a plain number.
    emergency_numbers: Literal["digits_in_context", "digits_always", "number_always"] = "digits_in_context"
    dollar_sign_currency: Literal["peso", "dolar"] = "peso"  # what a bare "$" means (Mexico: pesos)
    euro_cents: Literal["centimos", "centavos"] = "centimos"
    feminine_one_before_thousand: bool = False  # "veintiuna mil mujeres" instead of "veintiún mil mujeres"
    feminine_hundreds_before_thousand: bool = True  # "doscientas mil mujeres" instead of "doscientos mil mujeres"
    first_of_month_ordinal: bool = True  # "primero de mayo", not "uno de mayo"
    marked_ordinals_up_to: int = 30  # 3.er, 21.º are ordinals up to here; above they are cardinals
    ordinal_centuries_up_to_ten: bool = True  # "siglo V" as "siglo quinto"; False: "siglo cinco"
    half_million_as_y_medio: bool = True  # "2.5 millones" as "dos millones y medio"
    phone_digits_in_pairs: bool = False  # a phone number by two-digit numbers, not digit by digit
    decimal_word: Literal["punto", "coma"] = "punto"  # Mexico says "punto"; Spain "coma"
    time_with_daypart: bool = True  # "las once cuarenta y cinco de la noche", not "las veintitrés cuarenta y cinco"


class Speech:
    """Reads digit tokens and finds the noun after a number."""

    def __init__(self, options: Options, max_value: int):
        self.options, self.max_value = options, max_value
        self.cardinals = Cardinals(options.feminine_one_before_thousand, options.feminine_hundreds_before_thousand)

    def cardinal(self, value: int, gender: str | None = None) -> str:
        return digits_words(str(value)) if value > self.max_value else self.cardinals.words(value, gender)

    @staticmethod
    def next_word(text: str, position: int) -> str | None:
        match = NEXT_WORD.match(text, position)
        return match.group(1) if match else None

    def gender_after(self, text: str, position: int) -> str | None:
        """The gender a number takes from the noun that follows it; None when no noun follows ("tenía 21." is "veintiuno")."""
        word = self.next_word(text, position)
        if word is None or word.lower() in FUNCTION_WORDS:
            return None
        return gender_of(word)

    @staticmethod
    def parse(token: str, measure: bool = False) -> tuple[int, str | None] | None:
        """(whole, decimals) of a token with Mexican separators ("," thousands, "." decimal); None when it cannot be one number ("2.0.1").
        With both marks the last is the decimal one; a lone "," or "." with three digits after is thousands unless a unit follows (`measure`)."""
        if " " in token:
            return int(token.replace(" ", "")), None
        if "." in token and "," in token:
            decimal = "." if token.rfind(".") > token.rfind(",") else ","
            thousands = "," if decimal == "." else "."
            whole, _, fraction = token.rpartition(decimal)
            pattern = THOUSANDS_COMMAS if thousands == "," else re.compile(r"\d{1,3}(?:\.\d{3})+")
            return (int(whole.replace(thousands, "")), fraction) if pattern.fullmatch(whole) else None
        if "," in token:
            if THOUSANDS_COMMAS.fullmatch(token):
                return int(token.replace(",", "")), None
            whole, _, fraction = token.partition(",")
            return (int(whole), fraction) if token.count(",") == 1 else None
        if "." in token:
            if THOUSANDS_DOTS.fullmatch(token):
                return int(token.replace(".", "")), None
            if token.count(".") != 1:
                return None
            whole, fraction = token.split(".")
            if len(fraction) == 3 and 1 <= len(whole) <= 3 and not whole.startswith("0") and not measure:
                return int(whole + fraction), None
            return int(whole), fraction
        return int(token), None

    def decimals(self, fraction: str) -> str:
        """The digits after the mark: one or two as a number ("catorce"), leading zeros as "cero", three or more digit by digit."""
        rest = fraction.lstrip("0")
        zeros = ["cero"] * (len(fraction) - len(rest))
        if len(rest) > 2:
            return digits_words(fraction)
        return " ".join(zeros + ([self.cardinals.words(int(rest))] if rest else []))

    def number(self, token: str, gender: str | None = None, measure: bool = False, sign: bool = False) -> tuple[str, bool]:
        """(words, is exactly one) for a digit token. `gender` shapes "uno"/"una"/"un" and the hundreds; unparseable tokens are read group by group."""
        prefix = "menos " if sign else ""
        if re.fullmatch(r"0\d+", token):
            rest = token.lstrip("0")
            return prefix + " ".join(["cero"] * (len(token) - len(rest)) + ([self.cardinal(int(rest), gender)] if rest else [])), False
        parsed = self.parse(token, measure)
        if parsed is None:
            return prefix + " punto ".join(self.number(part)[0] for part in re.split(r"[.,]", token)), False
        whole, fraction = parsed
        if fraction is None:
            return prefix + self.cardinal(whole, gender), whole == 1
        return f"{prefix}{self.cardinal(whole)} {self.options.decimal_word} {self.decimals(fraction)}", False

    def scaled(self, token: str, multiplier: int, scale_word: str, gender: str | None) -> tuple[str, int | None]:
        """(words, whole value or None) of `token` times a scale word ("5" x million). A decimal takes its exact value when the scale is
        thousands ("3.5 mil millones" is 3 500 000 000), "y medio" for a half million, else "dos punto tres millones" (value None)."""
        parsed = self.parse(token, True)
        if parsed is None:
            return f"{self.number(token)[0]} {scale_word}", None
        whole, fraction = parsed
        if fraction is None:
            return self.cardinal(whole * multiplier, gender), whole * multiplier
        exact = Decimal(f"{whole}.{fraction}") * multiplier
        if (multiplier < MILLION or multiplier == 10 ** 9) and exact == exact.to_integral_value():
            return self.cardinal(int(exact), gender), int(exact)
        if multiplier >= MILLION and multiplier != 10 ** 9 and fraction == "5" and self.options.half_million_as_y_medio:
            if not whole:
                return "medio " + scale_word[:-2], None
            return self.cardinal(whole * multiplier, gender) + " y medio", None
        return f"{self.cardinal(whole)} {self.options.decimal_word} {self.decimals(fraction)} {scale_word}", None

    @staticmethod
    def needs_de(value: int | None) -> bool:
        """A whole number of millions (or more) takes "de" before its noun: "dos millones de personas"."""
        return value is not None and value >= MILLION and value % MILLION == 0

    def with_de(self, text: str, position: int, words: str, value: int | None) -> str:
        """`words` followed by "de" when the number is whole millions and a noun comes next without it."""
        following = self.next_word(text, position)
        return words + " de" if self.needs_de(value) and following and following.lower() not in ("de", "del") else words

    def pair_words(self, group: str) -> str:
        """A digit group by two-digit numbers ("1234" as "doce treinta y cuatro"; "05" as "cero cinco")."""
        return " ".join(self.number(group[i:i + 2])[0] for i in range(0, len(group), 2))


class Rule(ABC):
    """One family of numbers (dates, money, units...) found in a text and replaced by words; rules run in a fixed order so a number a rule
    spoke is gone (it holds no digits) before the next rule looks."""

    def __init__(self, speech: Speech):
        self.speech = speech

    @abstractmethod
    def apply(self, text: str) -> str:
        """The text with this rule's numbers in words."""
