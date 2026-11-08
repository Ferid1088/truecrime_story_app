"""Brazilian Portuguese number reading: every row of the researched rule table (scratchpad numbers/pt.md) that is not marked UNSURE, plus the
options the owner can flip, the protected spans and the text that must stay as it is."""

import pytest

from app.documentary.spoken_numbers import reader_for
from app.documentary.spoken_numbers.pt import Numbers, Options
from app.documentary.spoken_numbers.pt_gender import feminine_after
from app.documentary.spoken_numbers.pt_words import cardinal, ordinal, roman_value

MAX_VALUE = 999_999_999_999_999


def say(text: str, **options) -> str:
    return Numbers(options, MAX_VALUE).text(text)


CARDINALS = [
    ("1.100 pessoas", "mil e cem pessoas"),
    ("1.234", "mil duzentos e trinta e quatro"),
    ("1.234 pessoas", "mil duzentas e trinta e quatro pessoas"),
    ("2.007", "dois mil e sete"),
    ("1.500", "mil e quinhentos"),
    ("101", "cento e um"),
    ("100 pessoas", "cem pessoas"),
    ("100.000", "cem mil"),
    ("1.000.000 de pessoas", "um milhão de pessoas"),
    ("5.000.000 de pessoas", "cinco milhões de pessoas"),
    ("5.000.000 pessoas", "cinco milhões de pessoas"),
    ("2.500.000", "dois milhões e quinhentos mil"),
    ("2.500.000 pessoas", "dois milhões e quinhentas mil pessoas"),
    ("1,5 milhão de pessoas", "um milhão e meio de pessoas"),
    ("2,5 milhões de pessoas", "dois milhões e meio de pessoas"),
    ("21 mulheres", "vinte e uma mulheres"),
    ("21 homens", "vinte e um homens"),
    ("2 mulheres", "duas mulheres"),
    ("2 policiais militares", "dois policiais militares"),
    ("200 pessoas", "duzentas pessoas"),
    ("200 homens", "duzentos homens"),
    ("2.000 mulheres", "duas mil mulheres"),
    ("21.000 mulheres", "vinte e uma mil mulheres"),
    ("201.000 mulheres", "duzentas e uma mil mulheres"),
    ("1.002.000 mulheres", "um milhão e duas mil mulheres"),
    ("3 mil pessoas", "três mil pessoas"),
    ("2 mil mulheres", "duas mil mulheres"),
    ("100 mil", "cem mil"),
    ("1.000.000.000 de reais", "um bilhão de reais"),
    ("16, 17 e 19 anos", "dezesseis, dezessete e dezenove anos"),
    ("1.000", "mil"),
    ("1.200.300", "um milhão duzentos mil e trezentos"),
    ("1.000.001", "um milhão e um"),
    ("3.050", "três mil e cinquenta"),
    ("1.234.567", "um milhão duzentos e trinta e quatro mil quinhentos e sessenta e sete"),
    ("1.101", "mil cento e um"),
    ("2.000.100", "dois milhões e cem"),
    ("1.001.000", "um milhão e mil"),
    ("100.001", "cem mil e um"),
    ("24 horas", "vinte e quatro horas"),
    ("2 horas", "duas horas"),
    ("2 grandes casas", "duas grandes casas"),
    ("2 dias", "dois dias"),
    ("Ela tinha 1 filho e 2 filhas", "Ela tinha um filho e duas filhas"),
    ("Av. Paulista, 1578", "Av. Paulista, mil quinhentos e setenta e oito"),
    ("apto 301", "apto trezentos e um"),
]

DATES = [
    ("13 de novembro de 2007", "treze de novembro de dois mil e sete"),
    ("1º de maio de 1999", "primeiro de maio de mil novecentos e noventa e nove"),
    ("1 de maio", "primeiro de maio"),
    ("2 de março", "dois de março"),
    ("dia 1", "dia primeiro"),
    ("dia 21", "dia vinte e um"),
    ("13/11/2007", "treze de novembro de dois mil e sete"),
    ("01/05/1999", "primeiro de maio de mil novecentos e noventa e nove"),
    ("novembro de 2007", "novembro de dois mil e sete"),
    ("em 1999", "em mil novecentos e noventa e nove"),
    ("nos anos 90", "nos anos noventa"),
    ("nos anos 1990", "nos anos noventa"),
    ("década de 1980", "década de oitenta"),
    ("anos 2000", "anos dois mil"),
    ("2010", "dois mil e dez"),
    ("2000", "dois mil"),
    ("1980", "mil novecentos e oitenta"),
    ("9/11", "onze de setembro"),
    ("em 13 de novembro de 2007, aos 35 anos", "em treze de novembro de dois mil e sete, aos trinta e cinco anos"),
]

