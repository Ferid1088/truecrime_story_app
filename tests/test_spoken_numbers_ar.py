"""The Arabic number reader (spoken_numbers/ar.py): the words a narrator says for the digits of a text. The words are plain Modern Standard Arabic (no vowel marks);
the rules and the sources behind the expected strings are in docs/spoken_numbers_ar.md. Words of the text keep their own spelling; only numbers are rewritten."""

import re

import pytest

from app.documentary.spoken_numbers.ar import Numbers, Options

BIG = 10**15 - 1


def say(text: str, **options) -> str:
    return Numbers(options, BIG).text(text)


@pytest.mark.parametrize("text, expected", [
    ("0", "صفر"), ("7", "سبعة"), ("11", "أحد عشر"), ("12", "اثنا عشر"), ("21", "واحد وعشرون"), ("99", "تسعة وتسعون"),
    ("100", "مئة"), ("200", "مئتان"), ("2026", "ألفان وستة وعشرون"), ("102000", "مئة ألف وألفان"),
    ("911 شخصا", "تسعمئة وأحد عشر شخصا"), ("007", "صفر صفر سبعة"),
])
def test_a_plain_number_is_a_nominative_masculine_cardinal(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("في عام 2007", "في عام ألفين وسبعة"),
    ("عام 1999", "عام ألف وتسعمئة وتسعة وتسعين"),
    ("منذ عام 2000", "منذ عام ألفين"),
    ("عام 2100", "عام ألفين ومئة"),
    ("في فبراير 2026، وصل", "في فبراير ألفين وستة وعشرين، وصل"),
    ("بلغ 2000 دولار", "بلغ ألفين دولار"),
    ("قال إن 2026 كان", "قال إن ألفين وستة وعشرين كان"),
    ("وصل 20 شخصا", "وصل عشرون شخصا"),
])
def test_the_case_of_a_number_follows_the_word_before_it(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("عمره 35 عاماً", "عمره خمسة وثلاثين عاماً"),
    ("عمرها 35 سنة", "عمرها خمس وثلاثين سنة"),
    ("بالسجن 15 سنة سجناً", "بالسجن خمس عشرة سنة سجناً"),
    ("بالسجن 15 عاماً سجناً", "بالسجن خمسة عشر عاماً سجناً"),
    ("قتلت 11 امرأة", "قتلت إحدى عشرة امرأة"),
    ("قتل 11 رجلاً", "قتل أحد عشر رجلاً"),
    ("12 فتاة", "اثنتا عشرة فتاة"),
    ("في 22 ضحية", "في اثنتين وعشرين ضحية"),
    ("في 22 شخصاً", "في اثنين وعشرين شخصاً"),
    ("أطلق 20 رصاصة", "أطلق عشرين رصاصة"),
    ("100 رجل", "مئة رجل"),
    ("3 ضحايا", "ثلاث ضحايا"), ("3 رجال", "ثلاثة رجال"), ("7 سنوات", "سبع سنوات"), ("3 ساعات", "ثلاث ساعات"),
    ("13 ليلة", "ثلاث عشرة ليلة"), ("13 يوماً", "ثلاثة عشر يوماً"),
    ("حُكِمَ عليه بـ 7 سنوات", "حُكِمَ عليه بـ سبع سنوات"),
])
def test_a_number_takes_the_gender_of_the_noun_it_counts(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("في 4 أغسطس 1892", "في الرابع من أغسطس عام ألف وثمانمئة واثنين وتسعين"),
    ("في 13 نوفمبر 2007", "في الثالث عشر من نوفمبر عام ألفين وسبعة"),
    ("1 مايو 2020", "الأول من مايو عام ألفين وعشرين"),
    ("13/11/2007", "الثالث عشر من نوفمبر عام ألفين وسبعة"),
    ("2007-11-13", "الثالث عشر من نوفمبر عام ألفين وسبعة"),
    ("13 نوفمبر 2007م", "الثالث عشر من نوفمبر عام ألفين وسبعة للميلاد"),
    ("1445هـ", "ألف وأربعمئة وخمسة وأربعين للهجرة"),
    ("2007م", "ألفين وسبعة للميلاد"),
    ("1990-1995", "من عام ألف وتسعمئة وتسعين إلى عام ألف وتسعمئة وخمسة وتسعين"),
    ("1990-95", "من عام ألف وتسعمئة وتسعين إلى عام ألف وتسعمئة وخمسة وتسعين"),
    ("بين 1990 و1995", "بين ألف وتسعمئة وتسعين وألف وتسعمئة وخمسة وتسعين"),
])
def test_dates_and_years(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("في القرن 20", "في القرن العشرين"), ("القرن 21", "القرن الحادي والعشرين"), ("الحرب العالمية II", "الحرب العالمية الثانية"),
    ("الفصل 3", "الفصل الثالث"), ("الصفحة 5", "الصفحة الخامسة"), ("المادة 12", "المادة الثانية عشرة"),
    ("لويس XIV", "لويس الرابع عشر"), ("يوحنا بولس II", "يوحنا بولس الثاني"), ("إليزابيث II", "إليزابيث الثانية"),
    ("في الـ35 من عمره", "في الخامسة والثلاثين من عمره"), ("في سن 35", "في سن الخامسة والثلاثين"),
    ("في الـ13 من أغسطس", "في الثالث عشر من أغسطس"), ("رقم 5", "رقم خمسة"), ("الطابق 3", "الطابق الثالث"),
])
def test_ordinals_after_the_article_or_a_head_noun(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("$5", "خمسة دولارات"), ("$11", "أحد عشر دولارا"), ("$100", "مئة دولار"), ("$1,000,000", "مليون دولار"),
    ("5 USD", "خمسة دولارات"), ("$2", "دولاران"), ("$1", "دولار واحد"), ("50 ر.س", "خمسون ريالا"),
    ("15 ج.م", "خمسة عشر جنيها مصري"), ("$5.50", "خمسة دولارات وخمسون سنتا"), ("€3.5 مليون", "ثلاثة ملايين وخمسمئة ألف يورو"),
    ("2.5 مليون", "مليونان وخمسمئة ألف"), ("5 ملايين دولار", "خمسة ملايين دولار"),
])
def test_money(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("رقم الطوارئ 911", "رقم الطوارئ تسعة واحد واحد"), ("الإسعاف 997", "الإسعاف تسعة تسعة سبعة"),
    ("اتصل بالشرطة على 122", "اتصل بالشرطة على واحد اثنان اثنان"), ("اتصل بـ 911", "اتصل بـ تسعة واحد واحد"),
    ("على الرقم 997", "على الرقم تسعة تسعة سبعة"), ("وصل اتصال إلى 911", "وصل اتصال إلى تسعمئة وأحد عشر"),
    ("+966 50 123 4567", "زائد تسعة ستة ستة ، خمسة صفر ، واحد اثنان ثلاثة ، أربعة خمسة ستة سبعة"),
    ("هاتفه 0501234567", "هاتفه صفر خمسة صفر واحد اثنان ثلاثة أربعة خمسة ستة سبعة"),
    ("رقم القضية 1234", "رقم القضية واحد اثنان ثلاثة أربعة"),
])
def test_emergency_phone_and_identity_numbers_are_said_digit_by_digit_when_named_as_such(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("الساعة 10:30", "الساعة العاشرة والنصف"), ("23:45", "الثانية عشرة إلا ربعا مساء"), ("3:00 صباحاً", "الثالثة صباحاً"),
    ("الساعة 1:15", "الساعة الواحدة والربع"), ("الساعة 12:00 ظهراً", "الساعة الثانية عشرة ظهراً"),
    ("الساعة 3 صباحاً", "الساعة الثالثة صباحاً"), ("الساعة 9:05", "الساعة التاسعة وخمس دقائق"),
    ("الساعة 9:50", "الساعة العاشرة إلا عشر دقائق"), ("في الساعة 4:27", "في الساعة الرابعة وسبع وعشرين دقيقة"), ("الساعة 4:27", "الساعة الرابعة وسبع وعشرون دقيقة"),
    ("15:30", "الثالثة والنصف بعد الظهر"),
])
def test_clock_times(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("3.5", "ثلاثة فاصلة خمسة"), ("3.25", "ثلاثة فاصلة خمسة وعشرون"), ("0.05", "صفر فاصلة صفر خمسة"),
    ("3.141", "ثلاثة فاصلة واحد أربعة واحد"), ("٣٫٥", "ثلاثة فاصلة خمسة"), ("۳٫۵", "ثلاثة فاصلة خمسة"),
    ("٢٠٢٦", "ألفان وستة وعشرون"), ("۲۰۲۶", "ألفان وستة وعشرون"), ("3,5", "ثلاثة فاصلة خمسة"),
    ("١٬٢٣٤", "ألف ومئتان وأربعة وثلاثون"), ("1,234", "ألف ومئتان وأربعة وثلاثون"),
    ("1,234,567", "مليون ومئتان وأربعة وثلاثون ألف وخمسمئة وسبعة وستون"), ("1,234.5", "ألف ومئتان وأربعة وثلاثون فاصلة خمسة"),
    ("-5", "سالب خمسة"), ("3.5%", "ثلاثة فاصلة خمسة بالمئة"), ("بنسبة 20%", "بنسبة عشرين بالمئة"), ("٢٠٪", "عشرون بالمئة"),
    ("100%", "مئة بالمئة"),
])
def test_decimals_separators_digit_systems_and_percent(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("1/2", "نصف"), ("1/3", "ثلث"), ("3/4", "ثلاثة أرباع"), ("2/3", "ثلثان"), ("1 1/2", "واحد ونصف"), ("5/12", "خمسة من اثني عشر"),
    ("10-15 عاماً", "من عشرة إلى خمسة عشر عاماً"), ("10-15 سنة", "من عشر إلى خمس عشرة سنة"), ("بين 10-15 عاماً", "بين عشرة وخمسة عشر عاماً"),
    ("3:1", "ثلاثة مقابل واحد"),
])
def test_fractions_ranges_and_scores(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("9 ملم", "تسعة مليمترات"), ("25°C", "خمس وعشرون درجة مئوية"), ("15 كم", "خمسة عشر كيلومترا"), ("100 كم/س", "مئة كيلومتر في الساعة"),
    ("3 كم", "ثلاثة كيلومترات"), ("2 كغ", "كيلوجرامان"), ("عيار 38", "عيار ثمانية وثلاثين"), ("مسدس عيار 9 ملم", "مسدس عيار تسعة مليمترات"),
])
def test_units_and_calibres(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text", ["لا أرقام هنا", "A1", "3D و 5G", "COVID-19", "F-16", "MP5 و B2B", "https://example.com/page2", "mail2me@example.com", ""])
def test_text_that_is_not_a_number_stays_as_written(text):
    assert say(text) == text


def test_a_protected_span_is_put_back_between_numbers():
    assert say("A1 و 5 و COVID-19") == "A1 و خمسة و COVID-19"


def test_the_direction_marks_and_the_kashida_before_digits_are_dropped():
    assert say("‏2026‏") == "ألفان وستة وعشرون"
    assert say("في الـ13 من أغسطس") == "في الثالث عشر من أغسطس"


def test_digits_no_rule_understands_are_still_said_as_numbers():
    assert say("1.2.3") == "واحد.اثنان.ثلاثة"


@pytest.mark.parametrize("text, expected", [
    ("9-1-1", "تسعة واحد واحد"), ("اتصل على 9-1-1 فورا", "اتصل على تسعة واحد واحد فورا"),
    ("استمعت الهيئة إلى مكالمة 911", "استمعت الهيئة إلى مكالمة تسعة واحد واحد"), ("اتصل بـ 997 أو 998", "اتصل بـ تسعة تسعة سبعة أو تسعة تسعة ثمانية"),
    ("خط الطوارئ 104", "خط الطوارئ واحد صفر أربعة"), ("اتصل بالرقم 19", "اتصل بالرقم تسعة عشر"),
])
def test_codes_and_emergency_numbers_of_the_calls_people_make(text, expected):
    assert say(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("5 نقاط مئوية", "خمس نقاط مئوية"), ("7 جلسات", "سبع جلسات"), ("3 تهم", "ثلاث تهم"), ("9 مدن", "تسع مدن"), ("4 حالات", "أربع حالات"),
    ("3 تريليون دولار", "ثلاثة تريليونات دولار"), ("2 تريليون", "تريليونان"), ("1000000000000", "تريليون"),
])
def test_feminine_nouns_of_the_news_and_the_trillion(text, expected):
    assert say(text) == expected


