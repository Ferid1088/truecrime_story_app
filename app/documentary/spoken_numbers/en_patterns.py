"""The patterns and word tables of the English number reader (the rules themselves are in en.py)."""

from __future__ import annotations

import re

from app.documentary.spoken_numbers.base import PROTECTED as BASE_PROTECTED

PRIVATE_USE = 0xE000
PROTECTED = BASE_PROTECTED
INTEGER = r"\d{1,3}(?:,\d{3})+|\d+"
# A number with an optional decimal part that is not glued to another number, a word or a dotted sequence ("1.2.3").
AMOUNT = r"(?<![\w.,])(?P<int>" + INTEGER + r")(?:\.(?P<dec>\d+))?(?!\d)(?!,\d)(?!\.\d)"
NOT_LETTER_AFTER = r"(?![A-Za-z])"

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
MONTH_ABBREVIATIONS = {"jan.": 0, "feb.": 1, "mar.": 2, "apr.": 3, "jun.": 5, "jul.": 6, "aug.": 7, "sep.": 8, "sept.": 8, "oct.": 9, "nov.": 10, "dec.": 11}
MONTH = r"(?P<m>" + "|".join(MONTHS) + r"|(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.)"
MONTH_WORDS = {month.lower() for month in MONTHS}
STRONG_YEAR_WORDS = MONTH_WORDS | {"since", "year", "circa", "c", "until", "till", "mid", "early", "late", "summer", "spring", "autumn", "fall", "winter", "class"}
YEAR_PREVIOUS_WORDS = STRONG_YEAR_WORDS | {"in", "by", "from", "during", "before", "after", "between", "through", "back"}
AMOUNT_PREVIOUS_WORDS = {"about", "around", "approximately", "nearly", "almost", "over", "under", "roughly", "some", "only", "just", "than", "estimated", "worth"}
COUNTED_NOUNS = {"people", "persons", "men", "women", "boys", "girls", "children", "kids", "victims", "suspects", "witnesses", "officers", "deaths", "dollars", "bucks",
                 "pounds", "euros", "cents", "pence", "miles", "feet", "foot", "yards", "meters", "metres", "kilometers", "kilometres", "kilos", "kilograms", "grams",
                 "ounces", "acres", "hours", "minutes", "seconds", "days", "weeks", "months", "years", "times", "shots", "rounds", "bullets", "pages", "words", "calls",
                 "cases", "homes", "houses", "residents", "employees", "workers", "soldiers", "troops", "participants", "votes", "copies", "pieces", "items", "lbs", "ft"}
NEXT_WORD = re.compile(r"\s+(?P<word>[A-Za-z]+)")
PREVIOUS_WORD = re.compile(r"(?P<word>[A-Za-z]+)[\s,]*$")

ORDINAL = re.compile(r"(?<![\w.,])(?P<n>\d+)(?:st|nd|rd|th)" + NOT_LETTER_AFTER)
SUFFIX = r"(?:st|nd|rd|th)?"
DATE_ISO = re.compile(r"(?<![\d-])(?P<y>\d{4})-(?P<m>0[1-9]|1[0-2])-(?P<d>0[1-9]|[12]\d|3[01])(?![\d-])")
DATE_NUMERIC = re.compile(r"(?<![\d/.-])(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y>\d{4})(?![\d/])")
DATE_MONTH_FIRST = re.compile(MONTH + r"\s+(?P<d>\d{1,2})" + SUFFIX + r"(?:\s?[-–]\s?(?P<d2>\d{1,2})" + SUFFIX + r")?(?:,?\s+(?P<y>\d{4}))?(?![\d:])")
DATE_DAY_FIRST = re.compile(r"(?<![\w.,])(?P<the>[Tt]he\s+)?(?P<d>\d{1,2})" + SUFFIX + r"\s+(?:of\s+)?" + MONTH + r"(?:,?\s+(?P<y>\d{4}))?(?![\w])")
NINE_ELEVEN = re.compile(r"(?<![\w/])9/11(?![\w/])")

