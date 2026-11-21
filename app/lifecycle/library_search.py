"""Search of the suggestions already found: by words (lexical) and by meaning (semantic).

The lexical score is the share of the query's words found in a suggestion's text (a word matches by
prefix, accents and case ignored, so "stab" finds "stabbing"). The semantic score is the cosine of
the multilingual embedding of the query and of the suggestion. Without an embedding client the
search is lexical only and says so.
"""
from __future__ import annotations

import json
import re
import unicodedata

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import DiscoveryCandidate
from app.lifecycle.selection import candidate_dict
from app.research_engine.embeddings import EmbeddingError, cosine_matrix

HIDDEN_STATES = ("deleted",)
MIN_WORD_LENGTH = 3


def words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", (text or "").casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return [w for w in re.findall(r"\w+", folded) if len(w) >= MIN_WORD_LENGTH]


def candidate_text(c: DiscoveryCandidate) -> str:
    parts = [c.title, c.location or "", " ".join(json.loads(c.aliases_json or "[]")),
             " ".join(json.loads(c.people_json or "[]")), c.rationale or "", c.query or ""]
    return ". ".join(p for p in parts if p)


def lexical_score(query_words: list[str], text_words: set[str]) -> float:
    if not query_words:
        return 0.0
    hit = sum(1 for q in query_words if any(t.startswith(q) or q.startswith(t) for t in text_words))
    return hit / len(query_words)


async def search_candidates(db: Session, query: str, embedder=None) -> dict:
    cfg = ai_config.discovery
    query_words = words(query)
    rows = [r for r in db.query(DiscoveryCandidate).all() if (r.state or "suggested") not in HIDDEN_STATES]
    texts = [candidate_text(r) for r in rows]
    lexical = [lexical_score(query_words, set(words(t))) for t in texts]
    semantic = [0.0] * len(rows)
    mode = "lexical"
    if embedder is not None and rows and query.strip():
        try:
            vectors = await embedder.embed([query] + texts)
            semantic = [float(x) for x in cosine_matrix(vectors[:1], vectors[1:])[0]]
            mode = "lexical+semantic"
        except EmbeddingError:
            mode = "lexical"
    weight = cfg.search_semantic_weight if mode == "lexical+semantic" else 0.0
    found = []
    for row, lex, sem in zip(rows, lexical, semantic, strict=True):
        if lex <= 0.0 and sem < cfg.search_min_semantic:
            continue
        found.append({**candidate_dict(row), "lexical_score": round(lex, 3), "semantic_score": round(sem, 3),
                      "match_score": round((1 - weight) * lex + weight * sem, 3)})
    found.sort(key=lambda r: -r["match_score"])
    return {"query": query, "mode": mode, "results": found[:cfg.search_max_results]}
