"""Measures and quantities: units, temperatures, coordinates, percentages, calibres, fractions, ranges and scores."""

from __future__ import annotations

import re

from app.documentary.spoken_numbers.es_lexicon import COUNTED_NOUNS, UNIT_PATTERN, UNITS, gender_of
from app.documentary.spoken_numbers.es_speech import CLOSE, OPEN, SIGNED_NUMBER, START, Rule

FRACTION_NAMES = {2: ("medio", "medios"), 3: ("tercio", "tercios"), 4: ("cuarto", "cuartos"), 5: ("quinto", "quintos"), 6: ("sexto", "sextos"),
                  7: ("séptimo", "séptimos"), 8: ("octavo", "octavos"), 9: ("noveno", "novenos"), 10: ("décimo", "décimos"),
                  100: ("centésimo", "centésimos"), 1000: ("milésimo", "milésimos")}
UNICODE_FRACTIONS = {"½": (1, 2), "¼": (1, 4), "¾": (3, 4), "⅓": (1, 3), "⅔": (2, 3), "⅕": (1, 5), "⅛": (1, 8)}
ACCENTS = str.maketrans("éíóúü", "eiouu")
LATITUDE = {"N": "latitud norte", "S": "latitud sur", "E": "longitud este", "O": "longitud oeste", "W": "longitud oeste"}


class Units(Rule):
    """A number and its unit symbol ("120 km/h", "9 mm", "35 °C", "2 h 30 min"): the unit is spoken in full, singular only for exactly one, and
    "un/una" or "veintiún/veintiuna" follows the unit's gender."""

    UNIT = re.compile(rf"{SIGNED_NUMBER}\s?(?P<unit>{UNIT_PATTERN})(?![\w{CLOSE}])")
    DURATION = re.compile(rf"{START}(?P<h>\d+)\s?(?:h|hr|hrs)\.?\s+(?P<m>\d+)\s?min(?![\w{CLOSE}])(?:\s+(?P<s>\d+)\s?s(?![\w{CLOSE}]))?")
    TEMPERATURE = re.compile(rf"{SIGNED_NUMBER}\s?[°º]\s?(?P<scale>[CcFf])(?![\w{CLOSE}])")
    DEGREES = re.compile(rf"{SIGNED_NUMBER}\s?°(?![\w{CLOSE}])")

    def apply(self, text: str) -> str:
        text = self.DURATION.sub(self._duration, text)
        text = self.TEMPERATURE.sub(self._temperature, text)
        text = self.UNIT.sub(self._unit, text)
        return self.DEGREES.sub(lambda m: self._with_noun(m, "grado", "grados", "m"), text)

    def _with_noun(self, match: re.Match, singular: str, plural: str, gender: str, suffix: str = "") -> str:
        words, one = self.speech.number(match["num"], gender, True, bool(match["sign"]))
        return f"{words} {singular if one else plural}{suffix}"

    def _unit(self, match: re.Match) -> str:
        unit = UNITS[match["unit"]]
        return self._with_noun(match, unit.singular, unit.plural, unit.gender)

    def _temperature(self, match: re.Match) -> str:
        fahrenheit = match["scale"] in "fF"
        return self._with_noun(match, "grado", "grados", "m", " Fahrenheit" if fahrenheit else " centígrados")

    def _duration(self, match: re.Match) -> str:
        hours, minutes = int(match["h"]), int(match["m"])
        words = (f"{self.speech.cardinal(hours, 'f')} {'hora' if hours == 1 else 'horas'} con "
                 f"{self.speech.cardinal(minutes, 'm')} {'minuto' if minutes == 1 else 'minutos'}")
        if match["s"]:
            seconds = int(match["s"])
            words += f" y {self.speech.cardinal(seconds, 'm')} {'segundo' if seconds == 1 else 'segundos'}"
        return words


