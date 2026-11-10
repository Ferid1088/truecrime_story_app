"""Italian numbers for the voice: what a native documentary narrator says for the digits of a script (years as one word, "ventun anni", "le dieci e
mezza", "l'undici settembre", "centomila euro", ...). The research and the verified/unverified status of every rule is in the project notes
(scratchpad numbers/it.md); the rules run in a fixed order, each on what the previous ones left."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.documentary.spoken_numbers.base import PROTECTED, LanguageNumbers
from app.documentary.spoken_numbers.it_calendar import Calendar
from app.documentary.spoken_numbers.it_ordinals import Ordinals
from app.documentary.spoken_numbers.it_words import (
    FUNCTION_WORDS,
    Speaker,
    cardinal,
    counted,
    is_feminine,
    ordinal,
    split_amount,
)

AMOUNT = r"(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2}|\.\d{1,2}(?!\d))?"
THOUSANDS_GROUP = r"(?:\d{1,3}(?:\.\d{3})+|\d+)"
SCALE = r"(?:milioni|milione|miliardi|miliardo|mln|mld|mila)"
SCALE_WORDS = {"milione": ("milione", "milioni"), "milioni": ("milione", "milioni"), "mln": ("milione", "milioni"),
               "miliardo": ("miliardo", "miliardi"), "miliardi": ("miliardo", "miliardi"), "mld": ("miliardo", "miliardi")}
CURRENCIES = {"euro": ("euro", "euro", "centesimo", "centesimi"), "dollaro": ("dollaro", "dollari", "centesimo", "centesimi"),
              "sterlina": ("sterlina", "sterline", "penny", "penny"), "lira": ("lira", "lire", None, None),
              "franco": ("franco", "franchi", "centesimo", "centesimi"), "yen": ("yen", "yen", None, None)}
CURRENCY_WORDS = {"euro": "euro", "EUR": "euro", "dollari": "dollaro", "dollaro": "dollaro", "USD": "dollaro", "sterline": "sterlina",
                  "sterlina": "sterlina", "GBP": "sterlina", "lire": "lira", "lira": "lira", "franchi": "franco", "franco": "franco", "CHF": "franco",
                  "yen": "yen"}
UNIT_WORDS = {"km/h": ("chilometro orario", "chilometri orari"), "km": ("chilometro", "chilometri"), "kg": ("chilo", "chili"),
              "mg": ("milligrammo", "milligrammi"), "g": ("grammo", "grammi"), "mm": ("millimetro", "millimetri"),
              "cm": ("centimetro", "centimetri"), "m": ("metro", "metri"), "ml": ("millilitro", "millilitri"),
              "cl": ("centilitro", "centilitri"), "l": ("litro", "litri"), "m²": ("metro quadrato", "metri quadrati"),
              "m³": ("metro cubo", "metri cubi"), "mq": ("metro quadrato", "metri quadrati"), "ha": ("ettaro", "ettari"),
              "t": ("tonnellata", "tonnellate")}
FRACTION_DENOMINATOR_MAX = 100
SPORT = re.compile(r"partita|vince|vinto|vinse|vittoria|pareggi|risultato|\bgol\b|goal|finale|sconfitta|battut|punteggio|segna|stravinc|derby|campionato",
                   re.I)
EMERGENCY_CONTEXT = re.compile(
    r"chiam|telefon|numero|emergenz|soccors|polizia|carabinier|ambulanz|pompier|vigili|centralino|\bnue\b|pronto|allert|linea|compon|digit|\bsos\b"
    r"|guardia|operator", re.I)
PHONE_CONTEXT = re.compile(r"tel|cell|numero|chiam|whatsapp|contatt|sms|compon", re.I)

LETTER_DASH_NUMBER = re.compile(r"(?<=[A-Za-zÀ-ÿ])-(?=\d)")
PLUS = re.compile(r"(?P<sign>(?<![\w+])\+(?=\d))|(?P<sum>(?<=\d)\s?\+\s?(?=\d))")
ABBREVIATIONS = (
    (re.compile(r"(?<![\w])nn\.\s*(?=\d)"), "numeri "),
    (re.compile(r"(?<![\w])(?:n|nr|N)(?:\.|°|º)(?:ro)?\s*(?=\d)"), "numero "),
    (re.compile(r"(?<![\w])artt\.\s*(?=\d)"), "articoli "),
    (re.compile(r"(?<![\w])art\.\s*(?=\d)"), "articolo "),
    (re.compile(r"(?<![\w])co\.\s*(?=\d)"), "comma "),
    (re.compile(r"(?<![\w])pag\.\s*(?=\d)"), "pagina "),
    (re.compile(r"(?<![\w])pp\.\s*(?=\d)"), "pagine "),
    (re.compile(r"(?<![\w])sent\.\s*(?=n|\d)"), "sentenza "),
    (re.compile(r"(?<=\d)\s+c\.p\.p\."), " del codice di procedura penale"),
    (re.compile(r"(?<=\d)\s+c\.p\.c\."), " del codice di procedura civile"),
    (re.compile(r"(?<=\d)\s+c\.p\."), " del codice penale"),
    (re.compile(r"(?<=\d)\s+c\.c\."), " del codice civile"),
    (re.compile(r"(?<![\w])(?:SS|S\.S\.)\s*(?=\d)"), "strada statale "),
    (re.compile(r"(?<![\w])(?:SP|S\.P\.)\s*(?=\d)"), "strada provinciale "),
    (re.compile(r"(?<![\w])(?:SR|S\.R\.)\s*(?=\d)"), "strada regionale "),
    (re.compile(r"(?<![\w/])km\s+(?=\d)"), "chilometro "),
)
DOCUMENT_NUMBER = re.compile(
    r"(?P<p>(?:numero|numeri|R\.G\.|RG|sentenza|ordinanza|decreto|procedimento|causa|fascicolo|protocollo)\s+(?:numero\s+)?)"
    r"(?P<n>\d{1,7})/(?P<y>(?:19|20)\d{2})\b")
US_EMERGENCY = "911"        # the American number is said digit by digit ("nove uno uno") whatever the sentence
EMERGENCY = re.compile(r"(?<![\d.,])(?P<n>11[2-9]|1522|1530|911)(?![\d]|[.,]\d)")
PHONE = re.compile(
    r"(?<![\d.,])(?:(?P<mob>3\d{2})(?P<msep>[ -])(?P<mrest>\d{6,7})|(?P<land>0\d{1,3})(?P<lsep>[ /-])(?P<lrest>\d{5,8}))(?!\d)")
BARE_MOBILE = re.compile(r"(?<![\d.,])(?P<mob>3\d{2})(?P<mrest>\d{6,7})(?!\d)")
GROUPED_MOBILE = re.compile(r"(?<![\d.,])(?P<mob>3\d{2})[ -](?P<mrest>\d{3}[ -]\d{3,4})(?!\d)")
POSTAL_CODE = re.compile(r"\b(?P<cap>CAP|cap)\s*(?P<n>\d{5})\b")
CALIBRE_DOT = re.compile(r"(?<![\w.])\.(?=(?:22|32|38|44|45|357|380)\b)")
ROUND_THE_CLOCK = re.compile(r"(?<![\d/])24\s*/\s*7(?![\d/])")
TEMPERATURE = re.compile(rf"(?<![\d.,])(?P<neg>[-−–]\s?)?(?P<a>{AMOUNT})\s*°\s*(?P<s>[CF])\b")
CURRENCY_CODES = "|".join(sorted(CURRENCY_WORDS, key=len, reverse=True))
CURRENCY = re.compile(
    rf"(?:(?P<pre>[€$£]|\b(?:EUR|USD|GBP|CHF)|\bLit\.|\bL\.)\s*(?P<a1>{AMOUNT})(?:\s*(?P<s1>{SCALE}))?(?![\d])"
    rf"|(?<![\d.,])(?P<a2>{AMOUNT})(?:\s*(?P<s2>{SCALE}))?(?:\s+di)?\s*(?P<post>[€$£]|\b(?:{CURRENCY_CODES})(?!\w)))")
PERCENT = re.compile(rf"(?<![\d.,])(?P<a>{AMOUNT})\s*(?P<k>%|‰)")
SCALED = re.compile(rf"(?<![\d.,])(?P<a>{AMOUNT})\s*(?P<s>{SCALE})(?!\w)(?:(?P<sp>\s+)(?P<next>[^\W\d_]+))?")
UNIT = re.compile(
    rf"(?<![\d.,])(?P<neg>[-−]\s?)?(?P<a>{AMOUNT})\s*(?P<u>km/h|m²|m³|mq|[Kk]m|[Kk]g|KG|KM|mg|mm|cm|ml|cl|ha|g|l|m|t)(?![\w²³/'])")
PAIR = re.compile(
    rf"(?P<prep>\b(?:dall'|dal|tra|fra|da)\s*)?(?<![\w.,/:-])(?P<a>{THOUSANDS_GROUP})\s*(?P<sep>[–:-])\s*(?P<b>{THOUSANDS_GROUP})(?![\w.,/:-])")
FRACTION = re.compile(r"(?<![\d/.,])(?P<a>\d{1,3})/(?P<b>\d{1,3})(?![\d/])(?:(?P<sp>\s+)(?P<noun>[^\W\d_]+))?")
LETTER_SUFFIX = re.compile(r"(?<![\d/.,])(?P<a>\d{1,4})/(?P<l>[A-Z])\b")
DECIMAL = re.compile(rf"(?<![\d.,])(?P<whole>{THOUSANDS_GROUP})(?:,(?P<frac>\d+)|\.(?P<dot>\d{{1,2}}))(?!\d)")
MINUS = re.compile(r"(?<![\w)])[-−](?=\d)")
LEADING_ZERO = re.compile(r"(?<![\d.,])0\d+(?!\d|[.,]\d)")
INTEGER = re.compile(r"(?<![\d.,])(?P<n>\d{1,3}(?:[.']\d{3})+|\d+)(?!\d|[.,]\d)(?:(?P<sp>\s+)(?P<noun>[^\W\d_]+))?")
ARTICLE = re.compile(r"\b(?P<art>il|lo|del|dello|al|allo|dal|dallo|nel|nello|sul|sullo|col)\s+(?=(?:otto\w*|ottanta\w*|undici|uno)\b)", re.I)
ELIDED_ARTICLE = {"il": "l'", "lo": "l'", "del": "dell'", "dello": "dell'", "al": "all'", "allo": "all'", "dal": "dall'", "dallo": "dall'",
                  "nel": "nell'", "nello": "nell'", "sul": "sull'", "sullo": "sull'", "col": "con l'"}
TENS_BEFORE_YEARS = re.compile(r"\b(venti|trenta|quaranta|cinquanta|sessanta|settanta|ottanta|novanta)\s+anni\b")
AGE = re.compile(r"(?<![\d.,])(\d{1,3})enn([ei])\b")
UNIT_GLUED = re.compile(r"(?<=\d)(?=(?:km/h|m²|m³|mq|[Kk]m|[Kk]g|KG|KM|mg|mm|cm|ml|cl|ha|g|l|m|t)(?![\w²³/']))")
GUN_CALIBRE = re.compile(r"(?<![\w.,])(?P<a>9|7,62|5,56|7,65|6,35)x(?P<b>17|18|19|21|39|45|51)(?!\d)")
THOUSANDS_SEPARATOR = re.compile(r"(?<=\d)[   ](?=\d{3}\b)")
SPACED_THOUSANDS = re.compile(r"\b\d{1,3}(?: \d{3})+\b")
PRIVATE_START = 0xE000
PLACEHOLDER = re.compile("[-]")


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emergency_numbers: Literal["digits_in_context", "digits_always", "number_always"] = "digits_in_context"   # 112 in a call context only
    emergency_context_chars: int = 40                   # how far around the number a call word (chiama, numero, polizia, ...) counts as context
    symbol_currencies: dict[str, str] = {"€": "euro", "$": "dollaro", "£": "sterlina", "L.": "lira", "Lit.": "lira"}   # what a bare symbol means
    kilogram_word: Literal["chili", "chilogrammi"] = "chili"            # "20 kg": venti chili (colloquial) or venti chilogrammi
    elide_tens_before_anni: bool = False                # "trenta anni" (False) or "trent'anni"
    space_thousands: bool = True                        # "12 000" as 12000 (ISTAT style); off if a script writes two numbers apart by a space
    decimals_as_number_up_to: int = 2                   # "3,14" tre virgola quattordici; more decimals are read digit by digit
    half_as_mezzo: bool = True                          # "2,5 km" due chilometri e mezzo (False: due virgola cinque chilometri)
    quarter_and_half: bool = True                       # "10:15" le dieci e un quarto, "10:30" le dieci e mezza (False: e quindici / e trenta)


class Numbers(LanguageNumbers):
    def __init__(self, options: dict, max_value: int):
        super().__init__(options, max_value)
        self.opt = Options.model_validate(options)
        self.speaker = Speaker(max_value)
        self.calendar = Calendar(self.speaker, self.opt.quarter_and_half)
        self.ordinals = Ordinals(self.speaker)
        self.units = {**UNIT_WORDS, "kg": ("chilo", "chili") if self.opt.kilogram_word == "chili" else ("chilogrammo", "chilogrammi")}
        self.rules = (self._phones, self._normalised, self._signs, self._abbreviations, self._documents, self._emergency, self.calendar.dates,
                      self.calendar.times, self._postal_codes, self.ordinals.romans, self.calendar.eras, self.calendar.decades,
                      self.calendar.apostrophe_years, self._calibres, self._round_the_clock, self.calendar.day_months, self._temperatures,
                      self.ordinals.marks, self._money, self._percent, self._scaled, self._measures, self._pairs, self._fractions,
                      self._decimals, self._minus, self._leading_zeros, self._integers, self._articles)

    def text(self, text: str) -> str:
        """The base class puts a placeholder made of digits where a protected span was; here the digits of a number are converted, so the
        placeholder is a private-use character. Glued forms the base would protect but a narrator says as numbers ("35enne", "9mm") are opened first."""
        kept: list[str] = []

        def keep(match: re.Match) -> str:
            kept.append(match.group())
            return chr(PRIVATE_START + len(kept) - 1)

        converted = self.convert(PROTECTED.sub(keep, self._unglued(text)))
        return PLACEHOLDER.sub(lambda m: kept[ord(m.group()) - PRIVATE_START], converted)

    def _unglued(self, text: str) -> str:
        text = AGE.sub(lambda m: self.speaker.age(int(m[1]), m[2]), text)
        text = GUN_CALIBRE.sub(lambda m: f"{m['a']} per {m['b']}", text)
        return UNIT_GLUED.sub(" ", text)

    def convert(self, text: str) -> str:
        for rule in self.rules:
            text = rule(text)
        return text

    def _normalised(self, text: str) -> str:
        text = text.replace("’", "'").replace("‘", "'").replace("´", "'").replace("`", "'")
        text = THOUSANDS_SEPARATOR.sub(".", text).replace(" ", " ")
        if self.opt.space_thousands:
            text = SPACED_THOUSANDS.sub(lambda m: m.group().replace(" ", "."), text)
        return text

    def _signs(self, text: str) -> str:
        """"+39" più trentanove, "3+1" tre più uno, "Covid-19" Covid diciannove (a dash between a name and its number is only a separator)."""
        text = LETTER_DASH_NUMBER.sub(" ", text)
        return PLUS.sub(lambda m: "più " if m["sign"] else " più ", text)

    def _abbreviations(self, text: str) -> str:
        for pattern, spoken in ABBREVIATIONS:
            text = pattern.sub(spoken, text)
        return text

    def _documents(self, text: str) -> str:
        """"sentenza n. 1234/2019": the number "del" the year (the slash is not a fraction)."""
        return DOCUMENT_NUMBER.sub(
            lambda m: f"{m['p']}{self.speaker.integer(int(m['n']))} del {self.speaker.integer(int(m['y']))}", text)

    def _emergency(self, text: str) -> str:
        """112, 113, 118 ... said digit by digit (1522: two pairs) when a call word is around; a plain 118 elsewhere is a number (option)."""
        def spoken(m: re.Match) -> str:
            number, mode = m["n"], self.opt.emergency_numbers
            near = m.string[max(0, m.start() - self.opt.emergency_context_chars):m.end() + self.opt.emergency_context_chars]
            if mode == "number_always" or (mode == "digits_in_context" and number != US_EMERGENCY and not EMERGENCY_CONTEXT.search(near)):
                return cardinal(int(number))
            if len(number) == 4:
                return f"{cardinal(int(number[:2]))} {cardinal(int(number[2:]))}"
            return self.speaker.digits(number)
        return EMERGENCY.sub(spoken, text)

    def _phones(self, text: str) -> str:
        """A mobile or landline number is read digit by digit, a pause between its written groups; a bare 10-digit mobile or one written in groups of
        three ("333 123 4567", which is also a number of millions) only near a phone word."""
        def grouped(*parts: str) -> str:
            return ", ".join(self.speaker.digits(part) for part in parts)

        def phone(m: re.Match) -> str:
            return grouped(m["mob"], m["mrest"]) if m["mob"] else grouped(m["land"], m["lrest"])

        def with_context(m: re.Match) -> str:
            near = m.string[max(0, m.start() - 30):m.end() + 30]
            return grouped(m["mob"], m["mrest"]) if PHONE_CONTEXT.search(near) else m.group()
        return GROUPED_MOBILE.sub(with_context, BARE_MOBILE.sub(with_context, PHONE.sub(phone, text)))

    def _postal_codes(self, text: str) -> str:
        return POSTAL_CODE.sub(lambda m: f"{m['cap']} {self.speaker.digits(m['n'])}", text)

    def _calibres(self, text: str) -> str:
        """".38" is the calibre "trentotto": the dot of the written calibre is dropped so the number is said as a number."""
        return CALIBRE_DOT.sub("", text)

    def _round_the_clock(self, text: str) -> str:
        return ROUND_THE_CLOCK.sub("ventiquattro ore su ventiquattro, sette giorni su sette", text)

    def _temperatures(self, text: str) -> str:
        def temperature(m: re.Match) -> str:
            whole, frac = split_amount(m["a"])
            number = f"{self.speaker.decimal(whole, frac, self.opt.decimals_as_number_up_to)} gradi" if frac else counted(
                whole, "grado" if whole == 1 else "gradi")
            return f"{'meno ' if m['neg'] else ''}{number}{' Fahrenheit' if m['s'] == 'F' else ''}"
        return TEMPERATURE.sub(temperature, text)

    def _scale_words(self, whole: int, frac: str, scale: str) -> str:
        """"2,5 milioni" due milioni e mezzo, "1,2 milioni" uno virgola due milioni, "10 mila" diecimila, "1 milione" un milione."""
        if scale == "mila":
            return self.speaker.integer(whole * 1000 + int(frac.ljust(3, "0")) if frac else whole * 1000)
        singular, plural = SCALE_WORDS[scale]
        if frac == "5" and self.opt.half_as_mezzo:
            return f"mezzo {singular}" if whole == 0 else f"{counted(whole, singular if whole == 1 else plural)} e mezzo"
        if frac:
            return f"{self.speaker.decimal(whole, frac, self.opt.decimals_as_number_up_to)} {plural}"
        return counted(whole, singular if whole == 1 else plural)

    def _money(self, text: str) -> str:
        """"€ 1.500,50" and "1.500,50 euro": millecinquecento euro e cinquanta centesimi; "5 milioni di euro" cinque milioni di euro."""
        def money(m: re.Match) -> str:
            code = m["pre"] or m["post"]
            key = self.opt.symbol_currencies.get(code) or CURRENCY_WORDS.get(code)
            if key is None:
                return m.group()
            singular, plural, cent_singular, cent_plural = CURRENCIES[key]
            whole, frac = split_amount(m["a1"] or m["a2"])
            scale = m["s1"] or m["s2"]
            if scale:
                spoken = self._scale_words(whole, frac, scale)
                return f"{spoken} {plural}" if scale == "mila" else f"{spoken} di {plural}"
            cents = int(frac.ljust(2, "0")[:2]) if frac and cent_singular else 0
            parts = []
            if whole or not cents:
                parts.append(f"{cardinal(whole)} di {plural}" if whole and whole % 10 ** 6 == 0 else
                             counted(whole, singular if whole == 1 else plural))
            if cents:
                parts.append(counted(cents, cent_singular if cents == 1 else cent_plural))
            return " e ".join(parts)
        return CURRENCY.sub(money, text)

    def _percent(self, text: str) -> str:
        def percent(m: re.Match) -> str:
            whole, frac = split_amount(m["a"])
            return f"{self.speaker.decimal(whole, frac, self.opt.decimals_as_number_up_to)} {'per cento' if m['k'] == '%' else 'per mille'}"
        return PERCENT.sub(percent, text)

    def _scaled(self, text: str) -> str:
        """"5 milioni", "10 mila", "3 mld" without a currency; a noun straight after an exact million/billion takes "di" ("3 milioni persone")."""
        def scaled(m: re.Match) -> str:
            whole, frac = split_amount(m["a"])
            spoken = self._scale_words(whole, frac, m["s"])
            nxt = m["next"]
            if nxt is None:
                return spoken
            joiner = " di " if m["s"] != "mila" and nxt.lower() not in FUNCTION_WORDS else m["sp"]
            return f"{spoken}{joiner}{nxt}"
        return SCALED.sub(scaled, text)

    def _measures(self, text: str) -> str:
        """"100 km/h" cento chilometri orari, "2,5 km" due chilometri e mezzo, "35 °C" trentacinque gradi, "9 mm" nove millimetri."""
        def measure(m: re.Match) -> str:
            singular, plural = self.units[m["u"].lower()]
            whole, frac = split_amount(m["a"])
            if frac == "5" and self.opt.half_as_mezzo:
                spoken = f"mezzo {singular}" if whole == 0 else f"{counted(whole, singular if whole == 1 else plural)} e mezzo"
            elif frac:
                spoken = f"{self.speaker.decimal(whole, frac, self.opt.decimals_as_number_up_to)} {plural}"
            else:
                spoken = counted(whole, singular if whole == 1 else plural)
            return f"{'meno ' if m['neg'] else ''}{spoken}"
        return UNIT.sub(measure, text)

    def _pairs(self, text: str) -> str:
        """Two numbers joined by a dash or colon: a score ("3-1" tre a uno, in a sport sentence or when the first is not smaller), a ratio
        ("1:100.000" uno a centomila), a range of years ("1990-1995" dal ... al ...) or of amounts ("5-10 anni" da cinque a dieci anni)."""
        def pair(m: re.Match) -> str:
            a, b = int(m["a"].replace(".", "")), int(m["b"].replace(".", ""))
            prep = (m["prep"] or "").strip()
            lead = m["prep"] or ""
            first, second = self.speaker.integer(a), self.speaker.integer(b)
            sentence = _sentence(m.string, m.start(), m.end())
            if m["sep"] == ":" or (len(m["a"]) <= 2 and len(m["b"]) <= 2 and SPORT.search(sentence)):
                return f"{lead}{first} a {second}"
            if len(m["a"]) == 4 and len(m["b"]) == 2:
                return f"{lead}{first}-{second}"
            if a >= b:
                return f"{lead}{first} a {second}"
            years = len(m["a"]) == 4 and len(m["b"]) == 4 and a >= 1000 and b <= 2199
            if prep in ("tra", "fra"):
                return f"{lead}{first} e {second}"
            if prep in ("dal", "dall'"):
                return f"{lead}{first} al {second}"
            if prep == "da":
                return f"{lead}{first} a {second}"
            return f"dal {first} al {second}" if years else f"da {first} a {second}"
        return PAIR.sub(pair, text)

    def _fractions(self, text: str) -> str:
        """"3/4" tre quarti, "1/2" un mezzo (before a noun mezzo/mezza/mezz': "1/2 litro", "1/2 ora" mezz'ora); a letter after the slash is a house
        number suffix ("12/A" dodici A); a denominator over 100 is not a fraction ("5/200" cinque barra duecento)."""
        def fraction(m: re.Match) -> str:
            a, b = int(m["a"]), int(m["b"])
            noun = m["noun"]
            tail = f"{m['sp']}{noun}" if noun else ""
            if b < 2 or b > FRACTION_DENOMINATOR_MAX:
                return f"{cardinal(a)} barra {cardinal(b)}{tail}"
            if a == 1 and b == 2 and noun and noun.lower() not in FUNCTION_WORDS:
                half = "mezz'" if is_feminine(noun, False) and noun[0].lower() in "aeiou" else ("mezza " if is_feminine(noun, False) else "mezzo ")
                return f"{half}{noun}"
            denominator = "mezzo" if b == 2 else ordinal(b)
            spoken = f"un {denominator}" if a == 1 else f"{cardinal(a)} {denominator[:-1]}i"
            return f"{spoken}{tail}"
        text = LETTER_SUFFIX.sub(lambda m: f"{cardinal(int(m['a']))} {m['l']}", text)
        return FRACTION.sub(fraction, text)

    def _decimals(self, text: str) -> str:
        def decimal(m: re.Match) -> str:
            whole, frac = int(m["whole"].replace(".", "")), m["frac"] or m["dot"]
            return self.speaker.decimal(whole, frac, self.opt.decimals_as_number_up_to)
        return DECIMAL.sub(decimal, text)

    def _leading_zeros(self, text: str) -> str:
        """007 and 00100: a number written with a leading zero is a code, said digit by digit."""
        return LEADING_ZERO.sub(lambda m: self.speaker.digits(m.group()), text)

    def _minus(self, text: str) -> str:
        return MINUS.sub("meno ", text)

    def _integers(self, text: str) -> str:
        """Every number left, as a cardinal that agrees with the noun it counts; an exact million/billion before a noun takes "di"."""
        def integer(m: re.Match) -> str:
            n = int(re.sub(r"[.']", "", m["n"]))
            noun = m["noun"]
            if n > self.max_value:
                return f"{self.speaker.digits(str(n))}{m['sp'] or ''}{noun or ''}"
            if noun is None:
                return cardinal(n)
            if n >= 10 ** 6 and n % 10 ** 6 == 0 and noun.lower() not in FUNCTION_WORDS:
                return f"{cardinal(n)} di {noun}"
            return counted(n, noun)
        return INTEGER.sub(integer, text)

    def _articles(self, text: str) -> str:
        """"il 8" -> l'otto, "dal 11" -> dall'undici: an article before a number word that starts with a vowel is elided."""
        def elide(m: re.Match) -> str:
            elided = ELIDED_ARTICLE[m["art"].lower()]
            return elided.capitalize() if m["art"][0].isupper() else elided
        text = ARTICLE.sub(elide, text)
        if self.opt.elide_tens_before_anni:
            text = TENS_BEFORE_YEARS.sub(lambda m: f"{m[1][:-1]}'anni", text)
        return text


def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(mark, 0, start) for mark in ".!?\n")
    rights = [i for i in (text.find(mark, end) for mark in ".!?\n") if i != -1]
    return text[left + 1:min(rights) if rights else len(text)]
