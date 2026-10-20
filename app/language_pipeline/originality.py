"""The originality guard. A final text is compared with every research transcript of the case
(in its own language and in the English canonical wording), by three deterministic measures:
the longest run of words it shares with one source, the share of its n-word sequences one
source also contains, and sentences that are near-verbatim by (multilingual) embedding."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import TranscriptSegment, VideoSource
from app.language_pipeline.coverage import sentences
from app.research_engine.embeddings import cosine_matrix

_WORD = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def ngrams(tokens: list[str], n: int) -> list[tuple]:
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def longest_shared_run(story: list[str], source_index: dict[tuple, list[int]], n: int) -> int:
    """Longest run of consecutive words the story shares with the source, by extending
    matching n-grams along their diagonal. Linear in the number of matches."""
    best = 0
    runs: dict[int, int] = {}
    for i, gram in enumerate(ngrams(story, n)):
        current: dict[int, int] = {}
        for j in source_index.get(gram, ()):
            current[i - j] = runs.get(i - j, 0) + 1
        runs = current
        if runs:
            best = max(best, max(runs.values()) + n - 1)
    return best


class OriginalityGuard:
    def __init__(self, embedder=None):
        self.embedder = embedder

    @staticmethod
    def corpora(db: Session, case_id: int) -> dict[tuple[int, str], str]:
        """Every transcript's text, once per wording (original and English canonical)."""
        rows = (db.query(TranscriptSegment)
                .join(VideoSource, TranscriptSegment.video_source_id == VideoSource.id)
                .filter(VideoSource.case_id == case_id)
                .order_by(TranscriptSegment.video_source_id, TranscriptSegment.segment_index).all())
        out: dict[tuple[int, str], list[str]] = defaultdict(list)
        for seg in rows:
            if seg.text_original:
                out[(seg.video_source_id, "original")].append(seg.text_original)
            if seg.canonical_text_en:
                out[(seg.video_source_id, "canonical_en")].append(seg.canonical_text_en)
        return {k: " ".join(v) for k, v in out.items()}

    async def check(self, db: Session, case_id: int, text: str) -> dict:
        cfg = ai_config.language_pipeline.gates.originality
        story = words(text)
        story_grams = ngrams(story, cfg.ngram_size)
        corpora = self.corpora(db, case_id)
        worst: dict[str, Any] = {"video_source_id": None, "wording": None, "ngram_share": 0.0, "longest_run": 0}
        per_source = {}
        for (video_id, wording), corpus_text in corpora.items():
            corpus = words(corpus_text)
            index: dict[tuple, list[int]] = defaultdict(list)
            for j, gram in enumerate(ngrams(corpus, cfg.ngram_size)):
                index[gram].append(j)
            shared = sum(1 for g in story_grams if g in index)
            share = shared / len(story_grams) if story_grams else 0.0
            run = longest_shared_run(story, index, cfg.ngram_size) if shared else 0
            per_source[f"{video_id}:{wording}"] = {"ngram_share": round(share, 4), "longest_run": run}
            if (share / max(cfg.max_ngram_share, 1e-9) + run / max(cfg.max_run_words, 1)
                    > worst["ngram_share"] / max(cfg.max_ngram_share, 1e-9)
                    + worst["longest_run"] / max(cfg.max_run_words, 1)):
                worst = {"video_source_id": video_id, "wording": wording,
                         "ngram_share": round(share, 4), "longest_run": run}
        near = await self._near_verbatim(text, corpora) if self.embedder is not None else []
        failures = []
        if worst["ngram_share"] > cfg.max_ngram_share:
            failures.append("ngram_share")
        if worst["longest_run"] > cfg.max_run_words:
            failures.append("longest_run")
        if len(near) > cfg.max_near_verbatim_sentences:
            failures.append("near_verbatim_sentences")
        return {"pass": not failures, "failures": failures, "transcripts_compared": len(corpora),
                "worst": worst, "limits": {"max_ngram_share": cfg.max_ngram_share,
                                           "max_run_words": cfg.max_run_words,
                                           "max_near_verbatim_sentences": cfg.max_near_verbatim_sentences},
                "near_verbatim": near[:cfg.max_listed_sentences], "near_verbatim_count": len(near),
                "embedding_checked": self.embedder is not None}

    async def _near_verbatim(self, text: str, corpora: dict[tuple[int, str], str]) -> list[dict]:
        cfg = ai_config.language_pipeline.gates.originality
        mine = sentences(text, cfg.min_sentence_words)
        theirs: list[tuple[int, str, str]] = []
        for (video_id, wording), corpus_text in corpora.items():
            theirs += [(video_id, wording, s) for s in sentences(corpus_text, cfg.min_sentence_words)]
        if not mine or not theirs:
            return []
        sim = cosine_matrix(await self.embedder.embed(mine), await self.embedder.embed([t[2] for t in theirs]))
        found = []
        for i, sentence in enumerate(mine):
            j = int(sim[i].argmax())
            if sim[i, j] >= cfg.near_verbatim_similarity:
                found.append({"sentence": sentence, "video_source_id": theirs[j][0],
                              "wording": theirs[j][1], "similarity": round(float(sim[i, j]), 3),
                              "source_sentence": theirs[j][2]})
        return found
