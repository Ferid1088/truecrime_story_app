"""Roman numerals, dates, decades and the time of day, as a Mexican narrator says them."""

from __future__ import annotations

import re

from app.documentary.spoken_numbers.es_lexicon import DAYPARTS, MONTH_NAMES, MONTH_PATTERN, MONTHS
from app.documentary.spoken_numbers.es_speech import CLOSE, END, OPEN, START, Rule
from app.documentary.spoken_numbers.es_words import ordinal, roman_value

ROMAN_TOKEN = r"[IVXLCDM]+"
FEMININE_NAMES = frozenset(["Isabel", "Isabela", "María", "Victoria", "Juana", "Catalina", "Margarita", "Ana", "Cristina", "Elena", "Sofía", "Beatriz", "Leonor", "Carlota", "Luisa", "Mary", "Jacinta", "Urraca"])
NOT_A_NUMERAL = frozenset(["Malcolm", "Generación", "Rayos", "Fórmula"])


class RomanNumerals(Rule):
    """Roman numerals read only after a trigger word (siglo, capítulo, tomo...) or a capitalised name (Juan Pablo II, Luis XIV): kings and popes are
    ordinals up to X and cardinals after, chapters are cardinals, centuries are ordinals up to X (an option) and cardinals after. A letter that
    only looks Roman (vitamina C, rayos X, an initial such as "Mario V. Guerrero") stays."""

    CENTURY = re.compile(rf"(?<![\w{OPEN}])(?P<word>siglos?|s\.)\s+(?P<first>{ROMAN_TOKEN})(?:\s+(?P<link>y|al|a|e)\s+(?P<second>{ROMAN_TOKEN}))?(?![\w{CLOSE}])")
    COUNTED = re.compile(rf"(?<![\w{OPEN}])(?P<word>(?i:capítulos?|tomos?|partes?|actos?|libros?|volumen|episodios?|temporadas?|fases?)|Super Bowl)\s+"
                         rf"(?P<roman>{ROMAN_TOKEN})(?![\w{CLOSE}])")
    NAMED = re.compile(rf"(?<![\w{OPEN}])(?P<name>[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+(?P<roman>[IVX]{{1,5}})(?![\w{CLOSE}])")

    def apply(self, text: str) -> str:
        text = self.CENTURY.sub(self._century, text)
        text = self.COUNTED.sub(self._counted, text)
        return self.NAMED.sub(self._named, text)

    def _century_number(self, numeral: str) -> str | None:
        value = roman_value(numeral)
        if value is None:
            return None
        if value <= 10 and self.speech.options.ordinal_centuries_up_to_ten:
            return ordinal(value)
        return self.speech.cardinal(value)

    def _century(self, match: re.Match) -> str:
        first = self._century_number(match["first"])
        second = self._century_number(match["second"]) if match["second"] else ""
        if first is None or second is None:
            return match.group()
        word = "siglo" if match["word"] == "s." else match["word"]
        return f"{word} {first}" + (f" {match['link']} {second}" if second else "")

    def _counted(self, match: re.Match) -> str:
        value = roman_value(match["roman"])
        return match.group() if value is None else f"{match['word']} {self.speech.cardinal(value)}"

    def _named(self, match: re.Match) -> str:
        value = roman_value(match["roman"])
        initial = len(match["roman"]) == 1 and re.match(r"\.\s*[A-ZÁÉÍÓÚÑ]", match.string[match.end():])
        if value is None or match["name"] in NOT_A_NUMERAL or initial:
            return match.group()
        words = ordinal(value, feminine=match["name"] in FEMININE_NAMES) if value <= 10 else self.speech.cardinal(value)
        return f"{match['name']} {words}"


