"""Brazilian Portuguese numbers for the voice: the words a native documentary narrator says for the digits of a script (pt-BR only; the research and the
rule list are in the project's number-reading notes). Brazil's conventions: "." groups thousands and "," marks decimals, short scale (bilhão = 10^9),
the 1st of a month is "primeiro", years are said in full ("dois mil e sete"), and the emergency numbers (190, 192, 193 ...) are said digit by digit."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.documentary.spoken_numbers.base import PLACEHOLDER, LanguageNumbers
from app.documentary.spoken_numbers.pt_gender import WORD, feminine_after
from app.documentary.spoken_numbers.pt_words import (
    LETTERS,
    MONTHS,
    ROMAN,
    SCALE_WORDS,
    cardinal,
    digit_words,
    feminine_form,
    ordinal,
    roman_value,
)

NUM = r"\d{1,3}(?:\.\d{3})+|\d+"
BEFORE_NUMBER = r"(?<![\w.,/@#])"
# Brazilian emergency and public-utility numbers said digit by digit; 911 and 112 are foreign numbers, said as numbers unless the owner asks.
BRAZILIAN_EMERGENCY = {"136", "180", "181", "188", "190", "191", "192", "193", "197", "199"}
FOREIGN_EMERGENCY = {"112", "911"}
EMERGENCY_CONTEXT = {"ligue", "ligou", "ligar", "ligam", "ligaram", "ligando", "ligação", "ligações", "liga", "telefone", "telefonou", "telefonar",
                     "número", "numero", "disque", "discou", "discar", "chamou", "chamar", "chame", "chamada", "chamaram", "emergência", "emergencia",
                     "samu", "pm", "prf", "cvv", "bombeiros", "polícia", "policia", "denúncia", "denuncia", "central", "pelo", "ramal", "atendimento"}
VEHICLE_CONTEXT = re.compile(r"tração|picape|pickup|jipe|veículo|camionete|caminhonete|\bSUV\b|\bcarro|\bmoto\b", re.I)
DEGREE_FOLLOWERS = {"de", "do", "da", "em", "ao", "na", "no", "acima", "abaixo", "norte", "sul", "leste", "oeste", "graus", "para"}
NOT_COUNTED_NOUNS = {"mais", "menos", "depois", "antes", "pois"}
URL_OR_EMAIL = re.compile(r"(https?://\S+|www\.\S+|\S+@\S+\.\S+)")
FEMININE_NAMES = {"Elizabeth", "Isabel", "Maria", "Vitória", "Catarina", "Ana", "Joana", "Margarida", "Teresa", "Luísa", "Beatriz", "Cristina",
                  "Eduarda", "Sofia", "Carlota", "Leopoldina"}
TRIGGERS = {"século": False, "séc.": False, "milênio": False, "guerra": True, "ato": False, "capítulo": False, "parte": True, "livro": False,
            "tomo": False, "volume": False, "título": False, "seção": True, "fase": True, "inciso": False, "alínea": True}
STATES = "BR|AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO"
# unit -> (singular, plural); every unit is masculine ("dois gramas").
UNITS_OF_MEASURE = {
    "km/h": ("quilômetro por hora", "quilômetros por hora"), "km": ("quilômetro", "quilômetros"), "kg": ("quilo", "quilos"),
    "mg": ("miligrama", "miligramas"), "cm": ("centímetro", "centímetros"), "mm": ("milímetro", "milímetros"), "ml": ("mililitro", "mililitros"),
    "mL": ("mililitro", "mililitros"), "ha": ("hectare", "hectares"), "°C": ("grau Celsius", "graus Celsius"),
    "ºC": ("grau Celsius", "graus Celsius"), "°F": ("grau Fahrenheit", "graus Fahrenheit"), "ºF": ("grau Fahrenheit", "graus Fahrenheit"),
    "m²": ("metro quadrado", "metros quadrados"), "m³": ("metro cúbico", "metros cúbicos"), "g": ("grama", "gramas"), "m": ("metro", "metros"),
    "L": ("litro", "litros"),
}
CURRENCIES = {  # symbol -> (singular, plural, feminine)
    "R$": ("real", "reais", False), "US$": ("dólar", "dólares", False), "U$S": ("dólar", "dólares", False), "€": ("euro", "euros", False),
    "£": ("libra", "libras", True),
}
FRACTION_NAMES = {3: "terço", 4: "quarto", 5: "quinto", 6: "sexto", 7: "sétimo", 8: "oitavo", 9: "nono", 10: "décimo", 100: "centésimo",
                  1000: "milésimo"}
LAW_WORDS = r"(?:Lei|Decreto|Portaria|Resolução|Emenda|Súmula|lei|decreto|portaria)(?:\s+[Cc]omplementar)?(?:\s+n[úu]mero)?"
SCALE_PATTERN = "|".join(sorted(SCALE_WORDS, key=len, reverse=True))
PLATE_NOT = "ISO|NBR|RFC|DIN|IEC"


class Options(BaseModel):
    """What the owner may flip for Brazilian Portuguese; the defaults are the researched native usage."""

    emergency_numbers: Literal["digits_in_context", "digits_always", "number_always"] = "digits_in_context"  # 190 "um nove zero" only near "ligue"/SAMU...
    feminine_mil: bool = True  # "duas mil mulheres" (False: "dois mil mulheres")
    half_hour_as_meia: bool = True  # 10h30 "dez e meia" (False: "dez e trinta")
    decimal_cardinal_max_digits: int = 2  # 3,14 "três vírgula catorze"; longer decimals digit by digit
    ordinal_max: int = 99  # "21º" up to here is "vigésimo primeiro"; above it the plain number
    phone_ddd_as_number: bool = True  # (11) "onze" (False: "um um")
    six_as_meia: bool = False  # 6 as "meia" in digit-by-digit numbers (colloquial; the narrator says "seis")
    fraction_tens_as_ordinal: bool = True  # 1/20 "um vigésimo" (False: "um vinte avos")
    bare_dollar_sign: Literal["dollar", "real"] = "dollar"  # what a lone "$" means


class Numbers(LanguageNumbers):
    """Rules run in a fixed order; each rewrites a kind of number into words, so the later, more general rules never see it. `text` first
    claims the digit-and-letter tokens the base class would set aside untouched (times, units, scores, codes, plates), because a narrator says them."""

    def __init__(self, options: dict, max_value: int):
        super().__init__(options, max_value)
        self.opt = Options.model_validate(options)
        prefix, suffix = PLACEHOLDER.split("{}")
        self.placeholder = re.compile("(" + re.escape(prefix) + r"\d+" + re.escape(suffix) + ")")
        self.claims = [
            (re.compile(r"(?<![\w)\d.,])-(?=\d)"), lambda m: "menos "),
            (re.compile(rf"\b(?!(?:{PLATE_NOT})-)([A-Z]{{3}})-(\d{{4}})\b"), self.plate_old),
            (re.compile(r"\b([A-Z]{3})(\d)([A-Z])(\d{2})\b"), self.plate_mercosul),
            (re.compile(rf"\b({STATES})-(\d{{2,3}})\b"), self.road),
            (re.compile(rf"{BEFORE_NUMBER}((?:{NUM})(?:,\d+)?)\s?(km/h|km|kg|mg|cm|mm|ml|mL|ha|[°º][CF]|m[²³]|g|m|L)(?![\w/²³])"), self.measure),
            (re.compile(r"(?<![\w/@.#:])(\d{1,3})[hH](?:([0-5]\d)(min)?)?(?:\s?[-–]\s?(\d{1,2})[hH](?:([0-5]\d))?)?(?![\w])"), self.clock),
            (re.compile(r"(?<![\w:.])([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?![\w:])"), self.colon_clock),
            (re.compile(r"(?<![\w/@.#,])(\d{1,3})\s?[x×]\s?(\d{1,3})(?![\w.,])"), self.times_sign),
            (re.compile(r"(?<![\w/@.#])(\d{1,3})[xX](?![\w])"), lambda m: f"{self.say(int(m[1]))} vezes"),
            (re.compile(r"(?<![\w/@.#,])(\d{1,3})\s?(min|seg)(?![\w])"), self.short_time),
            (re.compile(r"(?<![\w/@.#])(\d{1,2})([oa])(?=\s+[a-zà-ÿ])"), self.plain_ordinal),
            (re.compile(r"(?<![\w/@.#-])([A-Z]{1,5})-?(\d{1,3})\b"), lambda m: f"{m[1]} {self.say(int(m[2]))}"),
            (re.compile(r"(?<![\w/@.#-])(\d{1,3})([A-Z])\b"), lambda m: f"{self.say(int(m[1]))} {m[2]}"),
        ]
        self.rules = [
            (re.compile(rf"{BEFORE_NUMBER}9/11\b"), lambda m: "onze de setembro"),
            (re.compile(rf"{BEFORE_NUMBER}24/7\b"), lambda m: "vinte e quatro horas por dia, sete dias por semana"),
            (re.compile(r"(?<![\w])[Nn](?:\.º|º|°)(s?)\s?(?=\d)"), lambda m: f"número{m[1]} "),
            (re.compile(rf"(R\$|US\$|U\$S|€|£|\$)\s?({NUM})(?:[,.](\d{{1,2}})(?!\d))?(?:\s?({SCALE_PATTERN})\b)?"), self.money),
            (re.compile(rf"\b({LAW_WORDS})\s+({NUM})/(\d{{4}}|\d{{2}})\b"), self.law),
            (re.compile(rf"{BEFORE_NUMBER}(\d{{1,2}})/(\d{{1,2}})/(\d{{4}})\b"), self.numeric_date),
            (re.compile(r"\b(dia|em|no dia|até|desde)\s(\d{1,2})/(\d{1,2})\b(?![/\d])"), self.short_date),
            (re.compile(rf"{BEFORE_NUMBER}(\d{{1,2}})\s?[ºo°]?\s+de\s+({'|'.join(MONTHS)})\b", re.I), self.day_of_month),
            (re.compile(r"\b(dia)\s+1(?![\d.,/ºª°])", re.I), lambda m: f"{m[1]} primeiro"),
            (re.compile(r"\b(anos|década de|década dos anos)\s+(19\d0|20\d0)\b"), self.decade),
            (re.compile(r"\b([Àà]s?)\s+(\d{1,2})(?=\s+d[ao]s?\s+(?:manhã|tarde|noite|madrugada)\b|\s+horas?\b)"), self.hour_of_day),
            (re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b"), lambda m: self.groups(re.split(r"[-.]", m[0]))),
            (re.compile(rf"{BEFORE_NUMBER}(\d{{2}})\.(\d{{3}})\.(\d{{3}})/(\d{{4}})-(\d{{2}})\b"), self.cnpj),
            (re.compile(rf"{BEFORE_NUMBER}(\d{{3}})\.(\d{{3}})\.(\d{{3}})-(\d{{2}})\b"), self.cpf),
            (re.compile(rf"{BEFORE_NUMBER}(\d{{1,2}})\.(\d{{3}})\.(\d{{3}})-([\dXx])\b"), self.rg),
            (re.compile(rf"{BEFORE_NUMBER}(\d{{5}})-(\d{{3}})\b"), lambda m: self.groups([m[1], m[2]])),
            (re.compile(rf"{BEFORE_NUMBER}0800[\s-]?(\d{{3}})[\s-]?(\d{{3,4}})\b"), lambda m: "zero oitocentos, " + self.groups([m[1], m[2]])),
            (re.compile(rf"{BEFORE_NUMBER}(\+55\s?)?(?:\((\d{{2}})\)\s?|(\d{{2}})\s(?=9\d{{4}}-\d{{4}}))?(9?\d{{4}})-(\d{{4}})(?![\d-])"), self.phone),
            (re.compile(rf"{BEFORE_NUMBER}({'|'.join(sorted(BRAZILIAN_EMERGENCY | FOREIGN_EMERGENCY))})(?![\w]|[.,]\d|/|-\d)(?!\s+(?:{SCALE_PATTERN})\b)"), self.emergency),
            (re.compile(rf"\b(?i:{'|'.join(re.escape(word) for word in TRIGGERS)})\s+({ROMAN})(?![\w.])"), self.roman_after_word),
            (re.compile(r"\b(?P<w>[A-ZÀ-Ý][a-zà-ÿ]+)\s+(?P<r>" + ROMAN + r")(?![\w.])"), self.roman_after_name),
            (re.compile(rf"\b({ROMAN})\s+(Guerra)\b"), lambda m: f"{ordinal(roman_value(m[1]), True)} {m[2]}"),
            (re.compile(r"\b((?i:art(?:igos?|s?\.)))\s*(\d+)[º°]?"), self.article),
            (re.compile(r"(§§?)\s?(\d+)[º°]?"), self.paragraph),
            (re.compile(rf"{BEFORE_NUMBER}(\d{{1,3}})\.?([ºª°])(s?)(?![\w])(?![CF]\b)"), self.marked_ordinal),
            (re.compile(rf"{BEFORE_NUMBER}({NUM})(?:,(\d+))?\s?%"), lambda m: self.decimal_text(m[1], m[2], m, False) + " por cento"),
            (re.compile(r"(?<![\w.,)])\.(\d{2,3})\b"), lambda m: self.say(int(m[1]))),
            (re.compile(r"(?<![\w])[Kk]m\s?(?=\d)"), lambda m: "quilômetro "),
            (re.compile(rf"{BEFORE_NUMBER}(\d+)\s(\d{{1,2}})/(\d{{1,3}})(?![\d/])"), self.mixed_fraction),
            (re.compile(rf"{BEFORE_NUMBER}(\d{{1,2}})/(\d{{1,3}})(?![\d/])"), self.fraction),
            (re.compile(rf"{BEFORE_NUMBER}(\d{{4}})\s?[-–]\s?(\d{{4}})(?![\d-])"), self.year_range),
            (re.compile(rf"{BEFORE_NUMBER}(\d{{1,3}})\s?[-–]\s?(\d{{1,3}})(?=\s+[a-zà-ÿ])"), self.counted_range),
            (re.compile(r"(?<![\w.,/])(\d+)\.(\d{1,2})(?![\d.,\w])"), lambda m: self.decimal_text(m[1], m[2], m)),
            (re.compile(rf"{BEFORE_NUMBER}({NUM})(?:,(\d+))?\s({SCALE_PATTERN})\b"), self.scaled),
            (re.compile(rf"{BEFORE_NUMBER}({NUM})(?:,(\d+))?(?![\d])"), self.plain),
        ]

    def text(self, text: str) -> str:
        pieces = URL_OR_EMAIL.split(text)
        for pattern, handler in self.claims:
            pieces = [piece if index % 2 else pattern.sub(handler, piece) for index, piece in enumerate(pieces)]
        return super().text("".join(pieces))

    def convert(self, text: str) -> str:
        pieces = self.placeholder.split(text)
        for pattern, handler in self.rules:
            pieces = [piece if index % 2 else pattern.sub(handler, piece) for index, piece in enumerate(pieces)]
        return "".join(pieces)

    def say(self, value: int, feminine: bool = False) -> str:
        if value > self.max_value:
            return digit_words(str(value))
        return cardinal(value, feminine, self.opt.feminine_mil)

    def groups(self, parts: list[str]) -> str:
        return ", ".join(digit_words(part, self.opt.six_as_meia) for part in parts)

    def decimal_text(self, integer: str, decimals: str | None, match: re.Match, feminine: bool | None = None) -> str:
        """`integer` may carry thousand dots; the decimals are said as a number up to the option's digits, else one by one. The whole number takes the
        gender of the noun after it unless `feminine` says otherwise."""
        if feminine is None:
            feminine = feminine_after(match.string, match.end())
        words = self.say(int(integer.replace(".", "")), feminine and decimals is None)
        if decimals is None:
            return words
        if len(decimals) <= self.opt.decimal_cardinal_max_digits and not decimals.startswith("0"):
            return f"{words} vírgula {self.say(int(decimals))}"
        return f"{words} vírgula {digit_words(decimals)}"

    def plain(self, match: re.Match) -> str:
        """Any remaining number: digit by digit when it has leading zeros or is too long to be a count, else the cardinal with the gender of its noun."""
        integer, decimals = match[1], match[2]
        digits = integer.replace(".", "")
        if decimals is None and ((digits.startswith("0") and len(digits) > 1) or ("." not in integer and len(digits) >= 7) or int(digits) > self.max_value):
            return digit_words(digits, self.opt.six_as_meia)
        words = self.decimal_text(integer, decimals, match)
        if decimals is None and int(digits) >= 10 ** 6 and int(digits) % 10 ** 6 == 0 and self.takes_de(match.string, match.end()):
            words += " de"
        return words

    @staticmethod
    def takes_de(text: str, end: int) -> bool:
        """After a round milhão/bilhão a counted plural noun follows with "de" ("cinco milhões de pessoas"), not a verb or "mais"."""
        following = re.match(r"\s+([a-zà-ÿ]+)", text[end:])
        return bool(following) and following[1].endswith("s") and len(following[1]) > 3 and following[1] not in NOT_COUNTED_NOUNS

    def scaled(self, match: re.Match) -> str:
        """"5 milhões", "2,5 milhões", "3 mil": the number times its scale word, as the words of the product ("dois milhões e meio" for ,5)."""
        integer, decimals, scale = match[1], match[2], match[3]
        factor = SCALE_WORDS[scale]
        whole = int(integer.replace(".", ""))
        feminine = factor == 1000 and feminine_after(match.string, match.end())
        if decimals == "5" and factor >= 10 ** 6:
            words = f"{self.say(whole)} {scale if whole == 1 else self.plural_scale(scale)} e meio"
            return words + (" de" if self.takes_de(match.string, match.end()) else "")
        value = Decimal(f"{whole}.{decimals or '0'}") * factor
        if value != value.to_integral_value():
            return f"{self.decimal_text(integer, decimals, match, False)} {scale}"
        words = self.say(int(value), feminine)
        if factor >= 10 ** 6 and int(value) % 10 ** 6 == 0 and self.takes_de(match.string, match.end()):
            words += " de"
        return words

    @staticmethod
    def plural_scale(scale: str) -> str:
        return {"milhão": "milhões", "bilhão": "bilhões", "trilhão": "trilhões"}.get(scale, scale)

    def money(self, match: re.Match) -> str:
        symbol, integer, cents, scale = match[1], match[2], match[3], match[4]
        if symbol == "$":
            symbol = "US$" if self.opt.bare_dollar_sign == "dollar" else "R$"
        singular, plural, feminine = CURRENCIES[symbol]
        whole = int(integer.replace(".", ""))
        if scale:
            total = Decimal(f"{whole}.{cents or '0'}") * SCALE_WORDS[scale]
            whole, cents = int(total), None
        amount = int(cents.ljust(2, "0")) if cents else 0
        parts: list[str] = []
        if whole or not amount:
            de = " de" if whole >= 10 ** 6 and whole % 10 ** 6 == 0 else ""
            parts.append(f"{self.say(whole, feminine)}{de} {singular if whole == 1 else plural}")
        if amount:
            of = "" if symbol == "R$" or whole else f" de {singular}"
            parts.append(f"{self.say(amount)} {'centavo' if amount == 1 else 'centavos'}{of}")
        return " e ".join(parts)

    def law(self, match: re.Match) -> str:
        return f"{match[1]} {self.say(int(match[2].replace('.', '')))}, de {self.say(int(match[3]))}"

    def month_date(self, day: str, month: int) -> str:
        return f"{'primeiro' if int(day) == 1 else self.say(int(day))} de {MONTHS[month - 1]}"

    def numeric_date(self, match: re.Match) -> str:
        day, month, year = match[1], int(match[2]), match[3]
        if not 1 <= month <= 12 or not 1 <= int(day) <= 31:
            return match[0]
        return f"{self.month_date(day, month)} de {self.say(int(year))}"

    def short_date(self, match: re.Match) -> str:
        day, month = match[2], int(match[3])
        if not 1 <= month <= 12 or not 1 <= int(day) <= 31:
            return match[0]
        return f"{match[1]} {self.month_date(day, month)}"

    def day_of_month(self, match: re.Match) -> str:
        return f"{'primeiro' if int(match[1]) == 1 else self.say(int(match[1]))} de {match[2]}"

    def decade(self, match: re.Match) -> str:
        year = int(match[2])
        return f"{match[1]} {self.say(year % 100 if year < 2000 and year % 100 else year)}"

    def hour_of_day(self, match: re.Match) -> str:
        return f"{match[1]} {self.say(int(match[2]), int(match[2]) in (1, 2))}"

    def hour_words(self, hour: int) -> str:
        return {0: "meia-noite", 12: "meio-dia"}.get(hour) or self.say(hour, hour in (1, 2))

    def time_words(self, hour: int, minute: int) -> str:
        """10h30 "dez e meia", 23h45 "vinte e três e quarenta e cinco", 1h30 "uma e meia", 0h30 "meia-noite e meia", 10h "dez horas"."""
        if not minute:
            return self.hour_words(hour) if hour in (0, 12) else f"{self.say(hour, hour in (1, 2))} {'hora' if hour == 1 else 'horas'}"
        past = "meia" if minute == 30 and self.opt.half_hour_as_meia else self.say(minute)
        return f"{self.hour_words(hour)} e {past}"

    def duration(self, hours: int, minutes: int, seconds: int = 0) -> str:
        parts = [(hours, "hora", "horas", True), (minutes, "minuto", "minutos", False), (seconds, "segundo", "segundos", False)]
        words = [f"{self.say(value, feminine and value in (1, 2))} {singular if value == 1 else plural}" for value, singular, plural, feminine in parts if value]
        return ", ".join(words[:-1]) + (" e " if len(words) > 1 else "") + words[-1] if words else "zero horas"

    def clock(self, match: re.Match) -> str:
        hour, minute, is_duration = int(match[1]), int(match[2] or 0), bool(match[3])
        if match[4]:
            end_hour, end_minute = int(match[4]), int(match[5] or 0)
            if not minute and not end_minute:
                return f"{self.say(hour, hour in (1, 2))} a {self.say(end_hour, end_hour in (1, 2))} horas"
            return f"{self.time_words(hour, minute)} a {self.time_words(end_hour, end_minute)}"
        if is_duration or hour > 24:
            return self.duration(hour, minute)
        return self.time_words(hour, minute)

    def colon_clock(self, match: re.Match) -> str:
        if match[3]:
            return self.duration(int(match[1]), int(match[2]), int(match[3]))
        return self.time_words(int(match[1]), int(match[2]))

    def short_time(self, match: re.Match) -> str:
        unit = ("minuto", "minutos") if match[2] == "min" else ("segundo", "segundos")
        return f"{self.say(int(match[1]))} {unit[0] if int(match[1]) == 1 else unit[1]}"

    def measure(self, match: re.Match) -> str:
        number, unit = match[1], match[2]
        value = Decimal(number.replace(".", "").replace(",", "."))
        integer, _, decimals = number.partition(",")
        words = self.decimal_text(integer, decimals or None, match, False)
        singular, plural = UNITS_OF_MEASURE[unit]
        return f"{words} {plural if abs(value) > 1 else singular}"

    def times_sign(self, match: re.Match) -> str:
        """"7 x 1" is a score ("sete a um"); "4x4" on a vehicle and "9x19" are sizes ("por")."""
        first, second = int(match[1]), int(match[2])
        context = match.string[max(0, match.start() - 40):match.end() + 40]
        size = (first < 10 <= second) or first >= 20 or second >= 20 or VEHICLE_CONTEXT.search(context) or re.match(r"\s*mm", match.string[match.end():])
        return f"{self.say(first)} {'por' if size else 'a'} {self.say(second)}"

    def plain_ordinal(self, match: re.Match) -> str:
        return ordinal(int(match[1]), match[2] == "a") if 0 < int(match[1]) <= self.opt.ordinal_max else match[0]

    def marked_ordinal(self, match: re.Match) -> str:
        """The mark gives the gender: 1º "primeiro", 1ª "primeira"; above the option's limit the number is said plainly."""
        value, feminine, plural = int(match[1]), match[2] == "ª", match[3]
        following = re.match(r"\s+([A-Za-zÀ-ÿ]+)", match.string[match.end():])
        if match[2] == "°" and (not following or following[1].lower() in DEGREE_FOLLOWERS):
            return f"{self.say(value)} {'graus' if value != 1 else 'grau'}"
        if value == 0 or (value > self.opt.ordinal_max and value != 100):
            return self.say(value, feminine)
        return ordinal(value, feminine) + plural

    def plate_old(self, match: re.Match) -> str:
        return " ".join(LETTERS[letter] for letter in match[1]) + ", " + digit_words(match[2])

    def plate_mercosul(self, match: re.Match) -> str:
        return " ".join(LETTERS[letter] for letter in match[1]) + f" {digit_words(match[2])} {LETTERS[match[3]]} {digit_words(match[4])}"

    def road(self, match: re.Match) -> str:
        digits = match[2]
        return f"{match[1]} {'zero ' if digits.startswith('0') else ''}{self.say(int(digits))}"

    def cpf(self, match: re.Match) -> str:
        return self.groups([match[1], match[2], match[3]]) + ", traço, " + self.groups([match[4]])

    def cnpj(self, match: re.Match) -> str:
        return self.groups([match[1], match[2], match[3]]) + ", barra, " + self.groups([match[4]]) + ", traço, " + self.groups([match[5]])

    def rg(self, match: re.Match) -> str:
        last = "xis" if match[4] in "Xx" else self.groups([match[4]])
        return self.groups([match[1], match[2], match[3]]) + ", traço, " + last

    def phone(self, match: re.Match) -> str:
        country, ddd, ddd_bare, first, second = match[1], match[2], match[3], match[4], match[5]
        area = ddd or ddd_bare
        if not (country or area or len(first) == 5) and 1000 <= int(first) < int(second) <= 2100:
            return match[0]
        parts = ["mais cinquenta e cinco"] if country else []
        if area:
            parts.append(self.say(int(area)) if self.opt.phone_ddd_as_number else digit_words(area))
        return ", ".join(parts + [self.groups([first, second])])

    def emergency(self, match: re.Match) -> str:
        number, mode = match[1], self.opt.emergency_numbers
        clause = re.split(r"[.;:!?\n]", match.string[max(0, match.start() - 60):match.start()])[-1]
        before = WORD.findall(clause)[-4:]
        in_context = any(word.lower() in EMERGENCY_CONTEXT for word in before)
        spelled = mode == "digits_always" or (mode == "digits_in_context" and number in BRAZILIAN_EMERGENCY and in_context)
        return digit_words(number) if spelled else match[0]

    def roman_after_word(self, match: re.Match) -> str:
        trigger = match[0].split()[0].lower()
        return match[0][:match.start(1) - match.start()] + self.roman_words(roman_value(match[1]), TRIGGERS[trigger])

    def roman_after_name(self, match: re.Match) -> str:
        numeral = match["r"]
        value = roman_value(numeral)
        if value > 40 or numeral in {"L", "C", "D", "M"}:
            return match[0]
        return f"{match['w']} {self.roman_words(value, match['w'] in FEMININE_NAMES)}"

    def roman_words(self, value: int, feminine: bool) -> str:
        """Kings, popes, centuries, chapters and parts: ordinal up to décimo, cardinal from onze ("Pio doze", "século vinte")."""
        return ordinal(value, feminine) if value <= 10 else self.say(value, feminine)

    def article(self, match: re.Match) -> str:
        label = {"art.": "artigo", "arts.": "artigos"}.get(match[1].lower(), match[1])
        value = int(match[2])
        return f"{label} {ordinal(value) if 0 < value <= 9 else self.say(value)}"

    def paragraph(self, match: re.Match) -> str:
        value = int(match[2])
        return f"{'parágrafos' if match[1] == '§§' else 'parágrafo'} {ordinal(value) if 0 < value <= 9 else self.say(value)}"

    def fraction_words(self, numerator: int, denominator: int, feminine: bool = False) -> str:
        """1/2 "meio", 1/3 "um terço", 2/3 "dois terços", 1/11 "um onze avos", 1/20 "um vigésimo"."""
        if denominator == 2:
            return feminine_form("meio") if feminine and numerator == 1 else "meio" if numerator == 1 else f"{self.say(numerator)} meios"
        if denominator in FRACTION_NAMES or (self.opt.fraction_tens_as_ordinal and 20 <= denominator <= 90 and denominator % 10 == 0):
            name = FRACTION_NAMES.get(denominator) or ordinal(denominator)
            return f"{self.say(numerator)} {name}{'s' if numerator > 1 else ''}"
        return f"{self.say(numerator)} {self.say(denominator)} avos"

    def fraction(self, match: re.Match) -> str:
        numerator, denominator = int(match[1]), int(match[2])
        if not numerator or denominator < 2:
            return match[0]
        return self.fraction_words(numerator, denominator, feminine_after(match.string, match.end()))

    def mixed_fraction(self, match: re.Match) -> str:
        numerator, denominator = int(match[2]), int(match[3])
        if not numerator or denominator < 2:
            return match[0]
        feminine = feminine_after(match.string, match.end())
        return f"{self.say(int(match[1]), feminine)} e {self.fraction_words(numerator, denominator, feminine)}"

    def year_range(self, match: re.Match) -> str:
        start, end = int(match[1]), int(match[2])
        if not 1000 <= start < end <= 2100:
            return match[0]
        before = WORD.findall(match.string[max(0, match.start() - 12):match.start()])[-1:]
        lead = "" if before and before[0].lower() in {"de", "entre", "desde", "em"} else "de "
        return f"{lead}{self.say(start)} a {self.say(end)}"

    def counted_range(self, match: re.Match) -> str:
        start, end = int(match[1]), int(match[2])
        if start >= end:
            return match[0]
        feminine = feminine_after(match.string, match.end())
        before = WORD.findall(match.string[max(0, match.start() - 12):match.start()])[-1:]
        word = before[0].lower() if before else ""
        lead = "" if word in {"de", "entre", "desde", "em"} else "de "
        joiner = "e" if word == "entre" else "a"
        return f"{lead}{self.say(start, feminine)} {joiner} {self.say(end, feminine)}"
