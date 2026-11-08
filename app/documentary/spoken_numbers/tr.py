"""Turkish numbers for the voice. The rules follow the research in scratchpad/numbers/tr.md (TDK writing rules for suffixes after numerals,
Turkish separators: dot for thousands, comma for decimals). Each rule is a regular expression with a handler; the rules run in order, each one
leaves words behind, and the last one (a plain number with an optional apostrophe ending) says every digit run that is left, so no digit survives."""

from __future__ import annotations

import re
from typing import Callable, Literal

from pydantic import BaseModel

from app.documentary.spoken_numbers import tr_suffix, tr_words
from app.documentary.spoken_numbers.base import PLACEHOLDER, LanguageNumbers
from app.documentary.spoken_numbers.tr_words import capitalized, cardinal, digits

APOSTROPHE = r"['’‘ʼ´`]"
SUFFIX = rf"(?:{APOSTROPHE}(?P<suf>[^\W\d_]+))?"
NUMBER = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+[.,]\d+|\d+"
ANY_NUMBER = r"\d[\d.,]*\d|\d"
SCALE_WORDS = r"bin|milyon|milyar|trilyon"
PLACEHOLDERS = re.compile("(" + re.escape(PLACEHOLDER).replace(re.escape("{}"), r"\d+") + ")")
THOUSANDS = re.compile(r"\d{1,3}(?:\.\d{3})+")
ENGLISH_AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{1,2}")
MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
EMERGENCY_NUMBERS = "110|112|114|122|153|155|156|157|177|178|183|184"
EMERGENCY_CONTEXT = re.compile(
    r"alo|acil|numaral|hatt|arayın|aradı|aradığı|arama|aramak|ihbar|çağrı|imdat|itfaiye|ambulans|telefon|\btel\b|numara", re.IGNORECASE)
CURRENCIES = {
    "₺": ("lira", "kuruş"), "TL": ("lira", "kuruş"), "TRY": ("lira", "kuruş"), "$": ("dolar", "sent"), "USD": ("dolar", "sent"),
    "€": ("avro", "sent"), "EUR": ("avro", "sent"), "£": ("sterlin", "peni"), "GBP": ("sterlin", "peni")}
ENGLISH_FORMAT_CURRENCIES = {"$", "USD", "£", "GBP"}
UNITS = {
    "km/sa": "kilometre", "km/s": "kilometre", "km/h": "kilometre", "km²": "kilometrekare", "m²": "metrekare", "m³": "metreküp", "km": "kilometre",
    "cm": "santimetre", "mm": "milimetre", "kg": "kilogram", "mg": "miligram", "ha": "hektar", "°C": "derece", "°": "derece", "ton": "ton",
    "dk": "dakika", "sn": "saniye", "lt": "litre", "m": "metre", "g": "gram", "l": "litre"}
SPEED_UNITS = {"km/sa", "km/s", "km/h"}
UNIT_PATTERN = "|".join(re.escape(unit) for unit in sorted(UNITS, key=len, reverse=True)).replace(r"°C", r"°\s?C")
CALIBRE_NAMES = r"kalibre|Magnum|Special|ACP|Colt|Long|Smith|Wesson"
ORDINAL_HEADS = {
    "Dünya", "Ordu", "Tümen", "Bölük", "Sokak", "Cadde", "Asliye", "Ağır", "Sulh", "Daire", "Dairesi", "Kolordu", "Kat", "Gün", "Bölüm", "Sezon",
    "Mahkeme", "Cumhuriyet", "Meclis", "Lig", "Şube", "Anayasa", "Sınıf", "Yıl", "Dönem", "Kısım", "Cilt", "Madde", "Fıkra", "Bent"}
