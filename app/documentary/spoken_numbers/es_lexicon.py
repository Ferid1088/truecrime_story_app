"""Spanish word tables the number rules need: months, units, currencies and the gender of the noun a number counts."""

from __future__ import annotations

import re
from dataclasses import dataclass

MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
MONTH_ABBREVIATIONS = {"ene": "enero", "feb": "febrero", "mar": "marzo", "abr": "abril", "may": "mayo", "jun": "junio", "jul": "julio",
                       "ago": "agosto", "sept": "septiembre", "sep": "septiembre", "set": "septiembre", "oct": "octubre", "nov": "noviembre",
                       "dic": "diciembre"}
MONTH_NAMES = {**{month: month for month in MONTHS}, "setiembre": "septiembre", **MONTH_ABBREVIATIONS}
# the full of a month name ends at the word; only an abbreviation owns the full stop after it ("feb."), so a date that ends a sentence keeps its period
MONTH_PATTERN = ("(?:(?:" + "|".join(sorted((m for m in MONTH_NAMES if m not in MONTH_ABBREVIATIONS), key=len, reverse=True)) + r")\b"
                 "|(?:" + "|".join(sorted(MONTH_ABBREVIATIONS, key=len, reverse=True)) + r")\b\.?)")
DAYPARTS = "mañana|tarde|noche|madrugada"

FEMININE_NOUNS = frozenset(["mujer", "mujeres", "vez", "veces", "calle", "calles", "noche", "noches", "tarde", "tardes", "madre", "madres", "mano", "manos", "foto", "fotos", "moto", "motos", "llave", "llaves", "clase", "clases", "cruz", "cruces", "luz", "luces", "voz", "voces", "flor", "flores", "fuente", "fuentes", "muerte", "muertes", "cárcel", "cárceles", "ley", "leyes", "razón", "razones", "paz", "nariz", "sede", "sedes", "serie", "series", "especie", "especies", "edad", "edades", "mitad", "mitades", "ciudad", "ciudades", "gente", "pared", "paredes", "sal", "fe"])
MASCULINE_A_NOUNS = frozenset(["día", "días", "mapa", "mapas", "problema", "problemas", "sistema", "sistemas", "tema", "temas", "planeta", "planetas", "programa", "programas", "idioma", "idiomas", "drama", "dramas", "clima", "poema", "poemas", "diploma", "diplomas", "síntoma", "síntomas", "trauma", "traumas", "policía", "policías", "guía", "guías", "colega", "colegas", "atleta", "atletas", "turista", "turistas", "artista", "artistas", "cometa", "cometas", "pijama"])
FEMININE_ENDINGS = ("ión", "iones", "dad", "dades", "tud", "tudes", "umbre")
FUNCTION_WORDS = frozenset(["de", "del", "y", "e", "o", "u", "a", "al", "en", "con", "por", "para", "que", "como", "más", "menos", "sin", "sobre", "entre", "desde", "hasta", "contra", "según", "tras", "ni", "pero", "sino", "se", "lo", "la", "las", "los", "el", "un", "una", "unos", "unas", "son", "es", "fue", "era", "eran", "había"])


def gender_of(word: str) -> str:
    """"f" for a noun the narrator makes feminine ("21 mujeres", "doscientas personas"), else "m" (the safe default)."""
    word = word.lower()
    if word in MASCULINE_A_NOUNS:
        return "m"
    if word in FEMININE_NOUNS or word.endswith(FEMININE_ENDINGS) or word.endswith(("a", "as")) and len(word) > 2:
        return "f"
    return "m"


@dataclass(frozen=True)
class Unit:
    singular: str
    plural: str
    gender: str


