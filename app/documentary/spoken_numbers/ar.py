"""Arabic (Modern Standard) numbers for the voice: the words a documentary narrator says for the digits of a text, built on arabic_numbers.py (the cardinal
engine) and ar_words.py (the tables). Rules, sources and what stays unverified: docs/spoken_numbers_ar.md. The words are plain: no vowel marks, no case endings.

The text is read in stages, each one replacing the spans it understands with words (marked with private-use characters so that later stages and the context
checks ignore them): numeric and written dates, phone numbers, clock times, currency,
percent, a number before "million", Hijri/Gregorian year marks, units, fractions, year ranges and ranges, scores, ordinals after "ال" or a head noun
("القرن 20"), Roman numerals, then every other number as a cardinal. A cardinal takes its gender from the counted noun that follows it (a closed lexicon: a
noun not listed is masculine, the safe default) and its case from the word before it (oblique after a word, nominative only at the start of a sentence or after
a subject-taking verb): "عام 2007" is "عام ألفين وسبعة"."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal, NamedTuple

from pydantic import BaseModel

from app.documentary import arabic_numbers as AN
from app.documentary.arabic_numbers import Form
from app.documentary.persian_numbers import DIGITS
from app.documentary.spoken_numbers import ar_words as W
from app.documentary.spoken_numbers.base import PROTECTED, LanguageNumbers

BIDI_MARKS = re.compile("[‎‏؜‪-‮⁦-⁩﻿]")
SEPARATORS = str.maketrans({"٫": ".", "٬": ","})
SPAN_OPEN, SPAN_CLOSE = "", ""
SPAN = re.compile(f"{SPAN_OPEN}(.*?){SPAN_CLOSE}", re.S)
LETTER = "ء-ي"
START = r"(?<![A-Za-z0-9_{])(?<![A-Za-z]-)(?<!\d[.,])"      # not inside a token of letters and digits ("F-16") or of a version ("1.2.3"), not a placeholder
NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)"
END = r"(?![\d}])(?![.,]\d)"
NEXT_WORD = re.compile(r"[ \t]*([^\s\d.,;:!?؟،؛()\[\]{}«»\"'\-–—%٪/]+)")
PERCENT_WORDS = {"بالمئة": "بالمئة", "في المئة": "في المئة"}
ERA_WORDS = {"هـ": "للهجرة", "ه": "للهجرة", "م": "للميلاد"}
POUNDS = {"sterling": "gbp", "egyptian": "egp"}
FRACTION_WORD = "فاصلة"

PHONE = re.compile(r"(?<![\w{+])(?P<plus>\+)?(?P<body>\d[\d \-()]{5,}\d)(?![\d}])")
NUMERIC_DATE_YEAR_FIRST = re.compile(START + r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})" + END)
NUMERIC_DATE_DAY_FIRST = re.compile(START + r"(?P<d>\d{1,2})[-/.](?P<m>\d{1,2})[-/.](?P<y>\d{4})" + END)
WRITTEN_DATE = re.compile(START + r"(?P<d>\d{1,2})\s+(?P<month>" + W.MONTH_PATTERN + f")(?![{LETTER}])"
                          r"(?:\s+(?P<y>\d{3,4})(?:\s*(?P<era>هـ|ه|م)(?![" + LETTER + r"\d]))?(?![\d.,]))?")
CLOCK = re.compile(r"(?P<lead>" + W.loose("الساعة") + r"\s+)?" + START + r"(?P<h>\d{1,2})(?:(?P<sep>[:.])(?P<m>\d{2}))?" + END)
CURRENCY = re.compile(r"(?:(?P<pre>" + W.SYMBOL_PATTERN + r")\s*" + START + "(?P<a>" + NUM + ")" + END + "|" + START + "(?P<b>" + NUM + ")" + END +
                      r"\s*(?P<post>" + W.SYMBOL_PATTERN + r"))(?:\s+(?P<scale>" + W.SCALE_PATTERN + f")(?![{LETTER}]))?")
PERCENT = re.compile("(?:" + START + "(?P<a>" + NUM + ")" + END + r"\s*[%٪]|[%٪]\s*" + START + "(?P<b>" + NUM + ")" + END + ")")
SCALED = re.compile(START + "(?P<n>" + NUM + ")" + END + r"\s+(?P<scale>" + W.SCALE_PATTERN + f")(?![{LETTER}])")
ERA = re.compile(START + r"(?P<y>\d{3,4})\s*(?P<era>هـ|ه|م)(?![" + LETTER + r"\d])")
UNIT = re.compile(START + "(?P<n>" + NUM + ")" + END + r"\s*(?P<u>" + W.UNIT_PATTERN + f")(?![A-Za-z{LETTER}])")
FRACTION_END = r"(?![\d}/])(?![.,]\d)"
MIXED_FRACTION = re.compile(START + r"(?P<w>\d+)\s+(?P<a>\d{1,2})/(?P<b>\d{1,2})" + FRACTION_END)
FRACTION = re.compile(START + r"(?P<a>\d{1,3})\s*/\s*(?P<b>\d{1,3})" + FRACTION_END)
YEAR_RANGE = re.compile(START + r"(?P<a>\d{4})\s*[-–—]\s*(?P<b>\d{4}|\d{2})" + END)
RANGE = re.compile(START + "(?P<a>" + NUM + ")" + END + r"\s*[-–—]\s*" + START + "(?P<b>" + NUM + ")" + END)
SCORE = re.compile(START + r"(?P<a>\d{1,2})\s*:\s*(?P<b>\d{1,2})" + END)
ARTICLE_ORDINAL = re.compile(f"(?<![{LETTER}])ال" + r"\s?(?P<n>\d{1,2})" + END)
DIGIT_CHAIN = re.compile(START + r"\d(?:-\d){2,}" + END)
ROMAN = re.compile(r"(?<![A-Za-z\d])(?P<r>[IVXLCDM]{1,8})(?![A-Za-z\d])")
NUMBER = re.compile(r"(?P<neg>(?<![\w}" + SPAN_CLOSE + r"])-(?=\d))?" + START + "(?P<n>" + NUM + ")" + END)
LEFTOVER = re.compile(r"(?<![{\d])\d+(?![\d}])")
PLACEHOLDER = re.compile(r"\{(\d+)\}")


class Options(BaseModel):
    # An emergency number (911, 997, 122) said digit by digit when it is named as one ("اتصل بـ 911"), always, or never (a plain number).
    emergency_numbers: Literal["digits_in_context", "digits_always", "number_always"] = "digits_in_context"
    # The month names said for a numeric date: the Gulf/Egypt names (يناير ...) the pan-Arab news uses, or the Levant ones (كانون الثاني ...).
    month_names: Literal["gulf_egypt", "levant"] = "gulf_egypt"
    # The word after a percentage.
    percent_word: Literal["بالمئة", "في المئة"] = "بالمئة"
    # Which pound the sign "£" means.
    pound_symbol: Literal["sterling", "egyptian"] = "sterling"
    # The number takes the oblique case after a word (False: always the nominative, as a plain reading of a figure).
    case_from_context: bool = True
    # A clock time on the 24-hour clock (13:00 to 00:59) with no part of the day after it gets one (بعد الظهر, مساء, ليلا); 1:00 to 12:59 is left as written.
    day_parts: bool = True
    # The day of a date is an ordinal ("الثالث عشر من نوفمبر"), not a cardinal.
    ordinal_dates: bool = True
    # An amount with two decimals is said in its subunit ("5.50 دولار": five dollars and fifty cents).
    currency_subunits: bool = True


class Head(NamedTuple):
    feminine: bool
    length: int          # words
    fixed_oblique: bool


class Around:
    """The words before and after a number (without the spans already turned into words): what decides its case, its gender and its reading."""

    def __init__(self, match: re.Match):
        self.before = SPAN.sub(r"\1", match.string[:match.start()])
        self.after = SPAN.sub(r"\1", match.string[match.end():])

    def tokens_before(self, count: int) -> list[str]:
        return [W.norm(t) for t in self.before.split()[-count:]]

    def tokens_after(self, count: int) -> list[str]:
        return [W.norm(t) for t in self.after.split()[:count]]

    def next_word(self) -> str:
        found = NEXT_WORD.match(self.after)
        return W.norm(found.group(1)) if found else ""

    def oblique(self, skip: int = 0) -> bool:
        """False at the start of a sentence or after a word that takes its subject next; `skip` words before the number are not looked at (a head noun)."""
        tokens = self.before.split()
        tokens = tokens[:len(tokens) - skip]
        return bool(tokens) and tokens[-1][-1] not in W.SENTENCE_END and W.norm(tokens[-1]) not in W.NOMINATIVE_AFTER


def split_number(token: str) -> tuple[str, str | None]:
    """The whole part and the decimals of a written number: a comma before exactly three digits is a thousands mark, any other comma or a dot is the point."""
    if "," in token and "." in token:
        whole, frac = token.split(".")
        return whole.replace(",", ""), frac
    if "," in token:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", token):
            return token.replace(",", ""), None
        whole, frac = token.split(",")
        return whole, frac
    if "." in token:
        whole, frac = token.split(".")
        return whole, frac
    return token, None


class Numbers(LanguageNumbers):
    """`options` is the language's block of config voice.number_words (see `Options`)."""

    def __init__(self, options: dict, max_value: int):
        super().__init__(options, max_value)
        self.opts = Options.model_validate(options)
        self.stages = [self.numeric_dates, self.phones, self.digit_chains, self.written_dates, self.times, self.currency, self.percent, self.scaled, self.eras, self.units,
                       self.fractions, self.year_ranges, self.ranges, self.scores, self.article_ordinals, self.roman, self.plain, self.leftovers]

    def text(self, text: str) -> str:
        """The base class sets a protected span aside under its index as bare digits, which this reader would read as the number; here it is a "{n}"."""
        kept: list[str] = []

        def keep(match: re.Match) -> str:
            kept.append(match.group())
            return f"{{{len(kept) - 1}}}"

        return PLACEHOLDER.sub(lambda m: kept[int(m.group(1))], self.convert(PROTECTED.sub(keep, text)))

    def convert(self, text: str) -> str:
        text = self.normalized(text)
        for stage in self.stages:
            text = stage(text)
        return SPAN.sub(r"\1", text)

    # --- reading helpers -------------------------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def normalized(text: str) -> str:
        """Western digits and separators for every digit system; no direction marks, no kashida between "ال" and a digit, no thin space inside a number."""
        text = BIDI_MARKS.sub("", text)
        text = re.sub("(?<=[٠-٩۰-۹])،(?=[٠-٩۰-۹])", ".", text)
        text = text.translate(DIGITS).translate(SEPARATORS)
        text = re.sub(r"ـ(?=\d)", "", text)
        return re.sub(r"(?<=\d)[   ](?=\d{3}(?!\d))", "", text)

    @staticmethod
    def say(words: str) -> str:
        return SPAN_OPEN + words + SPAN_CLOSE

    def oblique(self, around: Around, skip: int = 0) -> bool:
        return self.opts.case_from_context and around.oblique(skip)

    def integer(self, whole: str, form: Form) -> str:
        if (len(whole) > 1 and whole[0] == "0") or int(whole) > self.max_value:
            return AN.digit_by_digit(whole)
        return AN.to_words(int(whole), form)

    def spoken(self, whole: str, frac: str | None, form: Form) -> str:
        """A number with its decimals: one decimal digit is said as that digit, two (no leading zero) as a number, any other as digits."""
        if frac is None:
            return self.integer(whole, form)
        decimals = AN.to_words(int(frac)) if len(frac) == 1 or (len(frac) == 2 and frac[0] != "0") else AN.digit_by_digit(frac)
        return f"{self.integer(whole, form)} {FRACTION_WORD} {decimals}"

    def noun_phrase(self, n: int, noun: W.Noun, oblique: bool) -> str:
        """`n` with the noun it counts: one and two are the noun alone in its singular or dual, three to ten take the plural, a last 11 to 99 the accusative
        singular, any other the genitive singular."""
        adjective = noun.adjective
        if n == 1:
            return f"{noun.singular} {AN.unit(1, Form(feminine=noun.feminine))}" + (f" {adjective}" if adjective else "") + noun.tail
        if n == 2:
            return W.dual(noun.singular, oblique) + (f" {W.dual(adjective, oblique)}" if adjective else "") + noun.tail
        number = AN.to_words(n, Form(feminine=noun.feminine, oblique=oblique))
        last = n % 100
        if 3 <= last <= 10:
            plural_adjective = noun.plural_adjective or adjective
            return f"{number} {noun.plural}" + (f" {plural_adjective}" if plural_adjective else "") + noun.tail
        base = W.accusative(noun.singular) if 11 <= last <= 99 else noun.singular
        return f"{number} {base}" + (f" {adjective}" if adjective else "") + noun.tail

    def year(self, year: str, era: str | None = None) -> str:
        """A year after "عام": the genitive, so the dual and tens are oblique. Its era mark is said as the words it stands for."""
        return AN.to_words(int(year), Form(oblique=True)) + (f" {ERA_WORDS[era]}" if era else "")

    def day(self, day: int, oblique: bool) -> str:
        return W.ordinal(day, False, oblique) if self.opts.ordinal_dates else AN.to_words(day, Form(oblique=oblique))

    def head_of(self, around: Around) -> Head | None:
        """The head noun right before the number that makes it an ordinal ("القرن", "الحرب العالمية")."""
        tokens = around.tokens_before(2)
        for length in (2, 1):
            key = " ".join(tokens[len(tokens) - length:])
            if len(tokens) >= length and key in W.ORDINAL_HEADS:
                return Head(W.ORDINAL_HEADS[key], length, key in W.ORDINAL_FIXED_OBLIQUE)
        return None

    def head_ordinal(self, n: int, head: Head, around: Around) -> str:
        return W.ordinal(n, head.feminine, head.fixed_oblique or self.oblique(around, head.length))

    def ordinal_feminine(self, around: Around) -> bool:
        after = around.tokens_after(2)
        if len(after) == 2 and after[0] == "من" and after[1].startswith("عمر"):      # "في الـ35 من عمره": the year of his life
            return True
        return around.next_word() in W.FEMININE_NOUNS

    # --- stages ----------------------------------------------------------------------------------------------------------------------------------------------
    def date_words(self, day: int, month: str, year: str | None, era: str | None, oblique: bool) -> str:
        words = f"{self.day(day, oblique)} من {month}"
        return f"{words} عام {self.year(year, era)}" if year else words

    def numeric_dates(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            day, month = int(m.group("d")), int(m.group("m"))
            if not (1 <= day <= 31 and 1 <= month <= 12):
                return m.group()
            return self.say(self.date_words(day, W.MONTH_NAMES[self.opts.month_names][month - 1], m.group("y"), None, self.oblique(Around(m))))
        return NUMERIC_DATE_DAY_FIRST.sub(sub, NUMERIC_DATE_YEAR_FIRST.sub(sub, text))

    def written_dates(self, text: str) -> str:
        """"13 نوفمبر 2007": the day is an ordinal, the month stays as written, the year follows "عام"."""
        def sub(m: re.Match) -> str:
            day, year = int(m.group("d")), m.group("y")
            if not 1 <= day <= 31 or (m.group("era") == "م" and len(year or "") < 4):
                return m.group()
            head = self.say(f"{self.day(day, self.oblique(Around(m)))} من")
            if not year:
                return f"{head} {m.group('month')}"
            return f"{head} {m.group('month')} {self.say('عام ' + self.year(year, m.group('era')))}"
        return WRITTEN_DATE.sub(sub, text)

    def phones(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            body, plus = m.group("body"), bool(m.group("plus"))
            digits = re.sub(r"\D", "", body)
            if not ((plus and len(digits) >= 7) or (body[0] == "0" and len(digits) >= 8)):
                return m.group()
            groups = " ، ".join(AN.digit_by_digit(g) for g in re.findall(r"\d+", body))
            return self.say(("زائد " if plus else "") + groups)
        return PHONE.sub(sub, text)

    def digit_chains(self, text: str) -> str:
        """Single digits joined by hyphens ("9-1-1") are a spelled-out code: said digit by digit, never as a range."""
        return DIGIT_CHAIN.sub(lambda m: self.say(AN.digit_by_digit(m.group())), text)

    def clock(self, h24: int, minute: int, oblique: bool) -> str:
        h12 = h24 % 12 or 12
        following = W.hour(h12 % 12 + 1)
        if minute == 0:
            return W.hour(h12)
        if minute in (15, 30):
            return f"{W.hour(h12)} {AN.AND}{'الربع' if minute == 15 else 'النصف'}"
        if minute == 45:
            return f"{following} إلا ربعا"
        if minute in (50, 55):
            return f"{following} إلا {self.noun_phrase(60 - minute, W.MINUTE, oblique)}"
        return f"{W.hour(h12)} {AN.AND}{self.noun_phrase(minute, W.MINUTE, oblique)}"

    def times(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            lead, sep = m.group("lead"), m.group("sep")
            if (lead is None and sep != ":") or int(m.group("h")) > 24:
                return m.group()
            minute = int(m.group("m") or 0)
            if minute > 59:
                return m.group()
            around = Around(m)
            h24 = int(m.group("h")) % 24
            words = self.clock(h24, minute, self.oblique(around))
            if self.opts.day_parts and (h24 == 0 or h24 >= 13) and around.next_word() not in W.DAY_PART_WORDS:
                words += " " + W.day_part(h24)
            return (lead or "") + self.say(words)
        return CLOCK.sub(sub, text)

    def amount(self, m: re.Match) -> tuple[str, str | None, Decimal | None]:
        whole, frac = split_number(m.group("a") or m.group("b"))
        if m.group("scale"):
            total = Decimal(f"{whole}.{frac or 0}") * W.SCALE_WORDS[W.norm(m.group("scale"))]
            return whole, frac, total if total == total.to_integral_value() else None
        return whole, frac, None

    def currency(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            symbol = re.sub(r"\s", "", m.group("pre") or m.group("post")).lower()
            noun, subunit = W.CURRENCIES[POUNDS[self.opts.pound_symbol] if symbol == "£" else W.SYMBOLS[symbol]]
            whole, frac, total = self.amount(m)
            oblique = self.oblique(Around(m))
            if m.group("scale"):
                n = int(total) if total is not None else None
            elif frac is None or set(frac) == {"0"}:
                n = int(whole)
            elif self.opts.currency_subunits and subunit and len(frac) == 2:
                cents = self.noun_phrase(int(frac), subunit, oblique)
                return self.say(cents if int(whole) == 0 else f"{self.noun_phrase(int(whole), noun, oblique)} {AN.AND}{cents}")
            else:
                return self.say(f"{self.spoken(whole, frac, Form(oblique=oblique))} {noun.plural}")
            if n is None or n > self.max_value:
                return m.group()
            return self.say(self.noun_phrase(n, noun, oblique))
        return CURRENCY.sub(sub, text)

    def percent(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            whole, frac = split_number(m.group("a") or m.group("b"))
            words = self.spoken(whole, frac, Form(oblique=self.oblique(Around(m))))
            return self.say(f"{words} {PERCENT_WORDS[self.opts.percent_word]}")
        return PERCENT.sub(sub, text)

    def scaled(self, text: str) -> str:
        """"5 مليون" and "2.5 مليون" are said as the whole number: the scale word is part of it."""
        def sub(m: re.Match) -> str:
            whole, frac = split_number(m.group("n"))
            total = Decimal(f"{whole}.{frac or 0}") * W.SCALE_WORDS[W.norm(m.group("scale"))]
            if total != total.to_integral_value() or total > self.max_value:
                return m.group()
            return self.say(AN.to_words(int(total), Form(oblique=self.oblique(Around(m)))))
        return SCALED.sub(sub, text)

    def eras(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            if m.group("era") == "م" and len(m.group("y")) < 4:
                return m.group()
            return self.say(self.year(m.group("y"), m.group("era")))
        return ERA.sub(sub, text)

    def units(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            noun = next(noun for pattern, noun in W.UNITS if pattern.fullmatch(m.group("u")))
            whole, frac = split_number(m.group("n"))
            oblique = self.oblique(Around(m))
            if frac is not None:
                adjective = noun.plural_adjective or noun.adjective
                return self.say(f"{self.spoken(whole, frac, Form(oblique=oblique))} {noun.plural}" + (f" {adjective}" if adjective else "") + noun.tail)
            if int(whole) > self.max_value or (len(whole) > 1 and whole[0] == "0"):
                return m.group()
            return self.say(self.noun_phrase(int(whole), noun, oblique))
        return UNIT.sub(sub, text)

    def fraction_words(self, a: int, b: int, oblique: bool) -> str:
        if 2 <= b <= 10 and 1 <= a < b:
            singular, plural = W.FRACTIONS[b]
            if a == 1:
                return singular
            if a == 2:
                return W.dual(singular, oblique)
            return f"{AN.ONES[a]} {plural}"
        return f"{AN.to_words(a)} من {AN.to_words(b, Form(oblique=True))}"

    def fractions(self, text: str) -> str:
        def mixed(m: re.Match) -> str:
            a, b = int(m.group("a")), int(m.group("b"))
            if b == 0:
                return m.group()
            oblique = self.oblique(Around(m))
            return self.say(f"{AN.to_words(int(m.group('w')), Form(oblique=oblique))} {AN.AND}{self.fraction_words(a, b, oblique)}")

        def simple(m: re.Match) -> str:
            a, b = int(m.group("a")), int(m.group("b"))
            return m.group() if b == 0 else self.say(self.fraction_words(a, b, self.oblique(Around(m))))
        return FRACTION.sub(simple, MIXED_FRACTION.sub(mixed, text))

    def year_ranges(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            first, last = m.group("a"), m.group("b")
            last = last if len(last) == 4 else first[:2] + last
            if not (1000 <= int(first) < int(last) <= 2200):
                return m.group()
            before = Around(m).tokens_before(1)
            years = (self.year(first), self.year(last))
            if before == ["بين"]:
                return self.say(f"{years[0]} {AN.AND}{years[1]}")
            return self.say(f"{'' if before == ['من'] else 'من '}عام {years[0]} إلى عام {years[1]}")
        return YEAR_RANGE.sub(sub, text)

    def ranges(self, text: str) -> str:
        """"10-15 عاما" is "from ten to fifteen", gender by the noun after it; after "بين" it is "between ten and fifteen"."""
        def sub(m: re.Match) -> str:
            (first, first_frac), (last, last_frac) = split_number(m.group("a")), split_number(m.group("b"))
            if len(first) == 4 and first_frac is None and len(last) <= 2:      # "2020-10": a year and a month, not a range
                return m.group()
            around = Around(m)
            form = Form(feminine=around.next_word() in W.FEMININE_NOUNS, oblique=True)
            low, high = self.spoken(first, first_frac, form), self.spoken(last, last_frac, form)
            return self.say(f"{low} {AN.AND}{high}" if around.tokens_before(1) == ["بين"] else f"من {low} إلى {high}")
        return RANGE.sub(sub, text)

    def scores(self, text: str) -> str:
        return SCORE.sub(lambda m: self.say(f"{AN.to_words(int(m.group('a')))} مقابل {AN.to_words(int(m.group('b')))}"), text)

    def article_ordinals(self, text: str) -> str:
        """A number written after the article ("الـ13 من نوفمبر", "في الـ35 من عمره") is an ordinal; its gender is the noun's after it or the head's before."""
        def sub(m: re.Match) -> str:
            n = int(m.group("n"))
            if not 1 <= n <= 99:
                return m.group()
            around = Around(m)
            head = self.head_of(around)
            if head:
                return self.say(self.head_ordinal(n, head, around))
            return self.say(W.ordinal(n, self.ordinal_feminine(around), self.oblique(around)))
        return ARTICLE_ORDINAL.sub(sub, text)

    def roman(self, text: str) -> str:
        """Roman figures are an ordinal only after a head noun ("الحرب العالمية II") or a ruler's name, never alone (a letter, an initial)."""
        def sub(m: re.Match) -> str:
            value = W.roman_value(m.group("r"))
            if value is None or not 1 <= value <= 99:
                return m.group()
            around = Around(m)
            head = self.head_of(around)
            if head:
                return self.say(self.head_ordinal(value, head, around))
            ruler = around.tokens_before(1)
            if value >= 2 and ruler and ruler[0] in W.RULERS:
                return self.say(W.ordinal(value, W.RULERS[ruler[0]]))
            return m.group()
        return ROMAN.sub(sub, text)

    def digits_said_one_by_one(self, around: Around, whole: str) -> bool:
        """A code (leading zero), an emergency number named as one, or a long number labelled as a phone, identity or case number."""
        if len(whole) > 1 and whole[0] == "0":
            return True
        before = around.tokens_before(3)
        if len(whole) >= 4 and W.IDENTITY_TRIGGERS & set(before):
            return True
        mode = self.opts.emergency_numbers
        if mode == "number_always" or len(whole) != 3:
            return False
        if mode == "digits_always":
            return whole in W.EMERGENCY_NUMBERS
        labelled = bool(before) and before[-1] in W.NUMBER_LABELS and whole in W.EMERGENCY_NUMBERS
        listed = len(before) >= 2 and before[-1] in W.CONJUNCTIONS and before[-2] in W.EMERGENCY_NUMBERS      # "997 أو 998": the second number of a list of codes
        named = bool(before) and (before[-1] in W.EMERGENCY_SERVICES or bool(W.EMERGENCY_VERBS & set(before)) or listed)
        return labelled or (named and around.next_word() not in W.NOUNS)

    def plain(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            around = Around(m)
            whole, frac = split_number(m.group("n"))
            if frac is None:
                if self.digits_said_one_by_one(around, whole):
                    return self.say(AN.digit_by_digit(whole))
                head = self.head_of(around)
                if head and 1 <= int(whole) <= 99:
                    return self.say(self.head_ordinal(int(whole), head, around))
            form = Form(feminine=around.next_word() in W.FEMININE_NOUNS, oblique=self.oblique(around))
            return self.say(("سالب " if m.group("neg") else "") + self.spoken(whole, frac, form))
        return NUMBER.sub(sub, text)

    def leftovers(self, text: str) -> str:
        """Any digits no rule understood ("1.2.3") are said as numbers, never left for the voice to read one by one."""
        return LEFTOVER.sub(lambda m: self.say(AN.read(m.group(), self.max_value)), text)
