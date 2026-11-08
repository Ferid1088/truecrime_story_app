"""German number reader (app/documentary/spoken_numbers/de.py): the rows of the research table scratchpad/numbers/de.md that are not marked UNSURE, the
protected spans, and text that must stay as it is."""

from __future__ import annotations

import re

import pytest

from app.documentary.spoken_numbers import de_words
from app.documentary.spoken_numbers.de import Numbers


@pytest.fixture
def numbers() -> Numbers:
    return Numbers({}, 10 ** 15)


CASES = [
    ("21", "einundzwanzig"), ("100", "hundert"), ("101", "hunderteins"), ("1.000 Euro", "tausend Euro"), ("1001 Menschen", "tausendeins Menschen"),
    ("2.500", "zweitausendfünfhundert"), ("1.000.000", "eine Million"), ("2.000.000 Euro", "zwei Millionen Euro"),
    ("1.500.000", "eine Million fünfhunderttausend"), ("3 Mrd.", "drei Milliarden"), ("1 Mrd. €", "eine Milliarde Euro"), ("2 Bio.", "zwei Billionen"),
    ("16", "sechzehn"), ("17", "siebzehn"), ("70", "siebzig"), ("30", "dreißig"), ("um 1 Uhr", "um ein Uhr"), ("1 Mann", "ein Mann"), ("1 Frau", "eine Frau"),
    ("für 1 Euro", "für einen Euro"), ("mit 1 Messer", "mit einem Messer"), ("Platz 1", "Platz eins"),
    ("im Jahr 1999", "im Jahr neunzehnhundertneunundneunzig"), ("Im Mai 2007", "Im Mai zweitausendsieben"), ("seit 2024", "seit zweitausendvierundzwanzig"),
    ("um 1900", "um neunzehnhundert"), ("1905", "neunzehnhundertfünf"), ("2000", "zweitausend"),
    ("1990-1995", "neunzehnhundertneunzig bis neunzehnhundertfünfundneunzig"), ("1900 Euro", "tausendneunhundert Euro"),
    ("Am 13. November 2007", "Am dreizehnten November zweitausendsieben"),
    ("Der 13. November 2007 war kalt.", "Der dreizehnte November zweitausendsieben war kalt."), ("des 3. Mai", "des dritten Mai"),
    ("vom 3. bis 5. Mai", "vom dritten bis fünften Mai"), ("am 1. Januar", "am ersten Januar"),
    ("am 7.8.1999", "am siebten August neunzehnhundertneunundneunzig"), ("bis zum 20. Juli", "bis zum zwanzigsten Juli"), ("Am 31. März", "Am einunddreißigsten März"),
    ("die 90er Jahre", "die neunziger Jahre"), ("in den 1990ern", "in den neunzigern"), ("die 1970er Jahre", "die siebziger Jahre"),
    ("Mitte der 80er", "Mitte der achtziger"), ("im 21. Jahrhundert", "im einundzwanzigsten Jahrhundert"), ("das 20. Jahrhundert", "das zwanzigste Jahrhundert"),
    ("zum 3. Mal", "zum dritten Mal"), ("der 2. Weltkrieg", "der zweite Weltkrieg"), ("im II. Weltkrieg", "im Zweiten Weltkrieg"),
    ("in der 7. Klasse", "in der siebten Klasse"), ("am 8. Tag", "am achten Tag"), ("Kapitel 3", "Kapitel drei"), ("Artikel 5", "Artikel fünf"),
    ("Heinrich IV.", "Heinrich der Vierte"), ("Karl V.", "Karl der Fünfte"), ("Papst Johannes Paul II.", "Papst Johannes Paul der Zweite"),
    ("das XX. Jahrhundert", "das zwanzigste Jahrhundert"), ("Band II", "Band zwei"), ("Er wurde 35.", "Er wurde fünfunddreißig."),
    ("1.500 €", "tausendfünfhundert Euro"), ("12,50 €", "zwölf Euro fünfzig"), ("0,99 €", "neunundneunzig Cent"), ("5 Mio. €", "fünf Millionen Euro"),
    ("$20", "zwanzig Dollar"), ("2.000 US-Dollar", "zweitausend US-Dollar"), ("£5", "fünf Pfund"), ("50 ct", "fünfzig Cent"), ("100 DM", "hundert D-Mark"),
    ("50k", "fünfzigtausend"), ("2 Euro", "zwei Euro"), ("Notruf 110", "Notruf eins eins null"), ("Notruf 112", "Notruf eins eins zwei"),
    ("Bereitschaftsdienst 116 117", "Bereitschaftsdienst eins eins sechs, eins eins sieben"),
    ("030 12345678", "null drei null, eins zwei drei vier fünf sechs sieben acht"),
    ("+49 30 1234567", "plus neunundvierzig, drei null, eins zwei drei vier fünf sechs sieben"), ("10115 Berlin", "eins null eins eins fünf Berlin"),
    ("DE89 3704 0044 0532 0130 00", "DE acht neun, drei sieben null vier, null null vier vier, null fünf drei zwei, null eins drei null, null null"),
    ("10:30 Uhr", "zehn Uhr dreißig"), ("23:45 Uhr", "dreiundzwanzig Uhr fünfundvierzig"), ("um 3 Uhr morgens", "um drei Uhr morgens"),
    ("9:05 Uhr", "neun Uhr fünf"), ("18:00 Uhr", "achtzehn Uhr"), ("um 22:15", "um zweiundzwanzig Uhr fünfzehn"), ("00:10 Uhr", "null Uhr zehn"),
    ("14.30 Uhr", "vierzehn Uhr dreißig"), ("der 35-Jährige", "der Fünfunddreißigjährige"), ("die 23-Jährige", "die Dreiundzwanzigjährige"),
    ("ein 1-jähriges Kind", "ein einjähriges Kind"), ("35 Jahre alt", "fünfunddreißig Jahre alt"), ("Mitte 30", "Mitte dreißig"),
    ("3,5", "drei Komma fünf"), ("3,5 %", "drei Komma fünf Prozent"), ("0,25", "null Komma zwei fünf"), ("1,72 m", "ein Meter zweiundsiebzig"),
    ("1/2", "ein halb"), ("3/4", "drei Viertel"), ("2/3 der Fälle", "zwei Drittel der Fälle"), ("100 %", "hundert Prozent"), ("3:1", "drei zu eins"),
    ("0:0", "null zu null"), ("Nr. 5", "Nummer fünf"), ("120 km/h", "hundertzwanzig Kilometer pro Stunde"), ("-5 °C", "minus fünf Grad Celsius"),
    ("80 m²", "achtzig Quadratmeter"), ("2 kg", "zwei Kilogramm"), ("9 mm", "neun Millimeter"), ("9-mm-Pistole", "Neun-Millimeter-Pistole"),
    ("Hauptstraße 12", "Hauptstraße zwölf"), ("Hauptstraße 12a", "Hauptstraße zwölf a"), ("Hauptstraße 12-14", "Hauptstraße zwölf bis vierzehn"),
    ("2 Schüsse", "zwei Schüsse"), ("8 Messerstiche", "acht Messerstiche"), ("15 Jahre Haft", "fünfzehn Jahre Haft"), ("zu 1 Jahr", "zu einem Jahr"),
    ("§ 211 StGB", "Paragraf zweihundertelf StGB"), ("1,500", "eins Komma fünf null null"),
]
# Rows of the research table that are UNSURE there, with the defaults the module chose.
CHOSEN_DEFAULTS = [
    ("1066", "tausendsechsundsechzig"), ("die 2000er", "die zweitausender"), ("unter Wilhelm II.", "unter Wilhelm dem Zweiten"),
    ("12,05 €", "zwölf Euro fünf"), ("0800 111 0 111", "null achthundert, eins eins eins, null, eins eins eins"), ("B-AB 1234", "B-AB eins zwei drei vier"),
    ("2,5 Millionen", "zweieinhalb Millionen"), (".38 Special", "achtunddreißig Special"), ("24/7", "vierundzwanzig sieben"),
    ("Az. 1 Ks 12/05", "Aktenzeichen eins Ks zwölf Schrägstrich null fünf"),
]


