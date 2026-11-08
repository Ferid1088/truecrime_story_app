"""Italian ordinals written two ways: roman numerals (only where a trigger word makes them a number: "Luigi XIV", "XIX secolo", "II Guerra Mondiale",
"via XX Settembre") and a digit with an ordinal mark ("1º", "2ª", "3° piano"). A bare "I", "V" or "MI" is never touched: they are words and letters too."""

from __future__ import annotations

import re

from app.documentary.spoken_numbers.it_words import (
    Speaker,
    cardinal,
    counted,
    is_feminine,
    ordinal,
    roman_value,
)

ROMAN_TOKEN = r"[IVXLC]{1,7}"
MALE_RULERS = (
    "Giovanni Paolo|Vittorio Emanuele|Giovanni|Paolo|Pio|Benedetto|Leone|Gregorio|Clemente|Urbano|Innocenzo|Alessandro|Sisto|Pietro|Luigi|Carlo|Enrico|"
    "Federico|Umberto|Napoleone|Filippo|Francesco|Ferdinando|Giorgio|Edoardo|Guglielmo|Ludovico|Nicola|Stefano|Adriano|Marcello|Callisto|Celestino|"
    "Bonifacio|Martino|Eugenio|Niccolò|Sergio|Silvestro")
FEMALE_RULERS = ("Elisabetta", "Vittoria", "Caterina", "Margherita", "Maria", "Giovanna", "Isabella", "Anna")
MONTHS = "gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
# After a degree number these words (or nothing) mean degrees of an angle or a place, not "3rd": "45° di latitudine", "45° N".
DEGREE_WORDS = frozenset(["di", "c", "f", "n", "s", "e", "o", "w", "nord", "sud", "est", "ovest", "celsius", "centigradi", "fahrenheit", "latitudine", "longitudine"])

CENTURY = re.compile(rf"(?<![\w])(?P<r>{ROMAN_TOKEN})\s+(?P<noun>(?i:secolo|millennio)|sec\.)")
ROMAN_BEFORE_NOUN = re.compile(
    rf"(?<![\w])(?P<r>{ROMAN_TOKEN})\s+(?P<noun>(?i:guerra mondiale|guerra|reich|repubblica|dinastia|legislatura|sinfonia|armata|flotta|divisione|"
    r"legione|olimpiade|internazionale|edizione|crociata))\b")
ROMAN_AFTER_NOUN = re.compile(
    rf"\b(?P<noun>(?i:capitolo|atto|libro|parte|volume|tomo|titolo|sezione|scena|canto|tavola))\s+(?P<r>{ROMAN_TOKEN})\b(?![\w.'])")
RULER = re.compile(
    rf"\b(?P<name>(?:Papa\s+)?(?:{MALE_RULERS}|{'|'.join(FEMALE_RULERS)}))\s+(?P<r>{ROMAN_TOKEN})(?![\w.'])")
STREET = re.compile(
    rf"\b(?P<street>(?i:via|viale|piazza|corso|largo|piazzale|vicolo|ponte))\s+(?P<r>{ROMAN_TOKEN})\s+(?P<month>(?i:{MONTHS}))\b")
MARK = re.compile(r"(?<![\d.,])(?P<n>\d+)\s?(?P<mark>[º°ª])(?:(?P<sp>\s+)(?P<noun>[^\W\d_]+))?")


class Ordinals:
    def __init__(self, speaker: Speaker):
        self.speaker = speaker

    def _roman_ordinal(self, token: str, feminine: bool) -> str | None:
        value = roman_value(token)
        return None if value is None else ordinal(value, feminine)

    def romans(self, text: str) -> str:
        text = STREET.sub(self._street, text)
        text = CENTURY.sub(self._century, text)
        text = ROMAN_BEFORE_NOUN.sub(self._before_noun, text)
        text = ROMAN_AFTER_NOUN.sub(self._after_noun, text)
        return RULER.sub(self._ruler, text)

    def _street(self, m: re.Match) -> str:
        value = roman_value(m["r"])
        if value is None:
            return m.group()
        number = "primo" if value == 1 else cardinal(value)
        return f"{m['street']} {number.capitalize()} {m['month'].capitalize()}"

    def _century(self, m: re.Match) -> str:
        spoken = self._roman_ordinal(m["r"], False)
        return m.group() if spoken is None else f"{spoken} {'secolo' if m['noun'] == 'sec.' else m['noun']}"

    def _before_noun(self, m: re.Match) -> str:
        spoken = self._roman_ordinal(m["r"], is_feminine(m["noun"].split()[0], False))
        return m.group() if spoken is None else f"{spoken} {m['noun']}"

    def _after_noun(self, m: re.Match) -> str:
        spoken = self._roman_ordinal(m["r"], is_feminine(m["noun"], False))
        return m.group() if spoken is None else f"{m['noun']} {spoken}"

    def _ruler(self, m: re.Match) -> str:
        feminine = m["name"].removeprefix("Papa ").strip() in FEMALE_RULERS
        spoken = self._roman_ordinal(m["r"], feminine)
        return m.group() if spoken is None else f"{m['name']} {spoken}"

    def marks(self, text: str) -> str:
        """"1º maggio" primo maggio, "2ª" seconda, "3° piano" terzo piano (° is read as an ordinal when a noun follows, as a degree otherwise)."""
        return MARK.sub(self._mark, text)

    def _mark(self, m: re.Match) -> str:
        n, mark, noun = int(m["n"]), m["mark"], m["noun"]
        tail = f"{m['sp']}{noun}" if noun else ""
        if mark == "°" and (noun is None or noun.lower() in DEGREE_WORDS):
            return f"{counted(n, 'grado' if n == 1 else 'gradi')}{tail}" if n <= self.speaker.max_value else m.group()
        feminine = mark == "ª" or (mark == "°" and is_feminine(noun, False))
        spoken = ordinal(n, feminine) or self.speaker.integer(n)
        return f"{spoken}{tail}"
