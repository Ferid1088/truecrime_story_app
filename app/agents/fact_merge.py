"""Merge duplicates of one fact that were extracted from sources in several languages.

Facts are written in the canonical (English) form, so the same event reported
in English, German, Arabic and Persian arrives as several near-identical
claims. They are merged into one fact whose sources are the union of all of
them: one corroborated claim instead of the same claim repeated per language.
Different numbers or dates are never merged, whatever the similarity.
"""

from __future__ import annotations

import re

import numpy as np

from app.core.ai_config import ai_config
from app.core.env import env_value
from app.research_engine.embeddings import EmbeddingClient, cosine_matrix

_STRENGTH = {"primary": 5, "strong_secondary": 4, "secondary": 3, "tertiary": 2,
             "weak": 1, "disputed": 0}
_NUMBER = re.compile(r"\d+")
_LIST_FIELDS = ("source_ids", "chunk_ids", "people", "locations")


def default_embedder() -> EmbeddingClient:
    provider = ai_config.generation_provider()
    return EmbeddingClient(
        base_url=provider.base_url,
        api_key=env_value(provider.secret_env),
        model=provider.models.get("embedding") or "",
    )


def _numbers(claim: str) -> frozenset[int]:
    return frozenset(int(n) for n in _NUMBER.findall(claim))


def _dates_agree(a: str | None, b: str | None) -> bool:
    """Missing dates agree with anything; partial dates (1924) agree with a
    full date inside them (1924-09-10)."""
    return not a or not b or a.startswith(b) or b.startswith(a)


def _rank(fact: dict) -> tuple:
    return (_STRENGTH.get(fact.get("evidence_strength") or "", 0), fact.get("confidence") or 0,
            len(fact.get("supporting_text") or ""))


def _union(values: list) -> list:
    return list(dict.fromkeys(v for group in values for v in group))


class FactMerger:
    def __init__(self, embedder, min_similarity: float):
        self.embedder = embedder
        self.min_similarity = min_similarity

    async def merge(self, facts: list[dict]) -> list[dict]:
        """The facts with duplicates merged, in order of first appearance."""
        if len(facts) < 2:
            return facts
        vectors = await self.embedder.embed([f["claim"] for f in facts])
        similar = np.argwhere(np.triu(cosine_matrix(vectors, vectors) >= self.min_similarity, 1))
        numbers = [_numbers(f["claim"]) for f in facts]
        clusters = {i: [i] for i in range(len(facts))}
        owner = list(range(len(facts)))

        def compatible(i: int, j: int) -> bool:
            return numbers[i] == numbers[j] and _dates_agree(
                facts[i].get("event_date"), facts[j].get("event_date"))

        for i, j in similar:
            a, b = owner[i], owner[j]
            if a == b or not all(compatible(x, y) for x in clusters[a] for y in clusters[b]):
                continue
            for member in clusters[b]:
                owner[member] = a
            clusters[a] += clusters.pop(b)
        return [self._combine([facts[i] for i in sorted(members)])
                for members in sorted(clusters.values(), key=min)]

    @staticmethod
    def _combine(group: list[dict]) -> dict:
        if len(group) == 1:
            return group[0]
        best = max(group, key=_rank)
        one = dict(best)
        for field in _LIST_FIELDS:
            values = [f.get(field) or [] for f in group]
            one[field] = sorted(_union(values)) if field in ("source_ids", "chunk_ids") \
                else _union(values)
        one["confidence"] = max(f.get("confidence") or 0 for f in group)
        one["disputed"] = any(f.get("disputed") for f in group)
        return one