ORDINALS = [
    ("1º lugar", "primeiro lugar"),
    ("1ª vez", "primeira vez"),
    ("10º andar", "décimo andar"),
    ("15º andar", "décimo quinto andar"),
    ("21º Batalhão", "vigésimo primeiro Batalhão"),
    ("12ª Vara Criminal", "décima segunda Vara Criminal"),
    ("3ª Turma", "terceira Turma"),
    ("2ª Guerra Mundial", "segunda Guerra Mundial"),
    ("1.º lugar", "primeiro lugar"),
    ("1ºs", "primeiros"),
    ("2ªs", "segundas"),
    ("1o lugar", "primeiro lugar"),
    ("andar 15", "andar quinze"),
    ("página 5", "página cinco"),
    ("artigo 5º", "artigo quinto"),
    ("artigo 9º", "artigo nono"),
    ("artigo 12", "artigo doze"),
    ("artigo 121", "artigo cento e vinte e um"),
    ("art. 157, § 3º", "artigo cento e cinquenta e sete, parágrafo terceiro"),
    ("§ 10", "parágrafo dez"),
    ("121º DP", "cento e vinte e um DP"),
    ("121ª DP", "cento e vinte e uma DP"),
    ("100º", "centésimo"),
]

ROMANS = [
    ("século XX", "século vinte"),
    ("século XXI", "século vinte e um"),
    ("século V", "século quinto"),
    ("século X", "século décimo"),
    ("século XV", "século quinze"),
    ("Dom Pedro II", "Dom Pedro segundo"),
    ("Dom Pedro I", "Dom Pedro primeiro"),
    ("Pio XII", "Pio doze"),
    ("Pio X", "Pio décimo"),
    ("João Paulo II", "João Paulo segundo"),
    ("Bento XVI", "Bento dezesseis"),
    ("João XXIII", "João vinte e três"),
    ("Luís XIV", "Luís catorze"),
    ("Luís XV", "Luís quinze"),
    ("Elizabeth II", "Elizabeth segunda"),
    ("Maria I", "Maria primeira"),
    ("Carlos III", "Carlos terceiro"),
    ("capítulo IV", "capítulo quarto"),
    ("capítulo XI", "capítulo onze"),
    ("II Guerra Mundial", "segunda Guerra Mundial"),
    ("inciso IV", "inciso quarto"),
    ("Hepatite C", "Hepatite C"),
    ("Vitamina D", "Vitamina D"),
]

MONEY = [
    ("R$ 1.250,50", "mil duzentos e cinquenta reais e cinquenta centavos"),
    ("R$1.250,50", "mil duzentos e cinquenta reais e cinquenta centavos"),
    ("R$ 5 milhões", "cinco milhões de reais"),
    ("R$ 1 milhão", "um milhão de reais"),
    ("R$ 2,5 milhões", "dois milhões e quinhentos mil reais"),
    ("R$ 1,2 mil", "mil e duzentos reais"),
    ("R$ 10 mil", "dez mil reais"),
    ("R$ 3 mil", "três mil reais"),
    ("R$ 1.000", "mil reais"),
    ("R$ 1.000.000", "um milhão de reais"),
    ("R$ 1,00", "um real"),
    ("R$ 2,00", "dois reais"),
    ("R$ 1,01", "um real e um centavo"),
    ("R$ 0,50", "cinquenta centavos"),
    ("R$ 0,01", "um centavo"),
    ("R$ 3,90", "três reais e noventa centavos"),
    ("R$ 100", "cem reais"),
    ("US$ 100", "cem dólares"),
    ("US$ 1", "um dólar"),
    ("US$ 1,50", "um dólar e cinquenta centavos"),
    ("US$ 1,5 milhão", "um milhão e quinhentos mil dólares"),
    ("€ 50", "cinquenta euros"),
    ("€ 1", "um euro"),
    ("€ 0,50", "cinquenta centavos de euro"),
    ("£ 20", "vinte libras"),
    ("£ 1", "uma libra"),
    ("£ 2", "duas libras"),
    ("£ 21.000", "vinte e uma mil libras"),
    ("levaram R$ 3 mil", "levaram três mil reais"),
    ("1.000 reais", "mil reais"),
    ("$ 20", "vinte dólares"),
]

