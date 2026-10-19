"""Persian and Arabic text must be stored in logical order (the order it is read and typed), as
plain letters. A terminal that shows right-to-left text from left to right makes a correct string
look reversed; a really reversed string, shaped presentation forms or direction marks would
break the voice, the subtitles and the checks."""

from __future__ import annotations

import unicodedata

_BIDI_MARKS = set("‎‏‪‫‬‭‮⁦⁧⁨⁩")
_SENTENCE_PUNCTUATION = set(".!?؟…،,;؛:")


def logical_order_issues(text: str) -> list[str]:
    """What is wrong with a right-to-left text as stored: empty when it is logical, plain Unicode."""
    issues = []
    stripped = (text or "").strip()
    if not stripped:
        return ["empty"]
    if unicodedata.normalize("NFC", text) != text:
        issues.append("not_nfc")
    if any(0xFB50 <= ord(c) <= 0xFDFF or 0xFE70 <= ord(c) <= 0xFEFF for c in text):
        issues.append("presentation_forms")
    if any(c in _BIDI_MARKS for c in text):
        issues.append("direction_marks")
    if stripped[0] in _SENTENCE_PUNCTUATION:
        issues.append("starts_with_punctuation_as_if_reversed")
    return issues
