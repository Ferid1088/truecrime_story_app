"""English number reader (app/documentary/spoken_numbers/en.py): the rules of docs/spoken_numbers_en.md that are not marked UNVERIFIED, the protected spans, options,
and text that must stay as it is."""

from __future__ import annotations

import re

import pytest

from app.documentary.spoken_numbers import en_words
from app.documentary.spoken_numbers.en import Numbers


@pytest.fixture
def numbers() -> Numbers:
    return Numbers({}, 10 ** 18)


CASES = [
    # cardinals, American "and"-less
    ("21", "twenty-one"), ("100", "one hundred"), ("101", "one hundred one"), ("120", "one hundred twenty"), ("1,000", "one thousand"),
    ("2,500", "two thousand five hundred"), ("1,000,000", "one million"), ("1,234,567", "one million two hundred thirty-four thousand five hundred sixty-seven"),
    ("3 billion", "three billion"), ("50k", "fifty thousand"), ("2.5k", "two point five thousand"), ("1500 people", "one thousand five hundred people"),
    ("about 1900 men", "about one thousand nine hundred men"), ("1,900 dollars", "one thousand nine hundred dollars"),
    # years
    ("in 1999", "in nineteen ninety-nine"), ("In 2007", "In two thousand seven"), ("2010", "twenty ten"), ("since 2024", "since twenty twenty-four"),
    ("in 1905", "in nineteen oh five"), ("in 1900", "in nineteen hundred"), ("in 2000", "in two thousand"), ("in 1066", "in ten sixty-six"),
    ("March 2007", "March two thousand seven"), ("in 1101", "in eleven oh one"), ("1990-1995", "nineteen ninety to nineteen ninety-five"),
    ("1990-95", "nineteen ninety to nineteen ninety-five"), ("the 1990s", "the nineteen nineties"), ("the 1900s", "the nineteen hundreds"),
    ("the 2000s", "the two thousands"), ("the 2010s", "the twenty tens"), ("in the '90s", "in the nineties"), ("in her 30s", "in her thirties"),
    ("the 1960's", "the nineteen sixties"),
    # ordinals and dates
    ("1st degree murder", "first degree murder"), ("the 21st century", "the twenty-first century"), ("the 3rd floor", "the third floor"),
    ("March 3rd", "March third"), ("March 3", "March third"), ("March 3, 2024", "March third, twenty twenty-four"), ("March 3-5", "March third to fifth"),
    ("Sept. 11, 2001", "September eleventh, two thousand one"), ("3 March 2024", "The third of March twenty twenty-four"),
    ("the 3rd of March", "the third of March"), ("3/3/2024", "March third, twenty twenty-four"), ("13/3/2024", "The thirteenth of March twenty twenty-four"),
    ("3/13/2024", "March thirteenth, twenty twenty-four"), ("3/4/2024", "March fourth, twenty twenty-four"), ("2024-03-03", "March third, twenty twenty-four"),
    ("since 9/11", "since nine eleven"), ("Dec. 25", "December twenty-fifth"),
    # times
    ("9:05 pm", "nine oh five p.m."), ("9:30 a.m.", "nine thirty a.m."), ("12:00 PM", "twelve p.m."), ("at 9:00", "at nine o'clock"), ("at 3:45", "at three forty-five"),
    ("2 a.m.", "two a.m."), ("11PM", "eleven p.m."), ("10 o'clock", "ten o'clock"), ("21:30", "twenty-one thirty"), ("08:00", "oh eight hundred"),
    ("1430 hours", "fourteen thirty hours"), ("0800 hours", "oh eight hundred hours"), ("3:1", "three to one"),
    # money, percents
    ("$3.50", "three dollars and fifty cents"), ("$0.50", "fifty cents"), ("$1", "one dollar"), ("$3.05", "three dollars and five cents"), ("$1,000", "one thousand dollars"),
    ("£20", "twenty pounds"), ("£3.50", "three pounds and fifty pence"), ("99p", "ninety-nine pence"), ("1p", "one penny"), ("50¢", "fifty cents"), ("€5", "five euros"),
    ("$2.5 million", "two point five million dollars"), ("$3 billion", "three billion dollars"), ("$5M", "five million dollars"), ("$20k", "twenty thousand dollars"),
    ("USD 5,000", "five thousand dollars"), ("40%", "forty percent"), ("3.5%", "three point five percent"), ("0.5%", "zero point five percent"), ("100%", "one hundred percent"),
    ("40-50%", "forty to fifty percent"), ("1%", "one percent"),
    # decimals, fractions, ranges, signs
    ("3.14", "three point one four"), ("0.25", "zero point two five"), ("1,234.5", "one thousand two hundred thirty-four point five"), ("1/2", "one half"),
    ("3/4", "three quarters"), ("2/3", "two thirds"), ("1 1/2", "one and a half"), ("2 3/4", "two and three quarters"), ("7/13", "seven over thirteen"),
    ("10-12 years", "ten to twelve years"), ("3-1", "three to one"), ("-5", "minus five"), ("24/7", "twenty-four seven"), ("1.2.3", "one dot two dot three"),
    # ages and compounds
    ("a 35-year-old", "a thirty-five-year-old"), ("a 5-year sentence", "a five-year sentence"), ("a 6-foot-2 man", "a six-foot-two man"),
    ("a 9-mm pistol", "a nine-millimeter pistol"),
    # phones, emergency numbers, addresses, codes
    ("(555) 123-4567", "five five five, one two three, four five six seven"), ("555-123-4567", "five five five, one two three, four five six seven"),
    ("1-800-555-0199", "one, eight hundred, five five five, oh one nine nine"), ("555-0143", "five five five, oh one four three"),
    ("+44 20 7946 0958", "plus four four, two oh, seven nine four six, oh nine five eight"), ("020 7946 0958", "oh two oh, seven nine four six, oh nine five eight"),
    ("He called 911.", "He called nine one one."), ("Dial 999", "Dial nine nine nine"), ("They rang 112 at once", "They rang one one two at once"),
    ("a 911 call", "a nine one one call"), ("9-1-1", "nine one one"), ("the 000 line, call 000", "the triple zero line, call triple zero"),
    ("It cost 911 dollars", "It cost nine hundred eleven dollars"), ("Page 911", "Page nine hundred eleven"),
    ("Case No. 2004-0153", "Case number two oh oh four, oh one five three"), ("badge 4521", "badge four five two one"), ("case 15", "case fifteen"),
    ("Room 304", "Room three oh four"), ("Flight 370", "Flight three seventy"), ("Route 66", "Route sixty-six"), ("Highway 101", "Highway one oh one"),
    ("1600 Pennsylvania Avenue", "sixteen hundred Pennsylvania Avenue"), ("221B Baker Street", "two twenty-one B Baker Street"), ("12 Elm St.", "twelve Elm St."),
    ("Beverly Hills, CA 90210", "Beverly Hills, CA nine oh two one oh"), ("No. 5", "number five"), ("#1 hit", "number one hit"), ("Code 007", "Code oh oh seven"),
    # calibres, measures
    ("a .38", "a thirty-eight"), ("a .22 caliber", "a twenty-two caliber"), ("the .357 Magnum", "the three fifty-seven Magnum"), (".380 ACP", "three eighty ACP"),
    (".308", "three oh eight"), (".30-06", "thirty oh six"), (".45-70", "forty-five seventy"), ("a 9mm", "a nine millimeter"), ("9x19mm round", "nine by nineteen millimeter round"),
    ("5'10\"", "five foot ten"), ("6 ft 2 in", "six foot two"), ("180 lbs", "one hundred eighty pounds"), ("1 lb", "one pound"), ("120 mph", "one hundred twenty miles per hour"),
    ("2.5 kg", "two point five kilograms"), ("98.6°F", "ninety-eight point six degrees Fahrenheit"), ("-5 °C", "minus five degrees Celsius"), ("100 km/h", "one hundred kilometers per hour"),
    ("6 feet", "six feet"),
    # Roman numerals
    ("Henry VIII", "Henry the Eighth"), ("Elizabeth II", "Elizabeth the Second"), ("Pope John Paul II", "Pope John Paul the Second"), ("Louis XIV", "Louis the Fourteenth"),
    ("World War II", "World War Two"), ("World War I", "World War One"), ("Chapter IV", "Chapter four"), ("Charles I was executed", "Charles the First was executed"),
]


