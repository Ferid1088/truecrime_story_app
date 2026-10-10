"""First-stage cheap ranking (Part 23) + fetch-decision scoring.

Combines search rank, domain quality, lexical relevance and novelty —
embeddings are layered on top only when available. LLM reranking is
reserved for the ambiguous middle band (Part 24).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.research_engine.search import SearchResult
from app.research_engine.source_quality import classify_source
from app.research_engine.urlnorm import canonicalize_url

_WORD = re.compile(r"[\w'’-]+", re.UNICODE)


@dataclass
class RankedResult:
    result: SearchResult
    score: float
    relevance: float
    quality: float
    novelty: float
    source_type: str
    rejected: bool = False
    rejection_reason: str | None = None


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "") if len(w) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def rank_results(
    results: list[SearchResult],
    query: str,
    case_terms: set[str] | None = None,
    seen_canonical: set[str] | None = None,
    similarity_to_corpus: list[float] | None = None,
    weights: dict | None = None,
    min_accept_score: float = 0.2,
) -> list[RankedResult]:
    """Cheap deterministic ranking — no LLM.

    score = w_rank*rank_score + w_lex*lexical + w_qual*domain_quality
            + w_nov*novelty
    `similarity_to_corpus` (0..1 per result, e.g. max embedding cosine
    to already-stored sources) is inverted into novelty when present.
    """
    w = {
        "rank": 0.30, "lexical": 0.30, "quality": 0.25, "novelty": 0.15,
        **(weights or {}),
    }
    q_toks = _tokens(query) | (case_terms or set())
    seen_canonical = seen_canonical or set()
    n = max(len(results), 1)
    out: list[RankedResult] = []
    for i, r in enumerate(results):
        rank_score = 1.0 - (r.rank - 1) / max(n, 1)
        lex = max(
            _jaccard(q_toks, _tokens(r.title or "")),
            _jaccard(q_toks, _tokens((r.snippet or "")[:400])) * 0.8,
        )
        quality = classify_source(r.url, r.title, r.snippet)
        if similarity_to_corpus is not None and i < len(similarity_to_corpus):
            novelty = 1.0 - min(max(similarity_to_corpus[i], 0.0), 1.0)
        else:
            novelty = 0.5
        canon = r.canonical_url or canonicalize_url(r.url) or ""
        already_seen = bool(canon) and (
            canon in seen_canonical
            or canon.split("?")[0] in seen_canonical)
        if already_seen:
            novelty = 0.0
        score = (w["rank"] * rank_score + w["lexical"] * lex +
                 w["quality"] * quality.quality_score +
                 w["novelty"] * novelty)
        ranked = RankedResult(
            result=r, score=round(score, 4),
            relevance=round(lex, 4),
            quality=quality.quality_score,
            novelty=round(novelty, 4),
            source_type=quality.source_type,
        )
        if already_seen:
            ranked.rejected = True
            ranked.rejection_reason = "duplicate"
        elif score < min_accept_score:
            ranked.rejected = True
            ranked.rejection_reason = "low_score"
        out.append(ranked)
    out.sort(key=lambda x: x.score, reverse=True)
    return out


def select_for_fetch(ranked: list[RankedResult],
                     max_fetches: int,
                     quality_floor: float = 0.15) -> list[RankedResult]:
    """Only the top non-rejected candidates proceed to fetching —
    bounded, quality-floored."""
    picked: list[RankedResult] = []
    for r in ranked:
        if r.rejected or r.quality < quality_floor:
            continue
        picked.append(r)
        if len(picked) >= max_fetches:
            break
    return picked


def needs_llm_rerank(r: RankedResult, band: tuple[float, float]) -> bool:
    """Ambiguous middle band worth a rerank call — never rerank
    hundreds of trivial results (Part 24)."""
    return band[0] <= r.score <= band[1] and not r.rejected

