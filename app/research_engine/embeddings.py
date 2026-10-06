"""Embeddings via the generation provider's embedding model (Parts 18-19).

Multilingual dense vectors for retrieval, dedupe and clustering —
never an expensive chat model. Results are cached by
(content_hash, model) so unchanged text is never re-embedded.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

import httpx
import numpy as np

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "embedding_cache")


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    """POST {base}/embeddings with model=<configured alias value>."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_s: float = 60.0,
        batch_size: int = 32,
        cache_dir: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.batch_size = batch_size
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.total_tokens = 0
        self.total_cost_usd = 0.0
        self.cache_hits = 0
        self.api_calls = 0

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def _key(self, text: str) -> str:
        h = hashlib.sha256(
            f"{self.model}|{text}".encode("utf-8")).hexdigest()
        return h

    def _path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.json")

    def _cache_get(self, text: str) -> list[float] | None:
        p = self._path(self._key(text))
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("model") != self.model:
                return None
            self.cache_hits += 1
            return d["embedding"]
        except (OSError, ValueError, KeyError):
            return None

    def _cache_put(self, text: str, vec: list[float]):
        os.makedirs(self.cache_dir, exist_ok=True)
        p = self._path(self._key(text))
        tmp = p + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"model": self.model, "embedding": vec}, f)
            os.replace(tmp, p)
        except OSError:
            pass

    async def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dims) float32 array of embeddings."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        if not self.is_configured():
            raise EmbeddingError("embedding client not configured")

        results: list[list[float] | None] = [None] * len(texts)
        to_fetch: list[tuple[int, str]] = []
        for i, t in enumerate(texts):
            t_norm = (t or "").strip()[:8000]
            if not t_norm:
                results[i] = []
                continue
            cached = self._cache_get(t_norm)
            if cached is not None:
                results[i] = cached
            else:
                to_fetch.append((i, t_norm))

        for start in range(0, len(to_fetch), self.batch_size):
            batch = to_fetch[start:start + self.batch_size]
            vecs = await self._call([t for _, t in batch])
            for (i, t), v in zip(batch, vecs):
                results[i] = v
                self._cache_put(t, v)

        dim = max((len(r) for r in results if r), default=0)
        arr = np.zeros((len(texts), dim), dtype=np.float32)
        for i, r in enumerate(results):
            if r:
                arr[i, : len(r)] = r
        return arr

    async def embed_one(self, text: str) -> np.ndarray:
        arr = await self.embed([text])
        return arr[0] if arr.size else np.zeros(0, dtype=np.float32)

    async def _call(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                r = await c.post(
                    f"{self.base_url}/embeddings",
                    headers=headers,
                    json={"model": self.model, "input": texts},
                )
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as e:
            raise EmbeddingError(
                f"embeddings HTTP {e.response.status_code}") from e
        except (httpx.HTTPError, ValueError) as e:
            raise EmbeddingError(f"embeddings failed: {e}") from e

        usage = data.get("usage") or {}
        self.total_tokens += int(usage.get("total_tokens") or 0)
        try:
            self.total_cost_usd += float(usage.get("cost") or 0.0)
        except (TypeError, ValueError):
            pass
        self.api_calls += 1

        rows = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
        if len(rows) != len(texts):
            raise EmbeddingError(
                f"embedding count mismatch: got {len(rows)} for {len(texts)}")
        return [list(map(float, row["embedding"])) for row in rows]


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise cosine similarity (n_a, n_b); zero rows are safe."""
    if a.size == 0 or b.size == 0:
        return np.zeros((a.shape[0], b.shape[0]), dtype=np.float32)
    an = a / np.clip(np.linalg.norm(a, axis=1, keepdims=True), 1e-9, None)
    bn = b / np.clip(np.linalg.norm(b, axis=1, keepdims=True), 1e-9, None)
    return an @ bn.T


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0:
        return 0.0
    m = cosine_matrix(a.reshape(1, -1), b.reshape(1, -1))
    return float(m[0, 0])
