"""Turkish numbers for the voice: the test table of scratchpad/numbers/tr.md (rows marked UNSURE are left out), the suffix algorithm and the options.
Divergences from the table: row 82 ("23.45") needs "saat" or an ending, else it is a decimal (the spec's own context rule); row 66 keeps "Alo";
digit ordinals before a capitalised word are capitalised like the roman ones (row 53, and "14. Asliye" gives "On dördüncü Asliye")."""

import pytest

from app.documentary.spoken_numbers import reader_for
from app.documentary.spoken_numbers.tr import Numbers
from app.documentary.spoken_numbers.tr_suffix import apply, parse

MAX_VALUE = 999_999_999_999_999


def say(text: str, **options) -> str:
    return reader_for("tr", options, MAX_VALUE).text(text)


CARDINALS = [
    ("911", "dokuz yüz on bir"), ("1000", "bin"), ("1.000", "bin"), ("1.001", "bin bir"), ("1.100", "bin yüz"), ("100", "yüz"), ("101", "yüz bir"),
    ("111", "yüz on bir"), ("12.345", "on iki bin üç yüz kırk beş"), ("1.000.000", "bir milyon"), ("2.500.000", "iki milyon beş yüz bin"),
    ("1.000.000.000", "bir milyar"), ("3.000.000.000.000", "üç trilyon"), ("0", "sıfır"), ("2007", "iki bin yedi"), ("2026", "iki bin yirmi altı"),
    ("1990", "bin dokuz yüz doksan"), ("1.234.567", "bir milyon iki yüz otuz dört bin beş yüz altmış yedi"),
]
SUFFIXES = [
    ("2007'de", "iki bin yedide"), ("2023'te", "iki bin yirmi üçte"), ("2024'te", "iki bin yirmi dörtte"), ("2004'e", "iki bin dörde"),
    ("1914'te", "bin dokuz yüz on dörtte"), ("1999'da", "bin dokuz yüz doksan dokuzda"), ("2000'de", "iki binde"), ("1900'de", "bin dokuz yüzde"),
    ("911'i", "dokuz yüz on biri"), ("3'ü", "üçü"), ("4'e", "dörde"), ("40'a", "kırka"), ("100'e", "yüze"), ("6'yı", "altıyı"), ("6'sı", "altısı"),
    ("5'e", "beşe"), ("10'a", "ona"), ("10'un", "onun"), ("7'yle", "yediyle"), ("5'le", "beşle"), ("3'ten", "üçten"), ("4'ten", "dörtten"),
    ("40'tan", "kırktan"), ("50'den", "elliden"), ("5'inde", "beşinde"), ("4'ünde", "dördünde"), ("7'sinde", "yedisinde"), ("8'de", "sekizde"),
    ("9'u", "dokuzu"), ("12'si", "on ikisi"), ("1990'lar", "bin dokuz yüz doksanlar"), ("1980'lerde", "bin dokuz yüz seksenlerde"),
    ("1990'lı yıllar", "bin dokuz yüz doksanlı yıllar"), ("'90'lar", "doksanlar"), ("5'lik", "beşlik"), ("4'lük", "dörtlük"), ("100'lük", "yüzlük"),
    ("50'lik", "ellilik"), ("45'inci", "kırk beşinci"), ("3'üncü", "üçüncü"), ("4'üncü", "dördüncü"), ("6'ncı", "altıncı"), ("2'nci", "ikinci"),
    ("1000'e", "bine"), ("1.000.000'a", "bir milyona"), ("1,5'u", "bir buçuğu"), ("1,5'a", "bir buçuğa"), ("2'yi", "ikiyi"), ("3'te", "üçte"),
    ("5’e", "beşe"),
]
ORDINALS_AND_ROMAN = [
    ("3. kat", "üçüncü kat"), ("1. Dünya Savaşı", "Birinci Dünya Savaşı"), ("21. yüzyıl", "yirmi birinci yüzyıl"), ("100. yıl", "yüzüncü yıl"),
    ("II. Dünya Savaşı", "İkinci Dünya Savaşı"), ("XX. yüzyıl", "yirminci yüzyıl"), ("XXI. yüzyıl", "yirmi birinci yüzyıl"),
    ("II. Abdülhamid", "İkinci Abdülhamid"), ("I. Mahmut", "Birinci Mahmut"), ("IV. Murat", "Dördüncü Murat"), ("XV. yüzyıl", "on beşinci yüzyıl"),
    ("20. yy.", "yirminci yüzyıl"), ("XX'nci yüzyıl", "yirminci yüzyıl"), ("14. Asliye Ceza", "On dördüncü Asliye Ceza"),
    ("Olay 2007.", "Olay iki bin yedi."), ("Ömer 25 yaşında. Sonra", "Ömer yirmi beş yaşında. Sonra"), ("13. Kasım 2007", "on üç. Kasım iki bin yedi"),
]
MONEY_PERCENT_FRACTIONS = [
    ("500 TL", "beş yüz lira"), ("₺500", "beş yüz lira"), ("12,50 TL", "on iki lira elli kuruş"), ("12,5 TL", "on iki lira elli kuruş"),
    ("12,05 TL", "on iki lira beş kuruş"), ("0,75 TL", "yetmiş beş kuruş"), ("1.250,50 ₺", "bin iki yüz elli lira elli kuruş"),
    ("5 milyon dolar", "beş milyon dolar"), ("10 bin avro", "on bin avro"), ("500 TL'yi", "beş yüz lirayı"), ("500 TL'den", "beş yüz liradan"),
    ("100 TL'ye", "yüz liraya"), ("10 bin TL'lik", "on bin liralık"), ("$12.50", "on iki dolar elli sent"), ("€3,20", "üç avro yirmi sent"),
    ("$1.250", "bin iki yüz elli dolar"), ("$1,250.50", "bin iki yüz elli dolar elli sent"), ("1,5 milyar TL", "bir buçuk milyar lira"),
    ("2 milyon 500 bin TL", "iki milyon beş yüz bin lira"), ("%50", "yüzde elli"), ("50%", "yüzde elli"), ("%100", "yüzde yüz"),
    ("%50'si", "yüzde ellisi"), ("%12,5", "yüzde on iki virgül beş"), ("% 5'e", "yüzde beşe"), ("1/3", "üçte bir"), ("3/4", "dörtte üç"),
    ("3/4'ü", "dörtte üçü"), ("2/3'ü", "üçte ikisi"), ("1/2", "yarım"), ("1 1/2", "bir buçuk"), ("3 1/3", "üç tam üçte bir"), ("1/10", "onda bir"),
    ("5/8", "sekizde beş"), ("24/7", "yirmi dört yedi"), ("7/24", "yedi yirmi dört"), ("7/3", "yedi bölü üç"),
]
DECIMALS_TIMES_UNITS = [
    ("1,5 milyon", "bir buçuk milyon"), ("2,5 milyon", "iki buçuk milyon"), ("2,5 saat", "iki buçuk saat"), ("0,5 litre", "yarım litre"),
    ("3,14", "üç virgül on dört"), ("0,05", "sıfır virgül sıfır beş"), ("2,75", "iki virgül yetmiş beş"), ("3,142", "üç virgül bir dört iki"),
    ("3.5", "üç buçuk"), ("saat 3'te", "saat üçte"), ("saat 15.00'te", "saat on beşte"), ("saat 22.30'da", "saat yirmi iki buçukta"),
    ("saat 23.45'te", "saat yirmi üç kırk beşte"), ("saat 23.45", "saat yirmi üç kırk beş"), ("10:30", "on buçuk"), ("10:00", "on"),
    ("10:15", "on on beş"), ("10:05", "on sıfır beş"), ("09:30", "dokuz buçuk"), ("35 yaşında", "otuz beş yaşında"),
    ("13 Kasım 2007", "on üç Kasım iki bin yedi"), ("13.11.2007", "on üç Kasım iki bin yedi"), ("13/11/2007", "on üç Kasım iki bin yedi"),
    ("13 Kasım 2007'de", "on üç Kasım iki bin yedide"), ("25 yıl hapis", "yirmi beş yıl hapis"), ("2 el ateş", "iki el ateş"),
    ("9 mm", "dokuz milimetre"), ("9 mm'lik", "dokuz milimetrelik"), ("7,62 mm", "yedi virgül altmış iki milimetre"), ("5 km'lik", "beş kilometrelik"),
    ("10 km'yi", "on kilometreyi"), ("100 km/s", "saatte yüz kilometre"), ("-5 °C", "eksi beş derece"), ("12 kg'ı", "on iki kilogramı"),
    ("45 dk", "kırk beş dakika"), ("30 sn", "otuz saniye"), ("1990-1995 arasında", "bin dokuz yüz doksan ile bin dokuz yüz doksan beş arasında"),
    ("5-10 yıl", "beş ila on yıl"), ("1914-18", "bin dokuz yüz on dört ila bin dokuz yüz on sekiz"), ("2-1", "iki bir"),
    ("Atatürk Caddesi No: 12", "Atatürk Caddesi numara on iki"), ("No: 12'de", "numara on ikide"), ("No: 12/3", "numara on iki bölü üç"),
    ("Kat: 3", "kat üç"), ("2019/1234 Esas", "iki bin on dokuz bölü bin iki yüz otuz dört Esas"), ("-5", "eksi beş"), ("+5", "artı beş"),
    (".38 kalibre", "otuz sekiz kalibre"), (".38", "otuz sekiz kalibre"), (".357 Magnum", "üç yüz elli yedi Magnum"), (".45'lik", "kırk beşlik"),
    ("1234. Sokak", "Bin iki yüz otuz dördüncü Sokak"),
]
CODES = [
    ("Alo 112", "Alo bir bir iki"), ("112'yi aradı", "bir bir ikiyi aradı"), ("155'e telefon etti", "bir beş beşe telefon etti"),
    ("112 Acil Çağrı Merkezi", "bir bir iki Acil Çağrı Merkezi"), ("110 numaralı hat", "bir bir sıfır numaralı hat"),
    ("155 kişi yaralandı", "yüz elli beş kişi yaralandı"), ("0 532 123 45 67", "sıfır beş üç iki, bir iki üç, kırk beş, altmış yedi"),
    ("+90 212 345 67 89", "artı doksan iki bir iki, üç dört beş, altmış yedi, seksen dokuz"), ("34 ABC 123", "otuz dört a be ce yüz yirmi üç"),
    ("06 AB 1234", "sıfır altı a be bin iki yüz otuz dört"), ("12345678901", "bir iki üç dört beş altı yedi sekiz dokuz sıfır bir"),
    ("007", "sıfır sıfır yedi"), ("1234567890", "bir milyar iki yüz otuz dört milyon beş yüz altmış yedi bin sekiz yüz doksan"),
]