ROMAN_TRIGGERS = {"yüzyıl", "yy", "bölüm", "dönem", "kısım", "cilt", "madde", "fasıl", "kitap", "sınıf", "dünya", "savaş"}
ROMAN_NAMES = {
    "Abdülhamid", "Abdülaziz", "Abdülmecid", "Mahmut", "Mehmet", "Mehmed", "Murat", "Selim", "Süleyman", "Osman", "Ahmet", "Ahmed", "Mustafa",
    "Bayezid", "Beyazıt", "Orhan", "Kılıçarslan", "Elizabeth", "Louis", "Napolyon", "Henry", "Charles", "Papa", "Dünya"}
ROMAN_VALUE = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
ROMAN_SHAPE = re.compile(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})")
NOT_PLATES = {"TL", "TRY", "USD", "EUR", "GBP", "ABD", "TC", "BM"}
CONTEXT_FRACTIONS = {(24, 7): ["yirmi dört", "yedi"], (7, 24): ["yedi", "yirmi dört"]}


class Options(BaseModel):
    emergency_numbers: Literal["digits_in_context", "digits_always", "number_always"] = "digits_in_context"  # 112 as "bir bir iki" only near call words
    phone_numbers: Literal["groups", "digits"] = "groups"  # last two groups as numbers ("kırk beş, altmış yedi"), or every digit alone
    decades: Literal["full", "short"] = "full"  # 1990'lar as "bin dokuz yüz doksanlar", or "doksanlar"
    euro_name: Literal["avro", "euro"] = "avro"  # TDK spells "avro"; "euro" is what many people say
    half_as_bucuk: bool = True  # 2,5 as "iki buçuk" (and 0,5 as "yarım") instead of "iki virgül beş"
    time_half_as_bucuk: bool = True  # 10:30 as "on buçuk" instead of "on otuz"
    money_with_ve: bool = False  # "yirmi altı lira ve elli altı kuruş" (formal) instead of "... lira elli altı kuruş"
    range_word: Literal["ila", "ile"] = "ila"  # 5-10 as "beş ila on" or "beş ile on"
    code_digits_from: int = 11  # a run of this many digits or more (TC kimlik no) is read digit by digit


