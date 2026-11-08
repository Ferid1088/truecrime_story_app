"""The little German grammar the number reader needs: the case a number stands in and the gender of the noun after it.

A number word agrees only in two places, the 1 before a noun ("ein Mann", "eine Frau", "einen Euro", "mit einem Messer") and the ending of an ordinal ("am
dritten Mai", "der dritte Mai"). A real parser is out of reach, so the case is read from the one or two words before the number and the gender from a small
lexicon of the nouns a true-crime script uses. What neither knows falls back to the safe form: "ein" for an unknown noun, "-en" for an ordinal.
"""

from __future__ import annotations

import re
from enum import Enum


class Case(Enum):
    NOMINATIVE = "nominative"
    ACCUSATIVE = "accusative"
    DATIVE = "dative"
    GENITIVE = "genitive"


WORD_OR_MARK = re.compile(r"[A-Za-zÄÖÜäöüß]+|[^\sA-Za-zÄÖÜäöüß]")
DATIVE_WORDS = {"mit", "seit", "von", "vom", "bei", "beim", "zu", "zum", "zur", "nach", "aus", "am", "im", "dem", "einem", "jedem", "diesem", "keinem", "meinem",
                "seinem", "ihrem", "ab", "an", "unter", "auf", "in", "vor", "neben", "hinter", "zwischen"}
ACCUSATIVE_WORDS = {"für", "durch", "gegen", "ohne", "um", "den", "einen", "ins", "pro"}
GENITIVE_WORDS = {"des", "eines", "wegen", "trotz", "während", "statt", "anstatt", "innerhalb", "außerhalb"}
# Prepositions that put a feminine "der" into the dative ("in der 7. Klasse") and not into the nominative ("der 3. Mai").
DATIVE_PREPOSITIONS = {"mit", "seit", "von", "bei", "zu", "nach", "aus", "ab", "an", "unter", "auf", "in", "vor", "neben", "hinter", "zwischen"}
# Words that turn a following number into a label ("Platz 1", "Kapitel 3"): the 1 is then counted, "eins".
LABEL_NOUNS = {"platz", "kapitel", "artikel", "seite", "absatz", "satz", "zimmer", "raum", "nummer", "nr", "gleis", "etage", "stock", "stufe", "runde",
               "staffel", "folge", "teil", "band", "akt", "spieltag", "gruppe", "block", "linie", "zelle", "station", "level", "version", "formel", "tor",
               "bahnsteig", "hausnummer", "zeile", "abschnitt", "paragraf", "paragraph", "buch", "szene", "tag"}
STREET_ENDINGS = ("straße", "strasse", "str", "weg", "allee", "gasse", "ring", "damm", "ufer", "chaussee", "platz")

FEMININE, MASCULINE, NEUTER = "f", "m", "n"
ONE = {(MASCULINE, Case.NOMINATIVE): "ein", (MASCULINE, Case.ACCUSATIVE): "einen", (MASCULINE, Case.DATIVE): "einem", (MASCULINE, Case.GENITIVE): "eines",
       (NEUTER, Case.NOMINATIVE): "ein", (NEUTER, Case.ACCUSATIVE): "ein", (NEUTER, Case.DATIVE): "einem", (NEUTER, Case.GENITIVE): "eines",
       (FEMININE, Case.NOMINATIVE): "eine", (FEMININE, Case.ACCUSATIVE): "eine", (FEMININE, Case.DATIVE): "einer", (FEMININE, Case.GENITIVE): "einer"}