@pytest.mark.parametrize("text, spoken", [
    *CARDINALS, *SUFFIXES, *ORDINALS_AND_ROMAN, *MONEY_PERCENT_FRACTIONS, *DECIMALS_TIMES_UNITS, *CODES])
def test_turkish_number_reading(text, spoken):
    assert say(text) == spoken


@pytest.mark.parametrize("text", [
    "Kahvaltıda ekmek yedi.", "Merhaba dünya", "Ankara'da yaşadı", "Bay X. geldi", "M. Kemal geldi", "Mustafa C. Yılmaz", "I. dönem yoktu"])
def test_text_without_numbers_stays_the_same(text):
    assert say(text) == {"I. dönem yoktu": "birinci dönem yoktu"}.get(text, text)


@pytest.mark.parametrize("text", [
    "https://example.com/5/3", "yaz@site2.com", "A4 kağıdı", "3D model", "4x4"])
def test_protected_spans_are_kept(text):
    assert say(text) == text


def test_protected_span_next_to_a_number():
    assert say("5 kişi A4 aldı") == "beş kişi A4 aldı"


def test_no_digit_is_left_after_a_number():
    text = "Olay 13.11.2007'de saat 22.30'da, 3. katta, 2,5 milyon TL için; 112'yi aradı; tel 0 532 123 45 67; %50'si; 9 mm; 1/3; 2-1; 3.5; 1.000.000"
    assert not any(char.isdigit() for char in say(text))


