"""The French number reader: what a narrator says for the digits of a script (rules and sources in scratchpad/numbers/fr.md)."""

from __future__ import annotations

import pytest

from app.documentary.spoken_numbers import fr_words as W
from app.documentary.spoken_numbers.fr import Numbers

MAX = 999999999999999


def say(text: str, **options) -> str:
    return Numbers(options, MAX).text(text)


@pytest.mark.parametrize("text,spoken", [
    ("17", "dix-sept"), ("21", "vingt et un"), ("71", "soixante et onze"), ("72", "soixante-douze"), ("80", "quatre-vingts"),
    ("81", "quatre-vingt-un"), ("91", "quatre-vingt-onze"), ("99", "quatre-vingt-dix-neuf"), ("100", "cent"), ("101", "cent un"),
    ("200", "deux cents"), ("201", "deux cent un"), ("280", "deux cent quatre-vingts"), ("1 000", "mille"), ("2 000", "deux mille"),
    ("80 000", "quatre-vingt mille"), ("200 000", "deux cent mille"), ("1 000 000", "un million"), ("2 000 000", "deux millions"),
    ("1 200 000 €", "un million deux cent mille euros"), ("3 000 000 €", "trois millions d'euros"), ("80 000 000", "quatre-vingts millions"),
    ("1 500 €", "mille cinq cents euros"), ("12,50 €", "douze euros cinquante"), ("0,50 €", "cinquante centimes"), ("5 M€", "cinq millions d'euros"),
    ("10 k€", "dix mille euros"), ("2 Md€", "deux milliards d'euros"), ("2007", "deux mille sept"),
    ("en 1999", "en mille neuf cent quatre-vingt-dix-neuf"), ("en 1900", "en mille neuf cent"),
    ("le 13 novembre 2007", "le treize novembre deux mille sept"), ("le 1er mai", "le premier mai"), ("le 2 janvier", "le deux janvier"),
    ("le 12/03/2021", "le douze mars deux mille vingt et un"), ("les années 90", "les années quatre-vingt-dix"), ("XXe siècle", "vingtième siècle"),
    ("XVIIIe siècle", "dix-huitième siècle"), ("Ve République", "Cinquième République"), ("Louis XIV", "Louis Quatorze"),
    ("Napoléon III", "Napoléon Trois"), ("Jean-Paul II", "Jean-Paul Deux"), ("François Ier", "François Premier"), ("tome II", "tome deux"),
    ("Ier arrondissement", "premier arrondissement"), ("21e", "vingt et unième"), ("1re", "première"), ("2nde", "seconde"),
    ("10 h 30", "dix heures trente"), ("23 h 45", "vingt-trois heures quarante-cinq"), ("21 h", "vingt et une heures"), ("12 h", "midi"),
    ("00:00", "minuit"), ("35 ans", "trente-cinq ans"), ("21 ans", "vingt et un ans"), ("3,5", "trois virgule cinq"),
    ("0,05", "zéro virgule zéro cinq"), ("3,5 %", "trois virgule cinq pour cent"), ("100 %", "cent pour cent"), ("1/2", "un demi"),
    ("3/4", "trois quarts"), ("1990-1995", "mille neuf cent quatre-vingt-dix à mille neuf cent quatre-vingt-quinze"), ("5 à 10", "cinq à dix"),
    ("n° 5", "numéro cinq"), ("120 km/h", "cent vingt kilomètres par heure"), ("25 m²", "vingt-cinq mètres carrés"),
    ("mesure 1,80 m", "mesure un mètre quatre-vingts"), ("-5 °C", "moins cinq degrés"), ("calibre 9 mm", "calibre neuf millimètres"),
    ("un revolver .38 Special", "un revolver trente-huit Special"), ("calibre .38", "calibre trente-huit"),
    ("12 bis, rue de la Paix", "douze bis, rue de la Paix"),
    ("06 12 34 56 78", "zéro six, douze, trente-quatre, cinquante-six, soixante-dix-huit"),
    ("+33 6 12 34 56 78", "plus trente-trois, six, douze, trente-quatre, cinquante-six, soixante-dix-huit"),
    ("appelez le 17", "appelez le dix-sept"), ("le 112", "le cent douze"), ("le 3919", "le trente-neuf dix-neuf"), ("3 coups de feu", "trois coups de feu"),
    ("25 ans de réclusion criminelle", "vingt-cinq ans de réclusion criminelle"), ("garde à vue de 48 heures", "garde à vue de quarante-huit heures"),
    ("1 victime", "une victime"), ("21 victimes", "vingt et une victimes"), ("24/7", "vingt-quatre heures sur vingt-quatre, sept jours sur sept"),
    ("1 500", "mille cinq cents"), ("1 500", "mille cinq cents"), ("1 500", "mille cinq cents"), ("2021 €", "deux mille vingt et un euros"),
    ("5 $", "cinq dollars"), ("12,50 $", "douze dollars cinquante"), ("14e chambre correctionnelle", "quatorzième chambre correctionnelle"),
    ("3 h du matin", "trois heures du matin"), ("en 1515", "en quinze cent quinze"), ("en 1066", "en mille soixante-six"),
    ("1 900 kilos", "mille neuf cents kilos"), ("1 900 euros", "mille neuf cents euros"), ("10 000 000 km", "dix millions de kilomètres"),
    ("3,25 $", "trois dollars vingt-cinq"), ("1,5 million d'euros", "un million cinq cent mille euros"), ("années 80", "années quatre-vingt"),
    ("2e Empire", "Second Empire"), ("IIe Empire", "Second Empire"), ("AB-123-CD", "a bé, cent vingt-trois, cé dé"),
])
def test_the_spec_table(text, spoken):
    assert say(text) == spoken