UNITS = {
    "km/h": Unit("kilómetro por hora", "kilómetros por hora", "m"), "kph": Unit("kilómetro por hora", "kilómetros por hora", "m"),
    "mph": Unit("milla por hora", "millas por hora", "f"), "m/s": Unit("metro por segundo", "metros por segundo", "m"),
    "km²": Unit("kilómetro cuadrado", "kilómetros cuadrados", "m"), "m²": Unit("metro cuadrado", "metros cuadrados", "m"),
    "m³": Unit("metro cúbico", "metros cúbicos", "m"), "km": Unit("kilómetro", "kilómetros", "m"), "kg": Unit("kilo", "kilos", "m"),
    "cm": Unit("centímetro", "centímetros", "m"), "mm": Unit("milímetro", "milímetros", "m"), "mg": Unit("miligramo", "miligramos", "m"),
    "ml": Unit("mililitro", "mililitros", "m"), "ha": Unit("hectárea", "hectáreas", "f"), "lb": Unit("libra", "libras", "f"),
    "oz": Unit("onza", "onzas", "f"), "ft": Unit("pie", "pies", "m"), "yd": Unit("yarda", "yardas", "f"), "min": Unit("minuto", "minutos", "m"),
    "hrs": Unit("hora", "horas", "f"), "hr": Unit("hora", "horas", "f"), "seg": Unit("segundo", "segundos", "m"),
    "m": Unit("metro", "metros", "m"), "g": Unit("gramo", "gramos", "m"), "t": Unit("tonelada", "toneladas", "f"),
    "l": Unit("litro", "litros", "m"), "L": Unit("litro", "litros", "m"), "h": Unit("hora", "horas", "f"), "s": Unit("segundo", "segundos", "m"),
}
UNIT_PATTERN = "|".join(re.escape(symbol) for symbol in sorted(UNITS, key=len, reverse=True))
# Plural nouns a range such as "5-10" counts: "de cinco a diez años".
COUNTED_NOUNS = frozenset(word for unit in UNITS.values() for word in (unit.singular, unit.plural) if " " not in word) | frozenset(["años", "meses", "semanas", "días", "horas", "minutos", "segundos", "personas", "veces", "pesos", "dólares", "euros", "balas", "disparos", "heridas", "puñaladas", "golpes", "hombres", "mujeres", "niños", "niñas", "policías", "agentes", "víctimas", "sospechosos", "testigos", "grados"])


@dataclass(frozen=True)
class Currency:
    singular: str
    plural: str
    gender: str
    minor_singular: str = ""
    minor_plural: str = ""


CURRENCIES = {
    "peso": Currency("peso", "pesos", "m", "centavo", "centavos"),
    "peso_mx": Currency("peso mexicano", "pesos mexicanos", "m", "centavo", "centavos"),
    "dolar": Currency("dólar", "dólares", "m", "centavo", "centavos"),
    "euro": Currency("euro", "euros", "m", "céntimo", "céntimos"),
    "libra": Currency("libra", "libras", "f", "penique", "peniques"),
    "yen": Currency("yen", "yenes", "m"),
}
SYMBOL_CURRENCY = {"US$": "dolar", "U$S": "dolar", "USD": "dolar", "MX$": "peso_mx", "MXN$": "peso_mx", "MXN": "peso_mx", "€": "euro",
                   "EUR": "euro", "£": "libra", "GBP": "libra", "¥": "yen"}
CODE_CURRENCY = {"dólares": "dolar", "dlls": "dolar", "dlls.": "dolar", "dll": "dolar", "dll.": "dolar", "USD": "dolar", "pesos": "peso",
                 "pesos mexicanos": "peso_mx", "MXN": "peso_mx", "M.N.": "peso", "euros": "euro", "EUR": "euro", "libras": "libra", "GBP": "libra",
                 "€": "euro", "mdp": "peso", "mdd": "dolar"}

# Scale words after a figure ("5 millones", "$3 mil millones", "500 mdp"): (multiplier, the scale in words for a decimal such as "2.3 millones").
SCALES = {"mil": (10 ** 3, "mil"), "millón": (10 ** 6, "millones"), "millones": (10 ** 6, "millones"), "mil millones": (10 ** 9, "mil millones"),
          "billón": (10 ** 12, "billones"), "billones": (10 ** 12, "billones"), "mdp": (10 ** 6, "millones"), "mdd": (10 ** 6, "millones")}
