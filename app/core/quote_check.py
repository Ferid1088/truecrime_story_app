"""Deterministic check: does a quoted/paraphrased passage actually occur in
the source text it claims to come from? No model call — a fuzzy sliding-
window match in code. Part 2(e): only facts this cannot confirm need an
LLM grounding check at all.
"""

from __future__ import annotations

from difflib import SequenceMatcher

MATCH_THRESHOLD = 0.5


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def quote_is_supported(quote: str | None, source_text: str) -> bool | None:
    """True if `quote` is a close match for some span of `source_text`,
    False if it clearly is not, None if there is nothing to check (no quote,
    or no source text to check it against — a configuration gap, not a
    verified-absent quote)."""
    if not quote:
        return None
    if not source_text:
        return None
    words = source_text.split()
    if not words:
        return None
    span = max(len(quote.split()), 5)
    best = 0.0
    for i in range(0, max(len(words) - span, 1), max(span // 2, 1)):
        window = " ".join(words[i:i + span * 2])
        best = max(best, _similar(quote, window))
        if best >= MATCH_THRESHOLD:
            break
    return best >= MATCH_THRESHOLD
