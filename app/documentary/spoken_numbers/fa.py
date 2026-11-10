"""Persian numbers for the voice: the words a narrator of colloquial documentary Persian says for the digits of a text (Persian, Latin and Arabic-Indic
digits alike), with no model call. Rules and sources: docs/spoken_numbers_fa.md (several rules are UNVERIFIED there: no native speaker has reviewed them).
Each rule is a regular expression with a handler; the rules run in order, each one leaves words behind, and the last one says every number that is
left, so no digit survives. The text is first put in one writing (ASCII digits, "." decimal, "," thousands, "%"); the story and the display are not
touched, only what is handed to the voice."""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.documentary.spoken_numbers.base import PLACEHOLDER, LanguageNumbers
from app.documentary.spoken_numbers.fa_words import (
    AMBIGUOUS_MONTHS,
    CURRENCY_SYMBOLS,
    ERAS,
    FRACTION_NAMES,
    GREGORIAN_MONTHS,
    MONTH_NAMES,
    SOLAR_MONTHS,
    UNITS,
    Words,
)

WRITING = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬٪", "01234567890123456789.,%")
PLACEHOLDERS = re.compile("(" + PLACEHOLDER.replace("{}", r"\d+") + ")")
LETTER = "ء-يپچژکگی‌"
NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,3}(?:\.\d{3}){2,}|\d+(?:\.\d+)?"
EZAFE = r"(?:ٔ|\u200c?ی)?"
SCALE_WORDS = "میلیارد|میلیون|هزار"
CURRENCY_CODES = {"USD": "$", "EUR": "€", "GBP": "£", "IRR": "﷼"}
EMERGENCY_CODES = "110|112|115|118|120|123|125|911|999"
IRANIAN_CODES = {"110", "112", "115", "118", "120", "123", "125"}
EMERGENCY_CONTEXT = re.compile(
    r"تماس|زنگ|شماره|تلفن|اورژانس|پلیس|آتش‌نشانی|اضطراری|فوریت|گرفت|بگیر|گوشی|اپراتور", re.IGNORECASE)
UNIT_PATTERN = "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True) if len(u) > 1 or u == "°").replace("°C", r"°\s?C")
ERA_PATTERN = "|".join(re.escape(e) for e in sorted(ERAS, key=len, reverse=True) if e != "م")


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emergency_numbers: Literal["digits_in_context", "digits_always", "number_always"] = "digits_in_context"  # 911 as "نُه یک یک" near call words
    speech_style: Literal["written", "spoken"] = "written"  # "پانصد" (the Academy's form) or "پونصد" (everyday speech)
    nine_word: str = "نُه"  # the written "نه" also means "no": the marked form is what the voice is given
    first_of_month: Literal["اول", "یکم"] = "اول"  # 1 فروردین: "اول فروردین" (speech) or "یکم فروردین" (Academy list)
    half_as_nim: bool = True  # 2.5 as "دو و نیم" (and 0.5 as "نیم") instead of "دو ممیز پنج"
    decimal_word: str = "ممیز"  # the Academy's word for the decimal separator
    decimal_cardinal_max_digits: int = 2  # up to this many decimals are one number (3.14: "چهارده"); more are said digit by digit
    time_quarters: bool = True  # 14:15 as "چهارده و ربع", 14:30 as "چهارده و نیم"
    phone_numbers: Literal["digits", "pairs"] = "digits"  # every digit alone, or the groups after the area code as two-digit numbers
    gregorian_month_names: Literal["farhangestan", "common"] = "farhangestan"  # "ژوئیه و اوت" (Academy) or "جولای و آگوست" in numeric dates
    solar_year_range: tuple[int, int] = (1200, 1500)  # a numeric date in these years uses the Persian month names
    digit_cues: list[str] = ["شماره", "تلفن", "کد", "پلاک"]  # a long number after one of these is an identifier, said digit by digit
    digit_cue_min_length: int = 5
    code_digits_from: int = 11  # a run of this many digits or more written without separators is a code, said digit by digit
    ordinal_heads: list[str] = ["قرن", "سده"]  # "قرن ۲۱" is "قرن بیست و یکم"
    range_word: str = "تا"
    score_word: str = "بر"  # 2-1 and 7/3: "دو بر یک"