EMERGENCY_AND_DOCUMENTS = [
    ("ligue 190", "ligue um nove zero"),
    ("ligou para o 190", "ligou para o um nove zero"),
    ("ligação para o 190", "ligação para o um nove zero"),
    ("SAMU (192)", "SAMU (um nove dois)"),
    ("Bombeiros, 193", "Bombeiros, um nove três"),
    ("Ligue 180", "Ligue um oito zero"),
    ("o CVV, 188", "o CVV, um oito oito"),
    ("denúncia pelo 181", "denúncia pelo um oito um"),
    ("PRF no 191", "PRF no um nove um"),
    ("ligue 193 ou 192", "ligue um nove três ou um nove dois"),
    ("Disque 100", "Disque cem"),
    ("ligou para o 911", "ligou para o novecentos e onze"),
    ("o número 112", "o número cento e doze"),
    ("190 centímetros de altura", "cento e noventa centímetros de altura"),
    ("180 dias depois", "cento e oitenta dias depois"),
    ("190 mil reais", "cento e noventa mil reais"),
    ("0800 123 4567", "zero oitocentos, um dois três, quatro cinco seis sete"),
    ("(11) 91234-5678", "onze, nove um dois três quatro, cinco seis sete oito"),
    ("(21) 98765-4321", "vinte e um, nove oito sete seis cinco, quatro três dois um"),
    ("(41) 3333-1234", "quarenta e um, três três três três, um dois três quatro"),
    ("3333-1234", "três três três três, um dois três quatro"),
    ("+55 11 91234-5678", "mais cinquenta e cinco, onze, nove um dois três quatro, cinco seis sete oito"),
    ("CPF 123.456.789-09", "CPF um dois três, quatro cinco seis, sete oito nove, traço, zero nove"),
    ("CNPJ 12.345.678/0001-95", "CNPJ um dois, três quatro cinco, seis sete oito, barra, zero zero zero um, traço, nove cinco"),
    ("RG 12.345.678-9", "RG um dois, três quatro cinco, seis sete oito, traço, nove"),
    ("CEP 01310-100", "CEP zero um três um zero, um zero zero"),
    ("placa ABC-1234", "placa a bê cê, um dois três quatro"),
    ("placa ABC1D23", "placa a bê cê um dê dois três"),
    ("processo 0001234-56.2020.8.26.0100", "processo zero zero zero um dois três quatro, cinco seis, dois zero dois zero, oito, dois seis, zero um zero zero"),
]

