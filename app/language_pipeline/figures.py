"""Figures (numbers and years) in a text, whatever script or separator it uses, and the
figures the evidence supports. Deterministic: no model is involved."""

from __future__ import annotations

import json
import re

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_THOUSANDS = r"[.,٬'   ]"
_FIGURE = re.compile(
    rf"\d{{1,3}}(?:{_THOUSANDS}\d{{3}})+(?:[.,٫]\d+)?|\d+(?:[.,٫]\d+)?")

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
          "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
          "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_TENS_RE, _UNITS_RE, _SMALL_RE = "|".join(_TENS), "|".join(_UNITS), "|".join(list(_UNITS)[1:10])
_WORDS = re.compile(
    rf"\b(?:(?:{_TENS_RE})(?:[- ](?:{_SMALL_RE}))?|(?:{_UNITS_RE}))(?:[ -](?:hundred|thousand))?"
    rf"(?:[ -]and[ -](?:{_TENS_RE})(?:[- ](?:{_SMALL_RE}))?)?\b", re.I)


_ID = re.compile(r"[A-Za-z]{1,4}[-_]?\d+")
_URL = re.compile(r"https?://|www\.")


def normalise(token: str) -> str:
    """A figure as digits only: separators dropped, a trailing decimal part kept."""
    token = token.translate(_DIGITS)
    decimal = re.search(r"[.,٫](\d{1,2})$", token)
    if decimal and not re.search(rf"{_THOUSANDS}\d{{3}}$", token):
        head = re.sub(r"\D", "", token[:decimal.start()])
        return f"{head}.{decimal.group(1)}"
    return re.sub(r"\D", "", token)


def figures_in(text: str, min_digits: int = 2) -> list[str]:
    """Every figure of `min_digits` or more digits (two: years, ages, amounts, durations;
    one: day numbers and counts too), as digits."""
    out = []
    for m in _FIGURE.finditer((text or "").translate(_DIGITS)):
        value = normalise(m.group(0))
        if len(value.replace(".", "")) >= min_digits:
            out.append(value)
    return out


def _words_to_number(phrase: str) -> int | None:
    tokens = [t for t in re.split(r"[- ]+", phrase.lower()) if t and t != "and"]
    total, current = 0, 0
    for t in tokens:
        if t in _UNITS:
            current += _UNITS[t]
        elif t in _TENS:
            current += _TENS[t]
        elif t == "hundred":
            current = max(current, 1) * 100
        elif t == "thousand":
            total += max(current, 1) * 1000
            current = 0
        else:
            return None
    return total + current


def written_figures(text: str, minimum: int = 10) -> set[str]:
    """English number words in an evidence text ("ninety", "forty-one"), as digits."""
    out = set()
    for m in _WORDS.finditer(text or ""):
        value = _words_to_number(m.group(0))
        if value is not None and value >= minimum:
            out.add(str(value))
    return out


def supported_figures(*sources, min_digits: int = 2) -> set[str]:
    """The figures an evidence pack and plan state: as digits, as words, and every
    number inside a range ("69 or 70")."""
    texts: list[str] = []

    def walk(value):
        if isinstance(value, str):
            if not _ID.fullmatch(value) and not _URL.search(value):
                texts.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    for source in sources:
        walk(source)
    figures: set[str] = set()
    minimum = 10 ** (min_digits - 1)
    for text in texts:
        figures.update(figures_in(text, min_digits))
        figures.update(written_figures(text, minimum))
    return figures


def unsupported(text: str, allowed: set[str], min_digits: int = 2) -> list[dict]:
    """Figures the text states that the evidence does not, each with where it stands."""
    flat = (text or "").translate(_DIGITS)
    found: list[dict] = []
    seen: set[str] = set()
    for m in _FIGURE.finditer(flat):
        value = normalise(m.group(0))
        if len(value.replace(".", "")) < min_digits or value in allowed or value in seen:
            continue
        seen.add(value)
        start = max(m.start() - 40, 0)
        found.append({"figure": value, "context": flat[start:m.end() + 40].replace("\n", " ")})
    return found


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)
