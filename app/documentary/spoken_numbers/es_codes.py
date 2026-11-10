"""Numbers said digit by digit: emergency numbers, telephone numbers and postal codes."""

from __future__ import annotations

import re

from app.documentary.spoken_numbers.es_lexicon import COUNTED_NOUNS
from app.documentary.spoken_numbers.es_speech import CLOSE, OPEN, Rule
from app.documentary.spoken_numbers.es_words import digits_words

EMERGENCY = frozenset({"911", "089", "066", "065"})
SENTENCE_END = re.compile(r"[.!?¿¡\n]")
EMERGENCY_CONTEXT = re.compile(r"\b(?:llam\w*|marc\w*|dial\w*|tel[eé]fono|n[uú]mero|l[ií]nea|emergencia\w*|urgencia\w*|denunci\w*|C5|report\w*|avis\w*|"
                               r"solicit\w*|operador\w*|despachador\w*|centro de atención)\b|\b(?:el|del|al)\s*$", re.IGNORECASE)
EMERGENCY_AFTER = re.compile(r"\s+de\s+(?:emergencias?|denuncias?|urgencias?)\b|\s+(?:anónim\w+)\b")


class EmergencyNumbers(Rule):
    """911, 089, 066, 065. Said digit by digit ("nueve uno uno") when the sentence says it is a number to dial ("llamó al 911", "el número 911",
    "línea 089", "antes del 911": the number with its article is the service) and not a count ("911 pesos"); otherwise it is a plain number ("novecientos once"). The option can force either way."""

    PATTERN = re.compile(rf"(?<![\w{OPEN}.,])(?P<num>{'|'.join(sorted(EMERGENCY))})(?![\w{CLOSE}]|[.,]\d)")

    def apply(self, text: str) -> str:
        mode = self.speech.options.emergency_numbers
        if mode == "number_always":
            return text
        return self.PATTERN.sub(lambda m: digits_words(m["num"]) if mode == "digits_always" or self._dialled(m) else m.group(), text)

    def _dialled(self, match: re.Match) -> bool:
        text = match.string
        following = self.speech.next_word(text, match.end())
        if following and following.lower() in COUNTED_NOUNS:
            return False
        sentence = SENTENCE_END.split(text[:match.start()])[-1]
        return bool(EMERGENCY_CONTEXT.search(sentence) or EMERGENCY_AFTER.match(text, match.end()))


class PostalCodes(Rule):
    """"C.P. 06600" and "código postal 06600" digit by digit."""

    PATTERN = re.compile(rf"(?P<lead>(?i:C\.?\s?P\.?|código postal))\s+(?P<code>\d{{5}})(?![\w{CLOSE}\d])")

    def apply(self, text: str) -> str:
        return self.PATTERN.sub(lambda m: f"{m['lead']} {digits_words(m['code'])}", text)


class Phones(Rule):
    """Mexican telephone numbers (10 digits, "55 1234 5678", "(55) 1234-5678", "+52 ...", 800 lines "01 800 123 4567") digit by digit, a pause
    between the written groups; optionally by two-digit numbers. Ten bare digits are a phone number only after a word like "teléfono"."""

    TOLL_FREE = re.compile(rf"(?<![\w{OPEN}])(?P<lada>01[\s-]?)?800[\s-]?(?P<a>\d{{3}})[\s-]?(?P<b>\d{{4}})(?![\w{CLOSE}\d])")
    GROUPED = re.compile(rf"(?<![\w{OPEN}.,])(?P<country>\+\s?52[\s-]?)?(?:\((?P<paren>\d{{2,3}})\)\s?|(?P<area>\d{{2,3}})[\s-])(?P<b>\d{{3,4}})[\s-](?P<c>\d{{4}})"
                         rf"(?![\w{CLOSE}]|[\s-]?\d)")
    PAIRS = re.compile(rf"(?<![\w{OPEN}.,])(?P<country>\+\s?52[\s-]?)?(?P<groups>\d{{2}}(?:[\s-]\d{{2}}){{4}})(?![\w{CLOSE}]|[\s-]?\d)")
    BARE = re.compile(rf"(?<![\w{OPEN}.,+])(?P<digits>\d{{10}})(?![\w{CLOSE}]|[.,]?\d)")
    PHONE_CONTEXT = re.compile(r"(?:tel[eé]fono|tel\.|celular|cel\.|whatsapp|llam\w*|contacto|número)\W*$", re.IGNORECASE)

    def apply(self, text: str) -> str:
        text = self.TOLL_FREE.sub(self._toll_free, text)
        text = self.PAIRS.sub(lambda m: self._join(m["country"], m["groups"].replace("-", " ").split()), text)
        text = self.GROUPED.sub(lambda m: self._join(m["country"], [m["paren"] or m["area"], m["b"], m["c"]]), text)
        return self.BARE.sub(self._bare, text)

    def _group(self, digits: str) -> str:
        return self.speech.pair_words(digits) if self.speech.options.phone_digits_in_pairs else digits_words(digits)

    def _join(self, country: str | None, groups: list[str]) -> str:
        spoken = ", ".join(self._group(group) for group in groups)
        return f"más cincuenta y dos, {spoken}" if country else spoken

    def _toll_free(self, match: re.Match) -> str:
        groups = ["ocho cero cero", digits_words(match["a"]), digits_words(match["b"])]
        return ", ".join((["cero uno"] if match["lada"] else []) + groups)

    def _bare(self, match: re.Match) -> str:
        if not self.PHONE_CONTEXT.search(match.string[max(0, match.start() - 30):match.start()]):
            return match.group()
        return self._join(None, [match["digits"][:2], match["digits"][2:6], match["digits"][6:]])