HOUR = r"(?P<h>\d{1,2})"
AM_PM = r"(?P<ap>[AaPp]\.?[Mm]\.?)"
TIME = re.compile(r"(?<![\w:.,])" + HOUR + r":(?P<mi>[0-5]\d)(?!\d)(?!:\d)(?:\s?" + AM_PM + r"(?![A-Za-z]))?")
HOUR_AM_PM = re.compile(r"(?<![\w:.,])" + HOUR + r"\s?" + AM_PM + r"(?![A-Za-z])")
O_CLOCK = re.compile(r"(?<![\w:.,])" + HOUR + r"\s+o['’]clock", re.IGNORECASE)
MILITARY = re.compile(r"(?<![\w:.,])(?P<t>[0-2]\d[0-5]\d)\s+hours?\b")
RATIO = re.compile(r"(?<![\d:.,])(?P<a>\d{1,3}):(?P<b>\d{1,3})(?![\d:])")

PHONE_PLUS = re.compile(r"(?<![\w+])\+\d{1,3}(?:[ .-]\(?\d{1,5}\)?){2,}(?!\d)")
PHONE_NANP = re.compile(r"(?<![\w+.-])(?P<country>1[-. ])?(?:\((?P<area_paren>\d{3})\)\s?|(?P<area>\d{3})[-.])(?P<exchange>\d{3})[-.](?P<line>\d{4})(?![\w-])")
PHONE_LOCAL = re.compile(r"(?<![\w+.-])(?P<exchange>\d{3})-(?P<line>\d{4})(?![\w-])")
PHONE_UK = re.compile(r"(?<![\w+.-])0\d{2,4} \d{3,4} \d{3,4}(?![\w-])")
ZIP = re.compile(r"\b(?:(?P<state>[A-Z]{2})\s+|(?P<zip_word>ZIP(?: code)?\s+))(?P<zip>\d{5})(?:-(?P<plus4>\d{4}))?(?!\d)")

STREET_WORDS = (r"Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?|Way|Court|Ct\.?|Place|Pl\.?|Terrace|Parkway|Pkwy\.?|Circle|Square|"
                r"Close|Crescent|Highway|Hwy\.?")
STREET = re.compile(r"(?<![\w.,$£€#-])(?P<n>\d{1,5})(?P<letter>[A-Z])?\s+(?P<street>(?:[NSEW]\.?\s+)?(?:(?:[A-Z][\w'’.-]*|\d+(?:st|nd|rd|th))\s+){1,3}(?:"
                    + STREET_WORDS + r")(?![A-Za-z]))")
CODE_LABEL = re.compile(r"\b(?P<label>case|file|docket|badge|serial|account|reference|evidence|plate|ID|policy|warrant)(?P<no>\s+(?:No\.?|Number|number|#)\s*|\s+)"
                        r"(?P<code>\d[\d-]*\d|\d)(?![\w])", re.IGNORECASE)
HOUSE_LABEL = re.compile(r"\b(?P<label>room|apartment|apt\.?|unit|suite|flight|gate|route|highway|interstate|bus|exit)\s+(?P<n>\d+)(?![\d,.]\d)(?![\w])", re.IGNORECASE)
NUMBER_SIGN = re.compile(r"\bNos?\.\s*(?=\d)|#(?=\d)")
HOUSE_LETTER = re.compile(r"(?<![\w.,$£€#-])(?P<n>\d{1,4})(?P<l>[A-Z])(?![\w])")

EMERGENCY = re.compile(r"(?<![\w$£€.,/-])(?:(?P<hyphen>9-1-1)|(?P<n>911|999|112|000|111))(?![\w-])(?!,\d)(?!\.\d)")
EMERGENCY_WORDS = {"call", "calls", "called", "calling", "dial", "dials", "dialed", "dialled", "dialing", "dialling", "phone", "phoned", "emergency", "dispatcher", "dispatch",
                   "operator", "hotline", "ring", "rang", "rung", "ringing", "report", "reported", "reporting", "police", "ambulance"}
EMERGENCY_NOUNS_AFTER = {"call", "calls", "caller", "dispatcher", "dispatchers", "operator", "operators", "recording", "recordings", "tape", "tapes", "audio", "system",
                         "center", "centre", "line", "response", "emergency", "transcript", "transcripts", "service", "services"}
EMERGENCY_SPOKEN = {"000": "triple zero"}