class Dates(Rule):
    """"13 de noviembre de 2007", "1.º de mayo" (primero), "del 3 al 5 de mayo", 13/11/2007 (day first, as in Mexico), 2007-11-13 and
    "noviembre 13, 2007" all as "trece de noviembre de dos mil siete"."""

    LONG = re.compile(rf"{START}(?:(?P<first>\d{{1,2}})\s+(?P<link>al|a|y)\s+)?(?P<day>\d{{1,2}})(?:\.?[º°ª]|\.?er|ro)?\s+de\s+(?P<month>{MONTH_PATTERN})"
                      rf"(?:\s+(?:de|del)\s+(?P<year>\d{{4}}))?(?![\w{CLOSE}])", re.IGNORECASE)
    US = re.compile(rf"(?P<month>{MONTH_PATTERN})\s+(?P<day>\d{{1,2}}),\s*(?P<year>\d{{4}})(?![\w{CLOSE}])", re.IGNORECASE)
    NUMERIC = re.compile(rf"{START}(?P<a>\d{{1,2}})(?P<mark>[/.-])(?P<b>\d{{1,2}})(?P=mark)(?P<year>\d{{4}}){END}")
    ISO = re.compile(rf"{START}(?P<year>\d{{4}})-(?P<month>\d{{2}})-(?P<day>\d{{2}}){END}")

    def apply(self, text: str) -> str:
        text = self.LONG.sub(self._long, text)
        text = self.US.sub(lambda m: self._date(int(m["day"]), MONTH_NAMES[m["month"].lower().rstrip(".")], m["year"], m.group()), text)
        text = self.ISO.sub(lambda m: self._date(int(m["day"]), self._month(int(m["month"])), m["year"], m.group()), text)
        return self.NUMERIC.sub(self._numeric, text)

    @staticmethod
    def _month(number: int) -> str | None:
        return MONTHS[number - 1] if 1 <= number <= 12 else None

    def day(self, value: int) -> str:
        return "primero" if value == 1 and self.speech.options.first_of_month_ordinal else self.speech.cardinal(value)

    def _date(self, day: int, month: str | None, year: str | None, original: str) -> str:
        if month is None or not 1 <= day <= 31:
            return original
        words = f"{self.day(day)} de {month}"
        return words + (f" de {self.speech.cardinal(int(year))}" if year else "")

    def _long(self, match: re.Match) -> str:
        month = MONTH_NAMES[match["month"].lower().rstrip(".")]
        first = match["first"]
        if first and not 1 <= int(first) <= 31:
            return match.group()
        words = self._date(int(match["day"]), month, match["year"], match.group())
        if words == match.group():
            return words
        if match["month"][0].isupper() and not match["year"] and not first:
            return self._street_name(int(match["day"]), match["month"])
        if not first:
            return words
        return f"{self.day(int(first))} {match['link']} {words}"

    def _street_name(self, day: int, month: str) -> str:
        """A capitalised month with no year is a name ("calle 5 de Mayo", "20 de Noviembre"): "Cinco de Mayo" keeps its capitals."""
        words = self.day(day)
        return f"{words[0].upper()}{words[1:]} de {month}"

    def _numeric(self, match: re.Match) -> str:
        first, second = int(match["a"]), int(match["b"])
        day, month = (second, first) if second > 12 >= first else (first, second)
        return self._date(day, self._month(month), match["year"], match.group())


class Decades(Rule):
    """"los 1990", "los 90's" as "los noventa"; a four-digit decade of the 1900s drops "mil novecientos". Plain "los 90" is a cardinal anyway."""

    PATTERN = re.compile(rf"(?<![\w{OPEN}])(?P<lead>los|años|década de los)\s+(?P<years>(?:años\s+)?)'?(?P<digits>\d{{2}}|\d{{4}})(?P<s>'?s)?(?![\w{CLOSE}\d])")

    def apply(self, text: str) -> str:
        return self.PATTERN.sub(self._decade, text)

    def _decade(self, match: re.Match) -> str:
        digits = match["digits"]
        if len(digits) == 2 and digits[1] == "0":
            words = self.speech.cardinal(int(digits))
        elif len(digits) == 4 and digits.startswith("19") and digits[3] == "0" and digits[2] != "0":
            words = self.speech.cardinal(int(digits[2:]))
        elif len(digits) == 4 and digits[3] == "0" and match["s"]:
            words = self.speech.cardinal(int(digits))
        else:
            return match.group()
        return f"{match['lead']} {match['years']}{words}"