def test_number_over_the_maximum_is_read_digit_by_digit():
    assert reader_for("tr", {}, 999).text("1000") == "bir sıfır sıfır sıfır"


@pytest.mark.parametrize("word, suffix, expected", [
    ("üç", "ünde", "üçünde"), ("dört", "ünde", "dördünde"), ("dört", "te", "dörtte"), ("dört", "lük", "dörtlük"), ("altı", "ya", "altıya"),
    ("yedi", "nci", "yedinci"), ("iki", "nin", "ikinin"), ("elli", "yi", "elliyi"), ("sıfır", "a", "sıfıra"), ("yüz", "de", "yüzde"),
    ("beş", "ten", "beşten"), ("altmış", "ta", "altmışta"), ("kırk", "ıncı", "kırkıncı"), ("kırk", "ta", "kırkta"), ("lira", "lık", "liralık"),
    ("kuruş", "ta", "kuruşta"), ("buçuk", "ta", "buçukta"), ("buçuk", "u", "buçuğu"), ("milyon", "uncu", "milyonuncu"), ("bin", "inci", "bininci"),
    ("dokuz", "da", "dokuzda"), ("doksan", "lar", "doksanlar"), ("yirmi", "le", "yirmiyle"), ("üç", "ler", "üçler"), ("üç", "lü", "üçlü"),
    ("iki", "sinden", "ikisinden"), ("beş", "inin", "beşinin"), ("altı", "ncı", "altıncı"),
])
def test_suffix_is_built_from_the_last_spoken_word(word, suffix, expected):
    assert apply([word], suffix) == [expected]