@pytest.mark.parametrize(("text", "spoken"), CASES + CHOSEN_DEFAULTS)
def test_numbers_are_said_as_a_german_narrator_says_them(numbers: Numbers, text: str, spoken: str):
    assert numbers.convert(text) == spoken


@pytest.mark.parametrize("text", ["A1", "B-52", "COVID-19", "3D", "5G", "4x4", "G20", "AK-47", "https://example.com/a1b2", "max123@mail.de"])
def test_codes_and_addresses_are_kept_as_they_are(numbers: Numbers, text: str):
    assert numbers.text(f"Siehe {text} dort.") == f"Siehe {text} dort."


def test_a_code_next_to_a_number_survives_while_the_number_is_said(numbers: Numbers):
    assert numbers.text("Auf der A1 starben 2 Menschen, siehe COVID-19.") == "Auf der A1 starben zwei Menschen, siehe COVID-19."


def test_decades_ages_and_house_letters_are_not_swallowed_by_the_protection(numbers: Numbers):
    assert numbers.text("In den 90ern wohnte die 35-Jährige in der Hauptstraße 12a.") == "In den neunzigern wohnte die Fünfunddreißigjährige in der Hauptstraße zwölf a."


@pytest.mark.parametrize("text", ["Der Täter schoss zweimal auf den Mann.", "Am dritten Mai kam die Polizei.", "Er war fünfunddreißig Jahre alt.", "Heinrich war müde.",
                                  "", "Paul V. Schmidt kam.", "Er rief an. Dann ging er."])
def test_text_without_numbers_stays_as_it_is(numbers: Numbers, text: str):
    assert numbers.convert(text) == text


