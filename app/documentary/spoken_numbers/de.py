"""German numbers for the voice: what a native narrator says for the digits left in a script ("um 1 Uhr" -> "um ein Uhr", "am 3. Mai" -> "am dritten Mai",
"12,50 €" -> "zwölf Euro fünfzig", "1999" -> "neunzehnhundertneunundneunzig", "Notruf 110" -> "Notruf eins eins null").

The German writer is told to write numbers as words, so this is the safety net for what remains. Text without digits is not touched, except the Roman numerals
of rulers and centuries ("Heinrich IV."), which `roman_numerals` can switch off. Words are produced by `de_words` (own cardinals: "ein/eine/einen/einem" agree
with the noun), the case and gender guesses by `de_grammar`, the patterns are in `de_patterns`."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.documentary.spoken_numbers import de_patterns as p
from app.documentary.spoken_numbers.base import LanguageNumbers
from app.documentary.spoken_numbers.de_grammar import (Case, case_before, gender_of, is_label, one_before_noun, ordinal_ending, words_before)
from app.documentary.spoken_numbers.de_words import MAX_CARDINAL, cardinal, digits, ordinal, year

CAPITAL_ARTICLE = {Case.NOMINATIVE: ("der", "e"), Case.DATIVE: ("dem", "en"), Case.ACCUSATIVE: ("den", "en"), Case.GENITIVE: ("des", "en")}
CENTS_BELOW_TEN = Literal["bare", "null", "and_cent"]


class Options(BaseModel):
    """What a project owner may reasonably flip; the defaults are the researched narrator habits (scratchpad/numbers/de.md)."""

    emergency_numbers: Literal["digits_in_context", "digits_always", "number_always"] = "digits_in_context"  # 110/112/116 117: digit by digit near "Notruf" etc.
    digit_two: Literal["zwei", "zwo"] = "zwei"  # a 2 in a digit string (telephone, 112): the narrator says "zwei"
    dollar_name: Literal["Dollar", "US-Dollar"] = "Dollar"  # what "$" and "USD" are called
    toll_free_as_number: bool = True  # 0800 is "null achthundert" and not "null acht null null"
    half_as_einhalb: bool = True  # "2,5 Jahre" and "2,5 Millionen" are "zweieinhalb", not "zwei Komma fünf"
    round_the_clock: str = "vierundzwanzig sieben"  # what "24/7" is
    paragraph_word: Literal["Paragraf", "Paragraph"] = "Paragraf"  # the spelling for "§" (the voice says it the same)
    roman_numerals: bool = True  # "Heinrich IV." -> "Heinrich der Vierte", "XX. Jahrhundert" -> "zwanzigste Jahrhundert"
    years_2000s: Literal["cardinal", "pairs"] = "cardinal"  # 2024: "zweitausendvierundzwanzig" or "zwanzig vierundzwanzig"
    cents_below_ten: CENTS_BELOW_TEN = "bare"  # 12,05 EUR: "zwölf Euro fünf", "zwölf Euro null fünf" or "zwölf Euro und fünf Cent"
    postcodes_digit_by_digit: bool = True  # 10115 Berlin: "eins null eins eins fünf"


class Numbers(LanguageNumbers):
    """Turns the digits of a German text into words, rule by rule in a fixed order (a rule that has said a number leaves no digits for the later ones)."""

    def __init__(self, options: dict, max_value: int):
        super().__init__(options, max_value)
        self.settings = Options.model_validate(options)
        self.rules = [self._normalise, self._romans, self._abbreviations, self._round_the_clock, self._dates, self._ordinals, self._times, self._ibans, self._phones,
                      self._case_files, self._plates, self._postcodes, self._ranges, self._signs, self._money, self._quantities, self._percents,
                      self._scores, self._fractions, self._compounds, self._calibres, self._units, self._emergency, self._decades, self._years, self._decimals,
                      self._house_letters, self._cardinals]

    def text(self, text: str) -> str:
        """Like the base, with this language's protected spans and a placeholder that is not a digit, so a kept code survives the rules."""
        kept: list[str] = []

        def keep(match: re.Match) -> str:
            kept.append(match.group())
            return chr(p.PRIVATE_USE + len(kept) - 1)

        converted = self.convert(p.PROTECTED.sub(keep, text))
        return converted.translate({p.PRIVATE_USE + index: value for index, value in enumerate(kept)})

    def convert(self, text: str) -> str:
        for rule in self.rules:
            text = rule(text)
        return text

    # -- words ----------------------------------------------------------------------------------------------------------------------------------------------
    def _number(self, value: int, one: str = "eins") -> str:
        if value > min(self.max_value, MAX_CARDINAL):
            return digits(str(value), self.settings.digit_two)
        return cardinal(value, one)

    def _counted(self, value: int, before: str, gender: str | None) -> str:
        """`value` before a noun of `gender`: a closing 1 agrees ("ein Mann", "eine Frau", "einen Euro", "mit einem Messer")."""
        return self._number(value, one_before_noun(before, gender) if value == 1 else "eins")

    def _digit_groups(self, groups: list[str]) -> str:
        return ", ".join(digits(group, self.settings.digit_two) for group in groups)

    def _year_words(self, value: int) -> str:
        return year(value, pairs=self.settings.years_2000s == "pairs")

    @staticmethod
    def _integer(text: str) -> int:
        return int(text.replace(".", ""))

    @staticmethod
    def _capitalised(words: str) -> str:
        return words[:1].upper() + words[1:]

    def _decimal_words(self, integer: int, decimals: str, half_allowed: bool) -> str:
        """"3,5" as "drei Komma fünf" (digits after the comma one by one); with `half_allowed` ",5" is "einhalb" ("zweieinhalb")."""
        if decimals == "5" and half_allowed and self.settings.half_as_einhalb and 1 <= integer < 10 ** 6:
            return cardinal(integer, "ein") + "einhalb"
        return f"{self._number(integer)} Komma {digits(decimals, self.settings.digit_two)}"

    # -- Roman numerals -------------------------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _roman_value(token: str) -> int | None:
        if not p.ROMAN.match(token) or token in {"L", "C", "D", "M"}:
            return None
        total = 0
        for index, char in enumerate(token):
            value = p.ROMAN_VALUES[char]
            total += -value if index + 1 < len(token) and p.ROMAN_VALUES[token[index + 1]] > value else value
        return total

    def _romans(self, text: str) -> str:
        if not self.settings.roman_numerals:
            return text
        text = p.RULER.sub(self._ruler, text)
        text = p.ROMAN_CENTURY.sub(self._roman_century, text)
        return p.ROMAN_LABEL.sub(self._roman_label, text)

    def _ruler(self, match: re.Match) -> str:
        """"Heinrich IV." as "Heinrich der Vierte", in the case the word before asks for ("unter Wilhelm II." -> "unter Wilhelm dem Zweiten"). A single letter before a
        capitalised word is an initial ("Paul V. Schmidt") and stays."""
        name, value = match["name"], self._roman_value(match["num"])
        last = name.split()[-1]
        genitive = last not in p.RULER_NAMES and last.endswith("s") and last[:-1] in p.RULER_NAMES
        initial = len(match["num"]) == 1 and p.CAPITALISED_WORD.match(match.string[match.end():])
        if value is None or initial or not (last in p.RULER_NAMES or genitive):
            return match.group()
        article, ending = CAPITAL_ARTICLE[Case.GENITIVE if genitive else case_before(match.string[: match.start()])]
        return f"{name} {article} {self._capitalised(ordinal(value, ending) or '')}"

    def _roman_century(self, match: re.Match) -> str:
        value = self._roman_value(match["r"])
        words = ordinal(value, ordinal_ending(match.string[: match.start()])) if value else None
        if words is None:
            return match.group()
        return f"{self._capitalised(words) if match['noun'] in p.CAPITALISED_ROMAN_NOUNS else words} {match['noun']}"

    def _roman_label(self, match: re.Match) -> str:
        value = self._roman_value(match["r"])
        return match.group() if value is None else f"{match['w']} {cardinal(value)}"

    # -- text shape -----------------------------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _normalise(text: str) -> str:
        """Thin and no-break spaces between groups of three digits (DIN 5008) are thousands separators."""
        return re.sub(r"(?<=\d)[  ](?=\d{3}(?!\d))", "", text)

    def _abbreviations(self, text: str) -> str:
        for pattern, words in p.ABBREVIATIONS:
            text = pattern.sub(words, text)
        return p.PARAGRAPH.sub(self.settings.paragraph_word, text)

    def _round_the_clock(self, text: str) -> str:
        return p.ROUND_THE_CLOCK.sub(self.settings.round_the_clock, text)

    # -- dates, ordinals, times -----------------------------------------------------------------------------------------------------------------------------
    def _dates(self, text: str) -> str:
        text = p.DATE_RANGE.sub(self._date_range, text)
        text = p.DATE_TEXT.sub(self._date_text, text)
        return p.DATE_NUMERIC.sub(self._date_numeric, text)

    @staticmethod
    def _valid_day(day: int) -> bool:
        return 1 <= day <= 31

    def _date_range(self, match: re.Match) -> str:
        first, last = int(match["a"]), int(match["d"])
        if not (self._valid_day(first) and self._valid_day(last)):
            return match.group()
        ending = ordinal_ending(match.string[: match.start()])
        words = f"{ordinal(first, ending)} bis {ordinal(last, ending)} {match['m']}"
        return words + (f" {self._year_words(int(match['y']))}" if match["y"] else "")

    def _date_text(self, match: re.Match) -> str:
        day = int(match["d"])
        if not self._valid_day(day):
            return match.group()
        words = f"{ordinal(day, ordinal_ending(match.string[: match.start()]))} {match['m']}"
        return words + (f" {self._year_words(int(match['y']))}" if match["y"] else "")

    def _date_numeric(self, match: re.Match) -> str:
        day, month = int(match["d"]), int(match["m"])
        previous = words_before(match.string[: match.start()], 1)
        if not (self._valid_day(day) and 1 <= month <= 12) or (not match["y"] and (not previous or previous[0] not in p.DATE_CUES)):
            return match.group()
        words = f"{ordinal(day, ordinal_ending(match.string[: match.start()]))} {p.MONTHS[month - 1]}"
        return words + (f" {self._year_words(int(match['y']))}" if match["y"] else "")

    def _ordinals(self, text: str) -> str:
        return p.ORDINAL.sub(self._ordinal, text)

    def _ordinal(self, match: re.Match) -> str:
        """"3." is an ordinal when a noun that takes one follows or an article/contraction precedes ("am 8. Tag"); otherwise it is the end of a sentence."""
        value, following = int(match["n"]), match["next"]
        before = match.string[: match.start()]
        previous = words_before(before, 1)
        if value == 0 or not (following in p.ORDINAL_NOUNS or (previous and previous[0] in p.ARTICLES)):
            return match.group()
        if following == "FC" and value == 1:
            return "Erster"
        return ordinal(value, ordinal_ending(before)) or match.group()

    def _times(self, text: str) -> str:
        text = p.TIME.sub(lambda m: self._clock(m["h"], m["mi"]), text)
        text = p.TIME_CUED.sub(lambda m: f"{m['cue']} {self._clock(m['h'], m['mi'])}", text)
        return p.HOUR_ONLY.sub(lambda m: self._clock(m["h"], "00"), text)

    def _clock(self, hour: str, minute: str) -> str:
        """The official 24-hour reading: "zehn Uhr dreißig", "ein Uhr", "null Uhr zehn"; "halb elf" is not generated."""
        hours = int(hour)
        words = f"{'ein' if hours == 1 else cardinal(hours)} Uhr"
        return words if int(minute) == 0 else f"{words} {cardinal(int(minute))}"

    # -- codes ----------------------------------------------------------------------------------------------------------------------------------------------
    def _phones(self, text: str) -> str:
        return p.PHONE.sub(self._phone, text)

    def _phone(self, match: re.Match) -> str:
        """A telephone number digit by digit in its written groups; "+49" is "plus neunundvierzig", the 0800 prefix "null achthundert"."""
        prefix, groups = match["prefix"], re.split(r"[ /\-]+", match["rest"].strip(" /-"))
        if sum(len(part) for part in [prefix, *groups] if part.isdigit()) < 7:
            return match.group()
        if prefix.startswith("+"):
            head = f"plus {cardinal(int(prefix[1:]))}"
        elif prefix.startswith("00"):
            head = f"null null {cardinal(int(prefix[2:]))}"
        elif prefix == "0800" and self.settings.toll_free_as_number:
            head = "null achthundert"
        else:
            head = digits(prefix, self.settings.digit_two)
        return f"{head}, {self._digit_groups(groups)}"

    def _ibans(self, text: str) -> str:
        return p.IBAN.sub(self._iban, text)

    def _iban(self, match: re.Match) -> str:
        number = re.sub(r"\s", "", match["rest"])
        chunks = [number[index: index + 4] for index in range(0, len(number), 4)]
        return f"{match['country']} {self._digit_groups([match['check'], *chunks])}"

    def _case_files(self, text: str) -> str:
        return p.CASE_FILE.sub(self._case_file, text)

    def _case_file(self, match: re.Match) -> str:
        """"Az. 1 Ks 12/05": the numbers as cardinals, the slash as "Schrägstrich", a two-digit year with its leading zero ("null fünf")."""
        short = match["y"]
        if len(short) == 4:
            closing = self._year_words(int(short))
        else:
            closing = f"null {cardinal(int(short))}" if short.startswith("0") else cardinal(int(short))
        return f"Aktenzeichen {cardinal(int(match['a']))} {match['l']} {cardinal(int(match['b']))} Schrägstrich {closing}"

    def _plates(self, text: str) -> str:
        return p.PLATE.sub(lambda m: f"{m['plate']} {digits(m['n'], self.settings.digit_two)}" + (f" {m['suffix']}" if m["suffix"] else ""), text)

    def _postcodes(self, text: str) -> str:
        if not self.settings.postcodes_digit_by_digit:
            return text
        return p.POSTCODE.sub(lambda m: digits(m["zip"], self.settings.digit_two) if p.POSTCODE_PREVIOUS.search(m.string[: m.start()]) else m.group(), text)

    def _emergency(self, text: str) -> str:
        if self.settings.emergency_numbers == "number_always":
            return text
        return p.EMERGENCY.sub(self._emergency_number, text)

    def _emergency_number(self, match: re.Match) -> str:
        """110, 112 and 116 117 digit by digit ("eins eins null"); with `digits_in_context` only near an emergency word and not before a counted noun."""
        if self.settings.emergency_numbers == "digits_in_context" and not self._near_emergency_word(match):
            return match.group()
        return ", ".join(digits(part, self.settings.digit_two) for part in match["n"].split())

    @staticmethod
    def _near_emergency_word(match: re.Match) -> bool:
        before = re.split(r"[.!?]", match.string[: match.start()])[-1]
        after = re.split(r"[.!?]", match.string[match.end():])[0]
        if p.NOUN_AFTER.match(after):
            return False
        words = [word.lower() for word in re.findall(rf"[{p.LETTER}]+", before)[-8:] + re.findall(rf"[{p.LETTER}]+", after)[:4]]
        return any(word in p.EMERGENCY_WORDS for word in words)

    # -- ranges, signs, money, quantities -------------------------------------------------------------------------------------------------------------------
    def _ranges(self, text: str) -> str:
        text = p.YEAR_RANGE.sub(self._year_range, text)
        return p.NUMBER_RANGE.sub(self._number_range, text)

    def _year_range(self, match: re.Match) -> str:
        first, second = int(match["a"]), match["b"]
        last = int(second) if len(second) == 4 else first // 100 * 100 + int(second)
        if not (1000 <= first <= 2099 and first < last <= 2099):
            return match.group()
        counted = p.NOUN_AFTER.match(match.string[match.end():])
        words = (self._number, self._number) if counted else (self._year_words, self._year_words)
        return f"{words[0](first)} bis {words[1](last)}"

    def _number_range(self, match: re.Match) -> str:
        """"10-12 Jahre" is "zehn bis zwölf Jahre"; the second number stays for the cardinal rule, which knows the noun after it."""
        value = self._integer(match["a"])
        counted = p.NOUN_AFTER.match(match.string[match.end() + len(match["b"]):])
        one = one_before_noun(match.string[: match.start()], gender_of(counted["noun"])) if counted and value == 1 else "eins"
        return f"{self._number(value, one)} bis "

    def _signs(self, text: str) -> str:
        return p.SIGN.sub(lambda m: "plus " if m["sign"] == "+" else "minus ", text)

    def _money(self, text: str) -> str:
        text = p.CURRENCY_AFTER.sub(self._money_amount, text)
        return p.CURRENCY_BEFORE.sub(self._money_amount, text)

    def _currency_name(self, symbol: str) -> str:
        name = p.CURRENCIES[symbol]
        return self.settings.dollar_name if name == "$" else name

    def _money_amount(self, match: re.Match) -> str:
        """"12,50 €" as "zwölf Euro fünfzig" and "0,99 €" as "neunundneunzig Cent"; "5 Mio. €" as "fünf Millionen Euro"; no plural -s after a number."""
        currency = self._currency_name(match["cur"])
        before = match.string[: match.start()]
        if match["mag"] or match["k"]:
            return f"{self._magnitude_words(match, before)} {currency}"
        value, decimals = self._integer(match["int"]), match["dec"]
        gender = p.CURRENCY_GENDER[currency]
        cents = int(decimals.ljust(2, "0")) if decimals and decimals.isdigit() else 0
        if cents == 0:
            return f"{self._counted(value, before, gender)} {currency}"
        subunit = p.CURRENCY_SUBUNIT.get(currency, "Cent")
        if value == 0:
            return f"{cardinal(cents, 'ein')} {subunit}"
        euros = f"{self._counted(value, before, gender)} {currency}"
        if cents >= 10 or self.settings.cents_below_ten == "bare":
            return f"{euros} {cardinal(cents)}"
        if self.settings.cents_below_ten == "null":
            return f"{euros} null {cardinal(cents)}"
        return f"{euros} und {cardinal(cents, 'ein')} {subunit}"

    def _magnitude_words(self, match: re.Match, before: str) -> str:
        """"3 Mrd." -> "drei Milliarden", "1 Million" -> "eine Million", "2,5 Mio." -> "zweieinhalb Millionen", "50k"/"50 Tsd." -> "fünfzigtausend"."""
        value, decimals = self._integer(match["int"]), match["dec"]
        if match["k"] or match["mag"].startswith("Tsd"):
            return cardinal(value, "ein") + "tausend"
        singular, plural = p.BIG_UNITS[p.MAGNITUDE_NOUN[match["mag"].lower().rstrip(".")]]
        if decimals:
            return f"{self._decimal_words(value, decimals, True)} {plural}"
        return f"{cardinal(value, 'eine')} {singular if value == 1 else plural}"

    def _quantities(self, text: str) -> str:
        return p.QUANTITY.sub(lambda m: self._magnitude_words(m, m.string[: m.start()]), text)

    def _percents(self, text: str) -> str:
        return p.PERCENT.sub(self._percent, text)

    def _percent(self, match: re.Match) -> str:
        word = "Prozent" if match["sign"] == "%" else "Promille"
        if match["dec"]:
            return f"{self._decimal_words(self._integer(match['int']), match['dec'], False)} {word}"
        return f"{self._counted(self._integer(match['int']), match.string[: match.start()], 'n')} {word}"

    def _scores(self, text: str) -> str:
        return p.SCORE.sub(lambda m: f"{cardinal(int(m['a']))} zu {cardinal(int(m['b']))}", text)

    def _fractions(self, text: str) -> str:
        return p.FRACTION.sub(self._fraction, text)

    def _fraction(self, match: re.Match) -> str:
        numerator, denominator = int(match["a"]), int(match["b"])
        if denominator not in p.FRACTION_NAMES or not 0 < numerator < denominator:
            return match.group()
        return f"{cardinal(numerator, 'ein')} {p.FRACTION_NAMES[denominator]}"

    # -- compounds, calibres, units -------------------------------------------------------------------------------------------------------------------------
    def _compounds(self, text: str) -> str:
        text = p.AGE.sub(self._age, text)
        text = p.UNIT_COMPOUND.sub(self._unit_compound, text)
        return p.HYPHEN_COMPOUND.sub(self._hyphen_compound, text)

    def _age(self, match: re.Match) -> str:
        """"35-jährig" as "fünfunddreißigjährig", "die 35-Jährige" as "die Fünfunddreißigjährige"."""
        words = cardinal(int(match["n"]), "ein") + "jähri" + match["rest"][len("ähri"):]
        return self._capitalised(words) if match["j"] == "J" else words

    def _unit_compound(self, match: re.Match) -> str:
        """"9-mm-Pistole" as "Neun-Millimeter-Pistole"."""
        number = match["n"].split(",")
        words = self._decimal_words(int(number[0]), number[1], False) if len(number) > 1 else cardinal(int(number[0]), "ein")
        return f"{self._capitalised(words)}-{p.UNIT_COMPOUND_WORDS[match['unit']]}-"

    def _hyphen_compound(self, match: re.Match) -> str:
        """"3-fach" as "dreifach", "2-Zimmer-Wohnung" as "Zwei-Zimmer-Wohnung"."""
        words = cardinal(int(match["n"]), "ein")
        following = match.string[match.end()]
        return f"{self._capitalised(words)}-" if following.isupper() else words

    def _calibres(self, text: str) -> str:
        return p.CALIBRE.sub(self._calibre, text)

    def _calibre(self, match: re.Match) -> str:
        """".38" as "Kaliber achtunddreißig" (the word is dropped after "Kaliber" and before a cartridge name: ".38 Special" -> "achtunddreißig Special")."""
        words = cardinal(int(match["n"]))
        previous = words_before(match.string[: match.start()], 1)
        return words + (match["name"] or "") if match["name"] or (previous and previous[0] == "kaliber") else f"Kaliber {words}"

    def _units(self, text: str) -> str:
        text = p.HEIGHT.sub(lambda m: f"{cardinal(int(m['m']), 'ein')} Meter {cardinal(int(m['cm']))}", text)
        return p.UNIT.sub(self._unit, text)

    def _unit(self, match: re.Match) -> str:
        """"120 km/h" as "hundertzwanzig Kilometer pro Stunde", "-5 °C" as "minus fünf Grad Celsius"; units stay singular after a number."""
        unit = match["unit"]
        singular, plural, gender = p.UNITS[unit]
        value, decimals = self._integer(match["int"]), match["dec"]
        if decimals:
            half = unit not in p.UNITS_WITHOUT_HALF
            return f"{self._decimal_words(value, decimals, half)} {plural}"
        return f"{self._counted(value, match.string[: match.start()], gender)} {singular if value == 1 else plural}"

    # -- decades, years, decimals, cardinals ----------------------------------------------------------------------------------------------------------------
    def _decades(self, text: str) -> str:
        return p.DECADE.sub(self._decade, text)

    def _decade(self, match: re.Match) -> str:
        """"die 90er Jahre" / "die 1990er" as "die neunziger", "in den 1990ern" as "in den neunzigern", "12er-Pack" as "zwölfer-Pack"."""
        value = int(match["n"])
        if 1910 <= value <= 1990 and value % 10 == 0:
            value %= 100
        return f"{cardinal(value, 'ein')}er{match['plural']}"

    def _years(self, text: str) -> str:
        return p.YEAR.sub(self._year, text)

    def _year(self, match: re.Match) -> str:
        """A 4-digit number is a year after a month or "Jahr", or when no counted noun follows ("1900 Euro", "1500 Menschen" are amounts: "tausend...")."""
        value = int(match["y"])
        previous = words_before(match.string[: match.start()], 1)
        counted = p.NOUN_AFTER.match(match.string[match.end():])
        if 1100 <= value <= 2099 and (not counted or (previous and previous[0] in p.YEAR_PREVIOUS_WORDS)):
            return self._year_words(value)
        return self._cardinal_in_context(match, value)

    def _decimals(self, text: str) -> str:
        return p.DECIMAL.sub(self._decimal, text)

    def _decimal(self, match: re.Match) -> str:
        following = p.NEXT_WORD.match(match.string[match.end():])
        half = bool(following) and following["word"].lower() in p.HALF_NOUNS
        return self._decimal_words(self._integer(match["int"]), match["dec"], half)

    def _house_letters(self, text: str) -> str:
        return p.HOUSE_LETTER.sub(lambda m: f"{cardinal(int(m['n']))} {m['l']}", text)

    def _cardinals(self, text: str) -> str:
        return p.CARDINAL.sub(lambda m: self._cardinal_in_context(m, self._integer(m["n"]), m["n"]), text)

    def _cardinal_in_context(self, match: re.Match, value: int, raw: str = "") -> str:
        """A plain number: a closing 1 before a capitalised noun is "ein/eine/..." by the noun, a labelled one ("Platz 1") or a bare one is "eins";
        a number with a leading zero is read digit by digit."""
        if len(raw) > 1 and raw.startswith("0"):
            return digits(raw, self.settings.digit_two)
        before = match.string[: match.start()]
        counted = p.NOUN_AFTER.match(match.string[match.end():])
        if value == 1 and counted and not is_label(before):
            return self._number(value, one_before_noun(before, gender_of(counted["noun"])))
        return self._number(value)