def test_the_words_of_a_number_carry_no_vowel_marks_and_the_marks_of_the_story_are_kept():
    assert say("في 13 نوفمبر 2007 قَالَ") == "في الثالث عشر من نوفمبر عام ألفين وسبعة قَالَ"


def test_a_number_above_the_limit_is_said_digit_by_digit():
    assert Numbers({}, 99).text("123") == "واحد اثنان ثلاثة"


@pytest.mark.parametrize("options, text, expected", [
    ({"emergency_numbers": "number_always"}, "اتصل بـ 911", "اتصل بـ تسعمئة وأحد عشر"),
    ({"emergency_numbers": "digits_always"}, "وصل 911", "وصل تسعة واحد واحد"),
    ({"month_names": "levant"}, "13/11/2007", "الثالث عشر من تشرين الثاني عام ألفين وسبعة"),
    ({"percent_word": "في المئة"}, "20%", "عشرون في المئة"),
    ({"pound_symbol": "egyptian"}, "£5", "خمسة جنيهات مصرية"),
    ({"pound_symbol": "sterling"}, "£5", "خمسة جنيهات إسترلينية"),
    ({"case_from_context": False}, "في عام 2007", "في عام ألفان وسبعة"),
    ({"day_parts": False}, "23:45", "الثانية عشرة إلا ربعا"),
    ({"ordinal_dates": False}, "13/11/2007", "ثلاثة عشر من نوفمبر عام ألفين وسبعة"),
    ({"currency_subunits": False}, "$5.50", "خمسة فاصلة خمسون دولارات"),
])
def test_every_option_flips_its_rule(options, text, expected):
    assert say(text, **options) == expected