CALIBRE_PAIR = re.compile(r"(?<![\w.,/$£€-])\.(?P<a>\d{2})-(?P<b>\d{2})(?!\d)")
CALIBRE = re.compile(r"(?<![\w.,/$£€-])\.(?P<n>\d{2,3})(?!\d)(?!\.\d)")
GUN_WORDS = r"(?:pistol|handgun|rifle|revolver|round|rounds|luger|parabellum|glock|semiautomatic|semi-automatic|automatic|caliber|calibre|ammunition|ammo|shell|shells|casing|casings|" \
            r"bullet|bullets|cartridge|cartridges|beretta|makarov|browning|sig|walther|slug|slugs)"
MILLIMETRE_PAIR = re.compile(r"(?<![\w.,])(?P<n>\d+(?:\.\d+)?)x(?P<n2>\d+(?:\.\d+)?)\s?mm" + NOT_LETTER_AFTER)
MILLIMETRE_ROUND = re.compile(r"(?<![\w.,])(?P<n>\d+(?:\.\d+)?)\s?mm(?P<gun>(?=\s+" + GUN_WORDS + r"\b)?)" + NOT_LETTER_AFTER, re.IGNORECASE)
CALIBRE_MILLIMETRES = {"9", "10", "12", "5.56", "5.7", "4.6", "6.35", "7.62", "7.65", "12.7"}
HEIGHT_MARKS = re.compile(r"(?<![\w.,])(?P<ft>\d)['’]\s?(?P<in>\d{1,2})(?:[\"”]|''|’’)?(?![\w])")
HEIGHT_WORDS = re.compile(r"(?<![\w.,])(?P<ft>\d)\s?(?:ft|feet|foot)\.?,?\s?(?P<in>\d{1,2})\s?(?:in\.?|inches)" + NOT_LETTER_AFTER)

YEAR_RANGE = re.compile(r"(?<![\w.,/-])(?P<a>\d{4})\s?[-–—]\s?(?P<b>\d{4}|\d{2})(?!\d)")
NUMBER_RANGE = re.compile(r"(?<![\w.,/-])(?P<a>" + INTEGER + r")\s?[-–—]\s?(?=\d)")
SIGN = re.compile(r"(?<![\w.,-])-(?=\d)")

CURRENCIES = {"$": ("dollar", "dollars", "cent", "cents"), "USD": ("dollar", "dollars", "cent", "cents"), "£": ("pound", "pounds", "penny", "pence"),
              "GBP": ("pound", "pounds", "penny", "pence"), "€": ("euro", "euros", "cent", "cents"), "EUR": ("euro", "euros", "cent", "cents")}
MAGNITUDE_WORDS = {"k": "thousand", "m": "million", "b": "billion", "bn": "billion", "thousand": "thousand", "million": "million", "billion": "billion", "trillion": "trillion"}
MAGNITUDE = r"(?:\s?(?P<mag>(?i:thousand|million|billion|trillion))|(?P<abbr>[kKmMbB]n?))" + NOT_LETTER_AFTER
CURRENCY_BEFORE = re.compile(r"(?<![\w])(?P<cur>[$£€]|USD|GBP|EUR)\s?(?P<int>" + INTEGER + r")(?:\.(?P<dec>\d+))?(?!\d)(?!,\d)(?:" + MAGNITUDE + r")?")
SUBUNIT_AFTER = re.compile(r"(?<![\w.,$£€])(?P<n>\d{1,3})(?P<cur>p|¢)(?![\w])")

PERCENT = re.compile(AMOUNT + r"\s?(?P<sign>%|‰)")
UNITS = {"km/h": ("kilometer per hour", "kilometers per hour"), "kph": ("kilometer per hour", "kilometers per hour"), "mph": ("mile per hour", "miles per hour"),
         "km": ("kilometer", "kilometers"), "kg": ("kilogram", "kilograms"), "cm": ("centimeter", "centimeters"), "mm": ("millimeter", "millimeters"),
         "mg": ("milligram", "milligrams"), "ml": ("milliliter", "milliliters"), "lbs": ("pound", "pounds"), "lb": ("pound", "pounds"), "oz": ("ounce", "ounces"),
         "ft": ("foot", "feet"), "mi": ("mile", "miles"), "°F": ("degree Fahrenheit", "degrees Fahrenheit"), "°C": ("degree Celsius", "degrees Celsius"),
         "°": ("degree", "degrees")}
