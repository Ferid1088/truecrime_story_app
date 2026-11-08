"""The patterns and word tables of the German number reader (the rules themselves are in de.py)."""

from __future__ import annotations

import re

NOT_AFTER_DIGIT = r"(?<!\d)(?<!\d[.,])"
LETTER = r"A-Za-zÄÖÜäöüß"
NOT_LETTER_AFTER = rf"(?![{LETTER}])"
# A German amount: thousands with dots, decimals after the comma ("1.500,50"). "3.50" (a dot and two digits) is no German amount and is left alone.
AMOUNT = NOT_AFTER_DIGIT + r"(?P<int>\d{1,3}(?:\.\d{3})+|\d+)(?:,(?P<dec>\d+))?(?!\d)(?!\.\d)"
MONEY_AMOUNT = NOT_AFTER_DIGIT + r"(?P<int>\d{1,3}(?:\.\d{3})+|\d+)(?:,(?P<dec>\d{1,2}|-{1,2}))?(?!\d)(?!\.\d)"
MAGNITUDE = r"(?:\s*(?P<mag>Mio\.|Mrd\.|Bio\.|Tsd\.|Millionen|Million|Milliarden|Milliarde|Billionen|Billion)|(?P<k>k))" + NOT_LETTER_AFTER

MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
MONTH_PATTERN = "|".join(MONTHS)
YEAR_PREVIOUS_WORDS = {month.lower() for month in MONTHS} | {"jahr", "jahre", "jahres", "jahren", "jahrgang", "baujahr", "frühjahr", "sommer", "herbst", "winter"}
DATE_CUES = {"am", "vom", "zum", "bis", "ab", "seit", "den", "der", "des"}
ARTICLES = {"der", "die", "das", "dem", "den", "des", "am", "im", "vom", "zum", "zur", "beim", "ins", "eine", "einem", "einer", "ein", "einen"}
ORDINAL_NOUNS = {"Jahrhundert", "Jahrhunderts", "Jahrtausend", "Mal", "Platz", "Klasse", "Weltkrieg", "Liga", "Bundesliga", "Stock", "Etage", "Geburtstag", "Jahrestag",
                 "Auflage", "Reich", "Stufe", "Runde", "Spieltag", "Rang", "Senat", "Kammer", "Semester", "Halbzeit", "Ausgabe", "Generation", "Dan", "FC"}

CURRENCIES = {"€": "Euro", "EUR": "Euro", "Euro": "Euro", "$": "$", "USD": "$", "Dollar": "Dollar", "US-Dollar": "US-Dollar", "£": "Pfund", "GBP": "Pfund",
              "Pfund": "Pfund", "DM": "D-Mark", "D-Mark": "D-Mark", "Mark": "Mark", "Cent": "Cent", "ct": "Cent"}
CURRENCY_GENDER = {"Euro": "m", "Dollar": "m", "US-Dollar": "m", "Pfund": "n", "D-Mark": "f", "Mark": "f", "Cent": "m"}
CURRENCY_SUBUNIT = {"Euro": "Cent", "Dollar": "Cent", "US-Dollar": "Cent", "Pfund": "Pence", "D-Mark": "Pfennig", "Mark": "Pfennig"}
CURRENCY_AFTER = re.compile(MONEY_AMOUNT + rf"(?:{MAGNITUDE})?\s*(?P<cur>US-Dollar|D-Mark|€|EUR|Euro|\$|USD|Dollar|£|GBP|Pfund|DM|Mark|Cent|ct)" + NOT_LETTER_AFTER)
CURRENCY_BEFORE = re.compile(r"(?<![A-Za-z])(?P<cur>€|EUR|\$|USD|£|GBP)\s*" + MONEY_AMOUNT + rf"(?:{MAGNITUDE})?")
QUANTITY = re.compile(AMOUNT + MAGNITUDE)
MAGNITUDE_NOUN = {"mio": 0, "million": 0, "millionen": 0, "mrd": 1, "milliarde": 1, "milliarden": 1, "bio": 2, "billion": 2, "billionen": 2}
BIG_UNITS = [("Million", "Millionen"), ("Milliarde", "Milliarden"), ("Billion", "Billionen")]

