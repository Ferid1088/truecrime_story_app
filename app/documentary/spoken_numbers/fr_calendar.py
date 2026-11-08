"""Dates, years, decades and clock times in French."""

from __future__ import annotations

import re

from app.documentary.spoken_numbers import fr_words as W
from app.documentary.spoken_numbers.fr_core import SP, Speaker

_MONTH = "(?i:" + "|".join(W.MONTHS) + ")"
# A 4-digit number after one of these is a year; what may follow it and make it a quantity ("en 1500 exemplaires") is excluded.
_YEAR_CUES = (r"(?i:\b(?:en|depuis|avant|après|vers|jusqu['’]en|dès|fin|début|milieu|l['’]an|l['’]année|printemps|été|automne|hiver|"
              + "|".join(W.MONTHS) + r"))")
_QUANTITY = (r"(?!" + SP + r"?(?:[€$£%°]|(?i:euros?|dollars?|francs?|kilos?|kg|mètres?|km|exemplaires|personnes|habitants|victimes|morts|blessés|hommes|"
             r"femmes|enfants|ans|jours|mois|fois|pages|mots|pièces|tonnes|litres|participants|spectateurs|soldats|élèves|policiers)\b))")
_DATE_TEXT = re.compile(rf"(?<![\d/.,])(\d{{1,2}})(er|re|ère)?{SP}({_MONTH})\b(?:{SP}(\d{{4}})(?!\d))?")
_DATE_NUMERIC = re.compile(r"(?<![\d/.,])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])")
_DATE_ISO = re.compile(r"(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\d-])")
_MONTH_YEAR = re.compile(r"(?<![\d/.,])(\d{1,2})/(\d{4})(?![\d/])")
_YEAR = re.compile(rf"({_YEAR_CUES}){SP}(\d{{4}})(?!\d){_QUANTITY}")
_YEAR_PAIR = re.compile(rf"((?i:\b(?:entre|de|du|d['’]))){SP}(\d{{4}})({SP}(?i:et|à|au){SP})(\d{{4}})(?!\d){_QUANTITY}")
_YEAR_RANGE = re.compile(rf"(?<![\d/.,-])(\d{{4}}){SP}?[-–]{SP}?(\d{{4}})(?![\d/-]){_QUANTITY}")
_DECADE = re.compile(rf"((?i:\bannées{SP}))(\d{{4}}|\d{{2}})(?:['’]?s)?(?!\d)")
_TIME = re.compile(rf"(?<![\d:.,/-])([01]?\d|2[0-4])(?:{SP}?h(?:{SP}?([0-5]\d)(?!\d)|(?!\w))|:([0-5]\d)(?::([0-5]\d))?(?!\d))")


