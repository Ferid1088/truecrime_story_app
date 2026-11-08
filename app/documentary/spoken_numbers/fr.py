"""French numbers for the voice (France standard): what a narrator says for the digits of a script. Rules and sources: scratchpad/numbers/fr.md.

The pipeline runs the specific rules first (phones, dates, years, times, money, units, ordinals, ranges...) and the plain cardinal last, so a number
that no rule claims is still said as a number and no digit run is left behind."""

from __future__ import annotations

import re

from app.documentary.spoken_numbers import fr_words as W
from app.documentary.spoken_numbers.base import PROTECTED, LanguageNumbers
from app.documentary.spoken_numbers.fr_calendar import CalendarRules
from app.documentary.spoken_numbers.fr_core import NUM, SP, Options
from app.documentary.spoken_numbers.fr_measures import MeasureRules

_ORDINAL_SUFFIXES = "ères|ère|eres|ere|ers|er|res|re|èmes|ème|emes|eme|ndes|nde|nd|des|de|es|e|d"
_KNOWN_SUFFIXES = (_ORDINAL_SUFFIXES + "|km|cm|mm|kg|mg|ml|cl|ha|min|ans|an|Mrd|Md|M|k|h|g|l|L|m|t").split("|")
_NUMBERISH = re.compile(r"\d+(?:" + "|".join(sorted(map(re.escape, _KNOWN_SUFFIXES), key=len, reverse=True)) + r")?(?:h\d{2})?|\d+h\d{2}")
_PRIVATE = 0xE000
_PLACEHOLDER = re.compile("[\ue000-\uf8ff]")
_PLATE = re.compile(r"\b([A-Z]{2})-(\d{3})-([A-Z]{2})\b")
_PS = r"[ .\-\u00a0\u202f]"
_PHONE = re.compile(rf"(?<![\w+.,-])(?:(?:\+33|0033)(?:{SP}?\(0\))?{SP}?([1-9])((?:{_PS}?\d{{2}}){{4}})|(0[1-9])((?:{_PS}?\d{{2}}){{4}}))(?!\d)")
_HELPLINE = re.compile(rf"(?<![\d,.])(116{SP}000|116{SP}117|3919|3977|3237)(?!\d)")
_EMERGENCY = re.compile(
    rf"((?i:\b(?:appel\w*|compos\w*|numéro|joindre|contact\w*|au|le)){SP})(15|17|18|112|114|115|119|191|196)(?![\d,.])"
    rf"(?!{SP}?(?i:ans?|jours?|mois|heures?|minutes?|secondes?|personnes?|euros?|%))")
_CALL_CUES = re.compile(r"(?i:appel|compos|numéro|joindre|contact)")
_POSTAL = re.compile(r"(?<=, )(\d{2})(\d{3})(?=\s[A-ZÉÈ])")
_AGE = re.compile(r"\b([A-ZÉ][a-zàâçéèêëîïôûù]+) \((\d{1,2})\)")
_NUMERO = re.compile(rf"(?<!\w)[nN][°º](s?){SP}?(?=\d)")
_ARABIC_ORDINAL = re.compile(rf"(?<![\d,.])(\d+)({_ORDINAL_SUFFIXES})\b(?!['’])")
_ROMAN_ORDINAL = re.compile(r"(?<!\w)([IVXLCDM]+)(ères|ère|eres|ere|ers|er|res|re|èmes|ème|emes|eme|es|e)\b(?!['’])")
_ROMAN_NAME = re.compile(rf"(?<!\w)([A-ZÉÈ][\wàâçéèêëîïôûù'’-]*){SP}([IVX]+)\b(?![\w'’])")
_ROMAN_SECTION = re.compile(
    rf"((?i:\b(?:tomes?|volumes?|livres?|chapitres?|actes?|parties?|articles?|classes?|saisons?|épisodes?|séances?|scènes?|numéros?|sections?|titres?|"
    rf"annexes?|figures?|tableaux?|fascicules?|cahiers?|séries?|phases?|rounds?|niveaux?)){SP})([IVXLC]+)\b(?![\w'’])")
_SCORE = re.compile(
    rf"((?i:\b(?:score|l['’]emporte|s['’]impose|mène|menait|menant|victoire|défaite|battu|gagné|perdu|nul|match)\b)[^\d\n]{{0,25}}?)"
    rf"(\d{{1,2}}){SP}?[-:–]{SP}?(\d{{1,2}})(?!\d)")
