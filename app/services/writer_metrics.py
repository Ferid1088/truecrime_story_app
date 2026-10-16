"""Deterministic metrics of narration text (Part 5b2): how repetitive, how
rhythmic, how cliché-ridden and how close to its planned length a beat is.
No model call; the same text always gives the same numbers."""

from __future__ import annotations

import re
import statistics
from collections import Counter

from app.core.ai_config import ai_config

_SENTENCE_END = re.compile(r"(?<=[.!?؟۔…])\s+")
_WORD = re.compile(r"[\w'’-]+", re.UNICODE)


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text or "") if s.strip()]


def sentence_length_stats(text: str) -> dict:
    lengths = [len(words(s)) for s in sentences(text)]
    if not lengths:
        return {"sentences": 0, "mean": 0.0, "variance": 0.0, "short_share": 0.0}
    short = ai_config.writer_metrics.short_sentence_words
    return {
        "sentences": len(lengths),
        "mean": round(statistics.fmean(lengths), 2),
        "variance": round(statistics.pvariance(lengths), 2),
        "short_share": round(sum(1 for n in lengths if n <= short) / len(lengths), 3),
    }


def repeated_trigram_rate(text: str) -> float:
    """Share of word trigrams that occur more than once (0 = never repeats)."""
    w = words(text)
    grams = [tuple(w[i:i + 3]) for i in range(len(w) - 2)]
    if not grams:
        return 0.0
    counts = Counter(grams)
    return round(sum(c for c in counts.values() if c > 1) / len(grams), 4)


def stock_phrase_hits(text: str, language: str) -> list[str]:
    low = (text or "").lower()
    return [p for p in ai_config.writer_metrics.stock_phrases.get(language, []) if p.lower() in low]


def length_error(text: str, target_words: int) -> float:
    """Signed relative error of the beat's length against its plan."""
    if not target_words:
        return 0.0
    return round((len(words(text)) - target_words) / target_words, 3)


def beat_metrics(text: str, language: str, target_words: int | None = None) -> dict:
    out = {
        "words": len(words(text)),
        "sentence_length": sentence_length_stats(text),
        "repeated_trigram_rate": repeated_trigram_rate(text),
        "stock_phrases": stock_phrase_hits(text, language),
    }
    if target_words:
        out["target_words"] = target_words
        out["length_error"] = length_error(text, target_words)
    return out


def stored_quality(critic_notes: dict) -> dict:
    """What the version's own validators recorded when it was written: the
    grounding result and the critic's scores (no new model call)."""
    gates = critic_notes.get("quality_gates") or {}
    return {
        "grounding_score": gates.get("grounding_score"),
        "unsupported_claims": gates.get("grounding_unsupported_claims"),
        "engagement_score": gates.get("engagement_score") or critic_notes.get("score"),
        "gate_failures": gates.get("failures") or [],
        "section_scores": critic_notes.get("section_scores") or [],
    }