def test_a_decimal_with_a_dot_and_two_digits_is_left_alone(numbers: Numbers):
    assert numbers.convert("3.50") == "3.50"


@pytest.mark.parametrize(("text", "spoken"), [("Um 3. Dann ging er.", "Um drei. Dann ging er."), ("Er kam um 3. Dann ging er.", "Er kam um drei. Dann ging er.")])
def test_a_period_after_a_number_before_a_new_sentence_is_a_sentence_end(numbers: Numbers, text: str, spoken: str):
    assert numbers.convert(text) == spoken


def test_a_one_before_a_noun_agrees_by_gender_and_case(numbers: Numbers):
    assert [numbers.convert(text) for text in ["1 Tag", "mit 1 Frau", "ohne 1 Zeugen", "für 1 Opfer", "wegen 1 Schuss", "1 Unbekanntes"]] == [
        "ein Tag", "mit einer Frau", "ohne ein Zeugen", "für ein Opfer", "wegen eines Schuss", "ein Unbekanntes"]


def test_a_one_that_counts_or_stands_alone_is_eins(numbers: Numbers):
    assert numbers.convert("Seite 1, Zimmer 1 und 1.") == "Seite eins, Zimmer eins und eins."


@pytest.mark.parametrize(("mode", "text", "spoken"), [
    ("digits_in_context", "Er wählte die 110.", "Er wählte die eins eins null."),
    ("digits_in_context", "Es waren 110 Patronen.", "Es waren hundertzehn Patronen."),
    ("digits_in_context", "Das macht 112.", "Das macht hundertzwölf."),
    ("digits_always", "Das macht 112.", "Das macht eins eins zwei."),
    ("number_always", "Notruf 112", "Notruf hundertzwölf"),
])
def test_emergency_numbers_follow_the_option(mode: str, text: str, spoken: str):
    assert Numbers({"emergency_numbers": mode}, 10 ** 15).convert(text) == spoken


def test_options_can_be_flipped():
    reader = Numbers({"digit_two": "zwo", "dollar_name": "US-Dollar", "years_2000s": "pairs", "cents_below_ten": "and_cent", "paragraph_word": "Paragraph",
                      "toll_free_as_number": False, "half_as_einhalb": False}, 10 ** 15)
    assert reader.convert("Notruf 112") == "Notruf eins eins zwo"
    assert reader.convert("$5 und 12,05 € und 2024 und § 211 und 0800 123 4567 und 2,5 Jahre") == (
        "fünf US-Dollar und zwölf Euro und fünf Cent und zwanzig vierundzwanzig und Paragraph zweihundertelf und null acht null null, eins zwo drei, vier fünf sechs sieben und "
        "zwei Komma fünf Jahre")


def test_roman_numerals_can_be_switched_off():
    assert Numbers({"roman_numerals": False}, 10 ** 15).convert("Heinrich IV. im XX. Jahrhundert") == "Heinrich IV. im XX. Jahrhundert"


def test_an_unknown_option_value_is_refused():
    with pytest.raises(ValueError):
        Numbers({"emergency_numbers": "sometimes"}, 10 ** 15)


def test_a_number_above_the_limit_is_said_digit_by_digit():
    assert Numbers({}, 999).convert("1000") == "eins null null null"


def test_a_leading_zero_is_read_digit_by_digit(numbers: Numbers):
    assert numbers.convert("Code 007") == "Code null null sieben"


def test_nothing_digit_is_left_after_conversion(numbers: Numbers):
    text = "Am 3. Mai 1999 um 23:45 Uhr zahlte er 12,50 € für 2 Schüsse, 35-jährig, 5 %, 1/2, 3:1, 120 km/h, Tel. 030 12345678, Az. 1 Ks 12/05."
    assert not re.search(r"\d", numbers.convert(text))


@pytest.mark.parametrize(("value", "words"), [(0, "null"), (11, "elf"), (12, "zwölf"), (1000, "tausend"), (1001, "tausendeins"), (21000, "einundzwanzigtausend"),
                                              (100000, "hunderttausend"), (999999, "neunhundertneunundneunzigtausendneunhundertneunundneunzig"),
                                              (1000000000, "eine Milliarde"), (2000000000000, "zwei Billionen"), (1234567, "eine Million zweihundertvierunddreißigtausendfünfhundertsiebenundsechzig")])
def test_cardinals(value: int, words: str):
    assert de_words.cardinal(value) == words


@pytest.mark.parametrize(("value", "words"), [(1, "erste"), (2, "zweite"), (3, "dritte"), (7, "siebte"), (8, "achte"), (11, "elfte"), (12, "zwölfte"), (17, "siebzehnte"),
                                              (20, "zwanzigste"), (21, "einundzwanzigste"), (100, "hundertste"), (101, "hunderterste"), (107, "hundertsiebte"),
                                              (108, "hundertachte"), (1000, "tausendste")])
def test_ordinals(value: int, words: str):
    assert de_words.ordinal(value) == words