UNIT_PATTERN = "|".join(re.escape(unit) for unit in sorted(UNITS, key=len, reverse=True))
UNIT = re.compile(AMOUNT + r"\s?(?P<unit>" + UNIT_PATTERN + r")(?![A-Za-z²³/])")
UNIT_COMPOUND = re.compile(r"(?<![\w.,-])(?P<n>\d+(?:\.\d+)?)-(?P<unit>mm|cm|km|kg|ft|lb|oz)(?![A-Za-z])")
THOUSANDS_K = re.compile(AMOUNT + r"[kK]" + NOT_LETTER_AFTER)

FRACTION = re.compile(r"(?<![\d/.,])(?:(?P<whole>\d+)\s+)?(?P<a>\d{1,3})/(?P<b>\d{1,3})(?![\d/])")
FRACTION_NAMES = {2: ("half", "halves"), 3: ("third", "thirds"), 4: ("quarter", "quarters"), 5: ("fifth", "fifths"), 6: ("sixth", "sixths"), 7: ("seventh", "sevenths"),
                  8: ("eighth", "eighths"), 9: ("ninth", "ninths"), 10: ("tenth", "tenths"), 16: ("sixteenth", "sixteenths"), 32: ("thirty-second", "thirty-seconds"),
                  100: ("hundredth", "hundredths")}
ROUND_THE_CLOCK = re.compile(r"(?<![\d/])24/7(?![\d/])")

DECADE = re.compile(r"(?<![\w.,])(?:['’](?P<short>\d0)|(?P<n>\d{2}|\d{4}))['’]?s" + NOT_LETTER_AFTER)
HYPHEN_COMPOUND = re.compile(r"(?<![\w.,/-])(?P<n>" + INTEGER + r"(?:\.\d+)?)-(?=[A-Za-z])")
DOTTED = re.compile(r"(?<![\w.,])\d+(?:\.\d+){2,}(?![\w.])")
DECIMAL = re.compile(r"(?<![\w.,])(?P<int>" + INTEGER + r")?\.(?P<dec>\d+)(?!\d)(?!\.\d)")
YEAR = re.compile(r"(?<![\w.,$£€#/-])(?P<y>\d{4})(?![\w])(?!,\d)(?!\.\d)")
CARDINAL = re.compile(r"(?<![\w.,])(?P<n>" + INTEGER + r")(?!\d)(?!,\d)" + NOT_LETTER_AFTER)

RULER_NAMES = ["John Paul", "Victor Emmanuel", "Juan Carlos", "Henry", "Edward", "George", "Elizabeth", "Charles", "James", "William", "Richard", "John", "Louis", "Philip",
               "Pius", "Gregory", "Benedict", "Leo", "Innocent", "Clement", "Urban", "Paul", "Francis", "Alexander", "Nicholas", "Peter", "Catherine", "Ivan", "Frederick",
               "Napoleon", "Mary", "Anne", "Victoria", "Stephen", "Harold", "Alfred", "Ferdinand", "Isabella", "Constantine", "Otto", "Boris", "Mohammed", "Ramses"]
ROMAN = re.compile(r"^(?=[IVXLC])C?(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$")
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
RULER = re.compile(r"\b(?P<name>" + "|".join(RULER_NAMES) + r")\s+(?P<num>[IVXLC]+)\b")
INITIAL_AFTER = re.compile(r"\.\s+[A-Z][a-z]+")
# A lone "I" after a ruler's name is the pronoun in "Mary I think" and the ordinal in "Elizabeth I was queen": it is an ordinal at the end of a clause or before one
# of these words, and the pronoun before any other word.
ORDINAL_I_FOLLOWERS = re.compile(r"^\s*(?:$|[.,;:!?)\u2019'\"\u201d]|(?:of|was|ruled|reigned|died|became|had|who|and|the|in|on|at|as|from)\b)")
WORLD_WAR = re.compile(r"\b(?P<w>World War)\s+(?P<num>I{1,3}|IV)\b")
ROMAN_LABEL = re.compile(r"\b(?P<w>Chapter|Part|Volume|Act|Book|Section|Article|Phase|Stage|Level|Appendix|Title|Schedule|Category)\s+(?P<num>(?!I\b)[IVXLC]+)\b")