UNITS = {"km/h": ("Kilometer pro Stunde", "Kilometer pro Stunde", "m"), "km²": ("Quadratkilometer", "Quadratkilometer", "m"),
         "km": ("Kilometer", "Kilometer", "m"), "m²": ("Quadratmeter", "Quadratmeter", "m"), "m³": ("Kubikmeter", "Kubikmeter", "m"),
         "mm": ("Millimeter", "Millimeter", "m"), "cm": ("Zentimeter", "Zentimeter", "m"), "kg": ("Kilogramm", "Kilogramm", "n"),
         "mg": ("Milligramm", "Milligramm", "n"), "ml": ("Milliliter", "Milliliter", "m"), "ha": ("Hektar", "Hektar", "n"),
         "°C": ("Grad Celsius", "Grad Celsius", "m"), "°": ("Grad", "Grad", "m"), "m": ("Meter", "Meter", "m"), "g": ("Gramm", "Gramm", "n"),
         "l": ("Liter", "Liter", "m"), "t": ("Tonne", "Tonnen", "f"), "Std.": ("Stunde", "Stunden", "f"), "Min.": ("Minute", "Minuten", "f"),
         "Sek.": ("Sekunde", "Sekunden", "f")}
UNITS_WITHOUT_HALF = {"°C", "°", "km/h"}
UNIT_PATTERN = "|".join(re.escape(unit) for unit in UNITS)
UNIT = re.compile(AMOUNT + rf"\s?(?P<unit>{UNIT_PATTERN})" + r"(?![A-Za-zÄÖÜäöüß²³/])")
HEIGHT = re.compile(NOT_AFTER_DIGIT + r"(?P<m>[12]),(?P<cm>\d{2})\s?m(?![A-Za-zÄÖÜäöüß²³/])")
UNIT_COMPOUND = re.compile(r"(?<![\w-])(?P<n>\d+(?:,\d+)?)-(?P<unit>mm|cm|m|kg|g|l)-(?=[A-ZÄÖÜ])")
UNIT_COMPOUND_WORDS = {"mm": "Millimeter", "cm": "Zentimeter", "m": "Meter", "kg": "Kilogramm", "g": "Gramm", "l": "Liter"}
HALF_NOUNS = {"jahre", "jahren", "monate", "monaten", "wochen", "tage", "tagen", "stunden", "minuten", "sekunden", "millionen", "milliarden", "kilometer", "meter",
              "kilo", "kilogramm", "liter", "jahrzehnte", "jahrzehnten"}

PERCENT = re.compile(AMOUNT + r"\s?(?P<sign>%|‰)")
SCORE = re.compile(r"(?<![\d:.,])(?P<a>\d{1,2}):(?P<b>\d{1,2})(?![\d:])")
FRACTION = re.compile(r"(?<![\d/.,])(?P<a>\d{1,3})/(?P<b>\d{1,3})(?![\d/])")
FRACTION_NAMES = {2: "halb", 3: "Drittel", 4: "Viertel", 5: "Fünftel", 6: "Sechstel", 7: "Siebtel", 8: "Achtel", 9: "Neuntel", 10: "Zehntel", 20: "Zwanzigstel",
                  100: "Hundertstel"}
ROUND_THE_CLOCK = re.compile(r"(?<![\d/])24/7(?![\d/])")

HOUR = r"(?P<h>[01]?\d|2[0-4])"
TIME = re.compile(r"(?<![\d:.,])" + HOUR + r"[:.](?P<mi>[0-5]\d)\s*Uhr" + NOT_LETTER_AFTER)
TIME_CUED = re.compile(r"\b(?P<cue>um|gegen|ab|bis|kurz vor|kurz nach)\s+" + HOUR + r":(?P<mi>[0-5]\d)(?![\d:])")
HOUR_ONLY = re.compile(r"(?<![\d:.,])" + HOUR + r"\s*Uhr" + NOT_LETTER_AFTER)