TIMES_AND_MEASURES = [
    ("10h30", "dez e meia"),
    ("23h45", "vinte e três e quarenta e cinco"),
    ("15h15", "quinze e quinze"),
    ("10h", "dez horas"),
    ("1h", "uma hora"),
    ("2h", "duas horas"),
    ("13h", "treze horas"),
    ("8h05", "oito e cinco"),
    ("1h30", "uma e meia"),
    ("0h", "meia-noite"),
    ("12h", "meio-dia"),
    ("0h30", "meia-noite e meia"),
    ("12h30", "meio-dia e meia"),
    ("12h15", "meio-dia e quinze"),
    ("24h", "vinte e quatro horas"),
    ("às 3 da tarde", "às três da tarde"),
    ("às 2 da tarde", "às duas da tarde"),
    ("às 2h", "às duas horas"),
    ("2h30min", "duas horas e trinta minutos"),
    ("45 min", "quarenta e cinco minutos"),
    ("1:45:30", "uma hora, quarenta e cinco minutos e trinta segundos"),
    ("10:30", "dez e meia"),
    ("das 9h às 17h", "das nove horas às dezessete horas"),
    ("35 anos", "trinta e cinco anos"),
    ("1 ano", "um ano"),
    ("21 anos", "vinte e um anos"),
    ("aos 18", "aos dezoito"),
    ("3,5", "três vírgula cinco"),
    ("0,25", "zero vírgula vinte e cinco"),
    ("3,14", "três vírgula catorze"),
    ("0,05", "zero vírgula zero cinco"),
    ("3,1415", "três vírgula um quatro um cinco"),
    ("3,5%", "três vírgula cinco por cento"),
    ("50%", "cinquenta por cento"),
    ("100%", "cem por cento"),
    ("1%", "um por cento"),
    ("1/2", "meio"),
    ("1/2 hora", "meia hora"),
    ("1/3", "um terço"),
    ("2/3", "dois terços"),
    ("1/4", "um quarto"),
    ("3/4", "três quartos"),
    ("1/10", "um décimo"),
    ("1/11", "um onze avos"),
    ("1/20", "um vigésimo"),
    ("1 1/2", "um e meio"),
    ("1990-1995", "de mil novecentos e noventa a mil novecentos e noventa e cinco"),
    ("de 1990-1995", "de mil novecentos e noventa a mil novecentos e noventa e cinco"),
    ("5-10 anos", "de cinco a dez anos"),
    ("5 a 10", "cinco a dez"),
    ("3 a 1", "três a um"),
    ("3x1", "três a um"),
    ("7 x 1", "sete a um"),
    ("0 a 0", "zero a zero"),
    ("nº 12", "número doze"),
    ("n.º 12", "número doze"),
    ("nºs 3 e 4", "números três e quatro"),
    ("BR-101", "BR cento e um"),
    ("BR-116", "BR cento e dezesseis"),
    ("SP-280", "SP duzentos e oitenta"),
    ("km 12", "quilômetro doze"),
    ("80 km/h", "oitenta quilômetros por hora"),
    ("80km/h", "oitenta quilômetros por hora"),
    ("36,5 °C", "trinta e seis vírgula cinco graus Celsius"),
    ("-5 °C", "menos cinco graus Celsius"),
    ("5 kg", "cinco quilos"),
    ("1 kg", "um quilo"),
    ("120 m²", "cento e vinte metros quadrados"),
    ("12 km", "doze quilômetros"),
    ("calibre .38", "calibre trinta e oito"),
    (".45", "quarenta e cinco"),
    (".380", "trezentos e oitenta"),
    ("9 mm", "nove milímetros"),
    ("9mm", "nove milímetros"),
    ("9x19", "nove por dezenove"),
    ("calibre 12", "calibre doze"),
    ("5,56 mm", "cinco vírgula cinquenta e seis milímetros"),
    ("Glock 17", "Glock dezessete"),
    ("AK-47", "AK quarenta e sete"),
    ("Lei 11.340", "Lei onze mil trezentos e quarenta"),
    ("Lei nº 8.072/90", "Lei número oito mil e setenta e dois, de noventa"),
]

TRUE_CRIME_AND_CODES = [
    ("2 disparos", "dois disparos"),
    ("3 tiros", "três tiros"),
    ("2 facadas", "duas facadas"),
    ("21 facadas", "vinte e uma facadas"),
    ("1 vítima", "uma vítima"),
    ("45 anos de prisão", "quarenta e cinco anos de prisão"),
    ("12 anos e 6 meses", "doze anos e seis meses"),
    ("30 anos, 2 meses e 10 dias", "trinta anos, dois meses e dez dias"),
    ("COVID-19", "COVID dezenove"),
    ("4x4 (picape)", "quatro por quatro (picape)"),
    ("3D", "três D"),
    ("5G", "cinco G"),
    ("G20", "G vinte"),
    ("10x", "dez vezes"),
    ("24/7", "vinte e quatro horas por dia, sete dias por semana"),
    ("007", "zero zero sete"),
    ("iPhone 15", "iPhone quinze"),
    ("Apollo 11", "Apollo onze"),
]

ALL_ROWS = CARDINALS + DATES + ORDINALS + ROMANS + MONEY + EMERGENCY_AND_DOCUMENTS + TIMES_AND_MEASURES + TRUE_CRIME_AND_CODES