class CalendarRules(Speaker):
    def year_words(self, year: int) -> str:
        """"cent" never takes an s in a year (Academie: "l'an mille neuf cent"); 1100-1699 is said by hundreds, 1700-1999 per `year_style`."""
        style = self.opts.year_style
        if 1100 <= year <= 1999 and (style == "hundreds" or (style == "hundreds_before_1700" and year < 1700)):
            hundreds, rest = divmod(year, 100)
            return f"{W.cardinal(hundreds)} cent" + (f" {W.cardinal(rest)}" if rest else "")
        return W.cardinal(year, plural_end=False)

    @staticmethod
    def day_words(day: int) -> str:
        return "premier" if day == 1 else W.cardinal(day)

    def _written_date(self, day: int, month: int, year: str | None) -> str:
        return " ".join(filter(None, (self.day_words(day), W.MONTHS[month - 1], self.year_words(int(year)) if year else "")))

    def dates(self, text: str) -> str:
        def textual(match: re.Match) -> str:
            day = int(match.group(1))
            if not 1 <= day <= 31:
                return match.group()
            year = f" {self.year_words(int(match.group(4)))}" if match.group(4) else ""
            return f"{self.day_words(day)} {match.group(3)}{year}"

        def numeric(match: re.Match) -> str:
            day, month = int(match.group(1)), int(match.group(2))
            return self._written_date(day, month, match.group(3)) if 1 <= day <= 31 and 1 <= month <= 12 else match.group()

        def iso(match: re.Match) -> str:
            day, month = int(match.group(3)), int(match.group(2))
            return self._written_date(day, month, match.group(1)) if 1 <= day <= 31 and 1 <= month <= 12 else match.group()

        def month_year(match: re.Match) -> str:
            month = int(match.group(1))
            return f"{W.MONTHS[month - 1]} {self.year_words(int(match.group(2)))}" if 1 <= month <= 12 else match.group()

        for pattern, handler in ((_DATE_TEXT, textual), (_DATE_NUMERIC, numeric), (_DATE_ISO, iso), (_MONTH_YEAR, month_year)):
            text = pattern.sub(handler, text)
        return text

    def years(self, text: str) -> str:
        def after_cue(match: re.Match) -> str:
            year = int(match.group(2))
            return f"{match.group(1)} {self.year_words(year)}" if 1000 <= year <= 2099 else match.group()

        def pair(match: re.Match) -> str:
            first, second = int(match.group(2)), int(match.group(4))
            if not (1000 <= first <= second <= 2099):
                return match.group()
            return f"{match.group(1)} {self.year_words(first)}{match.group(3)}{self.year_words(second)}"

        def span(match: re.Match) -> str:
            first, second = int(match.group(1)), int(match.group(2))
            if not (1000 <= first < second <= 2099):
                return match.group()
            return f"{self.year_words(first)} à {self.year_words(second)}"

        for pattern, handler in ((_YEAR_PAIR, pair), (_YEAR_RANGE, span), (_YEAR, after_cue)):
            text = pattern.sub(handler, text)
        return text

    def decades(self, text: str) -> str:
        """"les années 90" = "les années quatre-vingt-dix", "années 80" = "années quatre-vingt" (no s: Larousse), "années 2010" = "deux mille dix"."""
        def decade(match: re.Match) -> str:
            value = int(match.group(2))
            if value % 10:
                return match.group()
            if len(match.group(2)) == 2:
                said = W.cardinal(value, plural_end=False)
            elif value >= 2000 or value % 100 == 0:
                said = self.year_words(value)
            else:
                said = W.cardinal(value % 100, plural_end=False)
            return f"{match.group(1)}{said}"

        return _DECADE.sub(decade, text)

    def _hour_name(self, hour: int) -> str:
        if hour == 12:
            return "midi"
        return "minuit" if hour in (0, 24) else self._hours(hour)

    @staticmethod
    def _hours(hour: int) -> str:
        return f"{W.cardinal(hour, feminine=True)} heure{'s' if hour >= 2 else ''}"

    def _clock(self, hour: int, minute: int | None, second: int | None) -> str:
        if second is not None:
            return (f"{self._hours(hour)} {W.cardinal(minute, feminine=True)} minute{'s' if minute >= 2 else ''} "
                    f"{W.cardinal(second, feminine=True)} seconde{'s' if second >= 2 else ''}")
        if not minute:
            return self._hour_name(hour)
        if self.opts.time_style == "colloquial" and minute in (15, 30, 45):
            hour = hour % 24
            name = self._hour_name(hour) if hour in (0, 12) else self._hours(hour)
            if minute == 45:
                return f"{self._hour_name((hour + 1) % 24)} moins le quart"
            if minute == 30:
                return f"{name} et {'demi' if hour in (0, 12) else 'demie'}"
            return f"{name} et quart"
        head = "midi" if hour == 12 else self._hours(hour)
        return f"{head} {W.cardinal(minute)}"

    def times(self, text: str) -> str:
        def clock(match: re.Match) -> str:
            hour = int(match.group(1))
            minute = match.group(2) or match.group(3)
            second = match.group(4)
            return self._clock(hour, int(minute) if minute else None, int(second) if second else None)

        return _TIME.sub(clock, text)