@pytest.mark.parametrize(("text", "spoken"), CASES)
def test_numbers_are_said_as_an_english_narrator_says_them(numbers: Numbers, text: str, spoken: str):
    assert numbers.text(text) == spoken


@pytest.mark.parametrize("text", ["A1", "B-52", "COVID-19", "3D", "5G", "4x4", "G20", "AK-47", "mp3", "https://example.com/a1b2", "max123@mail.com", "F-16", "I-95"])
def test_codes_and_addresses_are_kept_as_they_are(numbers: Numbers, text: str):
    assert numbers.text(f"See {text} there.") == f"See {text} there."


def test_a_code_next_to_a_number_survives_while_the_number_is_said(numbers: Numbers):
    assert numbers.text("On the A1, 2 people died, see COVID-19.") == "On the A1, two people died, see COVID-19."


@pytest.mark.parametrize("text", ["The man shot twice at the woman.", "On March third the police came.", "He was thirty-five years old.", "Henry was tired.", "", "I am here.",
                                  "Paul V. Smith came.", "He called. Then he left.", "Nineteen ninety-nine was a long year."])
def test_text_without_numbers_stays_as_it_is(numbers: Numbers, text: str):
    assert numbers.text(text) == text


def test_a_single_letter_initial_after_a_ruler_name_is_not_a_numeral(numbers: Numbers):
    assert numbers.text("Paul V. Smith met Henry V. Jones.") == "Paul V. Smith met Henry V. Jones."


