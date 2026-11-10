"""Italian number reader (app/documentary/spoken_numbers/it.py): the rows of the research table (scratchpad numbers/it.md) that are not UNSURE,
the options, the protected spans and the text that must stay as written. Divergences from the table: row 37 keeps the noun's capitals, row 49
(a bare 118) is a number unless a call word is near (owner rule: plain numbers are said as numbers), rows 75/76/78 (4x4, 3D, A14) stay as
written because the base reader protects tokens that mix letters and digits."""

from __future__ import annotations

import pytest

from app.documentary.spoken_numbers import reader_for
from app.documentary.spoken_numbers.it import Numbers, Options
from app.documentary.spoken_numbers.it_words import cardinal, ordinal, roman_value

MAX = 999_999_999_999_999


def say(text: str, **options) -> str:
    return Numbers(options, MAX).text(text)


CARDINALS = [
    ("912", "novecentododici"), ("911", "nove uno uno"), ("la chiamata al 911", "la chiamata al nove uno uno"), ("prima del 911", "prima del nove uno uno"), ("1800", "milleottocento"), ("2026", "duemilaventisei"), ("21 anni", "ventun anni"),
    ("31 giorni", "trentun giorni"), ("28", "ventotto"), ("108", "centootto"), ("101", "centouno"), ("23", "ventitré"), ("103", "centotré"),
    ("2000", "duemila"), ("21.000", "ventunomila"), ("1.000.000 di persone", "un milione di persone"),
    ("2.000.000 di euro", "due milioni di euro"), ("1.200.000", "un milione duecentomila"), ("3.000.000.000", "tre miliardi"),
    ("1.000", "mille"), ("1.001", "milleuno"), ("1.500.000 euro", "un milione cinquecentomila euro"), ("2 milioni e mezzo", "due milioni e mezzo"),
    ("2.345", "duemilatrecentoquarantacinque"), ("Rai 1", "Rai uno"), ("007", "zero zero sette"), ("1 anno", "un anno"), ("1 ora", "un'ora"),
    ("1 donna", "una donna"), ("1 studente", "uno studente"), ("21 studenti", "ventuno studenti"), ("1 e mezzo", "uno e mezzo"),
    ("12 000 euro", "dodicimila euro"), ("10 000", "diecimila"), ("1000", "mille"), ("il numero 1", "il numero uno"),
]
DATES = [
    ("nel 2007", "nel duemilasette"), ("nel 1999", "nel millenovecentonovantanove"), ("nel 1348", "nel milletrecentoquarantotto"),
    ("il 13 novembre 2007", "il tredici novembre duemilasette"), ("il 1º maggio", "il primo maggio"), ("il 1 maggio", "il primo maggio"),
    ("il 2 giugno", "il due giugno"), ("l'11 settembre 2001", "l'undici settembre duemilauno"), ("il 8 marzo", "l'otto marzo"),
    ("il 21 aprile", "il ventuno aprile"), ("il 31 dicembre", "il trentuno dicembre"), ("gli anni '90", "gli anni Novanta"),
    ("negli anni 2000", "negli anni Duemila"), ("anni '70 e '80", "anni Settanta e Ottanta"), ("il '68", "il Sessantotto"),
    ("nel '45", "nel quarantacinque"), ("il '900", "il Novecento"), ("13/11/2007", "tredici novembre duemilasette"),
    ("1/5/2020", "primo maggio duemilaventi"), ("2007-11-13", "tredici novembre duemilasette"), ("dal 1 al 5 maggio", "dal primo al cinque maggio"),
    ("dal 3-5 maggio", "dal tre al cinque maggio"), ("era il 476 d.C.", "era il quattrocentosettantasei dopo Cristo"),
    ("I secolo a.C.", "primo secolo avanti Cristo"), ("per 3 anni 10 mesi", "per tre anni dieci mesi"),
    ("dal 2007 al 2010", "dal duemilasette al duemiladieci"), ("1990-1995", "dal millenovecentonovanta al millenovecentonovantacinque"),
]
ORDINALS = [
    ("XX secolo", "ventesimo secolo"), ("XIX secolo", "diciannovesimo secolo"), ("Giovanni Paolo II", "Giovanni Paolo secondo"),
    ("Luigi XIV", "Luigi quattordicesimo"), ("Vittorio Emanuele II", "Vittorio Emanuele secondo"), ("Benedetto XVI", "Benedetto sedicesimo"),
    ("Elisabetta II", "Elisabetta seconda"), ("II Guerra Mondiale", "seconda Guerra Mondiale"), ("V Sinfonia", "quinta Sinfonia"),
    ("capitolo IV", "capitolo quarto"), ("1ª classe", "prima classe"), ("3° piano", "terzo piano"), ("21º secolo", "ventunesimo secolo"),
    ("via XX Settembre", "via Venti Settembre"), ("12°", "dodici gradi"),
]
MONEY = [
    ("1.500 €", "millecinquecento euro"), ("€ 1.500,50", "millecinquecento euro e cinquanta centesimi"), ("5 milioni di euro", "cinque milioni di euro"),
    ("$ 100", "cento dollari"), ("£ 50", "cinquanta sterline"), ("1.000 lire", "mille lire"), ("1,00 €", "un euro"), ("€ 0,50", "cinquanta centesimi"),
    ("€ 2,05", "due euro e cinque centesimi"), ("1,01 €", "un euro e un centesimo"), ("$1.000.000", "un milione di dollari"),
    ("2,5 milioni di euro", "due milioni e mezzo di euro"), ("10 mila euro", "diecimila euro"), ("2 mld di euro", "due miliardi di euro"),
]
CODES = [
    ("chiama il 112", "chiama l'uno uno due"), ("chiama il 118", "chiama l'uno uno otto"), ("chiama la polizia al 112", "chiama la polizia all'uno uno due"),
    ("chiama il 1522", "chiama il quindici ventidue"), ("333 1234567", "tre tre tre, uno due tre quattro cinque sei sette"),
    ("+39 06 12345678", "più trentanove zero sei, uno due tre quattro cinque sei sette otto"), ("tel. 3331234567", "tel. tre tre tre, uno due tre quattro cinque sei sette"),
    ("CAP 00100", "CAP zero zero uno zero zero"), ("118", "centodiciotto"), ("+39", "più trentanove"),
]
CLOCK = [
    ("alle 10:30", "alle dieci e mezza"), ("alle 23:45", "alle ventitré e quarantacinque"), ("alle 3 di notte", "alle tre di notte"),
    ("alle 1:00", "all'una"), ("12:00", "mezzogiorno"), ("00:00", "mezzanotte"), ("10:15", "dieci e un quarto"), ("alle 12:30", "a mezzogiorno e mezza"),
    ("ore 21.30", "ore ventuno e trenta"), ("alle 9.30", "alle nove e mezza"), ("alle 00:15", "a mezzanotte e un quarto"),
]
MEASURES = [
    ("35 anni", "trentacinque anni"), ("35enne", "trentacinquenne"), ("21enne", "ventunenne"), ("23enne", "ventitreenne"), ("un 17enne", "un diciassettenne"),
    ("una 16enne", "una sedicenne"), ("3,5", "tre virgola cinque"), ("0,05", "zero virgola zero cinque"), ("3,50", "tre virgola cinquanta"),
    ("1/2", "un mezzo"), ("3/4", "tre quarti"), ("1/2 ora", "mezz'ora"), ("3,5%", "tre virgola cinque per cento"), ("l'1%", "l'uno per cento"),
    ("da 5 a 10", "da cinque a dieci"), ("5-10 anni", "da cinque a dieci anni"), ("partita finita 3-1", "partita finita tre a uno"),
    ("1:100.000", "uno a centomila"), ("n. 5", "numero cinque"), ("9 mm", "nove millimetri"), ("calibro .38", "calibro trentotto"),
    ("9x19", "nove per diciannove"), ("via Roma 12", "via Roma dodici"), ("Via Roma 12/A", "Via Roma dodici A"),
    ("n. 1234/2019", "numero milleduecentotrentaquattro del duemiladiciannove"), ("100 km/h", "cento chilometri orari"),
    ("35 °C", "trentacinque gradi"), ("-5 °C", "meno cinque gradi"), ("2,5 km", "due chilometri e mezzo"), ("20 kg di cocaina", "venti chili di cocaina"),
    ("24 ore su 24", "ventiquattro ore su ventiquattro"), ("Covid-19", "Covid diciannove"), ("2 + 2", "due più due"), ("3+1", "tre più uno"),
    ("5/200", "cinque barra duecento"),
]
TRUE_CRIME = [
    ("21 coltellate", "ventuno coltellate"), ("3 colpi di pistola", "tre colpi di pistola"), ("condannato a 4 anni e 6 mesi", "condannato a quattro anni e sei mesi"),
    ("sentenza n. 45 del 12 marzo 2015", "sentenza numero quarantacinque del dodici marzo duemilaquindici"),
    ("Il 5 maggio 2020 alle 9.30 pagò € 12,50.", "Il cinque maggio duemilaventi alle nove e mezza pagò dodici euro e cinquanta centesimi."),
]
ROWS = CARDINALS + DATES + ORDINALS + MONEY + CODES + CLOCK + MEASURES + TRUE_CRIME