class Numbers(LanguageNumbers):
    """Persian: every number the narrator would say, in Persian script words."""

    def __init__(self, options: dict, max_value: int):
        super().__init__(options, max_value)
        self.opt = Options.model_validate(options)
        self.words = Words(self.opt.nine_word, self.opt.speech_style == "spoken", max_value)
        self.gregorian = GREGORIAN_MONTHS[self.opt.gregorian_month_names]
        months = "|".join(MONTH_NAMES)
        cues = "|".join(re.escape(c) for c in sorted(self.opt.digit_cues, key=len, reverse=True))
        heads = "|".join(re.escape(h) for h in self.opt.ordinal_heads)
        start = r"(?<![\d.,/])"
        self.rules: list[tuple[re.Pattern, Callable[[re.Match], str]]] = [
            (re.compile(rf"{start}(?P<y>\d{{3,4}})\s?(?P<era>{ERA_PATTERN})(?![{LETTER}\d])|{start}(?P<y2>\d{{4}})(?P<era2>م)(?![{LETTER}\d])"), self.era),
            (re.compile(rf"{start}(?P<y>\d{{4}})[/-](?P<m>\d{{1,2}})[/-](?P<d>\d{{1,2}})(?![\d/-])"), self.date_year_first),
            (re.compile(rf"{start}(?P<d>\d{{1,2}})[/-](?P<m>\d{{1,2}})[/-](?P<y>\d{{4}})(?![\d/-])"), self.date_day_first),
            (re.compile(rf"{start}(?P<d>\d{{1,2}})(?=\s+(?P<month>{months})(?![{LETTER}]))"), self.day_before_month),
            (re.compile(r"(?<![\d.,:/])(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?(?![\d:])"), self.time),
            (re.compile(r"(?<![\w.,{+])(?:\+(?P<cc>\d{1,3})[\s-]?(?P<b1>\d{1,4}(?:[\s-]\d{2,4}){1,4})|(?P<b2>0\d{1,3}(?:[\s-]\d{2,4}){1,4}))(?!\d)"),
             self.phone),
            (re.compile(r"(?<![\d.,/])(?P<mobile>09\d{9})(?!\d)"), self.mobile),
            (re.compile(rf"{start}(?P<n>{EMERGENCY_CODES})(?![\d])(?![.,]\d)"), self.emergency),
            (re.compile(rf"(?<![{LETTER}])(?P<cue>{cues})(?P<ez>{EZAFE})(?P<gap>[\s:]*)(?P<n>\d+)(?![\d.,]\d)"), self.identifier),
            (re.compile(r"#\s?(?=\d)"), lambda m: "شماره "),
            (re.compile(r"(?<![\w.,/-])(?:(?P<minus>[-−])|(?P<plus>\+))(?=\d)"), self.sign),
            (re.compile(rf"(?P<cur>[$€£﷼])\s?(?P<num>{NUMBER})(?:\s+(?P<scale>{SCALE_WORDS}))?(?![\d])"), self.currency),
            (re.compile(rf"{start}(?P<num>{NUMBER})(?:\s+(?P<scale>{SCALE_WORDS}))?\s?(?P<cur>[$€£﷼]|USD|EUR|GBP|IRR)(?![A-Za-z])"), self.currency),
            (re.compile(rf"(?:%\s?(?P<a>{NUMBER})|{start}(?P<b>{NUMBER})\s?%)"), self.percent),
            (re.compile(rf"{start}(?P<num>{NUMBER})(?:\s?(?P<unit>{UNIT_PATTERN})|(?P<unit1>[mglLs]))(?![A-Za-z²³/])"), self.unit),
            (re.compile(rf"(?<![\d{LETTER}.,/])\.(?P<n>\d{{2,3}})(?![\d]|[.,]\d)"), self.calibre),
            (re.compile(rf"{start}(?P<n>\d{{1,4}})‌?(?P<suf>امین|مین|ام|م)(?![{LETTER}\d])"), self.ordinal),
            (re.compile(rf"(?P<head>{heads})(?P<ez>{EZAFE})(?P<gap>\s+)(?P<n>\d{{1,2}})(?![\d.,/])"), self.ordinal_head),
            (re.compile(rf"(?P<head>دهه)(?P<ez>{EZAFE})(?P<gap>\s*)(?P<y>\d{{4}})(?![\d.,/])"), self.decade),
            (re.compile(rf"{start}(?:(?P<whole>\d+)\s+)?(?P<a>\d{{1,3}})/(?P<b>\d{{1,3}})(?![\d/])"), self.fraction),
            (re.compile(rf"{start}(?P<a>\d{{4}})\s?[-–—]\s?(?P<b>\d{{2}}|\d{{4}})(?![\d.,])"), self.year_span),
            (re.compile(rf"{start}(?P<a>{NUMBER})\s?[-–—]\s?(?P<b>{NUMBER})(?![\d.,]\d)"), self.span),
            (re.compile(rf"(?<![\d.,])(?P<num>{NUMBER})(?![\d])"), self.plain)]

    def convert(self, text: str) -> str:
        pieces = PLACEHOLDERS.split(text)
        for index in range(0, len(pieces), 2):
            pieces[index] = pieces[index].translate(WRITING)
            for pattern, handler in self.rules:
                pieces[index] = pattern.sub(handler, pieces[index])
        return "".join(pieces)

    # numbers --------------------------------------------------------------------------------------------------------------------------------

    @staticmethod
    def split(token: str) -> tuple[str, str]:
        """(whole, fraction) of a written number: "1,234.5", "1.234.567" and "3.5"."""
        if token.count(".") > 1:
            return token.replace(".", ""), ""
        whole, _, fraction = token.replace(",", "").partition(".")
        return whole, fraction

    def value(self, token: str) -> Decimal:
        whole, fraction = self.split(token)
        return Decimal(f"{whole}.{fraction or 0}")

    def integer(self, text: str, bare: bool = False) -> list[str]:
        """A whole number; a leading zero ("007") and a code-length run written without separators are read digit by digit."""
        if (len(text) > 1 and text[0] == "0") or (bare and len(text) >= self.opt.code_digits_from):
            return self.words.digits(text)
        return self.words.words(int(text))

    def number(self, token: str) -> list[str]:
        whole, fraction = self.split(token)
        if not fraction:
            return self.integer(whole, bare=token.isdigit())
        if self.opt.half_as_nim and fraction == "5":
            return ["نیم"] if int(whole) == 0 else [*self.integer(whole), "و", "نیم"]
        decimals = self.words.words(int(fraction)) if len(fraction) <= self.opt.decimal_cardinal_max_digits and fraction[0] != "0" \
            else self.words.digits(fraction)
        return [*self.integer(whole), self.opt.decimal_word, *decimals]

    def plain(self, m: re.Match) -> str:
        return " ".join(self.number(m["num"]))

    def sign(self, m: re.Match) -> str:
        return "منفی " if m["minus"] else "مثبت "

    # years, dates, times ----------------------------------------------------------------------------------------------------------------------

    def era(self, m: re.Match) -> str:
        """1998م is a Gregorian year (not the 1998th), 1398 ه.ش a Solar Hijri one: the era is said in full."""
        year, era = (m["y"], m["era"]) if m["y"] else (m["y2"], m["era2"])
        return " ".join([*self.words.words(int(year)), ERAS[era]])

    def month_name(self, month: int, year: int) -> str:
        solar = self.opt.solar_year_range[0] <= year <= self.opt.solar_year_range[1]
        return (SOLAR_MONTHS if solar else self.gregorian)[month - 1]

    def date(self, day: int, month: int, year: int) -> str | None:
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return None
        return " ".join([*self.words.ordinal(day, self.opt.first_of_month), self.month_name(month, year), *self.words.words(year)])

    def date_year_first(self, m: re.Match) -> str:
        return self.date(int(m["d"]), int(m["m"]), int(m["y"])) or m.group()

    def date_day_first(self, m: re.Match) -> str:
        return self.date(int(m["d"]), int(m["m"]), int(m["y"])) or m.group()

    def day_before_month(self, m: re.Match) -> str:
        """"۱۶ فوریه" is "شانزدهم فوریه"; a month name that is also a noun (تیر: an arrow) needs a year or "ماه" after it to be a date."""
        day = int(m["d"])
        after = m.string[m.end():].lstrip()[len(m["month"]):].lstrip()
        if not 1 <= day <= 31 or (m["month"] in AMBIGUOUS_MONTHS and not re.match(r"\d{3,4}(?!\d)|ماه", after)):
            return m.group()
        return " ".join(self.words.ordinal(day, self.opt.first_of_month))

    def time(self, m: re.Match) -> str:
        """14:30 is "چهارده و نیم", 14:15 "چهارده و ربع", 14:05 "چهارده و پنج دقیقه", 14:00 "چهارده"; a clock time that does not exist stays for the number rules."""
        hour, minute = int(m["h"]), int(m["m"])
        if hour > 23 or minute > 59:
            return m.group()
        words = self.words.words(hour)
        if minute == 30 and self.opt.time_quarters and not m["s"]:
            words += ["و", "نیم"]
        elif minute == 15 and self.opt.time_quarters and not m["s"]:
            words += ["و", "ربع"]
        elif minute:
            words += ["و", *self.words.words(minute), "دقیقه"]
        if m["s"]:
            words += ["و", *self.words.words(int(m["s"])), "ثانیه"]
        return " ".join(words)

    # codes ------------------------------------------------------------------------------------------------------------------------------------

    def phone(self, m: re.Match) -> str:
        """+98 21 1234 5678 / 021-1234-5678: the country code as a number, the other groups digit by digit (or the last ones as pairs)."""
        groups = re.split(r"[\s-]", m["b1"] or m["b2"])
        if sum(len(g) for g in groups) + len(m["cc"] or "") < 8:
            return m.group()
        said = [" ".join(self.words.digits(groups[0]))]
        for group in groups[1:]:
            pair = self.opt.phone_numbers == "pairs" and len(group) == 2 and group[0] != "0"
            said.append(" ".join(self.words.words(int(group)) if pair else self.words.digits(group)))
        country = [" ".join(self.words.words(int(m["cc"])))] if m["cc"] else []
        return "، ".join([*country, *said])

    def mobile(self, m: re.Match) -> str:
        n = m["mobile"]
        return "، ".join(" ".join(self.words.digits(part)) for part in (n[:4], n[4:7], n[7:]))

    def emergency(self, m: re.Match) -> str:
        """110, 115, 125 ... are said as numbers (صد و ده), as Iranians say them; 911 and 999 digit by digit near a call word (the option)."""
        code = m["n"]
        near = m.string[max(0, m.start() - 30):m.start()] + " " + m.string[m.end():m.end() + 25]
        mode = self.opt.emergency_numbers
        by_digit = mode == "digits_always" or (mode == "digits_in_context" and code not in IRANIAN_CODES and EMERGENCY_CONTEXT.search(near))
        return " ".join(self.words.digits(code) if by_digit else self.words.words(int(code)))

    def identifier(self, m: re.Match) -> str:
        """A long number after a cue word (شماره، تلفن، کد، پلاک) is an identifier, said digit by digit; a short one is a number (شمارهٔ ۵: پنج)."""
        if len(m["n"]) < self.opt.digit_cue_min_length:
            return m.group()
        return m["cue"] + m["ez"] + m["gap"] + " ".join(self.words.digits(m["n"]))

    # money, percent, units ----------------------------------------------------------------------------------------------------------------------

    def currency(self, m: re.Match) -> str:
        """$12.50 and 12.50 USD: "دوازده دلار و پنجاه سنت"; "۵ میلیون €": "پنج میلیون یورو"."""
        symbol = CURRENCY_SYMBOLS.get(m["cur"]) or CURRENCY_SYMBOLS[CURRENCY_CODES[m["cur"]]]
        major, minor = symbol
        if m["scale"]:
            return " ".join([*self.number(m["num"]), m["scale"], major])
        whole, fraction = self.split(m["num"])
        cents = int(fraction.ljust(2, "0")[:2]) if fraction and minor else 0
        if fraction and not minor:
            return " ".join([*self.number(m["num"]), major])
        words = [*self.integer(whole), major] if int(whole) or not cents else []
        if cents:
            words += [*(["و"] if words else []), *self.words.words(cents), minor]
        return " ".join(words)

    def percent(self, m: re.Match) -> str:
        return " ".join([*self.number(m["a"] or m["b"]), "درصد"])

    def unit(self, m: re.Match) -> str:
        unit = re.sub(r"\s", "", m["unit"] or m["unit1"])
        return " ".join([*self.number(m["num"]), UNITS[unit]])

    def calibre(self, m: re.Match) -> str:
        """.38 is "کالیبر سی و هشت"."""
        return "کالیبر " + " ".join(self.words.words(int(m["n"])))

    # ordinals, decades, fractions, ranges ------------------------------------------------------------------------------------------------------

    def ordinal(self, m: re.Match) -> str:
        """۳ام is "سوم", ۳۰ام "سی‌ام", ۲۱ام "بیست و یکم", ۵۰مین "پنجاهمین", ۱مین "اولین"."""
        n = int(m["n"])
        words = self.words.ordinal_with_in(n) if m["suf"] in {"مین", "امین"} else self.words.ordinal(n, "یکم")
        return " ".join(words)

    def ordinal_head(self, m: re.Match) -> str:
        return m["head"] + m["ez"] + m["gap"] + " ".join(self.words.ordinal(int(m["n"]), "یکم"))

    def decade(self, m: re.Match) -> str:
        """دهه ۱۳۶۰ is "دهه شصت" (the decade of the sixties); a century year (۱۹۰۰) stays whole."""
        year = int(m["y"])
        words = self.words.words(year % 100 if year % 10 == 0 and year % 100 else year)
        return m["head"] + m["ez"] + m["gap"] + " ".join(words)

    def fraction(self, m: re.Match) -> str:
        """3/4 is "سه چهارم", 1/2 "نیم", 2 1/2 "دو و نیم"; a pair that is no fraction (7/3) is "هفت بر سه"."""
        whole = [*self.integer(m["whole"])] if m["whole"] else []
        a, b = int(m["a"]), int(m["b"])
        if not 1 <= a < b <= 100:
            return " ".join([*whole, *self.words.words(a), self.opt.score_word, *self.words.words(b)])
        if (a, b) == (1, 2):
            return " ".join([*whole, "و", "نیم"] if whole else ["نیم"])
        denominator = FRACTION_NAMES.get(b) or " ".join(self.words.ordinal(b))
        return " ".join([*whole, *(["و"] if whole else []), *self.words.words(a), denominator])

    def year_span(self, m: re.Match) -> str:
        """1990-1995 and 1990-95 are "هزار و نهصد و نود تا هزار و نهصد و نود و پنج"."""
        first, second = m["a"], m["b"]
        if len(second) == 2:
            second = first[:2] + second
        return self.range_words(first, second)

    def span(self, m: re.Match) -> str:
        return self.range_words(m["a"], m["b"])

    def range_words(self, first: str, second: str) -> str:
        """3-5 is "سه تا پنج"; a pair that does not go up (2-1) is a score: "دو بر یک"."""
        low, high = self.number(first), self.number(second)
        joint = self.opt.range_word if self.value(first) < self.value(second) else self.opt.score_word
        return " ".join([*low, joint, *high])
