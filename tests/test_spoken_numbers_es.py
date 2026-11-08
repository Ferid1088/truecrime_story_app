"""Mexican Spanish number words for the voice (app/documentary/spoken_numbers/es.py). The cases are the test table of the es-MX research; where a row
of that table contradicted a verified rule or an owner decision the expected text here follows the rule (noted in the case id)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.documentary.spoken_numbers import reader_for
from app.documentary.spoken_numbers.es_speech import Options

MAX_VALUE = 999_999_999_999_999


def spoken(text: str, **options) -> str:
    return reader_for("es", options, MAX_VALUE).text(text)


CARDINALS = [
    ("21 mujeres", "veintiuna mujeres"),
    ("21 años", "veintiún años"),
    ("200 mujeres", "doscientas mujeres"),
    ("200 000 mujeres", "doscientas mil mujeres"),
    ("200 000 pesos", "doscientos mil pesos"),
    ("100 personas", "cien personas"),
    ("101 años", "ciento un años"),
    ("101 balas", "ciento una balas"),
    ("1,000", "mil"),
    ("21,000 dólares", "veintiún mil dólares"),
    ("31 mil mujeres", "treinta y un mil mujeres"),
    ("1,000,000 de personas", "un millón de personas"),
    ("2,500,000 personas", "dos millones quinientas mil personas"),
    ("2,000,000 personas", "dos millones de personas"),
    ("1,500,000", "un millón quinientos mil"),
    ("1,000,000,000", "mil millones"),
    ("1,000,000,000,000", "un billón"),
    ("100 000", "cien mil"),
    ("1,001", "mil uno"),
    ("tenía 21.", "tenía veintiuno."),
    ("1 hora", "una hora"),
    ("1 año", "un año"),
    ("5 millones de pesos", "cinco millones de pesos"),
    ("21 puñaladas", "veintiuna puñaladas"),
    ("-5", "menos cinco"),
    ("089", "cero ochenta y nueve"),
]
YEARS_AND_DATES = [
    ("2007", "dos mil siete"),
    ("1999", "mil novecientos noventa y nueve"),
    ("1800", "mil ochocientos"),
    ("2001", "dos mil uno"),
    ("13 de noviembre de 2007", "trece de noviembre de dos mil siete"),
    ("1.º de mayo", "primero de mayo"),
    ("el 1° de mayo", "el primero de mayo"),
    ("1ro de mayo", "primero de mayo"),
    ("el 3 de abril", "el tres de abril"),
    ("13/11/2007", "trece de noviembre de dos mil siete"),
    ("2007-11-13", "trece de noviembre de dos mil siete"),
    ("31 de diciembre de 1999", "treinta y uno de diciembre de mil novecientos noventa y nueve"),
    ("del 3 al 5 de mayo", "del tres al cinco de mayo"),
    ("calle 5 de Mayo", "calle Cinco de Mayo"),
    ("Av. 20 de Noviembre", "Av. Veinte de Noviembre"),
    ("los 90", "los noventa"),
    ("los años 90", "los años noventa"),
    ("los 1990", "los noventa"),
    ("los 90's", "los noventa"),
    ("los 90s", "los noventa"),
    ("los 2000", "los dos mil"),
]
ORDINALS = [
    ("3.er lugar", "tercer lugar"),
    ("1.ª vez", "primera vez"),
    ("1.er piso", "primer piso"),
    ("5.º piso", "quinto piso"),
    ("2.ª edición", "segunda edición"),
    ("1.os lugares", "primeros lugares"),
    ("3ra vez", "tercera vez"),
    ("5to lugar", "quinto lugar"),
    ("2do piso", "segundo piso"),
    ("10.º lugar", "décimo lugar"),
    ("21.º aniversario", "vigésimo primer aniversario"),
    ("la 21.ª edición", "la vigésima primera edición"),
    ("artículo 1.º", "artículo primero"),
    ("el piso 15", "el piso quince"),
    ("artículo 14", "artículo catorce"),
    ("piso 3", "piso tres"),
    ("el 3.º y el 4.º", "el tercero y el cuarto"),
    ("31.º", "treinta y uno"),
    ("90°", "noventa grados"),
    ("1° piso", "primer piso"),
    ("N.º 5", "número cinco"),
    ("núm. 12", "número doce"),
    ("No. 7", "número siete"),
    ("#5", "número cinco"),
]
ROMANS = [
    ("siglo XX", "siglo veinte"),
    ("siglo XIX", "siglo diecinueve"),
    ("siglo V", "siglo quinto"),
    ("siglos XV y XVI", "siglos quince y dieciséis"),
    ("Juan Pablo II", "Juan Pablo segundo"),
    ("Luis XIV", "Luis catorce"),
    ("Juan XXIII", "Juan veintitrés"),
    ("Carlos V", "Carlos quinto"),
    ("Isabel II", "Isabel segunda"),
    ("León XIV", "León catorce"),
    ("capítulo IV", "capítulo cuatro"),
    ("Tomo II", "Tomo dos"),
    ("Super Bowl LVIII", "Super Bowl cincuenta y ocho"),
    ("Mario V. Guerrero", "Mario V. Guerrero"),
    ("vitamina C", "vitamina C"),
    ("rayos X", "rayos X"),
    ("Malcolm X", "Malcolm X"),
]
MONEY = [
    ("$500", "quinientos pesos"),
    ("$1,250.50", "mil doscientos cincuenta pesos con cincuenta centavos"),
    ("$1,250.00", "mil doscientos cincuenta pesos"),
    ("$1", "un peso"),
    ("$21", "veintiún pesos"),
    ("$0.50", "cincuenta centavos"),
    ("$500.00 M.N.", "quinientos pesos"),
    ("US$100", "cien dólares"),
    ("US$1", "un dólar"),
    ("$500 dólares", "quinientos dólares"),
    ("500 USD", "quinientos dólares"),
    ("MXN$500", "quinientos pesos mexicanos"),
    ("€50", "cincuenta euros"),
    ("€1.50", "un euro con cincuenta céntimos"),
    ("£500", "quinientas libras"),
    ("$5 millones", "cinco millones de pesos"),
    ("$1,000,000", "un millón de pesos"),
    ("$21,000", "veintiún mil pesos"),
    ("$100 mil", "cien mil pesos"),
    ("$3 mil millones", "tres mil millones de pesos"),
    ("$2.5 millones", "dos millones y medio de pesos"),
    ("1.5 millones de pesos", "un millón y medio de pesos"),
    ("2.3 mil millones de dólares", "dos mil trescientos millones de dólares"),
    ("200 mil libras", "doscientas mil libras"),
    ("500 mdp", "quinientos millones de pesos"),
]
CODES = [
    ("llamó al 911", "llamó al nueve uno uno"),
    ("marcó el 911 y colgó", "marcó el nueve uno uno y colgó"),
    ("el número 911", "el número nueve uno uno"),
    ("3 llamadas al 911", "tres llamadas al nueve uno uno"),
    ("línea 089", "línea cero ocho nueve"),
    ("la denuncia anónima al 089", "la denuncia anónima al cero ocho nueve"),
    ("911", "novecientos once"),
    ("Hay 911 casos", "Hay novecientos once casos"),
    ("55 1234 5678", "cinco cinco, uno dos tres cuatro, cinco seis siete ocho"),
    ("(55) 1234-5678", "cinco cinco, uno dos tres cuatro, cinco seis siete ocho"),
    ("+52 55 1234 5678", "más cincuenta y dos, cinco cinco, uno dos tres cuatro, cinco seis siete ocho"),
    ("01 800 123 4567", "cero uno, ocho cero cero, uno dos tres, cuatro cinco seis siete"),
    ("Tel. 5512345678", "Tel. cinco cinco, uno dos tres cuatro, cinco seis siete ocho"),
    ("C.P. 06600", "C.P. cero seis seis cero cero"),
    ("5512345678", "cinco mil quinientos doce millones trescientos cuarenta y cinco mil seiscientos setenta y ocho"),
]
CLOCK = [
    ("10:30 a. m. llegó", "las diez y media de la mañana llegó"),
    ("3 p. m. llegó", "las tres de la tarde llegó"),
    ("1:00 a. m. llegó", "la una de la madrugada llegó"),
    ("a las 3:15 a. m. llegó", "a las tres y cuarto de la madrugada llegó"),
    ("23:45", "las once cuarenta y cinco de la noche"),
    ("23:45 horas", "veintitrés cuarenta y cinco horas"),
    ("12:30 p. m. y", "las doce y media del día y"),
    ("00:15", "las doce y cuarto de la madrugada"),
    ("8:05", "las ocho cero cinco de la mañana"),
    ("a la 1 de la tarde", "a la una de la tarde"),
    ("a las 3 de la tarde", "a las tres de la tarde"),
    ("a las 3:15 de la tarde", "a las tres y cuarto de la tarde"),
]
MEASURES = [
    ("de 35 años", "de treinta y cinco años"),
    ("la víctima de 23 años", "la víctima de veintitrés años"),
    ("3.5", "tres punto cinco"),
    ("0.05", "cero punto cero cinco"),
    ("1,250.75", "mil doscientos cincuenta punto setenta y cinco"),
    ("3.5 %", "tres punto cinco por ciento"),
    ("100 %", "cien por ciento"),
    ("1 %", "uno por ciento"),
    ("1990-1995", "de mil novecientos noventa a mil novecientos noventa y cinco"),
    ("entre 2019-2020", "entre dos mil diecinueve y dos mil veinte"),
    ("1990-95", "de mil novecientos noventa a noventa y cinco"),
    ("de 5-10 años", "de cinco a diez años"),
    ("5-10 años", "de cinco a diez años"),
    ("ganó 3-1", "ganó tres a uno"),
    ("3:1", "tres a uno"),
    ("2-2", "dos a dos"),
    ("3 x 5", "tres por cinco"),
    ("9 x 19", "nueve por diecinueve"),
    ("24/7", "veinticuatro siete"),
    ("1/2 kilo", "medio kilo"),
    ("1/2 hora", "media hora"),
    ("3/4", "tres cuartos"),
    ("1/3", "un tercio"),
    ("7/12", "siete doceavos"),
    ("2 1/2 horas", "dos horas y media"),
    ("½ kilo", "medio kilo"),
    ("120 km/h", "ciento veinte kilómetros por hora"),
    ("120km/h", "ciento veinte kilómetros por hora"),
    ("35 °C", "treinta y cinco grados centígrados"),
    ("-5 °C", "menos cinco grados centígrados"),
    ("1 °C", "un grado centígrados"),
    ("2.5 km", "dos punto cinco kilómetros"),
    ("1 km", "un kilómetro"),
    ("21 hectáreas", "veintiuna hectáreas"),
    ("21 ha", "veintiuna hectáreas"),
    ("70 kg", "setenta kilos"),
    ("2 h 30 min", "dos horas con treinta minutos"),
    ("19°25′42″N", "diecinueve grados, veinticinco minutos, cuarenta y dos segundos latitud norte"),
    ("calibre .38", "calibre treinta y ocho"),
    ("calibre .45", "calibre cuarenta y cinco"),
    ("calibre 12", "calibre doce"),
    ("una .45", "una cuarenta y cinco"),
    ("9 mm", "nueve milímetros"),
    ("calibre 9 mm", "calibre nueve milímetros"),
    ("7.62 x 39", "siete punto sesenta y dos por treinta y nueve"),
    ("número 1234", "número mil doscientos treinta y cuatro"),
    ("45 años de prisión", "cuarenta y cinco años de prisión"),
    ("2 disparos", "dos disparos"),
    ("1.250 casos", "mil doscientos cincuenta casos"),
]


@pytest.mark.parametrize("text, expected", CARDINALS + YEARS_AND_DATES + ORDINALS + ROMANS + MONEY + CODES + CLOCK + MEASURES)
def test_mexican_spanish_numbers(text, expected):
    assert spoken(text) == expected


@pytest.mark.parametrize("text", ["COVID-19", "B-52", "AK-47", "F-16", "mp3", "3D", "5G", "A1", "4x4", "iPhone15", "https://ejemplo.com/2019",
                                  "escribe a a1@x.com"])
def test_codes_and_addresses_are_kept_as_written(text):
    assert spoken(text) == text


def test_protected_span_next_to_numbers():
    assert spoken("El AK-47 y 3 balas en el 4x4 de 2 mp3") == "El AK-47 y tres balas en el 4x4 de dos mp3"


@pytest.mark.parametrize("text", ["", "Sin números aquí.", "dos disparos", "Mario V. Guerrero llegó", "Tomás y María", "¿Quién?"])
def test_text_without_numbers_is_unchanged(text):
    assert spoken(text) == text


def test_a_numbers_text_leaves_no_digits():
    text = "El 3 de abril de 2019, a las 11:45 p. m., Ana (23) recibió 2 disparos de calibre .38; la fianza fue de $2.5 millones."
    assert not any(char.isdigit() for char in spoken(text))


def test_the_closing_period_of_an_abbreviated_day_part_is_kept_at_the_end_of_a_sentence():
    assert spoken("Llegó a las 3 p. m.") == "Llegó a las tres de la tarde."
    assert spoken("Llegó a las 3 p. m. y salió") == "Llegó a las tres de la tarde y salió"


def test_a_date_inside_a_sentence():
    assert (spoken("La víctima, de 23 años, recibió 2 disparos a las 11:45 p. m. del 3 de abril de 2019.")
            == "La víctima, de veintitrés años, recibió dos disparos a las once cuarenta y cinco de la noche del tres de abril de dos mil diecinueve.")


@pytest.mark.parametrize("text, expected", [
    ("3,5", "tres punto cinco"),
    ("1.250", "mil doscientos cincuenta"),
    ("1.250.000", "un millón doscientos cincuenta mil"),
    ("1.250,50", "mil doscientos cincuenta punto cincuenta"),
    ("1 250 000", "un millón doscientos cincuenta mil"),
    ("1.250 km", "uno punto dos cinco cero kilómetros"),
    ("2.0.1", "dos punto cero punto uno"),
    ("3.14159", "tres punto uno cuatro uno cinco nueve"),
])
def test_separators_follow_mexican_usage(text, expected):
    assert spoken(text) == expected


def test_digits_above_the_voice_limit_are_read_one_by_one():
    assert reader_for("es", {}, 999).text("1000 y 999") == "uno cero cero cero y novecientos noventa y nueve"


class TestOptions:
    def test_emergency_numbers_digits_always(self):
        assert spoken("el 911 y 089", emergency_numbers="digits_always") == "el nueve uno uno y cero ocho nueve"

    def test_emergency_numbers_as_numbers_always(self):
        assert spoken("llamó al 911", emergency_numbers="number_always") == "llamó al novecientos once"

    def test_emergency_number_followed_by_a_counted_noun_is_a_count(self):
        assert spoken("llamó por 911 pesos") == "llamó por novecientos once pesos"

    def test_dollar_sign_as_dollars(self):
        assert spoken("$500", dollar_sign_currency="dolar") == "quinientos dólares"

    def test_feminine_one_before_thousand(self):
        assert spoken("21 mil mujeres", feminine_one_before_thousand=True) == "veintiuna mil mujeres"

    def test_masculine_hundreds_before_thousand(self):
        assert spoken("200 mil mujeres", feminine_hundreds_before_thousand=False) == "doscientos mil mujeres"

    def test_first_of_month_as_cardinal(self):
        assert spoken("1 de mayo", first_of_month_ordinal=False) == "uno de mayo"

    def test_marked_ordinals_up_to(self):
        assert spoken("11.º lugar", marked_ordinals_up_to=10) == "once lugar"

    def test_centuries_as_cardinals(self):
        assert spoken("siglo V", ordinal_centuries_up_to_ten=False) == "siglo cinco"

    def test_half_million_as_decimal(self):
        assert spoken("2.5 millones", half_million_as_y_medio=False) == "dos punto cinco millones"

    def test_phone_in_pairs(self):
        assert spoken("55 1234 5678", phone_digits_in_pairs=True) == "cincuenta y cinco, doce treinta y cuatro, cincuenta y seis setenta y ocho"

    def test_decimal_word(self):
        assert spoken("3.5", decimal_word="coma") == "tres coma cinco"

    def test_euro_cents_as_centavos(self):
        assert spoken("€1.50", euro_cents="centavos") == "un euro con cincuenta centavos"

    def test_time_without_day_part(self):
        assert spoken("23:45", time_with_daypart=False) == "las veintitrés cuarenta y cinco"

    def test_unknown_option_is_refused(self):
        with pytest.raises(ValidationError):
            Options.model_validate({"nonsense": True})

    def test_defaults(self):
        options = Options()
        assert (options.emergency_numbers, options.dollar_sign_currency, options.feminine_one_before_thousand,
                options.decimal_word) == ("digits_in_context", "peso", False, "punto")


def test_a_date_that_ends_a_sentence_keeps_its_period_and_only_an_abbreviated_month_owns_its_own():
    assert spoken("Fue el 15 de febrero. Luego volvió.") == "Fue el quince de febrero. Luego volvió."
    assert spoken("Ocurrió el domingo 15 de febrero.") == "Ocurrió el domingo quince de febrero."
    assert spoken("el 15 de feb. de 2024") == "el quince de febrero de dos mil veinticuatro"
