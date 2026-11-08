"""Money, percentages, units, calibres, fractions and "1,5 million" in French."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import NamedTuple

from app.documentary.spoken_numbers import fr_words as W
from app.documentary.spoken_numbers.fr_core import NUM, NUM_ENGLISH, SP, Speaker


class Currency(NamedTuple):
    singular: str
    plural: str
    minor_singular: str
    minor_plural: str
    feminine: bool = False


_SCALE_FACTORS = {"k": 10**3, "M": 10**6, "Md": 10**9, "Mrd": 10**9}
_SCALE_WORDS = {"million": 10**6, "milliard": 10**9, "billion": 10**12}
_SYMBOLS = {"€": "EUR", "EUR": "EUR", "$": "USD", "USD": "USD", "£": "GBP", "GBP": "GBP", "CHF": "CHF", "FF": "FF"}
_WORD_CURRENCIES = {"euro": "EUR", "dollar": "USD", "franc": "FF"}
# Unit symbol -> (singular, plural). The symbols with a slash or an exponent come first in the pattern.
_UNITS = {
    "km/h": ("kilomètre par heure", "kilomètres par heure"), "m/s": ("mètre par seconde", "mètres par seconde"), "km²": ("kilomètre carré", "kilomètres carrés"),
    "m²": ("mètre carré", "mètres carrés"), "m³": ("mètre cube", "mètres cubes"), "km": ("kilomètre", "kilomètres"), "cm": ("centimètre", "centimètres"),
    "mm": ("millimètre", "millimètres"), "mg": ("milligramme", "milligrammes"), "ml": ("millilitre", "millilitres"), "cl": ("centilitre", "centilitres"),
    "ha": ("hectare", "hectares"), "°C": ("degré", "degrés"), "°F": ("degré Fahrenheit", "degrés Fahrenheit"), "°": ("degré", "degrés"),
    "g": ("gramme", "grammes"), "l": ("litre", "litres"), "L": ("litre", "litres"), "m": ("mètre", "mètres"), "kg": ("kilo", "kilos")}
_UNIT_PATTERN = "|".join(sorted(map(re.escape, _UNITS), key=len, reverse=True))
_CURRENCY_PATTERN = r"€|\$|£|EUR\b|USD\b|GBP\b|CHF\b|FF\b"
_MONEY_AFTER = re.compile(rf"({NUM})(?:{SP}?(Mrd|Md|M|k)(?=\s?(?:{_CURRENCY_PATTERN})))?{SP}?({_CURRENCY_PATTERN})")
_MONEY_WORD = re.compile(rf"({NUM}){SP}((?i:euros?|dollars?|francs?))\b")
_MONEY_BEFORE = re.compile(rf"([€$£]){SP}?({NUM_ENGLISH}|{NUM})")
_PERCENT = re.compile(rf"({NUM}){SP}?(%|‰)")
_HEIGHT = re.compile(rf"(?<![\d,.])([12]),(\d{{2}}){SP}?m(?![\w²³/'’])")
_HEIGHT_SPLIT = re.compile(rf"(?<![\d,.])([12]){SP}?m{SP}(\d{{2}})(?![\d\w])")
_UNIT = re.compile(rf"({NUM}){SP}?({_UNIT_PATTERN})(?![\w²³/'’])")
_CALIBRE_WORD = re.compile(rf"((?i:calibres?){SP}+)\.?(\d+)(?!\d)(?!{SP}?(?:mm|cm)\b)(?:{SP}(LR)\b)?")
_DESIGNATORS = "Special|Magnum|ACP|S&W|Auto|Colt|Super|Long|Rimfire"
_CALIBRE_DOT = re.compile(rf"(?<![\w.,])\.(\d{{2,3}})(?!\d)(?:{SP}(LR)\b|(?={SP}(?:{_DESIGNATORS})\b))")
_CALIBRE_CUE = re.compile(rf"((?i:revolver|pistolet|colt|magnum|arme){SP})\.(\d{{2,3}})(?!\d)(?:{SP}(LR)\b)?")
_ROUND_UP = re.compile(rf"({NUM})({SP})(millions?|milliards?|billions?)\b(?:{SP}(d['’]|de{SP}))?")
_FRACTION = re.compile(rf"(?<![\d/.,])(\d{{1,3}})/(\d{{1,3}})(?![\d/])(?:{SP}(heure|minute|journée|semaine|année|douzaine|litre|kilo|kilogramme|mètre|kilomètre|tour|siècle|finale)\b)?")
_ALL_DAY = re.compile(r"(?<![\d/])24/7(?![\d/])")
_FRACTION_SIGNS = {"½": "un demi", "¼": "un quart", "¾": "trois quarts", "⅓": "un tiers", "⅔": "deux tiers"}
_DENOMINATORS = {2: "demi", 3: "tiers", 4: "quart"}


class MeasureRules(Speaker):
    def _currency(self, code: str) -> Currency:
        return {
            "EUR": Currency("euro", "euros", "centime", "centimes"),
            "USD": Currency(self.opts.dollar_singular, self.opts.dollar_plural, "cent", "cents"),
            "GBP": Currency("livre sterling", "livres sterling", "penny", "pence", True),
            "CHF": Currency("franc suisse", "francs suisses", "centime", "centimes"),
            "FF": Currency("franc", "francs", "centime", "centimes"),
        }[code]

    @staticmethod
    def _particle(unit: str) -> str:
        return "d'" if unit[0].lower() in "aeiouhéèêàâîôû" else "de "

    def _amount(self, value: int, currency: Currency) -> str:
        """A whole amount; "de"/"d'" is said when the number ends on million/milliard ("trois millions d'euros")."""
        unit = currency.singular if value in (0, 1) else currency.plural
        said = W.cardinal(value, feminine=currency.feminine)
        particle = self._particle(unit) if value and value % 10**6 == 0 else ""
        return f"{said} {particle}{unit}"

    def _money(self, number: str, code: str, factor: int | None = None, english: bool = False) -> str:
        integer, decimal = self.split(number, english)
        currency = self._currency(code)
        if factor:
            amount = Decimal(f"{integer}.{decimal or 0}") * factor
            if amount == amount.to_integral_value():
                return self._amount(int(amount), currency)
        if decimal is None:
            return self._amount(int(integer), currency)
        if len(decimal) > 2:
            return f"{self.say_number(number, english=english)} {currency.singular if Decimal(f'{integer}.{decimal}') < 2 else currency.plural}"
        whole, cents = int(integer), int(decimal.ljust(2, "0"))
        if not cents:
            return self._amount(whole, currency)
        if not whole:
            return f"{W.cardinal(cents)} {currency.minor_singular if cents == 1 else currency.minor_plural}"
        return f"{self._amount(whole, currency)} {W.cardinal(cents)}"

    def money(self, text: str) -> str:
        def after(match: re.Match) -> str:
            return self._money(match.group(1), _SYMBOLS[match.group(3)], _SCALE_FACTORS.get(match.group(2) or ""))

        def word(match: re.Match) -> str:
            if not re.search(r"[,.]\d", match.group(1)):
                return match.group()
            return self._money(match.group(1), _WORD_CURRENCIES[match.group(2).lower().rstrip("s")])

        def before(match: re.Match) -> str:
            return self._money(match.group(2), _SYMBOLS[match.group(1)], english=True)

        for pattern, handler in ((_MONEY_AFTER, after), (_MONEY_WORD, word), (_MONEY_BEFORE, before)):
            text = pattern.sub(handler, text)
        return text

    def percents(self, text: str) -> str:
        return _PERCENT.sub(lambda m: f"{self.say_number(m.group(1))} {'pour cent' if m.group(2) == '%' else 'pour mille'}", text)

    def _unit_word(self, symbol: str, number: str) -> str:
        integer, decimal = self.split(number)
        singular, plural = _UNITS[symbol]
        if symbol == "kg":
            singular, plural = self.opts.mass_word, f"{self.opts.mass_word}s"
        if symbol == "°C" and self.opts.say_celsius:
            singular, plural = "degré Celsius", "degrés Celsius"
        return singular if Decimal(f"{integer}.{decimal or 0}") < 2 else plural

    def units(self, text: str) -> str:
        """Heights ("1,80 m" = "un mètre quatre-vingts") first, then every number followed by a unit symbol."""
        def height(match: re.Match) -> str:
            value = Decimal(f"{match.group(1)}.{match.group(2)}")
            if not Decimal("1.40") <= value <= Decimal("2.20"):
                return match.group()
            return f"{W.cardinal(int(match.group(1)))} mètre {W.cardinal(int(match.group(2)))}"

        def split_height(match: re.Match) -> str:
            return f"{W.cardinal(int(match.group(1)))} mètre{'s' if int(match.group(1)) > 1 else ''} {W.cardinal(int(match.group(2)))}"

        def unit(match: re.Match) -> str:
            word = self._unit_word(match.group(2), match.group(1))
            integer, decimal = self.split(match.group(1))
            particle = self._particle(word) if decimal is None and int(integer) and int(integer) % 10**6 == 0 else ""
            return f"{self.say_number(match.group(1))} {particle}{word}"

        text = _HEIGHT.sub(height, text)
        text = _HEIGHT_SPLIT.sub(split_height, text)
        return _UNIT.sub(unit, text)

    def calibres(self, text: str) -> str:
        """".38" is "trente-huit" (a number, never "point trente-huit"); ".22 LR" is "vingt-deux long rifle"."""
        long_rifle = " long rifle"
        text = _CALIBRE_WORD.sub(lambda m: f"{m.group(1)}{self.say_int(m.group(2))}{long_rifle if m.group(3) else ''}", text)
        text = _CALIBRE_CUE.sub(lambda m: f"{m.group(1)}{self.say_int(m.group(2))}{long_rifle if m.group(3) else ''}", text)
        return _CALIBRE_DOT.sub(lambda m: f"{self.say_int(m.group(1))}{long_rifle if m.group(2) else ''}", text)

    def million_words(self, text: str) -> str:
        """"1,5 million d'euros" is "un million cinq cent mille euros" (OQLF: that is how it is pronounced); the "de" falls away."""
        def scaled(match: re.Match) -> str:
            integer, decimal = self.split(match.group(1))
            if decimal is None:
                return match.group()
            amount = Decimal(f"{integer}.{decimal}") * _SCALE_WORDS[match.group(3).rstrip("s")]
            if amount != amount.to_integral_value():
                return match.group()
            return W.cardinal(int(amount)) + (" " if match.group(4) else "")

        return _ROUND_UP.sub(scaled, text)

    def fractions(self, text: str) -> str:
        def fraction(match: re.Match) -> str:
            numerator, denominator, noun = int(match.group(1)), int(match.group(2)), match.group(3)
            if not (2 <= denominator <= 10 and 0 < numerator < denominator):
                return f"{W.cardinal(numerator)} sur {W.cardinal(denominator)}" + (f" {noun}" if noun else "")
            if denominator == 2 and noun:
                return f"{'une' if noun in W.FEMININE_NOUNS else 'un'} demi-{noun}"
            word = _DENOMINATORS.get(denominator) or W.ordinal(denominator)
            plural = "s" if numerator > 1 and word != "tiers" else ""
            return f"{W.cardinal(numerator)} {word}{plural}" + (f" {noun}" if noun else "")

        text = _ALL_DAY.sub("vingt-quatre heures sur vingt-quatre, sept jours sur sept", text)
        for sign, said in _FRACTION_SIGNS.items():
            text = text.replace(sign, said)
        return _FRACTION.sub(fraction, text)
