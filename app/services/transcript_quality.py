"""Part 3(e) item 3: cheap, deterministic transcript quality gating.

Manual captions are already preferred over auto-generated ones
(app.providers.transcript.youtube) and are trusted by type — this module
only scores AUTO-CAPTION transcripts, where YouTube's own speech-to-text
can produce garbled fragments, repeated-character noise, and text with no
punctuation at all. No model call: every signal here is a regex/character
count, same spirit as app.core.quote_check's deterministic substring match.
"""
from __future__ import annotations

import re

from app.utils import detect_language

# Characters that are neither a Unicode word character, whitespace, nor
# ordinary punctuation — \w already covers Persian/Arabic/any script's own
# letters, so this only flags genuine symbol/noise characters, not a
# non-Latin script itself.
_NOISE_CHAR = re.compile(r"[^\w\s.,!?;:'\"()\-–—…،؟]", re.UNICODE)
_REPEATED_RUN = re.compile(r"(.)\1{4,}")
_TERMINAL_PUNCT = re.compile(r"[.!?…؟。]\s*$")


def is_segment_garbled(text: str, cfg) -> bool:
    """A single caption segment is garbled if it has a long run of the same
    character (ASR noise) or an unusually high share of non-text symbols."""
    text = (text or "").strip()
    if not text:
        return False
    if _REPEATED_RUN.search(text):
        return True
    noise = len(_NOISE_CHAR.findall(text))
    return (noise / len(text)) > cfg.max_segment_noise_ratio


def _has_terminal_punctuation(text: str) -> bool:
    return bool(_TERMINAL_PUNCT.search((text or "").strip()))


def score_transcript(texts: list[str], expected_language: str | None, cfg) -> dict:
    """Aggregate quality over a whole auto-caption transcript's segment
    texts. `expected_language` is the video's own declared research/
    discovery language (not re-detected per call — detect_language is run
    once here, cheaply, over a sample of the real text).

    Returns {garbled_rate, punctuation_ratio, detected_language,
    language_match, score, verdict}. `score` is 1 - garbled_rate (directly
    interpretable: the clean-content share); `verdict` is good/degraded/
    poor, derived from configured thresholds, not the raw score alone,
    since a language mismatch alone is disqualifying regardless of how
    clean the (wrong-language) text looks."""
    if not texts:
        return {
            "garbled_rate": 0.0, "punctuation_ratio": 1.0,
            "detected_language": None, "language_match": None,
            "score": 1.0, "verdict": "good",
        }
    garbled_flags = [is_segment_garbled(t, cfg) for t in texts]
    garbled_rate = sum(garbled_flags) / len(texts)
    punctuation_ratio = sum(1 for t in texts if _has_terminal_punctuation(t)) / len(texts)
    combined = " ".join(texts)[:5000]
    detected_lang, _confidence = detect_language(combined)
    language_match = None
    if expected_language and detected_lang:
        language_match = detected_lang == expected_language[:2].lower()
    score = round(1.0 - garbled_rate, 4)
    if garbled_rate > cfg.max_acceptable_garbled_rate or language_match is False:
        verdict = "poor"
    elif garbled_rate > cfg.degraded_garbled_rate or punctuation_ratio < cfg.min_punctuation_ratio:
        verdict = "degraded"
    else:
        verdict = "good"
    return {
        "garbled_rate": round(garbled_rate, 4),
        "punctuation_ratio": round(punctuation_ratio, 4),
        "detected_language": detected_lang,
        "language_match": language_match,
        "score": score,
        "verdict": verdict,
    }
