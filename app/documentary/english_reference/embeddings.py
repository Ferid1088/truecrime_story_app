"""Sentence embeddings for the English–Persian alignment, computed locally (no paid API): BGE-M3's dense vector (the CLS token, normalised), loaded
once per process from the local model cache. A similarity of two sentences is a hint, never proof that they tell the same thing."""

from __future__ import annotations

import threading
from functools import lru_cache

from app.core.ai_config import ai_config


class EmbeddingUnavailable(RuntimeError):
    pass


class Encoder:
    def __init__(self, model_name: str):
        try:
            import torch  # noqa: F401
            from transformers import AutoModel, AutoTokenizer
        except ImportError as e:
            raise EmbeddingUnavailable("the alignment needs torch + transformers") from e
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
            self.model = AutoModel.from_pretrained(model_name, local_files_only=True).eval()
        except OSError as e:
            raise EmbeddingUnavailable(f"{model_name} is not in the local model cache") from e
        self.lock = threading.Lock()
        self.name = model_name

    def encode(self, texts: list[str]):
        """One normalised vector per text (a numpy array, rows in order)."""
        import numpy as np
        import torch

        cfg, rows = ai_config.english_reference, []
        with self.lock, torch.no_grad():
            for i in range(0, len(texts), cfg.embedding_batch):
                batch = self.tokenizer(texts[i:i + cfg.embedding_batch], padding=True, truncation=True, max_length=cfg.embedding_max_tokens,
                                       return_tensors="pt")
                cls = self.model(**batch).last_hidden_state[:, 0]
                rows.append(torch.nn.functional.normalize(cls, dim=-1).numpy())
        return np.concatenate(rows) if rows else np.zeros((0, 0), dtype=np.float32)


@lru_cache(maxsize=1)
def _encoder(model_name: str) -> Encoder:
    return Encoder(model_name)


def get_encoder() -> Encoder:
    return _encoder(ai_config.english_reference.embedding_model)