class Numbers(LanguageNumbers):
    """Turkish: every number the narrator would say, in words, with its apostrophe ending put on the last spoken word."""

    def __init__(self, options: dict, max_value: int):
        super().__init__(options, max_value)
        self.opt = Options.model_validate(options)
        self.rules: list[tuple[re.Pattern, Callable[[re.Match], str]]] = [
            (re.compile(rf"(?<![\d.,/])(?P<d>\d{{1,2}})[./](?P<mo>\d{{1,2}})[./](?P<y>\d{{4}})(?!\d){SUFFIX}"), self.date),
            (re.compile(
                r"(?<![\d.,])(?P<cc>\+\d{1,3}\s?)?(?P<zero>0\s?)?(?P<par>\()?(?P<a>\d{3})\)?[\s-](?P<b>\d{3})[\s-](?P<c>\d{2})[\s-](?P<d>\d{2})(?!\d)"),
             self.phone),
            (re.compile(rf"(?<![\w.,/-])(?P<minus>[-−])(?=\d)|(?<![\w.,/-])(?P<plus>\+)(?=\d)"), self.sign),
            (re.compile(rf"(?P<saat>\bsaat\s+)?(?<![\d.,:])(?P<h>[01]?\d|2[0-3])(?P<sep>[.:])(?P<m>[0-5]\d)(?!\d)(?!\.\d)(?!:\d){SUFFIX}"), self.time),
            (re.compile(rf"(?<![\d/])(?P<y>(?:19|20)\d{{2}})/(?P<n>\d{{1,6}})(?![\d/]){SUFFIX}"), self.file_number),
            (re.compile(r"\b(?P<label>No|Kat|Daire|Blok|Madde|Sıra)(?:\s*:\s*|\.\s*(?=\d))(?=\d)(?:(?P<a>\d+)/(?P<b>\d+))?"), self.address_label),
            (re.compile(rf"(?<![\d.,/])(?P<alo>\b[Aa][Ll][Oo]\s+)?\b(?P<n>{EMERGENCY_NUMBERS})\b(?![.,]\d){SUFFIX}"), self.emergency),
            (re.compile(r"\b(?P<province>0[1-9]|[1-7]\d|8[01])\s(?P<letters>[A-ZÇĞİÖŞÜ]{1,3})\s(?P<number>\d{2,4})\b"), self.plate),
            (re.compile(rf"(?<![\w.,])(?P<cur>[₺$€£])\s?(?P<num>{ANY_NUMBER})(?P<scale>\s+(?:{SCALE_WORDS}))?{SUFFIX}"), self.currency),
            (re.compile(
                rf"(?<![\w.,])(?P<num>{ANY_NUMBER})(?P<scale>\s+(?:{SCALE_WORDS}))?\s?(?P<cur>₺|TL|TRY|USD|EUR|GBP|[$€£])(?![A-Za-z]){SUFFIX}"),
             self.currency),
            (re.compile(rf"(?:%\s?(?P<a>{NUMBER})|(?<![\d.,])(?P<b>{NUMBER})\s?%){SUFFIX}"), self.percent),
            (re.compile(rf"(?<![\d.,])(?P<num>{NUMBER})\s?(?P<unit>{UNIT_PATTERN})(?![\w²³/]){SUFFIX}"), self.unit),
            (re.compile(rf"(?<![\w.,])\.(?P<n>\d{{2,3}})(?!\d){SUFFIX}(?P<name>\s+(?:{CALIBRE_NAMES}))?"), self.calibre),
            (re.compile(rf"(?<![\w])(?P<r>[IVXLCDM]{{1,7}})\.(?=\s+\S)(?P<yy>\s+yy\b\.?)?"), self.roman),
            (re.compile(rf"(?<![\w])(?P<r>[IVXLC]{{1,7}}){APOSTROPHE}(?P<suf>(?:[ıiuü])?nc[ıiuü])"), self.roman_suffix),
            (re.compile(r"(?<![\d.,/])(?P<n>\d{1,4})\.(?=\s+\S)(?P<yy>\s+yy\b\.?)?"), self.ordinal),
            (re.compile(rf"(?<![\d.,/])(?:(?P<whole>\d+)\s+)?(?P<n>\d{{1,3}})/(?P<d>\d{{1,3}})(?![\d/]){SUFFIX}"), self.fraction),
            (re.compile(rf"(?<![\d.,])(?P<a>\d+)\s?[-–—]\s?(?P<b>\d+)(?![\d.,]\d){SUFFIX}"), self.span),
            (re.compile(rf"(?<![\w{APOSTROPHE}.,]){APOSTROPHE}(?P<d>[1-9]0){APOSTROPHE}(?P<suf>[^\W\d_]+)"), self.short_decade),
            (re.compile(rf"(?<!\d)(?P<num>{NUMBER}){SUFFIX}"), self.plain)]

    def convert(self, text: str) -> str:
        """Each piece between the placeholders the base class set in for protected spans goes through the rules in order."""
        pieces = PLACEHOLDERS.split(text)
        for index in range(0, len(pieces), 2):
            for pattern, handler in self.rules:
                pieces[index] = pattern.sub(handler, pieces[index])
        return "".join(pieces)

    # numbers ------------------------------------------------------------------------------------------------------------------------------

    def integer(self, text: str, bare: bool = False) -> list[str]:
        """A whole number as a quantity; a leading zero ("007"), a value over the voice's maximum, or a code-length run written without separators
        (`bare`: 11 digits is a TC kimlik no, while 3.000.000.000.000 is a quantity) is read digit by digit."""
        if (len(text) > 1 and text[0] == "0") or (bare and len(text) >= self.opt.code_digits_from) or int(text) > self.max_value:
            return digits(text)
        return cardinal(int(text))

    @staticmethod
    def split(token: str, english: bool = False) -> tuple[str, str]:
        """(whole, fraction) of a written number: Turkish "1.250,5" and "3.5", or English "1,250.50"."""
        if english:
            whole, _, fraction = token.replace(",", "").partition(".")
            return whole, fraction
        whole, comma, fraction = token.partition(",")
        if not comma and "." in whole and not THOUSANDS.fullmatch(whole):
            whole, _, fraction = whole.partition(".")
        return whole.replace(".", ""), fraction

    def number(self, token: str, half: bool = False, english: bool = False) -> list[str]:
        """A written number in words; ",5" after a whole number is "buçuk" (and 0,5 "yarım") when the context counts things."""
        whole, fraction = self.split(token, english)
        if not fraction:
            return self.integer(whole, bare=token.isdigit())
        if half and self.opt.half_as_bucuk and fraction == "5":
            return ["yarım"] if int(whole) == 0 else [*self.integer(whole), "buçuk"]
        return [*self.integer(whole), "virgül", *tr_words.decimals(fraction)]

    @staticmethod
    def say(words: list[str], suffix: str | None = None) -> str:
        return " ".join(tr_suffix.apply(words, suffix) if suffix else words)

    def plain(self, m: re.Match) -> str:
        words = self.number(m["num"], half=True)
        suffix = m["suf"]
        if suffix and self.opt.decades == "short" and (decade := self.decade_of(m["num"], suffix)):
            words = decade
        return self.say(words, suffix)

    @staticmethod
    def decade_of(token: str, suffix: str) -> list[str] | None:
        """1990'lar -> "doksan" (the century left out) when the owner asks for short decades; 1900 and 2000 stay whole."""
        morphemes = tr_suffix.parse(tr_suffix.lower(suffix)) or []
        if token.isdigit() and 1910 <= int(token) <= 2090 and int(token) % 100 and int(token) % 10 == 0 and morphemes[:1] in (
                [tr_suffix.Morpheme.PL], [tr_suffix.Morpheme.LI]):
            return cardinal(int(token) % 100)
        return None

    def short_decade(self, m: re.Match) -> str:
        return self.say(cardinal(int(m["d"])), m["suf"])

    # dates, times, files ------------------------------------------------------------------------------------------------------------------

    def date(self, m: re.Match) -> str:
        day, month = int(m["d"]), int(m["mo"])
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return m.group()
        return " ".join([*cardinal(day), MONTHS[month - 1], self.say(cardinal(int(m["y"])), m["suf"])])

    def time(self, m: re.Match) -> str:
        """10:30 and "saat 22.30" (a dot only after "saat" or with an ending, else it is a decimal); the half hour is "buçuk"."""
        if m["sep"] == "." and not (m["saat"] or m["suf"]):
            return m.group()
        hour, minute = cardinal(int(m["h"])), int(m["m"])
        if minute == 0:
            words = hour
        elif minute == 30 and self.opt.time_half_as_bucuk:
            words = [*hour, "buçuk"]
        else:
            words = [*hour, *(["sıfır"] if minute < 10 else []), *cardinal(minute)]
        return (m["saat"] or "") + self.say(words, m["suf"])

    def file_number(self, m: re.Match) -> str:
        """A court file number: "2019/1234" is "iki bin on dokuz bölü bin iki yüz otuz dört"."""
        return self.say([*cardinal(int(m["y"])), "bölü", *cardinal(int(m["n"]))], m["suf"])

    def address_label(self, m: re.Match) -> str:
        label = "numara" if m["label"] == "No" else m["label"].lower()
        if m["a"]:
            return f"{label} {self.say([*cardinal(int(m['a'])), 'bölü', *cardinal(int(m['b']))])}"
        return label + " "

    # phone, emergency, plates -------------------------------------------------------------------------------------------------------------

    def phone(self, m: re.Match) -> str:
        """0 532 123 45 67: needs the leading 0, a +country code or brackets. Zero and the first groups digit by digit, the last two as numbers."""
        if not (m["cc"] or m["zero"] or m["par"]):
            return m.group()
        country = ["artı", *cardinal(int(m["cc"].strip("+ ")))] if m["cc"] else []
        start = ["sıfır"] if m["zero"] else []
        last_pairs = self.opt.phone_numbers == "groups"
        groups = [digits(m["a"]), digits(m["b"]), cardinal(int(m["c"])) if last_pairs else digits(m["c"]),
                  cardinal(int(m["d"])) if last_pairs else digits(m["d"])]
        return ", ".join(" ".join(part) for part in [[*country, *start, *groups[0]], *groups[1:]])

    def emergency(self, m: re.Match) -> str:
        """112, 155, ... said digit by digit ("bir bir iki") when a call word is near; see Options.emergency_numbers."""
        near = m.string[max(0, m.start() - 30):m.start()] + " " + m.string[m.end():m.end() + 25]
        mode = self.opt.emergency_numbers
        by_digit = mode == "digits_always" or (mode == "digits_in_context" and (m["alo"] or EMERGENCY_CONTEXT.search(near)))
        words = digits(m["n"]) if by_digit else cardinal(int(m["n"]))
        return (m["alo"] or "") + self.say(words, m["suf"])

    def plate(self, m: re.Match) -> str:
        if m["letters"] in NOT_PLATES:
            return m.group()
        province = digits(m["province"]) if m["province"][0] == "0" else cardinal(int(m["province"]))
        letters = [tr_words.LETTER_NAMES[letter] for letter in m["letters"]]
        return " ".join([*province, *letters, *cardinal(int(m["number"]))])

    # money, percent, units, calibres ------------------------------------------------------------------------------------------------------

    def currency(self, m: re.Match) -> str:
        """500 TL, ₺500, 12,50 TL, $12.50, 5 milyon dolar; the currency word is the last word, so an ending goes on it (500 TL'yi: lirayı)."""
        cur = m["cur"]
        major, minor = CURRENCIES[cur]
        major = "euro" if major == "avro" and self.opt.euro_name == "euro" else major
        english = cur in ENGLISH_FORMAT_CURRENCIES and ENGLISH_AMOUNT.fullmatch(m["num"]) is not None
        if m["scale"]:
            return self.say([*self.number(m["num"], half=True, english=english), m["scale"].strip(), major], m["suf"])
        whole, fraction = self.split(m["num"], english)
        cents = int(fraction.ljust(2, "0")[:2]) if fraction else 0
        words = [*(self.integer(whole) + [major] if int(whole) or not cents else [])]
        if cents:
            words += [*(["ve"] if words and self.opt.money_with_ve else []), *cardinal(cents), minor]
        return self.say(words, m["suf"])

    def percent(self, m: re.Match) -> str:
        return self.say(["yüzde", *self.number(m["a"] or m["b"])], m["suf"])

    def unit(self, m: re.Match) -> str:
        unit = re.sub(r"\s", "", m["unit"])
        words, name = self.number(m["num"]), UNITS[unit]
        if unit in SPEED_UNITS:
            return "saatte " + self.say([*words, name], m["suf"])
        return self.say([*words, name], m["suf"])

    def calibre(self, m: re.Match) -> str:
        """.38 is "otuz sekiz kalibre" (".357 Magnum" keeps its name, ".45'lik" its ending)."""
        words = cardinal(int(m["n"]))
        if m["name"] or m["suf"]:
            return self.say(words, m["suf"]) + (m["name"] or "")
        return self.say([*words, "kalibre"])

    # ordinals and roman numerals ----------------------------------------------------------------------------------------------------------

    @staticmethod
    def next_word(m: re.Match) -> str:
        following = m.string[m.end():].split(None, 1)
        return following[0].strip(".,;:!?\"'”)") if following else ""

    def ordinal_words(self, value: int, following: str, yy: str | None) -> str:
        words = tr_suffix.apply(cardinal(value), "inci")
        if yy:
            return " ".join(words) + " yüzyıl"
        if following[:1].isupper():
            words[0] = capitalized(words[0])
        return " ".join(words)

    def ordinal(self, m: re.Match) -> str:
        """"3." before a lowercase word or a known head ("1. Dünya Savaşı") is an ordinal; a full stop at the end of a sentence is not."""
        following = "yy" if m["yy"] else self.next_word(m)
        if following[:1].isupper() and following not in ORDINAL_HEADS or following.capitalize() in MONTHS or not following[:1].isalpha():
            return m.group()
        return self.ordinal_words(int(m["n"]), following, m["yy"])

    def roman(self, m: re.Match) -> str:
        """"II. Dünya Savaşı", "XX. yüzyıl", "II. Abdülhamid": letters of a roman numeral and a full stop, with a word that takes an ordinal."""
        letters = m["r"]
        following = "yy" if m["yy"] else self.next_word(m)
        known = tr_suffix.lower(following) in ROMAN_TRIGGERS or following in ROMAN_NAMES
        plausible = (len(letters) > 1 and set(letters) <= set("IVXLC")) or known
        if not (plausible and ROMAN_SHAPE.fullmatch(letters) and not (len(letters) == 1 and letters in "MCDL")):
            return m.group()
        return self.ordinal_words(roman_value(letters), following, m["yy"])

    def roman_suffix(self, m: re.Match) -> str:
        if not ROMAN_SHAPE.fullmatch(m["r"]):
            return m.group()
        return self.say(cardinal(roman_value(m["r"])), m["suf"])

    # fractions, ranges, signs -------------------------------------------------------------------------------------------------------------

    def fraction(self, m: re.Match) -> str:
        """3/4 is "dörtte üç" (the denominator in the locative first); 24/7 is "yirmi dört yedi"; a pair that is no fraction is "A bölü B"."""
        numerator, denominator = int(m["n"]), int(m["d"])
        if (numerator, denominator) in CONTEXT_FRACTIONS:
            return self.say(CONTEXT_FRACTIONS[(numerator, denominator)], m["suf"])
        if not 1 <= numerator < denominator:
            return self.say([*cardinal(numerator), "bölü", *cardinal(denominator)], m["suf"])
        whole = [*self.integer(m["whole"])] if m["whole"] else []
        if (numerator, denominator) == (1, 2):
            return self.say([*whole, "buçuk"] if whole else ["yarım"], m["suf"])
        under = tr_suffix.apply(cardinal(denominator), "de")
        return self.say([*whole, *(["tam"] if whole else []), *under, *cardinal(numerator)], m["suf"])

    def span(self, m: re.Match) -> str:
        """1990-1995 is "A ila B" ("A ile B arasında" when "arasında" follows); 2-1 (descending) is a score, said "iki bir"."""
        first, second = m["a"], m["b"]
        if len(first) == 4 and len(second) == 2 and int(first[:2] + second) > int(first):
            second = first[:2] + second
        if int(first) >= int(second):
            return self.say([*self.integer(first), *self.integer(second)], m["suf"])
        between = m.string[m.end():].lstrip().lower().startswith("aras")
        joint = "ile" if between else self.opt.range_word
        return " ".join([*self.integer(first), joint, self.say(self.integer(second), m["suf"])])

    def sign(self, m: re.Match) -> str:
        return "eksi " if m["minus"] else "artı "


def roman_value(letters: str) -> int:
    """A valid roman numeral's value: a letter before a larger one is subtracted (IX is 9)."""
    values = [ROMAN_VALUE[letter] for letter in letters]
    return sum(-value if following > value else value for value, following in zip(values, [*values[1:], 0]))