DATE_RANGE = re.compile(rf"(?<![\d.])(?P<a>\d{{1,2}})\.\s*(?:bis|und|-|–)\s*(?P<d>\d{{1,2}})\.\s*(?P<m>{MONTH_PATTERN})\b(?:\s+(?P<y>\d{{4}})(?!\d))?")
DATE_NUMERIC = re.compile(r"(?<![\d.])(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?:(?P<y>\d{4})(?!\d))?")
DATE_TEXT = re.compile(rf"(?<![\d.])(?P<d>\d{{1,2}})\.\s*(?P<m>{MONTH_PATTERN})\b(?:\s+(?P<y>\d{{4}})(?!\d))?")
ORDINAL = re.compile(rf"(?<![\d.,:/-])(?P<n>\d{{1,3}})\.(?=\s+(?P<next>[{LETTER}]+))")

PHONE = re.compile(r"(?<![\w.,:/+\-])(?P<prefix>\+\d{1,3}|00\d{2}|0\d{2,4})(?P<rest>(?:[ /\-]\d{1,10}){1,6})(?![\w]|[.,]\d)")
IBAN = re.compile(r"\b(?P<country>[A-Z]{2})(?P<check>\d{2})(?P<rest>(?:\s?\d{4}){3,6}(?:\s?\d{1,4})?)(?!\d)")
CASE_FILE = re.compile(r"\b(?:Az\.?|Aktenzeichen)\s+(?P<a>\d+)\s+(?P<l>[A-ZÄÖÜ][A-Za-z]{0,3})\s+(?P<b>\d+)/(?P<y>\d{2,4})\b")
PLATE = re.compile(r"\b(?P<plate>[A-ZÄÖÜ]{1,3}-[A-Z]{1,2})\s(?P<n>\d{1,4})(?P<suffix>[EH])?\b")
POSTCODE = re.compile(r"(?<![\d.,/-])(?P<zip>\d{5})(?!\d|[.,]\d)(?=\s+[A-ZÄÖÜ][a-zäöüß])")
POSTCODE_PREVIOUS = re.compile(r"(^|[,;(]|\b(PLZ|Postleitzahl)|\d[a-z]?,?)\s*$")
EMERGENCY = re.compile(r"(?<![\d.,])(?P<n>110|112|116\s?117)(?!\d|[.,]\d)")
EMERGENCY_WORDS = {"notruf", "notrufnummer", "notrufnummern", "notrufzentrale", "polizeiruf", "polizeinotruf", "polizei", "feuerwehr", "rettungsdienst",
                   "rettungsleitstelle", "leitstelle", "rettungswagen", "krankenwagen", "notarzt", "wählte", "wählt", "wählen", "gewählt", "anrief", "anruft",
                   "anrufen", "angerufen", "rief", "ruft", "rufnummer", "hotline", "bereitschaftsdienst", "telefonnummer", "euronotruf", "nummer", "telefon",
                   "anruf", "ruf"}

YEAR_RANGE = re.compile(r"(?<![\d.,/-])(?P<a>\d{4})\s*[-–]\s*(?P<b>\d{4}|\d{2})(?!\d)")
NUMBER_RANGE = re.compile(r"(?<![\w.,/+-])(?P<a>\d{1,3}(?:\.\d{3})+|\d{1,6})\s?[-–]\s?(?=(?P<b>\d{1,3}(?:\.\d{3})+|\d{1,6})(?![\w.,/-]|[.,]\d))")
YEAR = re.compile(r"(?<![\d.,/-])(?P<y>\d{4})(?!\d|[.,]\d)")
SIGN = re.compile(r"(?<![\w)])(?P<sign>[-−+])(?=\d)")
DECADE = re.compile(r"(?<![\w.,/-])(?P<n>\d{1,4})er(?P<plural>n?)" + NOT_LETTER_AFTER)
AGE = re.compile(r"(?<![\w-])(?P<n>\d+)-(?P<j>[Jj])(?P<rest>ähri\w*)")
HYPHEN_COMPOUND = re.compile(r"(?<![\w-])(?P<n>\d+)-(?=[A-Za-zÄÖÜäöüß])")
CALIBRE = re.compile(r"(?<![\w\d.,])\.(?P<n>\d{2,3})(?!\d)(?P<name>\s+(?:Special|Magnum|Auto|Colt|Long|ACP|Super|Luger|Winchester|Remington|Parabellum))?")
DECIMAL = re.compile(NOT_AFTER_DIGIT + r"(?P<int>\d{1,3}(?:\.\d{3})+|\d+),(?P<dec>\d+)(?!\d)(?!,\d)")
HOUSE_LETTER = re.compile(r"(?<![\w.,/-])(?P<n>\d+)(?P<l>[a-e])(?![A-Za-z0-9ÄÖÜäöü])")
CARDINAL = re.compile(NOT_AFTER_DIGIT + r"(?P<n>\d{1,3}(?:\.\d{3})+|\d+)(?!\d)(?!\.\d)(?!,\d)")
NOUN_AFTER = re.compile(r"\s+(?P<noun>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*)")
NEXT_WORD = re.compile(r"\s+(?P<word>[A-Za-zÄÖÜäöüß]+)")

