"""Collision check of a title candidate against the stored corpus.

Exact (normalized), near (lexical + character) and semantic (bge-m3,
other cases only). No search backend and no web access: the only
outside call is the embedding of the candidate itself, plus corpus
titles that are not cached yet, through the existing embedding client.

Scope: a case's own identity titles (its canonical name, its other
language titles) are not collisions — they are one family. Its research
sources are (an existing article or video with that exact title is real
competition), and so are its own earlier candidates in the same language.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from rapidfuzz import fuzz

from app.core.ai_config import ai_config
from app.naming.corpus import Entry, TitleCollisionCorpus
from app.naming.normalize import norm_title


@dataclass
class Collision:
    exact: bool = False
    exact_with: Entry | None = None
    near_score: float = 0.0
    near_with: Entry | None = None
    semantic_score: float | None = None
    semantic_with: Entry | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return bool(self.reasons)

    def describe(self, e: Entry | None) -> str:
        return f"{e.kind}:{e.text}" if e else ""


def _own_family(e: Entry, case_id: int | None, language: str, skip_candidates: bool = False) -> bool:
    """True when `e` is part of the candidate case's own identity (not a
    competitor): its names/titles in other languages and approved titles."""
    if case_id is None or e.case_id != case_id:
        return False
    if e.kind in ("case_title", "discovery_title", "episode_title", "video_title"):
        return True
    if e.kind == "candidate":
        # same-language candidates compete, unless the caller re-checks
        # one of the case's own candidates (approval)
        return skip_candidates or e.language != language
    return False


def _phrase_in(needle: list[str], hay: list[str]) -> bool:
    n = len(needle)
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def check_lexical(title: str, language: str, case_id: int | None,
                  corpus: TitleCollisionCorpus, *, skip_own_candidates: bool = False) -> Collision:
    cfg = ai_config.case_naming
    res = Collision()
    norm = norm_title(title)
    if not norm:
        res.reasons.append("empty title")
        return res
    toks = norm.split()
    for e in corpus.by_norm.get(norm, []):
        if _own_family(e, case_id, language, skip_own_candidates):
            continue
        res.exact, res.exact_with = True, e
        prior = " (previously rejected)" if e.kind == "candidate" and e.status == "rejected" else ""
        res.reasons.append(f"exact collision with {res.describe(e)}{prior}")
        break
    best_margin = -1e9
    for e in corpus.entries:
        if e is res.exact_with or e.norm == norm or _own_family(e, case_id, language, skip_own_candidates):
            continue
        etoks = e.norm.split()
        tscore = fuzz.token_sort_ratio(norm, e.norm)
        cscore = fuzz.ratio(norm, e.norm)
        # the candidate as a whole phrase inside a longer headline
        if len(toks) >= 3 and len(etoks) > len(toks) and _phrase_in(toks, etoks):
            tscore = 100.0
        margin = max(tscore - cfg.near_token_threshold, cscore - cfg.near_char_threshold)
        if margin > best_margin:
            best_margin = margin
            res.near_score, res.near_with = max(tscore, cscore), e
    if res.near_with is not None and best_margin >= 0 and not res.exact:
        res.reasons.append(
            f"near collision {res.near_score:.0f} with {res.describe(res.near_with)}")
    return res


async def add_semantic(results: dict[str, Collision], titles: dict[str, str],
                       language_by_title: dict[str, str], case_id: int | None,
                       corpus: TitleCollisionCorpus, embedder) -> None:
    """Fill `semantic_score` for each candidate title against the identity
    titles of OTHER cases (any language). Uses the stored titles only."""
    cfg = ai_config.case_naming
    if not cfg.semantic_enabled or not titles:
        return
    pool = [e for e in corpus.entries
            if e.kind in cfg.semantic_kinds and e.case_id != case_id
            and not (e.kind == "candidate" and e.status is None)]
    pool = pool[:cfg.semantic_max_entries]
    if not pool:
        return
    cand_vecs = await embedder.embed(list(titles.values()))
    pool_vecs = await embedder.embed([e.text for e in pool])
    if cand_vecs.size == 0 or pool_vecs.size == 0:
        return
    cn = cand_vecs / (np.linalg.norm(cand_vecs, axis=1, keepdims=True) + 1e-9)
    pn = pool_vecs / (np.linalg.norm(pool_vecs, axis=1, keepdims=True) + 1e-9)
    sims = cn @ pn.T
    for row, key in enumerate(titles):
        j = int(np.argmax(sims[row]))
        res = results[key]
        res.semantic_score, res.semantic_with = float(sims[row, j]), pool[j]
        if res.semantic_score >= cfg.semantic_threshold:
            res.reasons.append(
                f"semantic duplicate {res.semantic_score:.2f} of {res.describe(pool[j])}")
