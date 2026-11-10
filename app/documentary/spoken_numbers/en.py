"""English numbers for the voice: what a native narrator says for the digits left in a script ("March 3rd" -> "March third", "1999" -> "nineteen ninety-nine",
"$3.50" -> "three dollars and fifty cents", "he called 911" -> "he called nine one one", "a .38" -> "a thirty-eight", "5'10"" -> "five foot ten").

The English writer is told to write numbers as words, so this is the safety net for what remains. The defaults are American; `and_after_hundred` and the other
options give the British habits. Text without digits is not touched, except the Roman numerals of rulers and wars ("Henry VIII", "World War II"), which
`roman_numerals` can switch off. Words are produced by `en_words`, the patterns are in `en_patterns`. The research is in docs/spoken_numbers_en.md."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.documentary.spoken_numbers import en_patterns as p
from app.documentary.spoken_numbers.base import LanguageNumbers
from app.documentary.spoken_numbers.en_words import (
    MAX_CARDINAL,
    cardinal,
    digits,
    house_number,
    ordinal,
    pair,
    plural,
    year,
)


class Options(BaseModel):
    """What a project owner may reasonably flip; the defaults are the researched narrator habits (docs/spoken_numbers_en.md)."""

    model_config = ConfigDict(extra="forbid")

    emergency_numbers: Literal["digits_in_context", "digits_always", "number_always"] = "digits_in_context"  # 911/999/112/000/111: digit by digit near "call", "dial"...
    zero_word: Literal["oh", "zero"] = "oh"  # a 0 in a phone, case or badge number: "five oh five" or "five zero five" (years always say "oh": "nineteen oh five")
    and_after_hundred: bool = False  # American "one hundred twenty" or British "one hundred and twenty" (also "two thousand and five")
    years_2000s: Literal["two_thousand", "twenty_oh"] = "two_thousand"  # 2005: "two thousand five" or "twenty oh five"
    years_after_2009: Literal["pairs", "two_thousand"] = "pairs"  # 2024: "twenty twenty-four" or "two thousand twenty-four"
    ambiguous_numeric_dates: Literal["month_first", "day_first"] = "month_first"  # 3/4/2024 (both parts up to 12): March 4th (US) or the 3rd of April (UK)
    money_cents: Literal["and_cents", "bare"] = "and_cents"  # $3.50: "three dollars and fifty cents" or "three dollars fifty"
    phone_double_digits: bool = False  # British phone habit: 55 as "double five", 000 as "triple zero"
    toll_free_800_as_number: bool = True  # 1-800: "one, eight hundred, ..." and not "eight oh oh"
    roman_numerals: bool = True  # "Henry VIII" -> "Henry the Eighth", "World War II" -> "World War Two"


class Numbers(LanguageNumbers):
    """Turns the digits of an English text into words, rule by rule in a fixed order (a rule that has said a number leaves no digits for the later ones)."""

    def __init__(self, options: dict, max_value: int):
        super().__init__(options, max_value)
        self.settings = Options.model_validate(options)
        self.rules = [self._nine_eleven, self._romans, self._dates, self._ordinals, self._times, self._phones, self._zips, self._streets, self._labels, self._emergency,
                      self._calibres, self._heights, self._ranges, self._signs, self._money, self._percents, self._units, self._fractions, self._decades,
                      self._compounds, self._dotted, self._decimals, self._years, self._house_letters, self._cardinals]

    def text(self, text: str) -> str:
        """Like the base, with a placeholder that is not a digit, so a kept code survives the rules."""
        kept: list[str] = []

        def keep(match: re.Match) -> str:
            kept.append(match.group())
            return chr(p.PRIVATE_USE + len(kept) - 1)

        converted = self.convert(p.PROTECTED.sub(keep, text))
        return converted.translate({p.PRIVATE_USE + index: value for index, value in enumerate(kept)})

    def convert(self, text: str) -> str:
        for rule in self.rules:
            text = rule(text)
        return text

    # -- words ----------------------------------------------------------------------------------------------------------------------------------------------
    def _number(self, value: int) -> str:
        if value > min(self.max_value, MAX_CARDINAL):
            return self._digits(str(value))
        return cardinal(value, self.settings.and_after_hundred)

    def _digits(self, text: str, doubles: bool = False) -> str:
        return digits(text, self.settings.zero_word, doubles)

    def _year_words(self, value: int) -> str:
        return year(value, self.settings.years_2000s, self.settings.years_after_2009, self.settings.and_after_hundred)

    @staticmethod
    def _integer(text: str) -> int:
        return int(text.replace(",", ""))

    def _plain(self, raw: str) -> str:
        """A whole number as written: with a leading zero ("007") digit by digit, otherwise a cardinal."""
        if len(raw) > 1 and raw.startswith("0"):
            return self._digits(raw)
        return self._number(self._integer(raw))

    def _decimal_words(self, whole: str | None, decimals: str) -> str:
        """"3.5" as "three point five" (the digits after the point one by one)."""
        return f"{self._plain(whole) if whole else 'zero'} point {digits(decimals, 'zero')}"

    def _amount_words(self, whole: str, decimals: str | None) -> str:
        return self._decimal_words(whole, decimals) if decimals else self._plain(whole)

    @staticmethod
    def _starts_sentence(before: str) -> bool:
        return not before.strip() or before.rstrip().endswith((".", "!", "?"))

    # -- Roman numerals -------------------------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _roman_value(token: str) -> int | None:
        if not p.ROMAN.match(token):
            return None
        total = 0
        for index, char in enumerate(token):
            value = p.ROMAN_VALUES[char]
            total += -value if index + 1 < len(token) and p.ROMAN_VALUES[token[index + 1]] > value else value
        return total

    def _romans(self, text: str) -> str:
        if not self.settings.roman_numerals:
            return text
        text = p.RULER.sub(self._ruler, text)
        text = p.WORLD_WAR.sub(lambda m: f"{m['w']} {cardinal(self._roman_value(m['num'])).capitalize()}", text)
        return p.ROMAN_LABEL.sub(self._roman_label, text)

    def _ruler(self, match: re.Match) -> str:
        """"Henry VIII" as "Henry the Eighth". A single letter before a capitalised word after a full stop is an initial ("Paul V. Smith") and stays."""
        value = self._roman_value(match["num"])
        if value is None or (len(match["num"]) == 1 and p.INITIAL_AFTER.match(match.string[match.end():])):
            return match.group()
        return f"{match['name']} the {ordinal(value).capitalize()}"

    def _roman_label(self, match: re.Match) -> str:
        value = self._roman_value(match["num"])
        return match.group() if value is None else f"{match['w']} {cardinal(value)}"

    # -- dates, ordinals, times -----------------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _month_name(text: str) -> str:
        return p.MONTHS[p.MONTH_ABBREVIATIONS[text.lower()]] if text.endswith(".") else text

    @staticmethod
    def _valid_day(day: int) -> bool:
        return 1 <= day <= 31

    def _month_first(self, month: str, day: int, year_text: str | None) -> str:
        words = f"{month} {ordinal(day)}"
        return words + (f", {self._year_words(int(year_text))}" if year_text else "")

    def _day_first(self, month: str, day: int, year_text: str | None, before: str, the: str | None = None) -> str:
        article = the or ("The " if self._starts_sentence(before) else "the ")
        words = f"{article.strip()} {ordinal(day)} of {month}"
        return words + (f" {self._year_words(int(year_text))}" if year_text else "")

    def _nine_eleven(self, text: str) -> str:
        return p.NINE_ELEVEN.sub("nine eleven", text)

    def _dates(self, text: str) -> str:
        text = p.DATE_ISO.sub(lambda m: self._month_first(p.MONTHS[int(m["m"]) - 1], int(m["d"]), m["y"]), text)
        text = p.DATE_NUMERIC.sub(self._date_numeric, text)
        text = p.DATE_MONTH_FIRST.sub(self._date_month_first, text)
        return p.DATE_DAY_FIRST.sub(self._date_day_first, text)

    def _date_numeric(self, match: re.Match) -> str:
        """3/3/2024: a part above 12 says which is the day; when both are up to 12 the option `ambiguous_numeric_dates` decides."""
        first, second = int(match["a"]), int(match["b"])
        day_first = first > 12 or (second <= 12 and self.settings.ambiguous_numeric_dates == "day_first")
        day, month = (first, second) if day_first else (second, first)
        if not (self._valid_day(day) and 1 <= month <= 12):
            return match.group()
        name, before = p.MONTHS[month - 1], match.string[: match.start()]
        return self._day_first(name, day, match["y"], before) if day_first else self._month_first(name, day, match["y"])

    def _date_month_first(self, match: re.Match) -> str:
        day = int(match["d"])
        if not self._valid_day(day) or (match["d2"] and not self._valid_day(int(match["d2"]))):
            return match.group()
        month = self._month_name(match["m"])
        if match["d2"]:
            words = f"{month} {ordinal(day)} to {ordinal(int(match['d2']))}"
            return words + (f", {self._year_words(int(match['y']))}" if match["y"] else "")
        return self._month_first(month, day, match["y"])

    def _date_day_first(self, match: re.Match) -> str:
        day = int(match["d"])
        if not self._valid_day(day):
            return match.group()
        return self._day_first(self._month_name(match["m"]), day, match["y"], match.string[: match.start()], match["the"])

    def _ordinals(self, text: str) -> str:
        return p.ORDINAL.sub(lambda m: ordinal(int(m["n"]), self.settings.and_after_hundred) if int(m["n"]) else m.group(), text)

    def _times(self, text: str) -> str:
        text = p.MILITARY.sub(self._military, text)
        text = p.TIME.sub(self._time, text)
        text = p.HOUR_AM_PM.sub(self._hour_am_pm, text)
        text = p.O_CLOCK.sub(lambda m: f"{cardinal(int(m['h']))} o'clock" if 1 <= int(m["h"]) <= 12 else m.group(), text)
        return p.RATIO.sub(lambda m: f"{self._number(int(m['a']))} to {self._number(int(m['b']))}", text)

    @staticmethod
    def _day_part(text: str) -> str:
        return "a.m." if text[0] in "aA" else "p.m."

    @staticmethod
    def _hundreds_style(hour: int, minute: int) -> str:
        """The 24-hour reading: "oh eight hundred", "eighteen hundred", "twenty-one thirty", "zero zero fifteen"."""
        head = pair(hour) if hour else "zero"
        if minute == 0:
            return f"{head} hundred"
        return f"{head if hour else 'zero zero'} {pair(minute)}"

    def _military(self, match: re.Match) -> str:
        hour, minute = int(match["t"][:2]), int(match["t"][2:])
        return f"{self._hundreds_style(hour, minute)} hours" if hour <= 24 else match.group()

    def _time(self, match: re.Match) -> str:
        hour, minute, day_part = int(match["h"]), int(match["mi"]), match["ap"]
        if hour > 24 or (day_part and not 1 <= hour <= 12):
            return match.group()
        if not day_part and (match["h"].startswith("0") or hour >= 13):
            return self._hundreds_style(hour, minute)
        words = cardinal(hour) if minute == 0 else f"{cardinal(hour)} {pair(minute)}"
        if day_part:
            return f"{words} {self._day_part(day_part)}"
        return f"{words} o'clock" if minute == 0 else words

    def _hour_am_pm(self, match: re.Match) -> str:
        hour = int(match["h"])
        return f"{cardinal(hour)} {self._day_part(match['ap'])}" if 1 <= hour <= 12 else match.group()

    # -- codes ----------------------------------------------------------------------------------------------------------------------------------------------
    def _phones(self, text: str) -> str:
        text = p.PHONE_PLUS.sub(self._phone_plus, text)
        text = p.PHONE_NANP.sub(self._phone_nanp, text)
        text = p.PHONE_LOCAL.sub(lambda m: f"{self._digits(m['exchange'], self.settings.phone_double_digits)}, {self._digits(m['line'], self.settings.phone_double_digits)}", text)
        return p.PHONE_UK.sub(lambda m: ", ".join(self._digits(group, self.settings.phone_double_digits) for group in m.group().split()), text)

    def _phone_groups(self, groups: list[str]) -> str:
        return ", ".join(self._digits(group, self.settings.phone_double_digits) for group in groups)

    def _phone_plus(self, match: re.Match) -> str:
        groups = re.findall(r"\d+", match.group())
        return f"plus {self._digits(groups[0])}, {self._phone_groups(groups[1:])}"

    def _phone_nanp(self, match: re.Match) -> str:
        area = match["area"] or match["area_paren"]
        head = "eight hundred" if area == "800" and self.settings.toll_free_800_as_number else self._digits(area, self.settings.phone_double_digits)
        country = "one, " if match["country"] else ""
        return f"{country}{head}, {self._phone_groups([match['exchange'], match['line']])}"

    def _zips(self, text: str) -> str:
        def zip_code(match: re.Match) -> str:
            prefix = match["state"] or match["zip_word"].strip()
            return f"{prefix} {self._digits(match['zip'])}" + (f", {self._digits(match['plus4'])}" if match["plus4"] else "")

        return p.ZIP.sub(zip_code, text)

    def _streets(self, text: str) -> str:
        return p.STREET.sub(lambda m: f"{house_number(int(m['n']))}{' ' + m['letter'] if m['letter'] else ''} {m['street']}", text)

    def _labels(self, text: str) -> str:
        text = p.CODE_LABEL.sub(self._code_label, text)
        text = p.HOUSE_LABEL.sub(lambda m: f"{m['label']} {house_number(int(m['n']))}", text)
        return p.NUMBER_SIGN.sub("number ", text)

    def _code_label(self, match: re.Match) -> str:
        """"case 15" is a number, "Case No. 2004-0153" and "badge 4521" are read digit by digit, a hyphen as a pause."""
        code = match["code"]
        words = self._plain(code) if len(code) <= 2 else ", ".join(self._digits(part) for part in code.split("-"))
        return f"{match['label']}{' number' if match['no'].strip() else ''} {words}"

    def _emergency(self, text: str) -> str:
        if self.settings.emergency_numbers == "number_always":
            return text
        return p.EMERGENCY.sub(self._emergency_number, text)

    def _emergency_number(self, match: re.Match) -> str:
        """9-1-1 always, and 911, 999, 112, 000, 111 digit by digit ("nine one one", 000 "triple zero"); with `digits_in_context` only near a word like "called"."""
        if match["hyphen"]:
            return "nine one one"
        if self.settings.emergency_numbers == "digits_in_context" and not self._near_emergency_word(match):
            return match.group()
        return p.EMERGENCY_SPOKEN.get(match["n"]) or self._digits(match["n"])

    @staticmethod
    def _near_emergency_word(match: re.Match) -> bool:
        before = re.split(r"[.!?]", match.string[: match.start()])[-1]
        after = re.split(r"[.!?]", match.string[match.end():])[0]
        following = p.NEXT_WORD.match(after)
        if following and following["word"].lower() in p.EMERGENCY_NOUNS_AFTER:
            return True
        words = [word.lower() for word in re.findall(r"[A-Za-z]+", before)[-8:] + re.findall(r"[A-Za-z]+", after)[:4]]
        return any(word in p.EMERGENCY_WORDS for word in words)

    # -- calibres and measures ------------------------------------------------------------------------------------------------------------------------------
    def _number_text(self, raw: str) -> str:
        whole, _, decimals = raw.partition(".")
        return self._decimal_words(whole, decimals) if decimals else self._plain(whole)

    def _calibres(self, text: str) -> str:
        text = p.CALIBRE_PAIR.sub(lambda m: f"{cardinal(int(m['a']))} {pair(int(m['b']))}", text)
        text = p.CALIBRE.sub(lambda m: cardinal(int(m["n"])) if len(m["n"]) == 2 else house_number(int(m["n"])), text)
        text = p.MILLIMETRE_PAIR.sub(lambda m: f"{self._number_text(m['n'])} by {self._number_text(m['n2'])} millimeter", text)
        return p.MILLIMETRE_ROUND.sub(self._millimetre_round, text)

    def _millimetre_round(self, match: re.Match) -> str:
        """"a 9mm" and "7.62 mm rifle" are said "nine millimeter": the calibre is singular; a measure ("5mm") is left to the units rule."""
        if match["gun"] or match["n"] in p.CALIBRE_MILLIMETRES:
            return f"{self._number_text(match['n'])} millimeter"
        return match.group()

    def _heights(self, text: str) -> str:
        text = p.HEIGHT_MARKS.sub(lambda m: f"{cardinal(int(m['ft']))} foot {cardinal(int(m['in']))}", text)
        return p.HEIGHT_WORDS.sub(lambda m: f"{cardinal(int(m['ft']))} foot {cardinal(int(m['in']))}", text)

    def _ranges(self, text: str) -> str:
        text = p.YEAR_RANGE.sub(self._year_range, text)
        return p.NUMBER_RANGE.sub(lambda m: f"{self._number(self._integer(m['a']))} to ", text)

    def _year_range(self, match: re.Match) -> str:
        """"1990-1995" as "nineteen ninety to nineteen ninety-five", "1990-95" likewise; two numbers that are no years ("1000-1500 people") are left to the plain range."""
        first, second = int(match["a"]), match["b"]
        last = int(second) if len(second) == 4 else first // 100 * 100 + int(second)
        counted = p.NEXT_WORD.match(match.string[match.end():])
        if not (1000 <= first <= 2099 and first < last <= 2099) or (counted and counted["word"].lower() in p.COUNTED_NOUNS):
            return match.group()
        return f"{self._year_words(first)} to {self._year_words(last)}"

    def _signs(self, text: str) -> str:
        return p.SIGN.sub("minus ", text)

    # -- money, percents, units, fractions ------------------------------------------------------------------------------------------------------------------
    def _money(self, text: str) -> str:
        text = p.CURRENCY_BEFORE.sub(self._currency, text)
        return p.SUBUNIT_AFTER.sub(self._subunit, text)

    def _currency(self, match: re.Match) -> str:
        """"$3.50" as "three dollars and fifty cents", "£20" as "twenty pounds", "$2.5 million" as "two point five million dollars", "50p" as "fifty pence"."""
        singular, plural_name, sub_singular, sub_plural = p.CURRENCIES[match["cur"]]
        whole, decimals, magnitude = match["int"], match["dec"], match["mag"] or match["abbr"]
        if magnitude:
            return f"{self._amount_words(whole, decimals)} {p.MAGNITUDE_WORDS[magnitude.lower()]} {plural_name}"
        if decimals and len(decimals) > 2:
            return f"{self._decimal_words(whole, decimals)} {plural_name}"
        value, cents = self._integer(whole), int(decimals.ljust(2, "0")) if decimals else 0
        unit = singular if value == 1 else plural_name
        if cents == 0:
            return f"{self._number(value)} {unit}"
        sub_unit = sub_singular if cents == 1 else sub_plural
        if value == 0:
            return f"{self._number(cents)} {sub_unit}"
        if self.settings.money_cents == "bare":
            return f"{self._number(value)} {unit} {self._number(cents)}"
        return f"{self._number(value)} {unit} and {self._number(cents)} {sub_unit}"

    def _subunit(self, match: re.Match) -> str:
        value = int(match["n"])
        names = ("penny", "pence") if match["cur"] == "p" else ("cent", "cents")
        return f"{self._number(value)} {names[0] if value == 1 else names[1]}"

    def _percents(self, text: str) -> str:
        return p.PERCENT.sub(lambda m: f"{self._amount_words(m['int'], m['dec'])} {'percent' if m['sign'] == '%' else 'per mille'}", text)

    def _units(self, text: str) -> str:
        text = p.UNIT_COMPOUND.sub(lambda m: f"{self._number_text(m['n'])}-{p.UNITS[m['unit']][0]}", text)
        text = p.UNIT.sub(self._unit, text)
        return p.THOUSANDS_K.sub(lambda m: f"{self._amount_words(m['int'], m['dec'])} thousand", text)

    def _unit(self, match: re.Match) -> str:
        """"180 lbs" as "one hundred eighty pounds", "120 mph" as "one hundred twenty miles per hour"; only exactly one takes the singular."""
        singular, plural_name = p.UNITS[match["unit"]]
        one = not match["dec"] and self._integer(match["int"]) == 1
        return f"{self._amount_words(match['int'], match['dec'])} {singular if one else plural_name}"

    def _fractions(self, text: str) -> str:
        text = p.ROUND_THE_CLOCK.sub("twenty-four seven", text)
        return p.FRACTION.sub(self._fraction, text)

    def _fraction(self, match: re.Match) -> str:
        """"1/2" as "one half", "3/4" as "three quarters", "1 1/2" as "one and a half"; a fraction with no name is "seven over thirteen"."""
        numerator, denominator = int(match["a"]), int(match["b"])
        if denominator in p.FRACTION_NAMES and 0 < numerator < denominator:
            singular, plural_name = p.FRACTION_NAMES[denominator]
            words = f"a {singular}" if numerator == 1 and match["whole"] else f"{self._number(numerator)} {singular if numerator == 1 else plural_name}"
        else:
            words = f"{self._number(numerator)} over {self._number(denominator)}"
        return f"{self._number(int(match['whole']))} and {words}" if match["whole"] else words

    # -- decades, compounds, decimals, years, cardinals -----------------------------------------------------------------------------------------------------
    def _decades(self, text: str) -> str:
        return p.DECADE.sub(self._decade, text)

    def _decade(self, match: re.Match) -> str:
        """"the 1990s" as "the nineteen nineties", "the 1900s" as "the nineteen hundreds", "her 30s" and "the '90s" as "thirties" and "nineties"."""
        raw = match["short"] or match["n"]
        value = int(raw)
        if value == 0 or value % 10:
            return match.group()
        return plural(cardinal(value) if len(raw) == 2 else self._year_words(value))

    def _compounds(self, text: str) -> str:
        """"a 35-year-old" as "a thirty-five-year-old", "a 9-foot" as "a nine-foot"."""
        return p.HYPHEN_COMPOUND.sub(lambda m: f"{self._number_text(m['n'])}-", text)

    def _dotted(self, text: str) -> str:
        return p.DOTTED.sub(lambda m: " dot ".join(self._plain(part) for part in m.group().split(".")), text)

    def _decimals(self, text: str) -> str:
        return p.DECIMAL.sub(lambda m: self._decimal_words(m["int"], m["dec"]), text)

    def _years(self, text: str) -> str:
        return p.YEAR.sub(self._year, text)

    def _year(self, match: re.Match) -> str:
        """A 4-digit number is a year after a month or "since", or when no counted noun and no "about" comes with it ("1500 people" and "about 1900" are amounts)."""
        value = int(match["y"])
        previous = p.PREVIOUS_WORD.search(match.string[: match.start()])
        word = previous["word"].lower() if previous else ""
        following = p.NEXT_WORD.match(match.string[match.end():])
        counted = bool(following) and following["word"].lower() in p.COUNTED_NOUNS
        if word in p.STRONG_YEAR_WORDS and 1000 <= value <= 2099:
            return self._year_words(value)
        if counted or word in p.AMOUNT_PREVIOUS_WORDS:
            return match.group()
        floor = 1000 if word in p.YEAR_PREVIOUS_WORDS else 1100
        return self._year_words(value) if floor <= value <= 2099 else match.group()

    def _house_letters(self, text: str) -> str:
        return p.HOUSE_LETTER.sub(lambda m: f"{house_number(int(m['n']))} {m['l']}", text)

    def _cardinals(self, text: str) -> str:
        return p.CARDINAL.sub(lambda m: self._plain(m["n"]), text)
