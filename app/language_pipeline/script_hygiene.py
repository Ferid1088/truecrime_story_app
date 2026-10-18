"""Script hygiene of an Arabic-script film text: what a TTS voice or a right-to-left display can trip
over. Counts, each with the first example position (line and column) and its surroundings. Information
only: nothing here blocks or changes a text (config language_stages.<lang>.report_script_hygiene)."""

from __future__ import annotations

import re

MARKER = re.compile(r"\[\[ACT:[^\]]+\]\]")
# Persian-form letters where Arabic forms are expected: keheh, Farsi yeh, heh with yeh above, ae.
PERSIAN_LETTERS = "کیۀە"
# Left/right marks, embeddings and overrides, isolates, the Arabic letter mark.
BIDI_CONTROLS = "‎‏‪‫‬‭‮⁦⁧⁨⁩؜"
TATWEEL = "ـ"
DIGIT_STYLES = {"western": "0123456789", "arabic_indic": "٠١٢٣٤٥٦٧٨٩",
                "persian": "۰۱۲۳۴۵۶۷۸۹"}
LATIN = re.compile(r"[A-Za-z]+")
CONTEXT = 20


def _where(text: str, index: int) -> dict:
    line = text.count("\n", 0, index) + 1
    column = index - (text.rfind("\n", 0, index) + 1) + 1
    return {"line": line, "column": column,
            "context": text[max(index - CONTEXT, 0):index + CONTEXT].replace("\n", " ")}


def _count(text: str, indexes: list[int]) -> dict:
    return {"count": len(indexes), "example": _where(text, indexes[0]) if indexes else None}


def _positions(text: str, characters: str) -> list[int]:
    return [i for i, ch in enumerate(text) if ch in characters]


def _lines(text: str):
    offset = 0
    for line in text.split("\n"):
        yield offset, line
        offset += len(line) + 1


def _beside_marker(text: str) -> list[int]:
    """Where a marker shares its line with other text (a marker must stand on a line of its own)."""
    found = []
    for start, line in _lines(text):
        marker = MARKER.search(line)
        if marker and line.replace(marker.group(0), "").strip():
            found.append(start + marker.start())
    return found


def check(text: str) -> dict:
    """The hygiene counts of one text. Markers are blanked (positions kept) before the digit and
    Latin-letter counts, so a marker's own letters and digits are not counted."""
    blanked = MARKER.sub(lambda m: " " * len(m.group(0)), text)
    styles = {name: _positions(blanked, digits) for name, digits in DIGIT_STYLES.items()}
    used = [name for name, found in styles.items() if found]
    return {
        "persian_letters": _count(text, _positions(text, PERSIAN_LETTERS)),
        "mixed_digit_styles": {"count": len(used) if len(used) > 1 else 0, "styles": used,
                               "example": _where(text, min(styles[n][0] for n in used)) if len(used) > 1 else None},
        "bidi_controls": _count(text, _positions(text, BIDI_CONTROLS)),
        "tatweel": _count(text, _positions(text, TATWEEL)),
        "latin_letters": _count(text, [m.start() for m in LATIN.finditer(blanked)]),
        "text_beside_marker": _count(text, _beside_marker(text)),
        "blocking": False, "model_calls": 0,
    }