@pytest.mark.parametrize(("written", "spoken"), ALL_ROWS)
def test_rule_table(written, spoken):
    assert say(written) == spoken


@pytest.mark.parametrize("text", [
    "Ele chegou em casa sem pressa.", "Hepatite C e Vitamina D.", "Estado de São Paulo", "http://exemplo.com/a1b2?id=10h30",
    "contato@exemplo.com.br e www.exemplo.com/2007", "ISO-9001", "H2O", "mp3", "",
])
def test_text_without_numbers_or_protected_spans_is_kept(text):
    assert say(text) == text


def test_no_digit_is_left_after_a_conversion():
    for written, _ in ALL_ROWS:
        assert not any(character.isdigit() for character in say(written)), written


def test_converting_twice_changes_nothing():
    for written, _ in ALL_ROWS:
        once = say(written)
        assert say(once) == once


def test_a_number_beyond_the_maximum_is_spelled_digit_by_digit():
    assert Numbers({}, 999).text("1.000") == "um zero zero zero"


@pytest.mark.parametrize(("mode", "written", "spoken"), [
    ("digits_in_context", "ligue 190", "ligue um nove zero"),
    ("digits_in_context", "190 centímetros", "cento e noventa centímetros"),
    ("digits_in_context", "ligou para o 911", "ligou para o novecentos e onze"),
    ("digits_always", "190 centímetros", "um nove zero centímetros"),
    ("digits_always", "ligou para o 911", "ligou para o nove um um"),
    ("digits_always", "o 112", "o um um dois"),
    ("number_always", "ligue 190", "ligue cento e noventa"),
    ("number_always", "SAMU (192)", "SAMU (cento e noventa e dois)"),
    ("number_always", "ligou para o 911", "ligou para o novecentos e onze"),
])
def test_emergency_numbers_option(mode, written, spoken):
    assert say(written, emergency_numbers=mode) == spoken


@pytest.mark.parametrize(("options", "written", "spoken"), [
    ({"feminine_mil": False}, "2.000 mulheres", "dois mil mulheres"),
    ({"half_hour_as_meia": False}, "10h30", "dez e trinta"),
    ({"decimal_cardinal_max_digits": 0}, "3,14", "três vírgula um quatro"),
    ({"ordinal_max": 20}, "21º Batalhão", "vinte e um Batalhão"),
    ({"phone_ddd_as_number": False}, "(11) 91234-5678", "um um, nove um dois três quatro, cinco seis sete oito"),
    ({"six_as_meia": True}, "CEP 06543-210", "CEP zero meia cinco quatro três, dois um zero"),
    ({"fraction_tens_as_ordinal": False}, "1/20", "um vinte avos"),
    ({"bare_dollar_sign": "real"}, "$ 20", "vinte reais"),
])
def test_options(options, written, spoken):
    assert say(written, **options) == spoken


def test_options_reject_unknown_values():
    with pytest.raises(ValueError):
        Options.model_validate({"emergency_numbers": "sometimes"})


def test_the_reader_is_found_by_language_code():
    assert reader_for("pt", {}, MAX_VALUE).text("2 mulheres") == "duas mulheres"


@pytest.mark.parametrize(("value", "feminine", "words"), [
    (0, False, "zero"), (21, True, "vinte e uma"), (200, True, "duzentas"), (1002000, True, "um milhão e duas mil"),
    (1_000_000_000_000, False, "um trilhão"), (2_000_000_003, False, "dois bilhões e três"),
])
def test_cardinal_words(value, feminine, words):
    assert cardinal(value, feminine) == words


def test_ordinal_words_and_roman_values():
    assert ordinal(21) == "vigésimo primeiro" and ordinal(21, True) == "vigésima primeira" and ordinal(100) == "centésimo"
    assert roman_value("MCMXC") == 1990 and roman_value("XIV") == 14


@pytest.mark.parametrize(("text", "feminine"), [
    (" mulheres", True), (" homens", False), (" grandes casas", True), (" dias", False), (" pessoas", True), (" são suspeitos", False),
    (" das vítimas", True), (" dos policiais", False), ("", False), (", mulheres", False),
])
def test_gender_of_the_counted_noun(text, feminine):
    assert feminine_after(text, 0) is feminine
