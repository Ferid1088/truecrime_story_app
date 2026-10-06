"""Originality / source-separation gates (Parts 16/17).

SourceSimilarityValidator: deterministic text-overlap check between a
generated story and the case's transcript corpus. Catches copied phrases
and long matching sequences — facts must overlap semantically, expression
must not. N-gram containment plus longest-common-sequence ratio, all
config-driven.

NarrativeStructureSimilarityChecker: compares the story's beat sequence
(section narrative_value ordering) against each video's claim-type
sequence — a story that replays one creator's exact structure trips the
gate even when every sentence is differently worded.
"""

import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    TranscriptClaim,
    TranscriptSegment,
    VideoSource,
)

_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def _ngrams(tokens: list[str], n: int) -> set[tuple]:
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _lcs_len(a: list[str], b: list[str]) -> int:
    """Longest common *contiguous* run between two token sequences —
    O(len(a)*len(b)) would be heavy, so use a two-row DP but only for
    contiguous matches (edit distance style is overkill here)."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _corpus_tokens(db: Session, case_id: int) -> dict[int, list[str]]:
    """Transcript token sequences keyed by video_source_id — the corpus the
    story must stay original against. Uses canonical English when present,
    otherwise the original text."""
    rows = (
        db.query(TranscriptSegment, TranscriptSegment.video_source_id)
        .join(VideoSource, TranscriptSegment.video_source_id == VideoSource.id)
        .filter(VideoSource.case_id == case_id)
        .order_by(TranscriptSegment.video_source_id, TranscriptSegment.segment_index)
        .all()
    )
    out: dict[int, list[str]] = {}
    for seg, vid in rows:
        out.setdefault(vid, []).extend(
            _tokens(seg.canonical_text_en or seg.text_original)
        )
    return out


def check_text_similarity(db: Session, case_id: int, story_text: str) -> dict:
    """How much of the story is verbatim-contiguous with any one transcript?

    Returns score 0..1 (higher = more similar), status pass|fail, and the
    worst offending video + matched n-grams for debugging. Zero similarity
    is NOT required — shared facts naturally share short phrases; the
    thresholds bound *sustained* copying only."""
    cfg = ai_config.similarity
    story_tokens = _tokens(story_text)
    if not story_tokens:
        return {"score": 0.0, "status": "pass", "detail": "empty story"}
    story_ngrams = _ngrams(story_tokens, cfg.ngram_size)
    worst = {"score": 0.0, "video_source_id": None, "overlap": 0.0, "lcs_ratio": 0.0}
    for vid, corpus in _corpus_tokens(db, case_id).items():
        if not corpus:
            continue
        c_ngrams = _ngrams(corpus, cfg.ngram_size)
        overlap = (
            len(story_ngrams & c_ngrams) / len(story_ngrams)
            if story_ngrams else 0.0
        )
        lcs_ratio = _lcs_len(story_tokens, corpus) / len(story_tokens)
        score = max(overlap / max(cfg.max_ngram_overlap_ratio, 1e-9),
                    lcs_ratio / max(cfg.max_longest_sequence_ratio, 1e-9))
        if score > worst["score"]:
            worst = {
                "score": score,
                "video_source_id": vid,
                "overlap": round(overlap, 4),
                "lcs_ratio": round(lcs_ratio, 4),
            }
    status = (
        "fail"
        if worst["score"] > 1.0
        else "pass"
    )
    return {
        "score": round(min(worst["score"], 9.99), 3),
        "status": status,
        "worst_video_source_id": worst["video_source_id"],
        "ngram_overlap": worst["overlap"],
        "longest_sequence_ratio": worst["lcs_ratio"],
    }


def _beat_sequence(sections: list[dict]) -> list[str]:
    return [
        (s.get("narrative_value") or s.get("type") or "fact")
        for s in sections
    ]


def check_structure_similarity(
    db: Session, case_id: int, sections: list[dict]
) -> dict:
    """Does the story's act/beat order replay one video's claim ordering?

    Each video's claim sequence (ordered by timestamp) becomes a type
    signature; the story's section signature is compared via LCS ratio of
    the type sequences. A high ratio means 'same story shape' — allowed to
    be similar where the evidence itself demands it, so this yields a soft
    flag rather than a hard fail unless the overlap is extreme."""
    cfg = ai_config.similarity
    story_sig = _beat_sequence(sections)
    if len(story_sig) < 3:
        return {"score": 0.0, "status": "pass", "detail": "too few sections"}
    worst = {"ratio": 0.0, "video_source_id": None}
    claims = (
        db.query(TranscriptClaim)
        .join(VideoSource, TranscriptClaim.video_source_id == VideoSource.id)
        .filter(VideoSource.case_id == case_id)
        .order_by(TranscriptClaim.video_source_id, TranscriptClaim.timestamp_start)
        .all()
    )
    per_video: dict[int, list[str]] = {}
    for c in claims:
        per_video.setdefault(c.video_source_id, []).append(
            c.claim_type or "fact"
        )
    for vid, sig in per_video.items():
        if len(sig) < 3:
            continue
        # LCS on TYPE sequences — order-of-topics similarity, not wording.
        lcs = _lcs_len(story_sig, sig)
        ratio = lcs / min(len(story_sig), len(sig))
        if ratio > worst["ratio"]:
            worst = {"ratio": ratio, "video_source_id": vid}
    return {
        "score": round(worst["ratio"], 3),
        "status": (
            "fail" if worst["ratio"] >= cfg.structure_overlap_threshold
            else "pass"
        ),
        "worst_video_source_id": worst["video_source_id"],
    }
