"""The Arabic word tables of the number reader (ar.py): ordinals, clock words, months, counted nouns (currencies, units) and the closed lexicons that tell a
number its context (the gender of the noun it counts, the heads that take an ordinal, the words that open an emergency number). Every lexicon key is compared
as `norm()` writes it: no vowel marks, no kashida, one form of alef, ya and ta marbuta, so a word the text writes with a tanween ("عاماً") still matches.
The words said for a number are plain, as arabic_numbers.py writes them: no vowel marks, no case endings (the counted noun after 11 to 99 takes an alef for its
accusative: "دولارا")."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.documentary import arabic_numbers as AN

EDGES = ".,;:!?؟،؛«»\"'()[]{}…-–—"
FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
MARKS = "ً-ْٰ"
VOWEL_MARKS = re.compile(f"[{MARKS}]")


def norm(word: str) -> str:
    return VOWEL_MARKS.sub("", word).replace("ـ", "").strip(EDGES).translate(FOLD)


def keys(words: str) -> frozenset[str]:
    return frozenset(norm(w) for w in words.split())


def loose(phrase: str) -> str:
    """A regex for the phrase that matches it with any vowel marks and any form of alef."""
    parts = []
    for ch in phrase:
        if ch == " ":
            parts.append(r"\s+")
        elif ch in "اأإآ":
            parts.append(f"[اأإآ][{MARKS}]*")
        else:
            parts.append(re.escape(ch) + f"[{MARKS}]*")
    return "".join(parts)


# --- gender of the counted noun: by the SINGULAR (a plural is listed with its singular's gender); a word not listed is masculine -----------------------------
FEMININE_NOUNS = keys(
    "سنة سنوات سنين ساعة ساعات دقيقة دقائق ثانية ثوان ثواني ليلة ليال ليالي مرة مرات رصاصة رصاصات طلقة طلقات طعنة طعنات ضربة ضربات امرأة نساء فتاة فتيات "
    "سيدة سيدات بنت بنات طفلة طفلات ضحية ضحايا جثة جثث سيارة سيارات غرفة غرف صفحة صفحات رسالة رسائل مكالمة مكالمات قطعة قطع جريمة جرائم قضية قضايا لقطة "
    "لقطات بصمة بصمات دولة دول ليرة ليرات هللة هللات يد أيد عين عيون محاولة محاولات درجة درجات وثيقة وثائق صورة صور")
MASCULINE_NOUNS = keys(
    "عام أعوام يوم أيام شهر أشهر شهور أسبوع أسابيع رجل رجال شخص أشخاص طفل أطفال ولد أولاد متهم متهمون شاهد شهود جندي جنود شرطي ضابط ضباط قتيل قتلى جريح "
    "جرحى دولار دولارات دينار دنانير ريال ريالات درهم دراهم جنيه جنيهات يورو قرش قروش فلس سنت سنتات كيلومتر كيلومترات متر أمتار طن أطنان لتر لترات رصاص منزل "
    "بيت بيوت مبنى مبان")
NOUNS = FEMININE_NOUNS | MASCULINE_NOUNS

# --- the case of a number: oblique unless it opens a sentence or follows one of these subject-taking words ------------------------------------------------
NOMINATIVE_AFTER = keys("أصيب أصيبت وصل وصلت حضر حضرت توفي توفيت مات ماتت هناك يوجد وجد")
SENTENCE_END = ".!?؟:؛;\n«"

# --- heads whose digit is an ordinal ("القرن 20" is the twentieth century), with their gender; two-word heads are written with one space -------------------
ORDINAL_HEADS = {norm(k): feminine for k, feminine in {
    "القرن": False, "الفصل": False, "الباب": False, "الجزء": False, "المجلد": False, "الموسم": False, "الطابق": False, "القسم": False, "الكتاب": False,
    "الحلقة": True, "الصفحة": True, "صفحة": True, "المادة": True, "الدورة": True, "الجولة": True, "الألفية": True, "المرحلة": True, "الفقرة": True,
    "سن": True, "الحرب العالمية": True}.items()}
ORDINAL_FIXED_OBLIQUE = keys("القرن الألفية")      # "القرن العشرين" is said in the genitive wherever it stands
# Names that take a regnal or papal number in Roman figures ("لويس XIV"), with their gender.
RULERS = {norm(k): feminine for k, feminine in {
    "لويس": False, "لوي": False, "بولس": False, "يوحنا": False, "بنديكتوس": False, "فرنسيس": False, "هنري": False, "إدوارد": False, "جورج": False,
    "شارل": False, "شارلز": False, "فيليب": False, "فريدريك": False, "نابليون": False, "رمسيس": False, "ألكسندر": False, "نيقولا": False,
    "ويليام": False, "ريتشارد": False, "إليزابيث": True, "فيكتوريا": True, "كاترين": True, "ماري": True, "إيزابيلا": True}.items()}

# --- emergency and identification numbers ----------------------------------------------------------------------------------------------------------------------
EMERGENCY_NUMBERS = frozenset({"911", "999", "997", "998", "993", "933", "112", "122", "123", "180", "190", "191", "197", "140", "125"})
EMERGENCY_SERVICES = keys("الطوارئ طوارئ النجدة الإسعاف الشرطة الإطفاء المدني ساخن الساخن")      # right before the number: "خط الطوارئ 122"
EMERGENCY_VERBS = keys("اتصل اتصلت اتصلوا اتصلي")                                                  # within three words before it: "اتصل بـ 911"
NUMBER_LABELS = keys("رقم الرقم")
IDENTITY_TRIGGERS = keys("هاتف الهاتف جوال الجوال واتساب الهوية جواز السفر اللوحة القضية الحساب الملف المحضر")
DAY_PART_WORDS = keys("صباحا مساء ظهرا ليلا فجرا عصرا بعد قبل ص م")

# --- months: all that people write; the names the app says for a numeric date ------------------------------------------------------------
WRITTEN_MONTHS = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر", "كانون الثاني", "شباط", "آذار", "نيسان",
    "أيار", "حزيران", "تموز", "آب", "أيلول", "تشرين الأول", "تشرين الثاني", "كانون الأول", "نونبر", "دجنبر", "يوليوز", "غشت", "شتنبر", "جانفي", "فيفري",
    "أفريل", "ماي", "جوان", "جويلية", "أوت", "محرم", "صفر", "ربيع الأول", "ربيع الآخر", "جمادى الأولى", "جمادى الآخرة", "رجب", "شعبان", "رمضان", "شوال",
    "ذو القعدة", "ذو الحجة"]
MONTH_NAMES = {
    "gulf_egypt": ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"],
    "levant": ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران", "تموز", "آب", "أيلول", "تشرين الأول", "تشرين الثاني",
               "كانون الأول"]}


MONTH_PATTERN = "|".join(loose(name) for name in sorted(WRITTEN_MONTHS, key=len, reverse=True))


# --- ordinals and the clock ------------------------------------------------------------------------------------------------------------------------------------
ORDINAL_FIRST = ("الأول", "الأولى")
ORDINALS = ("", "الحادي", "الثاني", "الثالث", "الرابع", "الخامس", "السادس", "السابع", "الثامن", "التاسع", "العاشر")
ORDINAL_TENS = ["", "", "العشرون", "الثلاثون", "الأربعون", "الخمسون", "الستون", "السبعون", "الثمانون", "التسعون"]
ORDINAL_TENS_OBLIQUE = ["", "", "العشرين", "الثلاثين", "الأربعين", "الخمسين", "الستين", "السبعين", "الثمانين", "التسعين"]
HOUR_ONE = "الواحدة"


def ordinal(n: int, feminine: bool = False, oblique: bool = False) -> str:
    """The ordinal of 1 to 99 ("الثالث عشر", "الحادي والعشرين"); a feminine one ends in ة and agrees on both parts of 11 to 19."""
    ending = "ة" if feminine else ""
    if n == 1:
        return ORDINAL_FIRST[feminine]
    if n <= 10:
        return ORDINALS[n] + ending
    if n < 20:
        return f"{ORDINALS[n - 10]}{ending} {'عشرة' if feminine else 'عشر'}"
    tens, one = divmod(n, 10)
    tens_word = (ORDINAL_TENS_OBLIQUE if oblique else ORDINAL_TENS)[tens]
    return tens_word if not one else f"{ORDINALS[one]}{ending} {AN.AND}{tens_word}"


def hour(h12: int) -> str:
    return HOUR_ONE if h12 == 1 else ordinal(h12, feminine=True)


def day_part(h24: int) -> str:
    if h24 < 4:
        return "ليلا"
    if h24 < 12:
        return "صباحا"
    if h24 < 13:
        return "ظهرا"
    if h24 < 16:
        return "بعد الظهر"
    return "مساء"


# --- counted nouns: currencies and units -----------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Noun:
    """A noun that follows a number. `adjective` agrees with it (fixed feminine after a plural of three to ten); `tail` is invariant words after it."""
    singular: str
    plural: str
    feminine: bool = False
    adjective: str = ""
    plural_adjective: str = ""
    tail: str = ""


def dual(word: str, oblique: bool) -> str:
    ending = "ين" if oblique else "ان"
    return word[:-1] + "ت" + ending if word.endswith("ة") else word + ending


def accusative(word: str) -> str:
    return word if word.endswith("ة") else word + "ا"


DOLLAR, EURO = Noun("دولار", "دولارات"), Noun("يورو", "يورو")
CENT = Noun("سنت", "سنتات")
CURRENCIES = {
    "usd": (DOLLAR, CENT), "eur": (EURO, CENT),
    "gbp": (Noun("جنيه", "جنيهات", adjective="إسترليني", plural_adjective="إسترلينية"), None),
    "egp": (Noun("جنيه", "جنيهات", adjective="مصري", plural_adjective="مصرية"), Noun("قرش", "قروش")),
    "sar": (Noun("ريال", "ريالات"), Noun("هللة", "هللات", feminine=True)),
    "aed": (Noun("درهم", "دراهم"), Noun("فلس", "فلوس")),
    "kwd": (Noun("دينار", "دنانير"), Noun("فلس", "فلوس")),
    "jod": (Noun("دينار", "دنانير"), None), "qar": (Noun("ريال", "ريالات"), None),
    "bhd": (Noun("دينار", "دنانير"), None), "omr": (Noun("ريال", "ريالات"), None)}
SYMBOLS = {"$": "usd", "€": "eur", "usd": "usd", "eur": "eur", "gbp": "gbp", "sar": "sar", "aed": "aed", "egp": "egp", "kwd": "kwd", "jod": "jod", "qar": "qar",
           "bhd": "bhd", "omr": "omr", "ر.س": "sar", "د.إ": "aed", "ج.م": "egp", "د.ك": "kwd", "د.أ": "jod", "ر.ق": "qar", "د.ب": "bhd", "ر.ع": "omr"}
SYMBOL_PATTERN = (r"[$€£]|\b(?:USD|EUR|GBP|SAR|AED|EGP|KWD|JOD|QAR|BHD|OMR)\b|ر\.\s?س|د\.\s?إ|ج\.\s?م|د\.\s?ك|د\.\s?أ|ر\.\s?ق|د\.\s?ب|ر\.\s?ع")

DEGREE = Noun("درجة", "درجات", feminine=True, adjective="مئوية", plural_adjective="مئوية")
UNITS = [
    (re.compile(r"km/h|كم/س(?:اعة)?"), Noun("كيلومتر", "كيلومترات", tail=" في الساعة")),
    (re.compile(r"km|كم"), Noun("كيلومتر", "كيلومترات")),
    (re.compile(r"kg|كغ|كجم|كلغ"), Noun("كيلوجرام", "كيلوجرامات")),
    (re.compile(r"cm|سم"), Noun("سنتيمتر", "سنتيمترات")),
    (re.compile(r"mm|ملم"), Noun("مليمتر", "مليمترات")),
    (re.compile(r"°\s?C|°\s?م"), DEGREE),
    (re.compile(r"°"), Noun("درجة", "درجات", feminine=True)),
    (re.compile(r"m|م"), Noun("متر", "أمتار"))]
UNIT_PATTERN = "|".join(p.pattern for p, _ in UNITS)
MINUTE = Noun("دقيقة", "دقائق", feminine=True)

# --- fractions: 1/2 .. 1/10 -----------------------------------------------------------------------------------------------------------------------------------
FRACTIONS = {2: ("نصف", ""), 3: ("ثلث", "أثلاث"), 4: ("ربع", "أرباع"), 5: ("خمس", "أخماس"), 6: ("سدس", "أسداس"), 7: ("سبع", "أسباع"),
             8: ("ثمن", "أثمان"), 9: ("تسع", "أتساع"), 10: ("عشر", "أعشار")}
SCALE_WORDS = {norm("مليون"): 10**6, norm("ملايين"): 10**6, norm("مليار"): 10**9, norm("مليارات"): 10**9, norm("ترليون"): 10**12,
               norm("ترليونات"): 10**12, norm("ألف"): 10**3, norm("آلاف"): 10**3}
SCALE_PATTERN = "|".join(loose(w) for w in ("مليون", "ملايين", "مليار", "مليارات", "ترليون", "ترليونات", "ألف", "آلاف"))

ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
ROMAN_FORM = re.compile(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})")


def roman_value(figures: str) -> int | None:
    if not ROMAN_FORM.fullmatch(figures):
        return None
    total = 0
    for ch, nxt in zip(figures, figures[1:] + " ", strict=True):
        total += -ROMAN[ch] if nxt != " " and ROMAN[ch] < ROMAN[nxt] else ROMAN[ch]
    return total
