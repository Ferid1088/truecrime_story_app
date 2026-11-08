"""Italian dates, clock times, decades and eras: "il 13/11/2007" il tredici novembre duemilasette, "il 1 maggio" il primo maggio, "alle 10:30" alle
dieci e mezza, "gli anni '90" gli anni Novanta, "il '68" il Sessantotto, "il '900" il Novecento."""

from __future__ import annotations

import re

from app.documentary.spoken_numbers.it_words import Speaker, cardinal, counted

MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre")
MONTH = "|".join(MONTHS)
DECADES = {"00": "Zero", "10": "Dieci", "20": "Venti", "30": "Trenta", "40": "Quaranta", "50": "Cinquanta", "60": "Sessanta", "70": "Settanta",
           "80": "Ottanta", "90": "Novanta"}
CENTURIES = {"2": "Duecento", "3": "Trecento", "4": "Quattrocento", "5": "Cinquecento", "6": "Seicento", "7": "Settecento", "8": "Ottocento",
             "9": "Novecento"}
# "alle 1:00" is "all'una", "alle 12:00" is "a mezzogiorno"; "le 12:00" stays "le dodici" ("le mezzogiorno" is not Italian).
CONTRACTED = {"alle": "all'", "dalle": "dall'", "delle": "dell'", "nelle": "nell'", "sulle": "sull'", "le": "l'"}
NOON_PREPOSITION = {"alle": "a", "dalle": "da", "delle": "di", "nelle": "a", "sulle": "a"}
APOSTROPHE_FORMS = {"all'": "alle", "dall'": "dalle"}
PREPOSITIONS = "alle|dalle|delle|nelle|sulle|le"

NUMERIC_DATE = re.compile(r"(?<![\d/.,-])(?P<d>\d{1,2})(?P<sep>[/.-])(?P<m>\d{1,2})(?P=sep)(?P<y>\d{4})(?![\d/])")
ISO_DATE = re.compile(r"(?<![\d-])(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?![\d-])")
COLON_TIME = re.compile(
    rf"(?:(?<![\w])(?P<prep>{PREPOSITIONS}|ore)\s+)?(?<![\d:.,])(?P<h>\d{{1,2}}):(?P<m>\d{{2}})(?::(?P<s>\d{{2}}))?(?![\d:])")
DOT_TIME = re.compile(rf"(?<![\w])(?P<prep>{PREPOSITIONS}|ore|all'|dall')\s*(?P<h>\d{{1,2}})\.(?P<m>\d{{2}})(?![\d.,])")
HOUR_ONLY = re.compile(
    rf"(?<![\w])(?P<prep>{PREPOSITIONS}|all'|dall')\s*(?P<h>\d{{1,2}})(?=\s+(?:di|del|della|dalla)\s+(?:notte|mattina|mattino|pomeriggio|sera))")
DAY_MONTH = re.compile(
    rf"(?<![\d.,/])(?P<d>\d{{1,2}})(?=\s+(?:(?i:di\s+)?(?i:{MONTH})|(?:al|all'|e|ed|a)\s*\d{{1,2}}\s+(?i:di\s+)?(?i:{MONTH}))\b)")
DAY_RANGE = re.compile(
    rf"(?:\b(?:il|dal|dall')\s*)?(?<![\d.,/])(?P<a>\d{{1,2}})\s*[-–]\s*(?P<b>\d{{1,2}})(?=\s+(?i:di\s+)?(?i:{MONTH})\b)")
DECADE = re.compile(r"(?<!\d\s)\b(?P<head>[Aa]nni)\s+'?(?P<first>[0-9]0)\b(?P<tail>(?:\s*(?:,|e|ed|o)\s+'?[0-9]0\b)*)")
DECADE_2000 = re.compile(r"\b(?P<head>[Aa]nni)\s+2000\b")
DECADE_ITEM = re.compile(r"'?([0-9]0)")
CENTURY_APOSTROPHE = re.compile(r"(?<![\w'])'(?P<c>[2-9])00\b")
YEAR_APOSTROPHE = re.compile(r"(?<![\w'])(?P<art>\bil\s+)?'(?P<y>\d{2})\b")
ERA = re.compile(r"\b(?P<kind>[ad])\.\s?C\.")