class Coordinates(Rule):
    """"19°25′42″N" as degrees, minutes and seconds of latitude or longitude. Decimal coordinates are read digit by digit by the generic rule."""

    DMS = re.compile(rf"{START}(?P<d>\d{{1,3}})°\s?(?P<m>\d{{1,2}})[′']\s?(?:(?P<s>\d{{1,2}}(?:\.\d+)?)(?:″|\"|'')\s?)?(?P<hemisphere>[NSEOW])(?![\w{CLOSE}])")

    def apply(self, text: str) -> str:
        return self.DMS.sub(self._coordinate, text)

    def _coordinate(self, match: re.Match) -> str:
        speech = self.speech
        parts = [f"{speech.cardinal(int(match['d']))} grados", f"{speech.cardinal(int(match['m']))} minutos"]
        if match["s"]:
            parts.append(f"{speech.number(match['s'])[0]} segundos")
        return ", ".join(parts) + " " + LATITUDE[match["hemisphere"]]


class Percentages(Rule):
    """"3.5 %" as "tres punto cinco por ciento"; "1 %" as "uno por ciento"; "‰" as "por mil"."""

    PATTERN = re.compile(rf"{SIGNED_NUMBER}\s?(?P<mark>[%‰])")

    def apply(self, text: str) -> str:
        return self.PATTERN.sub(self._percent, text)

    def _percent(self, match: re.Match) -> str:
        words = self.speech.number(match["num"], None, True, bool(match["sign"]))[0]
        return words + (" por ciento" if match["mark"] == "%" else " por mil")


class Calibres(Rule):
    """Gun calibres: ".38", "calibre .45", "calibre 12" are said as the plain number ("calibre treinta y ocho"), not "punto treinta y ocho"."""

    NAMED = re.compile(rf"(?P<word>(?i:calibre|cal\.))\s+\.?(?P<num>\d{{2,3}})(?![\w{CLOSE}\d])(?!\s?(?:mm|milímetros)\b)")
    BARE = re.compile(rf"(?<![\w{OPEN}.,\d$])\.(?P<num>\d{{2,3}})(?![\w{CLOSE}\d])")

    def apply(self, text: str) -> str:
        text = self.NAMED.sub(lambda m: f"{'calibre' if m['word'].lower() == 'cal.' else m['word']} {self.speech.cardinal(int(m['num']))}", text)
        return self.BARE.sub(lambda m: self.speech.cardinal(int(m["num"])), text)


class Fractions(Rule):
    """"3/4" as "tres cuartos", "1/2 kilo" as "medio kilo", "2 1/2" as "dos y medio", denominators above ten as "-avos" ("doceavos")."""

    MIXED = re.compile(rf"{START}(?P<whole>\d+)\s+(?P<top>\d{{1,3}})/(?P<bottom>\d{{1,3}})(?![\w{CLOSE}/\d])(?:\s+(?P<noun>[^\W\d_]+))?")
    SIMPLE = re.compile(rf"{START}(?P<top>\d{{1,3}})/(?P<bottom>\d{{1,3}})(?![\w{CLOSE}/\d])")
    UNICODE = re.compile(rf"(?:(?P<whole>\d+)\s?)?(?P<mark>[{''.join(UNICODE_FRACTIONS)}])")

    def apply(self, text: str) -> str:
        text = self.UNICODE.sub(self._ascii, text)
        text = self.MIXED.sub(self._mixed, text)
        return self.SIMPLE.sub(lambda m: self._simple(m, m.group(), int(m["top"]), int(m["bottom"])), text)

    @staticmethod
    def _ascii(match: re.Match) -> str:
        """"2½" as "2 1/2" so the one set of rules reads every fraction."""
        top, bottom = UNICODE_FRACTIONS[match["mark"]]
        return f"{match['whole']} {top}/{bottom}" if match["whole"] else f"{top}/{bottom}"

    def name(self, top: int, bottom: int) -> str | None:
        if bottom < 2:
            return None
        if bottom in FRACTION_NAMES:
            return FRACTION_NAMES[bottom][0 if top == 1 else 1]
        return self._avos(bottom) + ("" if top == 1 else "s")

    def _avos(self, bottom: int) -> str:
        """"doceavo", "quinceavo", "treintavo": the cardinal without its accent or final "a", plus "avo"."""
        words = self.speech.cardinal(bottom, "m").translate(ACCENTS)
        return words.removesuffix("a") + "avo"

    def _simple(self, match: re.Match, original: str, top: int, bottom: int) -> str:
        name = self.name(top, bottom)
        if name is None or top == 0:
            return original
        if top == 1:
            gender = self.speech.gender_after(match.string, match.end())
            if bottom == 2 and gender:
                return "media" if gender == "f" else "medio"
            return "un " + name
        return f"{self.speech.cardinal(top)} {name}"

    def _mixed(self, match: re.Match) -> str:
        """"2 1/2 horas" is "dos horas y media": a counted noun after the figure moves between the whole and the fraction."""
        top, bottom, whole = int(match["top"]), int(match["bottom"]), int(match["whole"])
        name = self.name(top, bottom)
        if name is None or top == 0:
            return match.group()
        noun = match["noun"] if match["noun"] in COUNTED_NOUNS else None
        gender = gender_of(noun) if noun else None
        if top == 1 and bottom == 2 and noun:
            name = "media" if gender == "f" else "medio"
        fraction = name if bottom in (2, 4) and top == 1 else f"{self.speech.cardinal(top)} {name}"
        if not noun:
            return f"{self.speech.cardinal(whole)} y {fraction}" + (f" {match['noun']}" if match["noun"] else "")
        shown = noun if whole != 1 else (noun[:-2] if noun.endswith("es") else noun[:-1])
        return f"{self.speech.cardinal(whole, gender)} {shown} y {fraction}"