def test_the_defaults_of_the_options():
    assert Options().model_dump() == {"emergency_numbers": "digits_in_context", "month_names": "gulf_egypt", "percent_word": "بالمئة", "pound_symbol": "sterling",
                                      "case_from_context": True, "day_parts": True, "ordinal_dates": True, "currency_subunits": True}


def test_an_unknown_option_value_is_refused():
    with pytest.raises(ValueError):
        Numbers({"emergency_numbers": "sometimes"}, BIG)


def test_the_output_never_keeps_digits_of_a_converted_number():
    text = "في 13/11/2007 الساعة 23:45 دفع $5.50 و 3.5% من 1,234 و 25°C و 1/2 و +966 50 123 4567 و 10-15 عاماً و 1990-1995 و الـ35 و 007"
    assert not any(c.isdigit() for c in say(text))


SAMPLES = ["في 13 نوفمبر 2007 قال إن 3.5% من 1,234 شخصا", "الساعة 23:45 دفع $5.50 و 25°C", "اتصل بـ 911 و +966 50 123 4567", "عمره 35 عاماً في القرن 20",
           "1990-1995 و 1/2 و 10-15 عاما", "A1 و COVID-19 و 5G و https://example.com/page2", "لا أرقام هنا", ""]


@pytest.mark.parametrize("text", ["لا أرقام هنا، ولا فواصل: «نص» بلا أرقام.", "قَالَ الشَّاهِدُ كَلِمَةً", "", "A1 و COVID-19 و https://example.com/page2"])
def test_a_text_without_a_number_to_say_comes_out_as_it_went_in(text):
    assert say(text) == text


@pytest.mark.parametrize("text", SAMPLES)
def test_reading_the_output_again_changes_nothing(text):
    once = say(text)
    assert say(once) == once


@pytest.mark.parametrize("text", SAMPLES[:6])
def test_the_output_has_no_vowel_marks_on_the_words_it_says(text):
    assert not re.search("[ً-ٰٟ]", re.sub("عاماً", "", say(text)))