def test_a_sentence_with_every_kind_of_number_leaves_no_digit(numbers: Numbers):
    text = ("On March 3rd, 1999 at 11:45 pm he paid $12.50 for 2 shots, a 35-year-old, 5%, 1/2, 3-1, 120 mph, call (555) 123-4567, Case No. 12-345, a .38, 5'10\", "
            "the 1990s, Room 12B, 1st degree, 2.5 kg, Henry VIII, 1990-1995, 1 1/2, 7/13, 24/7, 0800 hours, 1,234,567.")
    assert not re.search(r"\d", numbers.text(text))


def test_running_the_reader_twice_changes_nothing_more(numbers: Numbers):
    text = ("On March 3rd, 1999 at 11:45 pm he called 911, paid $12.50 for 2 shots, a 35-year-old, 5%, a .38, 5'10\", the 1990s, Henry VIII, World War II, Case 15, A1, COVID-19.")
    once = numbers.text(text)
    assert numbers.text(once) == once


def test_an_amount_before_a_counted_noun_is_not_a_year(numbers: Numbers):
    assert numbers.text("Over 1500 people came and 1999 dollars were found.") == "Over one thousand five hundred people came and one thousand nine hundred ninety-nine dollars were found."


def test_a_four_digit_number_after_a_month_or_since_is_a_year_even_before_a_noun(numbers: Numbers):
    assert numbers.text("In May 1500 people died; since 1999 men are missing.") == "In May fifteen hundred people died; since nineteen ninety-nine men are missing."


@pytest.mark.parametrize(("mode", "text", "spoken"), [
    ("digits_in_context", "He dialed 911.", "He dialed nine one one."),
    ("digits_in_context", "There were 911 bullets.", "There were nine hundred eleven bullets."),
    ("digits_in_context", "It was 112.", "It was one hundred twelve."),
    ("digits_always", "It was 112.", "It was one one two."),
    ("number_always", "He called 911", "He called nine hundred eleven"),
])
def test_emergency_numbers_follow_the_option(mode: str, text: str, spoken: str):
    assert Numbers({"emergency_numbers": mode}, 10 ** 18).text(text) == spoken


def test_the_british_habits_can_be_switched_on():
    reader = Numbers({"and_after_hundred": True, "ambiguous_numeric_dates": "day_first", "money_cents": "bare", "phone_double_digits": True, "zero_word": "zero",
                      "years_2000s": "twenty_oh", "years_after_2009": "two_thousand"}, 10 ** 18)
    assert reader.text("120 and 1,005 and 3/4/2024 and $3.50 and 555-0143 and 2005 and 2024") == (
        "one hundred and twenty and one thousand and five and the third of April two thousand and twenty-four and three dollars fifty and triple five, zero one four three and "
        "twenty oh five and two thousand and twenty-four")


def test_the_telephone_zero_follows_the_option(numbers: Numbers):
    assert numbers.text("555-0100") == "five five five, oh one oh oh"
    assert Numbers({"zero_word": "zero"}, 10 ** 18).text("555-0100") == "five five five, zero one zero zero"


def test_roman_numerals_can_be_switched_off():
    assert Numbers({"roman_numerals": False}, 10 ** 18).text("Henry VIII in World War II") == "Henry VIII in World War II"


def test_an_unknown_option_or_value_is_refused():
    with pytest.raises(ValueError):
        Numbers({"emergency_number": "number_always"}, 10 ** 18)
    with pytest.raises(ValueError):
        Numbers({"emergency_numbers": "sometimes"}, 10 ** 18)


def test_a_number_above_the_limit_is_said_digit_by_digit():
    assert Numbers({}, 999).text("a loss of 1000 dollars") == "a loss of one oh oh oh dollars"


def test_a_day_that_does_not_exist_is_not_turned_into_a_date(numbers: Numbers):
    assert numbers.text("March 45 and 45/45/2024") == "March forty-five and forty-five/forty-five/two thousand twenty-four"


@pytest.mark.parametrize(("value", "words"), [(0, "zero"), (11, "eleven"), (12, "twelve"), (40, "forty"), (1000, "one thousand"), (1001, "one thousand one"),
                                              (100000, "one hundred thousand"), (1000000000, "one billion"), (2000000000000, "two trillion")])
def test_cardinals(value: int, words: str):
    assert en_words.cardinal(value) == words


@pytest.mark.parametrize(("value", "words"), [(120, "one hundred and twenty"), (101, "one hundred and one"), (1005, "one thousand and five"),
                                              (1234567, "one million two hundred and thirty-four thousand five hundred and sixty-seven"), (1000, "one thousand")])
def test_british_cardinals_have_the_and(value: int, words: str):
    assert en_words.cardinal(value, with_and=True) == words


@pytest.mark.parametrize(("value", "words"), [(1, "first"), (2, "second"), (3, "third"), (5, "fifth"), (8, "eighth"), (9, "ninth"), (12, "twelfth"), (20, "twentieth"),
                                              (21, "twenty-first"), (30, "thirtieth"), (100, "one hundredth"), (101, "one hundred first"), (112, "one hundred twelfth")])
def test_ordinals(value: int, words: str):
    assert en_words.ordinal(value) == words