@pytest.mark.parametrize(("text", "spoken"), ROWS)
def test_spoken(text: str, spoken: str) -> None:
    assert say(text) == spoken


@pytest.mark.parametrize("text", ["4x4", "3D", "A14", "https://example.com/a1", "scrivi a mario.rossi@example.it", "È stato condannato all'ergastolo.",
                                  "Papa Francesco", "Rocky II", "nessun numero qui"])
def test_unchanged(text: str) -> None:
    assert say(text) == text


def test_protected_span_is_put_back_among_numbers() -> None:
    assert say("Il 3D costa 5 euro, vedi A14 e www.example.com") == "Il 3D costa cinque euro, vedi A14 e www.example.com"


def test_no_digit_is_left_by_a_converted_number() -> None:
    assert not any(char.isdigit() for char in say("Nel 2007, alle 22:10, il 118 chiamò 3 volte: 21 anni, 12,5 km, € 3.000 e 40%."))


def test_number_above_the_maximum_is_read_digit_by_digit() -> None:
    assert Numbers({}, 999).text("1234") == "uno due tre quattro"


@pytest.mark.parametrize(("mode", "spoken"), [("digits_in_context", "centodiciotto"), ("digits_always", "uno uno otto"),
                                              ("number_always", "centodiciotto")])