class Calendar:
    def __init__(self, speaker: Speaker, quarter_and_half: bool):
        self.speaker, self.quarter_and_half = speaker, quarter_and_half

    def _day(self, day: int) -> str:
        return "primo" if day == 1 else cardinal(day)

    def dates(self, text: str) -> str:
        text = NUMERIC_DATE.sub(self._date, text)
        return ISO_DATE.sub(self._date, text)

    def _date(self, m: re.Match) -> str:
        day, month = int(m["d"]), int(m["m"])
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return m.group()
        return f"{self._day(day)} {MONTHS[month - 1]} {self.speaker.integer(int(m['y']))}"

    def day_months(self, text: str) -> str:
        """The day before a month name, and the first day of a range of days ("dal 1 al 5 maggio", "3-5 maggio" dal tre al cinque maggio): the
        1st is "primo", the others cardinals."""
        text = DAY_RANGE.sub(lambda m: f"dal {self._day(int(m['a']))} al {self._day(int(m['b']))}", text)
        return DAY_MONTH.sub(lambda m: self._day(int(m["d"])), text)

    def times(self, text: str) -> str:
        text = COLON_TIME.sub(self._clock, text)
        text = DOT_TIME.sub(self._clock, text)
        return HOUR_ONLY.sub(self._clock, text)

    def _clock(self, m: re.Match) -> str:
        groups = m.groupdict()
        hour, minute = int(m["h"]), int(groups.get("m") or 0)
        second = int(groups["s"]) if groups.get("s") else None
        if hour > 23 or minute > 59 or (second or 0) > 59:
            return m.group()
        prep = APOSTROPHE_FORMS.get(m["prep"], m["prep"])
        return self._say(hour, minute, second, prep)

    def _say(self, hour: int, minute: int, second: int | None, prep: str | None) -> str:
        noon = hour in (0, 12) and prep != "le"
        if noon:
            word = "mezzogiorno" if hour == 12 else "mezzanotte"
        else:
            word = "una" if hour == 1 else cardinal(hour)
        if minute == 0:
            tail = ""
        elif self.quarter_and_half and hour <= 12 and minute in (15, 30):
            tail = " e un quarto" if minute == 15 else " e mezza"
        else:
            tail = f" e {cardinal(minute)}"
        if second is not None:
            tail += f" e {counted(second, 'secondo' if second == 1 else 'secondi')}"
        if noon:
            lead = f"{NOON_PREPOSITION[prep]} " if prep in NOON_PREPOSITION else (f"{prep} " if prep else "")
        elif hour == 1:
            lead = CONTRACTED.get(prep, f"{prep} ") if prep else "l'"
        else:
            lead = f"{prep} " if prep else ""
        return f"{lead}{word}{tail}"

    def decades(self, text: str) -> str:
        """"anni '90", "anni 90", "anni '70 e '80" -> anni Novanta, anni Settanta e Ottanta; "anni 2000" anni Duemila; "'900" Novecento."""
        text = DECADE.sub(lambda m: f"{m['head']} {DECADES[m['first']]}{DECADE_ITEM.sub(lambda i: DECADES[i[1]], m['tail'])}", text)
        text = DECADE_2000.sub(lambda m: f"{m['head']} Duemila", text)
        return CENTURY_APOSTROPHE.sub(lambda m: CENTURIES[m["c"]], text)

    def apostrophe_years(self, text: str) -> str:
        """"nel '68" nel sessantotto; after "il" it is the year as a name: "il '68" il Sessantotto."""
        def year(m: re.Match) -> str:
            spoken = cardinal(int(m["y"]))
            return f"{m['art']}{spoken.capitalize()}" if m["art"] else spoken
        return YEAR_APOSTROPHE.sub(year, text)

    def eras(self, text: str) -> str:
        return ERA.sub(lambda m: "avanti Cristo" if m["kind"] == "a" else "dopo Cristo", text)
