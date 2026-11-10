"""Persian number words for the voice: the cardinal engine, ordinals, the spoken (colloquial) forms and the tables of months, units and currencies.
Rules and sources: docs/spoken_numbers_fa.md."""

from __future__ import annotations

ONES = ["صفر", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه", "ده", "یازده", "دوازده", "سیزده", "چهارده", "پانزده", "شانزده", "هفده",
        "هجده", "نوزده"]
TENS = ["", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"]
HUNDREDS = ["", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد", "هشتصد", "نهصد"]
SCALES = [(10**9, "میلیارد"), (10**6, "میلیون"), (10**3, "هزار")]
NINE = "نه"
# Written forms a narrator says differently in everyday speech (the Academy's forms stay the default; see Options.speech_style).
SPOKEN = {"پانزده": "پونزده", "شانزده": "شونزده", "پانصد": "پونصد", "چهارصد": "چارصد", "ششصد": "شیشصد", "چهل": "چل", "شش": "شیش", "چهار": "چار"}
ORDINAL_STEMS = {"یک": "یکم", "سه": "سوم"}

SOLAR_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
GREGORIAN_MONTHS = {
    "farhangestan": ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"],
    "common": ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "جولای", "آگوست", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"]}
# Names a month shares with an ordinary noun (an arrow, a seal, fog): "۲ تیر" is a date only with a year or "ماه" after it.
AMBIGUOUS_MONTHS = {"تیر", "مهر", "دی", "مه", "می"}
MONTH_NAMES = sorted({*SOLAR_MONTHS, *GREGORIAN_MONTHS["farhangestan"], *GREGORIAN_MONTHS["common"], "می"}, key=len, reverse=True)

ERAS = {"هـ.ق": "هجری قمری", "ه.ق": "هجری قمری", "هـ.ش": "هجری شمسی", "ه.ش": "هجری شمسی", "هـ": "هجری قمری", "م": "میلادی"}
UNITS = {
    "km/h": "کیلومتر بر ساعت", "km²": "کیلومتر مربع", "m²": "متر مربع", "m³": "متر مکعب", "km": "کیلومتر", "cm": "سانتی‌متر", "mm": "میلی‌متر",
    "kg": "کیلوگرم", "mg": "میلی‌گرم", "ml": "میلی‌لیتر", "ha": "هکتار", "°C": "درجه سانتی‌گراد", "°": "درجه", "m": "متر", "g": "گرم", "l": "لیتر",
    "L": "لیتر", "s": "ثانیه"}
CURRENCY_SYMBOLS = {"$": ("دلار", "سنت"), "€": ("یورو", "سنت"), "£": ("پوند", "پنس"), "﷼": ("ریال", "")}
FRACTION_NAMES = {2: "دوم", 3: "سوم", 4: "چهارم", 5: "پنجم", 6: "ششم", 7: "هفتم", 8: "هشتم", 9: "نهم", 10: "دهم"}


class Words:
    """The cardinals, ordinals and digit words of Persian, with the one word for nine and the register chosen by the options."""

    def __init__(self, nine: str, spoken: bool, max_value: int):
        self.nine, self.spoken, self.max_value = nine, spoken, max_value

    def refine(self, words: list[str]) -> list[str]:
        words = [self.nine if w == NINE else w for w in words]
        return [SPOKEN.get(w, w) for w in words] if self.spoken else words

    @staticmethod
    def below_thousand(n: int) -> list[str]:
        parts: list[str] = []
        if n >= 100:
            parts.append(HUNDREDS[n // 100])
            n %= 100
        if n >= 20:
            parts.append(TENS[n // 10])
            n %= 10
        if n:
            parts.append(ONES[n])
        return parts

    def phrases(self, n: int) -> list[list[str]]:
        """The number as the phrases the word "و" joins: 2026 is [دو هزار] [بیست] [شش]."""
        if n < 1000:
            return [[word] for word in self.below_thousand(n)] or [[ONES[0]]]
        out: list[list[str]] = []
        for size, name in SCALES:
            if n >= size:
                count, n = divmod(n, size)
                out.append([name] if size == 1000 and count == 1 else [*self.words(count), name])
        return [*out, *([[word] for word in self.below_thousand(n)])]

    def words(self, n: int) -> list[str]:
        """The cardinal as a list of words, in the register of the options; a value over the maximum is read digit by digit."""
        if n > self.max_value:
            return self.digits(str(n))
        out: list[str] = []
        for phrase in self.phrases(n):
            out += (["و"] if out else []) + phrase
        return self.refine(out)

    def digits(self, text: str) -> list[str]:
        return self.refine([ONES[int(d)] for d in text if d.isdigit()])

    def ordinal(self, n: int, first: str = "اول") -> list[str]:
        """3 -> سوم, 30 -> سی‌ام, 21 -> بیست و یکم, 1 -> first ("اول", or "یکم" as the Academy's list has it)."""
        words = self.words(n)
        last = words[-1]
        if n == 1:
            return [first]
        if last == self.nine:
            words[-1] = NINE + "م"
        elif last in ORDINAL_STEMS:
            words[-1] = ORDINAL_STEMS[last]
        else:
            words[-1] = last + ("‌ام" if last.endswith("ی") else "م")
        return words

    def ordinal_with_in(self, n: int) -> list[str]:
        """The "-مین" form: اولین, سومین, سی‌امین, پنجاهمین."""
        if n == 1:
            return ["اولین"]
        words = self.ordinal(n)
        words[-1] += "ین"
        return words