def test_suffix_only_changes_the_last_word():
    assert apply(["iki", "bin", "yedi"], "de") == ["iki", "bin", "yedide"]


def test_a_suffix_that_is_not_an_ending_is_dropped_and_the_number_stays():
    assert parse("xyz") is None
    assert say("5'xyz") == "beş"


@pytest.mark.parametrize("options, text, spoken", [
    ({"emergency_numbers": "digits_always"}, "155 kişi", "bir beş beş kişi"),
    ({"emergency_numbers": "number_always"}, "Alo 112", "Alo yüz on iki"),
    ({"phone_numbers": "digits"}, "0 532 123 45 67", "sıfır beş üç iki, bir iki üç, dört beş, altı yedi"),
    ({"decades": "short"}, "1990'larda", "doksanlarda"), ({"decades": "short"}, "2000'ler", "iki binler"),
    ({"euro_name": "euro"}, "5 €", "beş euro"), ({"half_as_bucuk": False}, "2,5 saat", "iki virgül beş saat"),
    ({"time_half_as_bucuk": False}, "10:30", "on otuz"), ({"money_with_ve": True}, "26,56 TL", "yirmi altı lira ve elli altı kuruş"),
    ({"range_word": "ile"}, "5-10 yıl", "beş ile on yıl"), ({"code_digits_from": 9}, "123456789", "bir iki üç dört beş altı yedi sekiz dokuz"),
])
def test_options_change_the_reading(options, text, spoken):
    assert say(text, **options) == spoken


def test_default_reader_is_the_turkish_numbers_class():
    assert isinstance(reader_for("tr", {}, MAX_VALUE), Numbers)
