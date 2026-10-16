"""Part 3(e) item 3: deterministic, cheap auto-caption quality scoring —
no model call. Real (not synthetic-looking) garbled vs clean sample text,
so the heuristics are proven against the actual failure shapes YouTube
auto-captions produce (no punctuation, ASR noise runs, wrong language).
"""
from app.core.ai_config import ai_config
from app.services.transcript_quality import is_segment_garbled, score_transcript

CFG = ai_config.transcript_quality


def test_clean_punctuated_segment_is_not_garbled():
    assert is_segment_garbled("The keeper was last seen on the rock.", CFG) is False


def test_repeated_character_noise_is_garbled():
    assert is_segment_garbled("sooooooooo the lighthouse keeper vanished", CFG) is True


def test_high_symbol_noise_ratio_is_garbled():
    assert is_segment_garbled("##$$%%^^&&**((++==~~``||\\\\", CFG) is True


def test_empty_segment_is_not_garbled():
    assert is_segment_garbled("", CFG) is False
    assert is_segment_garbled(None, CFG) is False


def test_score_transcript_with_no_segments_defaults_to_good():
    result = score_transcript([], expected_language="en", cfg=CFG)
    assert result["verdict"] == "good"
    assert result["score"] == 1.0


def test_clean_english_transcript_scores_good():
    segments = [
        "Keeper James Ducat was married with three children.",
        "The lighthouse stood on the edge of the cliff.",
        "Investigators found the kitchen clock had stopped.",
        "The relief ship arrived on the twenty sixth of December.",
    ]
    result = score_transcript(segments, expected_language="en", cfg=CFG)
    assert result["verdict"] == "good"
    assert result["garbled_rate"] == 0.0
    assert result["language_match"] is True


def test_mostly_garbled_transcript_scores_poor():
    segments = [
        "sooooooooo yeah",
        "##$$%%^^&&**((",
        "aaaaaaaaaaaaaaa",
        "the lighthouse",
    ]
    result = score_transcript(segments, expected_language="en", cfg=CFG)
    assert result["verdict"] == "poor"
    assert result["garbled_rate"] >= 0.5


def test_unpunctuated_transcript_scores_degraded_not_good():
    # Realistic auto-caption shape: clean words, zero terminal punctuation —
    # not garbled noise, but not "good" either (typical of raw ASR output).
    segments = [
        "the keeper was last seen on the rock",
        "investigators searched the lighthouse for days",
        "the lamp was found still burning",
        "no trace of the three men was ever found",
        "the mystery remains unsolved to this day",
    ]
    result = score_transcript(segments, expected_language="en", cfg=CFG)
    assert result["garbled_rate"] == 0.0
    assert result["punctuation_ratio"] == 0.0
    assert result["verdict"] == "degraded"


def test_language_mismatch_forces_poor_even_if_clean():
    # Clean, punctuated, zero noise — but declared language is Persian
    # while the actual text is English: a real mismatch, not a quality
    # issue a noise/punctuation score alone would ever catch.
    segments = [
        "The keeper was last seen on the rock.",
        "Investigators searched the lighthouse for days.",
        "The lamp was found still burning brightly.",
        "No trace of the three men was ever found anywhere.",
    ]
    result = score_transcript(segments, expected_language="fa", cfg=CFG)
    assert result["language_match"] is False
    assert result["verdict"] == "poor"