def test_emergency_number_option_without_context(mode: str, spoken: str) -> None:
    assert say("118", emergency_numbers=mode) == spoken


@pytest.mark.parametrize(("mode", "spoken"), [("digits_in_context", "chiama l'uno uno otto"), ("digits_always", "chiama l'uno uno otto"),
                                              ("number_always", "chiama il centodiciotto")])
def test_emergency_number_option_with_context(mode: str, spoken: str) -> None:
    assert say("chiama il 118", emergency_numbers=mode) == spoken


def test_options_flip_the_choices() -> None:
    assert say("30 anni", elide_tens_before_anni=True) == "trent'anni"
    assert say("20 kg", kilogram_word="chilogrammi") == "venti chilogrammi"
    assert say("$ 5", symbol_currencies={"$": "yen"}) == "cinque yen"
    assert say("2,5 km", half_as_mezzo=False) == "due virgola cinque chilometri"
    assert say("3,14", decimals_as_number_up_to=1) == "tre virgola uno quattro"
    assert say("10:30", quarter_and_half=False) == "dieci e trenta"
    assert say("12 000", space_thousands=False) == "dodici zero zero zero"


def test_unknown_option_is_refused() -> None:
    with pytest.raises(ValueError):
        Options.model_validate({"emergency": "yes"})


def test_reader_is_found_by_language_code() -> None:
    assert isinstance(reader_for("it", {}, MAX), Numbers)


@pytest.mark.parametrize(("n", "spoken"), [(0, "zero"), (13, "tredici"), (180, "centoottanta"), (1003, "milletré"), (21_000_000, "ventun milioni"),
                                           (23_000_000, "ventitré milioni"), (1_000_003, "un milione tre"), (999_999, "novecentonovantanovemilanovecentonovantanove")])
def test_cardinal(n: int, spoken: str) -> None:
    assert cardinal(n) == spoken


@pytest.mark.parametrize(("n", "feminine", "spoken"), [(1, False, "primo"), (2, True, "seconda"), (10, False, "decimo"), (11, False, "undicesimo"),
                                                       (21, False, "ventunesimo"), (23, False, "ventitreesimo"), (26, False, "ventiseiesimo"),
                                                       (100, False, "centesimo"), (1000, False, "millesimo"), (2000, False, "duemillesimo")])
def test_ordinal(n: int, feminine: bool, spoken: str) -> None:
    assert ordinal(n, feminine) == spoken


@pytest.mark.parametrize(("token", "value"), [("XIV", 14), ("XIX", 19), ("XXI", 21), ("IIII", None), ("", None)])
def test_roman_value(token: str, value: int | None) -> None:
    assert roman_value(token) == value
