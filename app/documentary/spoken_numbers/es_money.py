"""Money: "$1,250.50" as "mil doscientos cincuenta pesos con cincuenta centavos", "$5 millones" as "cinco millones de pesos", dollars, euros, pounds."""

from __future__ import annotations

import re

from app.documentary.spoken_numbers.es_lexicon import (
    CODE_CURRENCY,
    CURRENCIES,
    SCALES,
    SYMBOL_CURRENCY,
    Currency,
)
from app.documentary.spoken_numbers.es_speech import CLOSE, NUMBER, OPEN, START, Rule

SCALE_WORDS = "mil millones|millones|millón|mil|mdp|mdd"
CODE_WORDS = r"M\.N\.|MXN|USD|EUR|GBP|dlls?\.?|dólares|pesos mexicanos|pesos|euros|libras|mdp|mdd"
SYMBOLS = r"US\$|U\$S|MXN\$|MX\$|\$|€|£|¥|USD|MXN|EUR|GBP"


class Currencies(Rule):
    """An amount with a currency symbol or code. A bare "$" is the option `dollar_sign_currency` (pesos for Mexico); a word or code after the
    amount ("$500 dólares", "500 USD") decides over the symbol. Centavos are said after "con"; whole millions take "de" before the currency."""

    BEFORE = re.compile(rf"(?<![\w{OPEN}])(?P<symbol>{SYMBOLS})\s?(?P<num>{NUMBER})(?:\s?(?P<scale>{SCALE_WORDS})(?![\w{CLOSE}]))?"
                        rf"(?:\s?(?P<code>{CODE_WORDS})(?![\w{CLOSE}]))?")
    AFTER = re.compile(rf"{START}(?P<num>{NUMBER})(?:\s?(?P<scale>{SCALE_WORDS})(?![\w{CLOSE}]))?\s?(?P<code>€|EUR|USD|MXN|GBP|dlls?\.?|M\.N\.|mdp|mdd)"
                       rf"(?![\w{CLOSE}])")

    def apply(self, text: str) -> str:
        text = self.BEFORE.sub(self._before, text)
        return self.AFTER.sub(self._after, text)

    def _before(self, match: re.Match) -> str:
        symbol = match["symbol"]
        currency = SYMBOL_CURRENCY.get(symbol, "peso" if self.speech.options.dollar_sign_currency == "peso" else "dolar")
        return self._amount(match, currency)

    def _after(self, match: re.Match) -> str:
        return self._amount(match, CODE_CURRENCY[match["code"]])

    def _amount(self, match: re.Match, currency_key: str) -> str:
        code = match["code"]
        if code and code != "M.N." and code in CODE_CURRENCY:
            currency_key = CODE_CURRENCY[code]
        scale = match["scale"] or (code if code in ("mdp", "mdd") else None)
        currency = CURRENCIES[currency_key]
        if currency_key == "euro" and self.speech.options.euro_cents == "centavos":
            currency = Currency(currency.singular, currency.plural, currency.gender, "centavo", "centavos")
        return self._scaled(match, scale, currency) if scale else self._plain(match["num"], currency)

    def _scaled(self, match: re.Match, scale: str, currency: Currency) -> str:
        multiplier, scale_word = SCALES[scale]
        words, _ = self.speech.scaled(match["num"], multiplier, scale_word, currency.gender)
        return f"{words}{' de' if multiplier >= 10 ** 6 else ''} {currency.plural}"

    def _plain(self, token: str, currency: Currency) -> str:
        speech = self.speech
        parsed = speech.parse(token)
        if parsed is None:
            return f"{speech.number(token)[0]} {currency.plural}"
        whole, fraction = parsed
        if fraction is not None and (len(fraction) > 2 or not currency.minor_plural):
            return f"{speech.number(token)[0]} {currency.plural}"
        minor = int((fraction or "0").ljust(2, "0"))
        parts = []
        if whole or not minor:
            parts.append(f"{speech.cardinal(whole, currency.gender)}{' de' if speech.needs_de(whole) else ''} "
                         f"{currency.singular if whole == 1 else currency.plural}")
        if minor:
            minor_name = currency.minor_singular if minor == 1 else currency.minor_plural
            parts.append(f"{speech.cardinal(minor, 'm')} {minor_name}")
        return " con ".join(parts)
