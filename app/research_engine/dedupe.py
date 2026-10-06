"""Deduplication & source-family detection (Part 26).

Ten copied articles must not count as ten independent sources.
Families are detected from same-account signals: near-identical text,
shared rare n-grams, title+publisher similarity, and citation chains.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_WORD = re.compile(r"[\w'’-]+", re.UNICODE)


def _shingles(text: str, n: int = 5) -> set[str]:
    words = [w.lower() for w in _WORD.findall(text or "")]
    return {
        hashlib.md5(" ".join(words[i:i + n]).encode()).hexdigest()[:16]
        for i in range(0, max(len(words) - n + 1, 0))
    }


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class FamilyDecision:
    is_duplicate: bool
    family: str | None
    independence_confidence: float
    reason: str | None = None


class SourceFamilyDetector:
    """Compares a candidate source against existing corpus text.

    Same-family means: substantially same account (copied/republished),
    not merely same topic. High bar: shared rare-word shingles and/or
    embedding similarity plus overlapping distinctive vocabulary.
    """

    def __init__(self, shingle_threshold: float = 0.35,
                 embed_threshold: float = 0.9):
        self.shingle_threshold = shingle_threshold
        self.embed_threshold = embed_threshold
        self._corpus: list[dict] = []   # {source_id, shingles, family, embed?}

    def add_source(self, source_id: int, text: str, family: str | None):
        self._corpus.append({
            "source_id": source_id,
            "shingles": _shingles(text),
            "family": family or f"src:{source_id}",
        })

    def classify(self, text: str,
                 embed_sim: float | None = None) -> FamilyDecision:
        """Decide whether `text` derives from an existing corpus source."""
        if not text or not self._corpus:
            return FamilyDecision(False, None, 1.0)
        shingles = _shingles(text)
        if not shingles:
            return FamilyDecision(False, None, 0.6)
        best = 0.0
        best_entry = None
        for entry in self._corpus:
            # Score = max(jaccard, containment). Containment catches a
            # long document republishing a short one — jaccard collapses
            # to ~0 on length mismatch even when one contains the other.
            ov = jaccard(shingles, entry["shingles"])
            smaller = min(len(shingles), len(entry["shingles"]))
            containment = len(shingles & entry["shingles"]) / smaller \
                if smaller else 0.0
            ov = max(ov, containment)
            if ov > best:
                best = ov
                best_entry = entry
        if best >= 0.65:
            return FamilyDecision(
                True, best_entry["family"], 0.1,
                f"near_identical:{best:.2f}")
        if best >= self.shingle_threshold or (
                embed_sim is not None and embed_sim >= self.embed_threshold):
            return FamilyDecision(
                False, best_entry["family"], 0.35,
                f"same_account:{best:.2f}")
        return FamilyDecision(False, None, 0.9)

    def family_key(self, publisher: str | None, title_norm: str) -> str:
        return f"{(publisher or '').strip().lower()}|{title_norm[:80]}".strip("|")