_LEXICON = {
    MASCULINE: "schuss tag monat mann täter zeuge stich schlag euro cent dollar meter kilometer zentimeter millimeter liter grad polizist beamter hinweis anruf "
               "schritt fall versuch weg mord zug wagen raum bruder sohn vater onkel nachbar freund mensch arzt richter prozess bericht brief schlüssel hund tod "
               "grund fehler verdacht abend morgen mittag verdächtiger angeklagter tatverdächtiger zeuge tote revolver schüler lehrer sekundenbruchteil "
               "quadratmeter kubikmeter",
    NEUTER: "jahr kind opfer mädchen haus messer gramm kilogramm mal pfund auto boot wort leben ergebnis buch bett zimmer foto telefon handy geständnis urteil "
            "mitglied wochenende jahrzehnt jahrhundert stück paar gewehr magazin loch blut glas dokument fahrzeug versteck prozent hektar",
    FEMININE: "frau woche stunde minute sekunde tat nacht million milliarde waffe wunde tochter mutter schwester nachbarin zeugin täterin polizistin spur kugel "
              "pistole leiche zeit strafe aussage nummer mark seite tür wohnung straße stadt familie person runde anzeige tasche flasche antwort verurteilung "
              "festnahme stimme tonne",
}
GENDER = {noun: gender for gender, nouns in _LEXICON.items() for noun in nouns.split()}
_SUFFIX_GENDERS = {"ung": FEMININE, "heit": FEMININE, "keit": FEMININE, "schaft": FEMININE, "tion": FEMININE, "tät": FEMININE, "chen": NEUTER, "lein": NEUTER,
                   "ling": MASCULINE, "ismus": MASCULINE}
_COMPOUND_KEYS = sorted((key for key in GENDER if len(key) >= 4), key=len, reverse=True)


def words_before(before: str, count: int = 2) -> list[str]:
    """The last `count` words (lower case) or punctuation marks of `before`."""
    return [token.lower() for token in WORD_OR_MARK.findall(before)[-count:]]


def case_before(before: str) -> Case:
    """The case the word after `before` most likely stands in: from the preposition or article just before it; nominative when nothing says otherwise."""
    previous = words_before(before, 1)
    word = previous[0] if previous else ""
    if word in GENITIVE_WORDS:
        return Case.GENITIVE
    if word in ACCUSATIVE_WORDS:
        return Case.ACCUSATIVE
    if word in DATIVE_WORDS:
        return Case.DATIVE
    return Case.NOMINATIVE


def gender_of(noun: str) -> str | None:
    """The gender of a German noun by the lexicon, its last compound part ("Messerstich" -> masculine) or a telling suffix; None when unknown."""
    word = noun.lower()
    if word in GENDER:
        return GENDER[word]
    for key in _COMPOUND_KEYS:
        if word.endswith(key):
            return GENDER[key]
    for suffix, gender in _SUFFIX_GENDERS.items():
        if word.endswith(suffix):
            return gender
    return None


def one_before_noun(before: str, gender: str | None) -> str:
    """The form of a 1 before a noun: "ein/eine/einen/einem/einer/eines" by gender and case; "ein" for an unknown gender, the safe form."""
    if gender is None:
        return "ein"
    return ONE[(gender, case_before(before))]


def is_label(before: str) -> bool:
    """Whether the word before is one that labels a number ("Platz 1", "Hauptstraße 1"), so the 1 is counted ("eins") and not an article."""
    previous = words_before(before, 1)
    if not previous:
        return False
    word = previous[0]
    return word in LABEL_NOUNS or word.endswith(STREET_ENDINGS)


def ordinal_ending(before: str) -> str:
    """The ending an ordinal takes after `before`: "-en" after am/vom/zum/zur/im/den/dem/des, "-e" after der/die/das in the nominative ("der dritte Mai") and
    "-en" for a feminine dative ("in der siebten Klasse"); "-en" when nothing says otherwise, because most dates follow a preposition."""
    previous = words_before(before, 2)
    word = previous[-1] if previous else ""
    earlier = previous[-2] if len(previous) > 1 else ""
    if word == "der":
        return "en" if earlier in DATIVE_PREPOSITIONS else "e"
    if word in {"die", "das", "ins", "eine"}:
        return "e"
    return "en"
