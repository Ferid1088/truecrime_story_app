"""Deterministic check that a rewrite adds no figures (Part 5d): every number
in a native rewrite must already occur in the approved master or in the
evidence. Digits of every script are compared as ASCII digits."""

from __future__ import annotations

import re

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_FIGURE = re.compile(r"\d[\d.,]*\d|\d")


def figures(text: str) -> set[str]:
    """Numbers of two or more digits (years, dates, amounts), separators removed."""
    found = set()
    for raw in _FIGURE.findall((text or "").translate(_DIGITS)):
        digits = re.sub(r"[.,]", "", raw)
        if len(digits) >= 2:
            found.add(digits)
    return found


def pack_text(pack: dict) -> str:
    parts: list[str] = []
    for key in ("facts", "timeline", "contradictions"):
        for item in pack.get(key) or []:
            parts += [str(item.get(k) or "") for k in (
                "claim", "original_claim", "supporting_text", "event_date", "description")]
    return " ".join(parts)


def added_figures(rewrite: str, master: str, pack: dict) -> list[str]:
    return sorted(figures(rewrite) - figures(master) - figures(pack_text(pack)))