ABBREVIATIONS = [(re.compile(r"§§(?=\s*\d)"), "Paragrafen"), (re.compile(r"\bNr\.\s*(?=\d)"), "Nummer "), (re.compile(r"\bAbs\.\s*(?=\d)"), "Absatz "),
                 (re.compile(r"\bArt\.\s*(?=\d)"), "Artikel ")]
PARAGRAPH = re.compile(r"§(?=\s*\d)")

ROMAN = re.compile(r"^(?=[IVXLCDM])M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
TITLES = "Papst|Kaiser|König|Königin|Zar|Zarin|Herzog|Fürst|Graf|Kurfürst|Prinz"
RULER_NAMES = {"Heinrich", "Karl", "Wilhelm", "Friedrich", "Ludwig", "Johannes", "Paul", "Benedikt", "Franz", "Josef", "Joseph", "Elisabeth", "Napoleon", "Otto",
               "Konrad", "Leopold", "Maximilian", "Philipp", "Peter", "Alexander", "Nikolaus", "Pius", "Gregor", "Clemens", "Urban", "Innozenz", "Leo", "Georg",
               "Eduard", "Richard", "Albert", "Viktor", "Ferdinand", "Rudolf", "Sigismund", "Wenzel", "Lothar", "Ruprecht", "Ernst", "August", "Johann", "Anna",
               "Maria", "Katharina", "Victoria", "Isabella", "Stephan", "Matthias", "Mathilde", "Eleonore", "Marie"}
RULER = re.compile(rf"(?P<name>(?:(?:{TITLES})\s)?(?:[A-ZÄÖÜ][a-zäöüß]+\s)?[A-ZÄÖÜ][a-zäöüß]+)\s+(?P<num>[IVXLC]{{1,6}})\.(?![A-Za-zäöüß0-9])")
ROMAN_CENTURY = re.compile(r"\b(?P<r>[IVXLCDM]+)\.\s+(?P<noun>Jahrhunderts|Jahrhundert|Jahrtausend|Weltkrieg|Reich)\b")
ROMAN_LABEL = re.compile(r"\b(?P<w>Band|Teil|Akt|Kapitel|Buch|Staffel|Folge|Abschnitt|Anhang)\s+(?P<r>[IVXLCDM]+)\b(?!\.)")
CAPITALISED_WORD = re.compile(r"\s+[A-ZÄÖÜ][a-zäöüß]+")
CAPITALISED_ROMAN_NOUNS = {"Weltkrieg", "Reich"}

# What the voice must keep as it is: web addresses, e-mails, and codes of letters and digits ("A1", "COVID-19", "B-52", "3D", "5G", "4x4"). Unlike the
# generic protection of base.py it leaves "90er", "35-jährig", "12a", "50k" to the rules, and a bank number (IBAN) is read.
PROTECTED = re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\S+"
                       r"|\b(?![A-Z]{2}\d{2}\s?\d{4})[A-Za-zÀ-ÿ]+-?\d[A-Za-zÀ-ÿ0-9]*(?:-[A-Za-zÀ-ÿ0-9]+)*\b"
                       r"|\b\d+[A-Z]\b|\b\d+[xX]\d+\b")
PRIVATE_USE = 0xE000