@pytest.mark.parametrize("text,spoken", [
    ("1,99 €", "un euro quatre-vingt-dix-neuf"), ("19,99 €", "dix-neuf euros quatre-vingt-dix-neuf"), ("0,01 €", "un centime"), ("12,00 €", "douze euros"),
    ("€5", "cinq euros"), ("$1,200.50", "mille deux cents dollars cinquante"), ("5 £", "cinq livres sterling"), ("1 £", "une livre sterling"),
    ("2,5 M€", "deux millions cinq cent mille euros"), ("12,50 euros", "douze euros cinquante"), ("2,5 millions de dollars", "deux millions cinq cent mille dollars"),
    ("200 000 000 kilos", "deux cents millions de kilos"), ("3 000 000 habitants", "trois millions d'habitants"),
    ("1 000 000 de personnes", "un million de personnes"),
])
def test_money_and_big_numbers(text, spoken):
    assert say(text) == spoken


@pytest.mark.parametrize("text,spoken", [
    ("1.500 €", "mille cinq cents euros"), ("3.5", "trois virgule cinq"), ("1,200,000", "un million deux cent mille"), ("2007 100", "deux mille sept cent"),
    ("007", "zéro zéro sept"), ("0,125", "zéro virgule cent vingt-cinq"), ("3,14159", "trois virgule un quatre un cinq neuf"), ("35ans", "trente-cinq ans"),
    ("123456789012345678", "un deux trois quatre cinq six sept huit neuf zéro un deux trois quatre cinq six sept huit"),
])
def test_separators_and_odd_shapes(text, spoken):
    assert say(text) == spoken


@pytest.mark.parametrize("text,spoken", [
    ("en 1789", "en mille sept cent quatre-vingt-neuf"), ("en 2100", "en deux mille cent"), ("en 1200", "en douze cent"),
    ("novembre 2007", "novembre deux mille sept"), ("entre 1990 et 1995", "entre mille neuf cent quatre-vingt-dix et mille neuf cent quatre-vingt-quinze"),
    ("en 1500 exemplaires", "en mille cinq cents exemplaires"), ("21/03/1985", "vingt et un mars mille neuf cent quatre-vingt-cinq"),
    ("03/2021", "mars deux mille vingt et un"), ("2021-03-01", "premier mars deux mille vingt et un"),
    ("les années 2000", "les années deux mille"), ("dans les années 1920", "dans les années vingt"), ("les années 2010", "les années deux mille dix"),
    ("rue du 8 Mai 1945", "rue du huit Mai mille neuf cent quarante-cinq"),
])
def test_years_dates_decades(text, spoken):
    assert say(text) == spoken


@pytest.mark.parametrize("text,spoken", [
    ("1ère", "première"), ("22e", "vingt-deuxième"), ("3ème fois", "troisième fois"), ("2d", "second"), ("2e tour", "deuxième tour"),
    ("la 2e Guerre mondiale", "la Seconde Guerre mondiale"), ("le IIIe Reich", "le Troisième Reich"), ("le XXIe siècle", "le vingt et unième siècle"),
    ("Charles Ier", "Charles Premier"), ("Henri IV", "Henri Quatre"), ("Pie XII", "Pie Douze"), ("chapitre 1", "chapitre un"),
    ("article 1er", "article premier"), ("tome Ier", "tome premier"), ("acte III", "acte trois"), ("chapitre XII", "chapitre douze"),
    ("Vitamine C", "Vitamine C"), ("Washington DC", "Washington DC"), ("Le Monde", "Le Monde"), ("la Vie privée", "la Vie privée"),
    ("Paris 11e", "Paris onzième"),
])
def test_ordinals_and_roman_numerals(text, spoken):
    assert say(text) == spoken