class Ranges(Rule):
    """Year ranges ("1990-1995": "de mil novecientos noventa a mil novecientos noventa y cinco"), counted ranges ("5-10 años": "de cinco a diez
    años"), scores and ratios ("3-1", "3:1": "tres a uno"), "4x4" and "9 x 19" ("por"), and "24/7"."""

    YEARS = re.compile(rf"{START}(?P<a>1\d{{3}}|20\d{{2}})\s?[–-]\s?(?P<b>1\d{{3}}|20\d{{2}}|\d{{2}})(?![\w{CLOSE}\d])")
    COUNTED = re.compile(rf"{START}(?P<a>\d+)\s?[–-]\s?(?P<b>\d+)(?=\s+(?:{'|'.join(sorted(COUNTED_NOUNS))})\b)")
    SCORE = re.compile(rf"(?<![\w{OPEN}.,/:-])(?P<a>\d{{1,2}})\s?[-–:]\s?(?P<b>\d{{1,2}})(?![\w{CLOSE}\d/:-])")
    CROSS = re.compile(rf"{START}(?P<a>\d+(?:\.\d+)?)\s?[x×]\s?(?P<b>\d+(?:\.\d+)?)(?![\w{CLOSE}])")
    ALWAYS_OPEN = re.compile(rf"{START}24/7(?![\w{CLOSE}/])")
    BEFORE_RANGE = re.compile(r"\b(?:de|del|desde|entre)\s$", re.IGNORECASE)

    def apply(self, text: str) -> str:
        text = self.YEARS.sub(self._years, text)
        text = self.COUNTED.sub(self._counted, text)
        text = self.SCORE.sub(lambda m: f"{self.speech.cardinal(int(m['a']))} a {self.speech.cardinal(int(m['b']))}", text)
        text = self.CROSS.sub(lambda m: f"{self.speech.number(m['a'])[0]} por {self.speech.number(m['b'])[0]}", text)
        return self.ALWAYS_OPEN.sub("veinticuatro siete", text)

    def _lead(self, match: re.Match) -> str:
        """"de" in front of a range unless the text already opened it ("entre", "de", "desde")."""
        return "" if self.BEFORE_RANGE.search(match.string[:match.start()]) else "de "

    def _years(self, match: re.Match) -> str:
        first, second = match["a"], match["b"]
        if len(second) == 2 and int(second) <= int(first[2:]):
            return match.group()
        joiner = "y" if re.search(r"\bentre\s$", match.string[:match.start()], re.IGNORECASE) else "a"
        return f"{self._lead(match)}{self.speech.cardinal(int(first))} {joiner} {self.speech.cardinal(int(second))}"

    def _counted(self, match: re.Match) -> str:
        gender = self.speech.gender_after(match.string, match.end())
        return f"{self._lead(match)}{self.speech.cardinal(int(match['a']), gender)} a {self.speech.cardinal(int(match['b']), gender)}"