class Clock(Rule):
    """Time of day. "10:30 a. m." is "las diez y media de la mañana"; 23:45 without "horas" is "las once cuarenta y cinco de la noche" and
    with "horas" it stays the 24-hour reading "veintitrés cuarenta y cinco horas". A day part the text already gives is not repeated."""

    COLON = re.compile(rf"{START}(?P<h>\d{{1,2}}):(?P<m>\d{{2}})(?::(?P<s>\d{{2}}))?(?:\s?(?P<ampm>[aApP]\.?\s?[mM](?![\w])(?:\.(?!\s*(?:$|[A-ZÁÉÍÓÚÑ¿¡])))?)|\s?(?P<hrs>(?:horas|hrs?\.?|h)(?![\w])))?(?![\w{CLOSE}\d:])")
    BARE = re.compile(rf"{START}(?P<h>\d{{1,2}})\s?(?P<ampm>[aApP]\.?\s?[mM](?![\w])(?:\.(?!\s*(?:$|[A-ZÁÉÍÓÚÑ¿¡])))?)")
    HALF_HOURS = {0: "", 15: " y cuarto", 30: " y media"}
    DAYPART_AFTER = re.compile(rf"\s+(?:de la (?:{DAYPARTS})|del día)")
    ARTICLE_BEFORE = re.compile(r"\b(?:las|la)\s$", re.IGNORECASE)
    # "a la 1 de la tarde": the hour one is feminine.
    ONE_BEFORE_DAYPART = re.compile(rf"\b(?P<article>a la|la) 1\b(?= (?:de la (?:{DAYPARTS})|del día|y |en punto))")

    def apply(self, text: str) -> str:
        text = self.ONE_BEFORE_DAYPART.sub(lambda m: f"{m['article']} una", text)
        text = self.COLON.sub(self._colon, text)
        return self.BARE.sub(self._bare, text)

    @staticmethod
    def _hour24(hour: int, ampm: str | None) -> int:
        if ampm and ampm[0] in "aA":
            return 0 if hour == 12 else hour
        if ampm:
            return hour + 12 if hour < 12 else hour
        return hour

    @staticmethod
    def _daypart(hour: int, minute: int) -> str:
        if hour == 0 and minute == 0:
            return "de la noche"
        if hour < 6:
            return "de la madrugada"
        if hour < 12:
            return "de la mañana"
        if hour == 12:
            return "del día"
        return "de la tarde" if hour < 20 else "de la noche"

    def _minutes(self, minute: int) -> str:
        if minute in self.HALF_HOURS:
            return self.HALF_HOURS[minute]
        return " " + self.speech.number(f"{minute:02d}")[0]

    def _spoken(self, text: str, match: re.Match, hour: int, minute: int, second: str | None, hours_style: bool) -> str:
        if hours_style:
            words = self.speech.cardinal(hour) + (" " + self.speech.number(f"{minute:02d}")[0] if minute else "") + " horas"
            return words if not second else f"{words} con {self.speech.cardinal(int(second))} segundos"
        options = self.speech.options
        has_daypart = bool(self.DAYPART_AFTER.match(text, match.end()))
        hour_words = hour if not options.time_with_daypart else hour % 12 or 12
        article = "" if self.ARTICLE_BEFORE.search(text[:match.start()]) else ("la " if hour_words == 1 else "las ")
        words = ("una" if hour_words == 1 else self.speech.cardinal(hour_words)) + self._minutes(minute)
        if second:
            words += f" con {self.speech.cardinal(int(second))} segundos"
        suffix = "" if has_daypart or not options.time_with_daypart else " " + self._daypart(hour, minute)
        return article + words + suffix

    def _colon(self, match: re.Match) -> str:
        hour, minute = int(match["h"]), int(match["m"])
        second = match["s"]
        if hour > 23 or minute > 59 or (second and int(second) > 59) or (match["ampm"] and not 1 <= hour <= 12):
            return match.group()
        return self._spoken(match.string, match, self._hour24(hour, match["ampm"]), minute, second, bool(match["hrs"]))

    def _bare(self, match: re.Match) -> str:
        hour = int(match["h"])
        if not 1 <= hour <= 12:
            return match.group()
        return self._spoken(match.string, match, self._hour24(hour, match["ampm"]), 0, None, False)
