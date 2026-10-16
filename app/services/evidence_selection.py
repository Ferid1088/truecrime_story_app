"""The fact cap, in one place: which facts a story pack or a research stage
(timeline, contradictions) receives. Never deletes a Fact row; every excluded
fact is returned with its reason."""

from __future__ import annotations

import json
import re

from app.core.ai_config import ai_config
from app.db.models import Contradiction, Fact, Source

EVIDENCE_RANK = {"primary": 5, "strong_secondary": 4, "secondary": 3, "tertiary": 2,
                 "weak": 1, "disputed": 0}
_TERM = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def fact_source_ids(f: Fact) -> list:
    try:
        return json.loads(f.source_ids_json or "[]")
    except (ValueError, TypeError):
        return []


def by_id(rows: list) -> list:
    """Deterministic order: database primary key first, insertion order for
    rows that are not flushed yet."""
    return sorted(rows, key=lambda r: (getattr(r, "id", None) is None, getattr(r, "id", None) or 0))


class EvidenceSelector:
    """At most `cap` facts, strongest first. Ranking: backed by a YouTube video
    transcript, then evidence_strength, the best source reliability, the number
    of corroborating sources, confidence. Disputed facts and facts that stand in
    a stored contradiction are never dropped (the cap applies to the rest).
    Taken round-robin over the fact types so humans, scenes, quotes and
    investigation all stay represented; returned in their original order."""

    def __init__(self, cap: int, sources: list[Source] | None = None,
                 contradictions: list[Contradiction] | None = None):
        self.cap = cap
        self.reliability = {s.id: (s.reliability_score if s.reliability_score is not None else 0.5)
                            for s in sources or []}
        self.video_sources = {s.id for s in sources or [] if s.source_type == "youtube_video"}
        self.contradictions = [(set(self._sources_of(c)), self._terms(f"{c.topic} {c.description}"))
                               for c in contradictions or []]

    @staticmethod
    def _sources_of(c: Contradiction) -> list:
        try:
            return json.loads(c.source_ids_json or "[]")
        except (ValueError, TypeError):
            return []

    @staticmethod
    def _terms(text: str) -> set[str]:
        n = ai_config.evidence_selection.term_min_length
        return {t.lower() for t in _TERM.findall(text or "") if len(t) >= n}

    def video_backed(self, f: Fact) -> bool:
        return (json.loads(f.transcript_claim_ids_json or "[]") != []
                or bool(self.video_sources & set(fact_source_ids(f))))

    def in_contradiction(self, f: Fact) -> bool:
        """The contradiction rows name sources, not facts: a fact stands in one
        when it shares a source with it and enough of its terms."""
        sources, terms = set(fact_source_ids(f)), self._terms(f.claim)
        need = ai_config.evidence_selection.contradiction_min_shared_terms
        return any(cs & sources and len(terms & ct) >= need for cs, ct in self.contradictions)

    def is_protected(self, f: Fact) -> bool:
        return bool(f.disputed) or self.in_contradiction(f)

    def _best_reliability(self, f: Fact) -> float:
        ids = fact_source_ids(f)
        return max((self.reliability.get(i, 0.5) for i in ids), default=0.5)

    def _rank_key(self, f: Fact) -> tuple:
        return (not self.video_backed(f), -EVIDENCE_RANK.get(f.evidence_strength, 0),
                -self._best_reliability(f), -len(fact_source_ids(f)), -(f.confidence or 0), f.id)

    def select(self, facts: list[Fact]) -> tuple[list[Fact], list[dict]]:
        protected = [f for f in facts if self.is_protected(f)]
        rest = [f for f in facts if f not in protected]
        room = max(self.cap - len(protected), 0)
        if len(rest) <= room:
            return by_id(protected + rest), []
        groups: dict[str, list[Fact]] = {}
        for f in sorted(rest, key=self._rank_key):
            groups.setdefault((f.narrative_value or "other").split("_")[0], []).append(f)
        queues = [iter(group) for group in groups.values()]
        picked: list[Fact] = []
        while len(picked) < room and queues:
            for queue in list(queues):
                fact = next(queue, None)
                if fact is None:
                    queues.remove(queue)
                elif len(picked) < room:
                    picked.append(fact)
        kept = {f.id for f in picked}
        dropped = [{"fact_id": f.id, "reason": (
            "ranked below the evidence diet cap "
            f"(video_backed={self.video_backed(f)}, evidence_strength={f.evidence_strength}, "
            f"max_source_reliability={self._best_reliability(f)}, "
            f"corroboration={len(fact_source_ids(f))}, confidence={f.confidence})")}
            for f in rest if f.id not in kept]
        return by_id(protected + picked), dropped