@pytest.mark.parametrize("text,spoken", [
    ("1 h", "une heure"), ("0 h 15", "zéro heure quinze"), ("12 h 30", "midi trente"), ("10h30", "dix heures trente"), ("6 h 05", "six heures cinq"),
    ("21:07:15", "vingt et une heures sept minutes quinze secondes"), ("24 h", "minuit"), ("11:30", "onze heures trente"),
    ("75 kg", "soixante-quinze kilos"), ("1 m 80", "un mètre quatre-vingts"), ("1,75 m", "un mètre soixante-quinze"), ("35 °C", "trente-cinq degrés"),
    ("9mm", "neuf millimètres"), ("12km", "douze kilomètres"), ("1,5 km", "un virgule cinq kilomètre"), ("2,5 km", "deux virgule cinq kilomètres"),
    ("7,62 mm", "sept virgule soixante-deux millimètres"), ("calibre 12", "calibre douze"), ("un .22 LR", "un vingt-deux long rifle"), ("calibre .22 LR", "calibre vingt-deux long rifle"),
    ("un .357 Magnum", "un trois cent cinquante-sept Magnum"),
    ("1/3 de tonne", "un tiers de tonne"), ("2/3", "deux tiers"), ("1/2 litre", "un demi-litre"), ("1/2 heure", "une demi-heure"), ("½", "un demi"),
    ("5/12", "cinq sur douze"), ("1 ‰", "un pour mille"), ("+3 %", "plus trois pour cent"),
    ("Le score fut de 3-1", "Le score fut de trois à un"), ("5-10 ans", "cinq à dix ans"), ("de 5-10", "de cinq à dix"),
    ("nos 5", "nos cinq"), ("n°3", "numéro trois"), ("n°1 mondial", "numéro un mondial"),
])
def test_times_units_calibres_fractions_ranges(text, spoken):
    assert say(text) == spoken


@pytest.mark.parametrize("text,spoken", [
    ("le 15", "le quinze"), ("au 18", "au dix-huit"), ("le 114", "le cent quatorze"), ("le 115", "le cent quinze"), ("le 119", "le cent dix-neuf"),
    ("le 3977", "le trente-neuf soixante-dix-sept"), ("le 116 000", "le cent seize zéro zéro zéro"), ("le 116 117", "le cent seize, cent dix-sept"),
    ("0612345678", "zéro six, douze, trente-quatre, cinquante-six, soixante-dix-huit"), ("01.02.03.04.05", "zéro un, zéro deux, zéro trois, zéro quatre, zéro cinq"),
    ("+33 (0)6 12 34 56 78", "plus trente-trois, six, douze, trente-quatre, cinquante-six, soixante-dix-huit"),
    ("0033 6 12 34 56 78", "zéro zéro trente-trois, six, douze, trente-quatre, cinquante-six, soixante-dix-huit"),
    ("07 80 70 60 10", "zéro sept, quatre-vingts, soixante-dix, soixante, dix"),
    ("12 rue X, 75001 Paris", "douze rue X, soixante-quinze zéro zéro un Paris"), ("à 94700 Maisons", "à quatre-vingt-quatorze mille sept cents Maisons"),
    ("rue X, 94700 Maisons", "rue X, quatre-vingt-quatorze sept cents Maisons"), ("Le 17 mars", "Le dix-sept mars"),
])
def test_helplines_phones_and_postal_codes(text, spoken):
    assert say(text) == spoken


@pytest.mark.parametrize("text", [
    "3D", "4x4", "COVID-19", "A4", "MP3", "B-52", "H2O", "http://example.org/page12", "https://example.org/1", "mail a1@b2.com", "www.site.fr/2",
    "Aucun chiffre ici, seulement des mots.", "Dix sept", "Louis le Grand", "", "  ",
])
def test_protected_spans_and_text_without_numbers_stay_as_they_are(text):
    assert say(text) == text


def test_protected_spans_are_put_back_inside_a_sentence():
    assert say("Le 3D de 3 chats, voir http://a.fr/9 et COVID-19.") == "Le 3D de trois chats, voir http://a.fr/9 et COVID-19."


@pytest.mark.parametrize("text", [
    "Il avait 17 ans, 3 coups de feu, 25 ans de réclusion, 1 500 € et 12,50 €.", "Le 17 mars 2007 à 10 h 30, 06 12 34 56 78, n° 5.",
    "La 3e fois, le XXe siècle, 1/2, 3,5 %, 1990-1995, 120 km/h, 1,80 m, calibre .38.", "75001 Paris, 12 rue X, 3919, 0612345678.",
])
def test_no_digit_is_left_in_a_sentence_of_numbers(text):
    assert not any(ch.isdigit() for ch in say(text))