_RANGE = re.compile(rf"(?<![\d/.,-])(\d+(?:,\d+)?){SP}?[-–]{SP}?(\d+(?:,\d+)?)(?![\d/-])")
_RANGE_CUE = re.compile(r"(?i:\b(?:de|d['’]|entre|pendant|environ|près de|sur|vers|à|jusqu['’]à)\s*)$")
_RANGE_UNIT = re.compile(rf"{SP}(?i:ans?|jours?|mois|semaines?|heures?|minutes?|secondes?|années?|personnes?|fois|euros?|kilos?|kilomètres?|mètres?|%|€)")
_SIGN_MINUS = re.compile(r"(?<![\w)\d])(?<!\d )[−-](?=\d)")
_SIGN_PLUS = re.compile(r"(?<![\w+])\+(?=\d)")
_NUMBER_NOUN = re.compile(rf"({NUM})(?:({SP}+)([^\W\d_]+))?")
_NO_PARTICLE = frozenset(("de", "d", "du", "des", "et", "à", "au", "aux", "ou", "pour", "par", "en", "sur", "dans", "contre", "avec", "sans"))
_REGNAL_VALUE = re.compile(r"^X{0,3}(IX|IV|V?I{0,3})$")
_SINGLE_ROMAN_NOUNS = frozenset((
    "siècle", "république", "arrondissement", "empire", "reich", "dynastie", "millénaire", "internationale", "armée", "législature", "symphonie", "corps",
    "division", "chambre", "section", "ligue", "régiment", "brigade"))
_REGNAL_NAMES = frozenset((
    "Louis", "Charles", "Henri", "Philippe", "Jean", "Napoléon", "François", "Pie", "Benoît", "Grégoire", "Clément", "Léon", "Urbain", "Paul", "Jean-Paul",
    "Alexandre", "Nicolas", "Pierre", "Guillaume", "Édouard", "Élisabeth", "Elizabeth", "Victor-Emmanuel", "Ferdinand", "Frédéric", "Léopold", "Albert",
    "Baudouin", "Henry", "George", "Georges", "Richard", "Jacques", "Catherine", "Isabelle", "Boniface", "Innocent", "Alphonse", "Juan", "Carlos",
    "Pedro", "Ramsès", "Rodolphe", "Joseph", "Constantin", "Étienne", "Hugues", "Robert", "Lothaire", "Clovis", "Dagobert"))
_FEMININE_SUFFIXES = frozenset(("ère", "ères", "ere", "eres", "re", "res", "de", "des", "nde", "ndes"))
_PLURAL_SUFFIXES = frozenset(("ers", "ères", "eres", "res", "es", "èmes", "emes", "des", "ndes"))
_SECOND_SUFFIXES = frozenset(("d", "nd", "de", "nde", "des", "ndes"))
_FIXED_SECOND = re.compile(rf"{SP}(Empire|Guerre mondiale)")


