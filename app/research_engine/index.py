"""Internal corpus index (Parts 20 + 49).

The external engine discovers pages; THIS index searches everything
already downloaded. Hybrid retrieval: BM25 lexical + BGE-M3 dense
cosine, fused with configurable weights. Built per case on demand and
cached in memory — SQLite-scale corpora need no external vector DB.
A VectorIndex abstraction keeps a future pgvector migration open.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import numpy as np
from rank_bm25 import BM25Okapi

from app.research_engine.embeddings import EmbeddingClient, cosine_matrix

_WORD = re.compile(r"[\w'’\u0600-\u06FF-]+", re.UNICODE)
_INDEX_TTL_S = 300.0


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


@dataclass
class IndexedChunk:
    chunk_id: int
    source_id: int
    source_title: str
    language: str | None
    text: str
    location: str | None = None
    embedding: np.ndarray | None = None


@dataclass
class IndexHit:
    chunk_id: int
    source_id: int
    source_title: str
    language: str | None
    text: str
    location: str | None
    score: float
    bm25_score: float
    dense_score: float


class CorpusIndex:
    """Per-case hybrid index over SourceChunk rows."""

    def __init__(self, embedder: EmbeddingClient | None = None,
                 bm25_weight: float = 0.55, dense_weight: float = 0.45):
        self.embedder = embedder
        self.bm25_weight = bm25_weight
        self.dense_weight = dense_weight
        self.chunks: list[IndexedChunk] = []
        self._bm25: BM25Okapi | None = None
        self._matrix: np.ndarray | None = None

    async def build(self, chunks: list[IndexedChunk],
                    embed: bool = True):
        self.chunks = chunks
        corpus = [_tokenize(c.text) for c in chunks]
        self._bm25 = BM25Okapi(corpus) if chunks else None
        self._matrix = None
        if embed and self.embedder and self.embedder.is_configured() and chunks:
            try:
                texts = [c.text[:1500] for c in chunks]
                vecs = await self.embedder.embed(texts)
                if vecs.size:
                    self._matrix = vecs
            except Exception:  # noqa: BLE001 - degrade to BM25-only
                self._matrix = None

    def add_embedding(self, chunk_pos: int, vec: np.ndarray):
        if self._matrix is None:
            self._matrix = np.zeros((len(self.chunks), vec.shape[0]),
                                    dtype=np.float32)
        self._matrix[chunk_pos] = vec

    async def search(self, query: str, limit: int = 10,
                     language: str | None = None) -> list[IndexHit]:
        if not self.chunks:
            return []
        bm = np.zeros(len(self.chunks), dtype=np.float32)
        if self._bm25 is not None:
            raw = np.asarray(self._bm25.get_scores(_tokenize(query)),
                             dtype=np.float32)
            if raw.max() > 0:
                bm = raw / raw.max()
        dense = np.zeros(len(self.chunks), dtype=np.float32)
        if self._matrix is not None and self.embedder is not None:
            try:
                qv = await self.embedder.embed_one(query)
                dense = cosine_matrix(qv.reshape(1, -1),
                                      self._matrix)[0]
            except Exception:  # noqa: BLE001
                dense = np.zeros(len(self.chunks), dtype=np.float32)
        fused = self.bm25_weight * bm + self.dense_weight * dense
        order = np.argsort(-fused)
        hits: list[IndexHit] = []
        for i in order:
            c = self.chunks[int(i)]
            if language and c.language and c.language != language:
                continue
            if fused[int(i)] <= 0:
                continue
            hits.append(IndexHit(
                chunk_id=c.chunk_id, source_id=c.source_id,
                source_title=c.source_title, language=c.language,
                text=c.text, location=c.location,
                score=round(float(fused[int(i)]), 4),
                bm25_score=round(float(bm[int(i)]), 4),
                dense_score=round(float(dense[int(i)]), 4),
            ))
            if len(hits) >= limit:
                break
        return hits

    def max_similarity(self, vec: np.ndarray) -> float:
        """Highest dense cosine of `vec` to any indexed chunk — used by
        the novelty engine (Part 25)."""
        if self._matrix is None or self._matrix.size == 0 or vec.size == 0:
            return 0.0
        return float(cosine_matrix(vec.reshape(1, -1), self._matrix).max())


class _IndexCache:
    def __init__(self):
        self._cache: dict[int, tuple[float, CorpusIndex]] = {}

    def get(self, case_id: int) -> CorpusIndex | None:
        entry = self._cache.get(case_id)
        if not entry:
            return None
        ts, idx = entry
        if time.time() - ts > _INDEX_TTL_S:
            self._cache.pop(case_id, None)
            return None
        return idx

    def put(self, case_id: int, idx: CorpusIndex):
        self._cache[case_id] = (time.time(), idx)

    def invalidate(self, case_id: int):
        self._cache.pop(case_id, None)


INDEX_CACHE = _IndexCache()