def test_years_follow_the_year_style_option():
    assert say("en 1999", year_style="hundreds") == "en dix-neuf cent quatre-vingt-dix-neuf"
    assert say("en 1515", year_style="mille") == "en mille cinq cent quinze"
    assert say("en 1900", year_style="hundreds") == "en dix-neuf cent"


@pytest.mark.parametrize("mode,text,spoken", [
    ("digits_in_context", "appelez le 17", "appelez le un sept"), ("digits_in_context", "le 17", "le dix-sept"),
    ("digits_always", "le 112", "le un un deux"), ("digits_always", "au 17 mars", "au dix-sept mars"), ("digits_always", "le 17 ans", "le dix-sept ans"),
    ("number_always", "appelez le 112", "appelez le cent douze"),
])
def test_emergency_numbers_option(mode, text, spoken):
    assert say(text, emergency_numbers=mode) == spoken


def test_other_options():
    assert say("5 $", dollar_singular="dollar américain", dollar_plural="dollars américains") == "cinq dollars américains"
    assert say("75 kg", mass_word="kilogramme") == "soixante-quinze kilogrammes"
    assert say("35 °C", say_celsius=True) == "trente-cinq degrés Celsius"
    assert say("10 h 30", time_style="colloquial") == "dix heures et demie"
    assert say("23 h 45", time_style="colloquial") == "minuit moins le quart"
    assert say("12 h 15", time_style="colloquial") == "midi et quart"
    assert say("0,125", three_decimals="digits") == "zéro virgule un deux cinq"
    assert say("2e Empire", second_in_fixed_phrases=False) == "deuxième Empire"
    assert say("Martin (35) a fui", age_in_parentheses=True) == "Martin (trente-cinq ans) a fui"
    assert say("Martin (35) a fui") == "Martin (trente-cinq) a fui"
    assert say(", 75001 Paris", postal_codes=False) == ", soixante-quinze mille un Paris"


def test_a_number_above_the_maximum_is_said_digit_by_digit():
    assert Numbers({}, 999).text("1234") == "un deux trois quatre"
    assert Numbers({}, 999).text("999") == "neuf cent quatre-vingt-dix-neuf"


def test_an_unknown_option_is_refused():
    with pytest.raises(ValueError):
        Numbers({"year_style": "roman"}, MAX)


@pytest.mark.parametrize("value,feminine,plural_end,spoken", [
    (0, False, True, "zéro"), (16, False, True, "seize"), (61, False, True, "soixante et un"), (61, True, True, "soixante et une"),
    (81, True, True, "quatre-vingt-une"), (97, False, True, "quatre-vingt-dix-sept"), (101, True, True, "cent une"), (121, False, True, "cent vingt et un"),
    (1001, False, True, "mille un"), (21000, False, True, "vingt et un mille"), (1900, False, False, "mille neuf cent"), (1900, False, True, "mille neuf cents"),
    (999999999999999, False, True, "neuf cent quatre-vingt-dix-neuf billions neuf cent quatre-vingt-dix-neuf milliards neuf cent quatre-vingt-dix-neuf "
                                   "millions neuf cent quatre-vingt-dix-neuf mille neuf cent quatre-vingt-dix-neuf"),
])
def test_cardinal_words(value, feminine, plural_end, spoken):
    assert W.cardinal(value, feminine, plural_end) == spoken


@pytest.mark.parametrize("value,spoken", [
    (1, "premier"), (2, "deuxième"), (4, "quatrième"), (5, "cinquième"), (9, "neuvième"), (11, "onzième"), (21, "vingt et unième"), (31, "trente et unième"),
    (71, "soixante et onzième"), (80, "quatre-vingtième"), (81, "quatre-vingt-unième"), (100, "centième"), (200, "deux centième"), (1000, "millième"),
    (2000000, "deux millionième"),
])
def test_ordinal_words(value, spoken):
    assert W.ordinal(value) == spoken


def test_ordinal_gender_and_number():
    assert W.ordinal(1, feminine=True) == "première"
    assert W.ordinal(3, plural=True) == "troisièmes"


@pytest.mark.parametrize("numeral,value", [("I", 1), ("IV", 4), ("XIV", 14), ("XXIII", 23), ("MCMXC", 1990), ("MMXXI", 2021)])
def test_roman_values(numeral, value):
    assert W.roman_value(numeral) == value


@pytest.mark.parametrize("text", ["", "IIII", "VX", "IC", "abc", "ii"])
def test_non_roman_text_has_no_value(text):
    assert W.roman_value(text) is None