class Numbers(CalendarRules, MeasureRules, LanguageNumbers):
    """The numbers of a French text in words; `options` is the language's block of config voice.number_words (see fr_core.Options)."""

    def __init__(self, options: dict, max_value: int):
        super().__init__(options, max_value)
        self.opts = Options.model_validate(options)

    def text(self, text: str) -> str:
        """The base class sets aside any token that mixes letters and digits, but "1er", "10h30" and "9mm" are numbers a French narrator says, so only
        the tokens that are not number-and-unit ("COVID-19", "A4", "3D", "4x4") are kept, behind a placeholder that holds no digit."""
        kept: list[str] = []

        def keep(match: re.Match) -> str:
            token = match.group()
            if "@" not in token and not token.startswith(("http", "www")) and all(_NUMBERISH.fullmatch(part) for part in token.split("-")):
                return token
            kept.append(token)
            return chr(_PRIVATE + len(kept) - 1)

        converted = self.convert(PROTECTED.sub(keep, _PLATE.sub(self._plate, text)))
        return _PLACEHOLDER.sub(lambda m: kept[ord(m.group()) - _PRIVATE], converted)

    def convert(self, text: str) -> str:
        for rule in (self.phones, self.dates, self.years, self.decades, self.helplines, self.signs, self.times, self.money, self.million_words, self.percents,
                     self.calibres, self.units, self.numero, self.ordinals, self.roman_numbers, self.fractions, self.scores, self.ranges, self.postal_codes,
                     self.ages, self.plain_numbers):
            text = rule(text)
        return text

    @staticmethod
    def _plate(match: re.Match) -> str:
        spell = " ".join
        return (f"{spell(W.LETTER_NAMES[c] for c in match.group(1))}, {W.cardinal(int(match.group(2)))}, "
                f"{spell(W.LETTER_NAMES[c] for c in match.group(3))}")

    @staticmethod
    def _pair(digits: str) -> str:
        return f"zéro {W.SMALL[int(digits[1])]}" if digits[0] == "0" else W.cardinal(int(digits))

    def phones(self, text: str) -> str:
        """French numbers are said in pairs, a pair starting with 0 as "zéro six"; "+33 6 12..." is "plus trente-trois, six, douze..."."""
        def phone(match: re.Match) -> str:
            if match.group(1):
                digits = re.sub(r"\D", "", match.group(2))
                prefix = "zéro zéro trente-trois" if match.group().startswith("0033") else "plus trente-trois"
                return ", ".join([prefix, W.SMALL[int(match.group(1))], *(self._pair(digits[i:i + 2]) for i in range(0, 8, 2))])
            digits = match.group(3) + re.sub(r"\D", "", match.group(4))
            return ", ".join(self._pair(digits[i:i + 2]) for i in range(0, 10, 2))

        return _PHONE.sub(phone, text)

    def helplines(self, text: str) -> str:
        """Four-digit helplines are two pairs ("3919" = "trente-neuf dix-neuf"); short emergency numbers are numbers ("le 112" = "le cent douze")
        unless the owner asks for digits after a call cue or after "le"/"au"."""
        def helpline(match: re.Match) -> str:
            code = re.sub(SP, " ", match.group(1))
            if code == "116 000":
                return "cent seize zéro zéro zéro"
            if code == "116 117":
                return "cent seize, cent dix-sept"
            return f"{W.cardinal(int(code[:2]))} {W.cardinal(int(code[2:]))}"

        def emergency(match: re.Match) -> str:
            before = match.string[max(0, match.start() - 30):match.start()]
            if self.opts.emergency_numbers == "digits_in_context" and not _CALL_CUES.search(before + match.group(1)):
                return match.group()
            return f"{match.group(1)}{W.digits_words(match.group(2))}"

        text = _HELPLINE.sub(helpline, text)
        return text if self.opts.emergency_numbers == "number_always" else _EMERGENCY.sub(emergency, text)

    def signs(self, text: str) -> str:
        return _SIGN_PLUS.sub("plus ", _SIGN_MINUS.sub("moins ", text))

    def numero(self, text: str) -> str:
        return _NUMERO.sub(lambda m: f"numéro{m.group(1)} ", text)

    def _ordinal_phrase(self, value: int, suffix: str, following: str, roman: bool) -> str:
        feminine, plural = suffix in _FEMININE_SUFFIXES, suffix in _PLURAL_SUFFIXES
        fixed = _FIXED_SECOND.match(following) if self.opts.second_in_fixed_phrases and value == 2 else None
        if value == 2 and (suffix in _SECOND_SUFFIXES or fixed):
            word = ("seconde" if feminine or (fixed and fixed.group(1) != "Empire") else "second") + ("s" if plural else "")
            return word.capitalize() if fixed else word
        word = W.ordinal(value, feminine, plural)
        return word.capitalize() if roman and following[1:2].isupper() else word

    def ordinals(self, text: str) -> str:
        """1er/1re = premier/première, 2e = deuxième (2d/2nd = second), 21e = vingt et unième; the suffix forms 3ème, ᵉ, ʳᵉ are the same."""
        def arabic(match: re.Match) -> str:
            value, suffix = int(match.group(1)), match.group(2)
            if suffix in _SECOND_SUFFIXES and value != 2:
                return match.group()
            return self._ordinal_phrase(value, suffix, match.string[match.end():], False)

        return _ARABIC_ORDINAL.sub(arabic, text.translate(str.maketrans("ᵉʳᵈⁿˢ", "erdns")))

    def roman_numbers(self, text: str) -> str:
        """A Roman numeral with an ordinal suffix is an ordinal ("XXe siècle", "Ier"); a bare one after a ruler is a cardinal ("Louis Quatorze",
        "Napoléon Trois", "Jean-Paul Deux", I = "Premier"); after tome/chapitre/acte... it is a cardinal too."""
        def suffixed(match: re.Match) -> str:
            numeral, suffix, rest = match.group(1), match.group(2), match.string[match.end():]
            value = W.roman_value(numeral)
            if value is None:
                return match.group()
            first_word = re.match(rf"{SP}(\w+)", rest)
            if len(numeral) == 1 and not (
                    (numeral == "I" and suffix in ("er", "re", "ère", "ers", "res", "ères")) or
                    (numeral in "VX" and suffix in ("e", "es", "ème", "èmes") and first_word and first_word.group(1).lower() in _SINGLE_ROMAN_NOUNS)):
                return match.group()
            said = self._ordinal_phrase(value, suffix, rest, True)
            return said.capitalize() if numeral == "I" and re.search(r"[A-ZÉ]\w*$", match.string[:match.start()].rstrip()) else said

        def regnal(match: re.Match) -> str:
            name, numeral = match.group(1), match.group(2)
            if not _REGNAL_VALUE.match(numeral) or (len(numeral) == 1 and name not in _REGNAL_NAMES):
                return match.group()
            said = "Premier" if numeral == "I" else re.sub(r"(^|-)(\w)", lambda m: m.group(1) + m.group(2).upper(), W.cardinal(W.roman_value(numeral)))
            return f"{name} {said}"

        def section(match: re.Match) -> str:
            value = W.roman_value(match.group(2))
            return match.group() if value is None else f"{match.group(1)}{W.cardinal(value)}"

        text = _ROMAN_ORDINAL.sub(suffixed, text)
        return _ROMAN_SECTION.sub(section, _ROMAN_NAME.sub(regnal, text))

    def scores(self, text: str) -> str:
        return _SCORE.sub(lambda m: f"{m.group(1)}{W.cardinal(int(m.group(2)))} à {W.cardinal(int(m.group(3)))}", text)

    def ranges(self, text: str) -> str:
        """"5-10 ans", "de 5-10" = "cinq à dix"; a bare hyphen between numbers elsewhere (a phone, a reference) is left to the plain numbers."""
        def span(match: re.Match) -> str:
            before, after = match.string[:match.start()], match.string[match.end():]
            if not (_RANGE_CUE.search(before) or _RANGE_UNIT.match(after)):
                return match.group()
            return f"{self.say_number(match.group(1))} à {self.say_number(match.group(2), feminine=self.next_word(match) in W.FEMININE_NOUNS)}"

        return _RANGE.sub(span, text)

    def postal_codes(self, text: str) -> str:
        """"75001 Paris": the département as a number, then the three digits one by one when they start with 0, else as a number (UNSURE in the spec,
        rarely read in a film; the owner can switch it off)."""
        def postal(match: re.Match) -> str:
            department, rest = int(match.group(1)), match.group(2)
            if not 1 <= department <= 98:
                return match.group()
            return f"{W.cardinal(department)} {W.digits_words(rest) if rest[0] == '0' else W.cardinal(int(rest))}"

        return _POSTAL.sub(postal, text) if self.opts.postal_codes else text

    def ages(self, text: str) -> str:
        return _AGE.sub(lambda m: f"{m.group(1)} ({W.cardinal(int(m.group(2)))} ans)", text) if self.opts.age_in_parentheses else text

    def plain_numbers(self, text: str) -> str:
        """Every number left: a cardinal, feminine before a feminine noun, no s on "vingt"/"cent" before "mille", "de"/"d'" between a whole number of
        millions and its noun ("trois millions d'habitants"), a space when letters touch it."""
        def plain(match: re.Match) -> str:
            token, gap, noun = match.groups()
            following = (noun or "").lower()
            said = self.say_number(token, feminine=following in W.FEMININE_NOUNS, plural_end=following != "mille")
            if not noun:
                return f"{said} " if self.starts_letter(match) else said
            integer, decimal = self.split(token)
            if decimal is None and integer.strip("0") and int(integer) % 10**6 == 0 and following not in _NO_PARTICLE:
                gap = f" {self._particle(following)}"
            return f"{said}{gap}{noun}"

        return _NUMBER_NOUN.sub(plain, text)
