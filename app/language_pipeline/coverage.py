"""Which evidence items a text tells, and in which act.

A sentence tells an evidence item when their multilingual embeddings are close enough.
The same measure works for every language, so the reveal-order gate and the
cross-language report speak about one thing."""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from app.core.ai_config import ai_config
from app.language_pipeline.materials import SharedMaterial
from app.research_engine.embeddings import cosine_matrix

_SENTENCE_END = re.compile(r"(?<=[.!?؟۔…])\s+|\n+")


def sentences(text: str, min_words: int) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text or "") if len(s.split()) >= min_words]


@dataclass(frozen=True)
class Telling:
    evidence_id: str
    act_id: str
    score: float
    sentence: str


class EvidenceCoverage:
    def __init__(self, embedder):
        self.embedder = embedder
        self._claims: dict[str, np.ndarray] = {}

    async def _claim_vectors(self, material: SharedMaterial) -> tuple[list[str], np.ndarray]:
        ids = [f["id"] for f in material.pack.get("facts") or []]
        key = material.cache_key()
        if key not in self._claims:
            claims = [f.get("claim") or "" for f in material.pack.get("facts") or []]
            self._claims[key] = await self.embedder.embed(claims)
        return ids, self._claims[key]

    async def tellings(self, material: SharedMaterial, sections: list[dict]) -> dict[str, Telling]:
        """For every evidence item, the sentence that tells it best (if any is close enough)."""
        cfg = ai_config.language_pipeline.gates.coverage
        ids, claim_vecs = await self._claim_vectors(material)
        owners: list[tuple[str, str]] = []
        for section in sections:
            for sentence in sentences(section["text"], cfg.min_sentence_words):
                owners.append((section["id"], sentence))
        if not owners or not ids:
            return {}
        vecs = await self.embedder.embed([s for _, s in owners])
        sim = cosine_matrix(claim_vecs, vecs)
        out: dict[str, Telling] = {}
        for row, evidence_id in enumerate(ids):
            best = int(sim[row].argmax())
            score = float(sim[row, best])
            if score >= cfg.min_similarity:
                act_id, sentence = owners[best]
                out[evidence_id] = Telling(evidence_id, act_id, round(score, 3), sentence)
        return out

    @staticmethod
    def told_before_their_act(material: SharedMaterial, tellings: dict[str, Telling]) -> list[dict]:
        """Items a plan act must withhold that this text tells inside that act."""
        threshold = ai_config.language_pipeline.gates.coverage.reveal_similarity
        found = []
        for act in material.acts:
            withheld = set(act.get("do_not_reveal") or [])
            for evidence_id in sorted(withheld):
                t = tellings.get(evidence_id)
                if t and t.act_id == str(act["id"]) and t.score >= threshold:
                    found.append({"act": t.act_id, "evidence_id": evidence_id,
                                  "score": t.score, "sentence": t.sentence})
        return found
