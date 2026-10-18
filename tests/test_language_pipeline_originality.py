"""R5/R7: a copied story fails the guard and a rewritten one passes; the guard lists the
offending sentences and sends that language back to stage 4."""

import asyncio

import numpy as np

from app.db.models import Case, TranscriptSegment, VideoSource
from app.language_pipeline.originality import OriginalityGuard, longest_shared_run, ngrams, words

TRANSCRIPT = ("On the morning of August fourth the maid came downstairs and found the old man "
              "dead on the sofa in the sitting room while the house stayed strangely quiet. "
              "Nobody in the street had heard a single sound from the house that whole morning.")
COPY = ("Then, as the video tells it: " + TRANSCRIPT)
REWRITTEN = ("A hot Thursday in Fall River. Bridget Sullivan, the family's maid, walked in and "
             "discovered Andrew Borden killed in his own parlor; the neighbors reported nothing.")


class _Embedder:
    """Same sentence -> same vector; different sentences are orthogonal."""

    def __init__(self):
        self.known = {}

    async def embed(self, texts):
        rows = []
        for t in texts:
            key = " ".join(t.lower().split())
            self.known.setdefault(key, len(self.known))
            rows.append(key)
        dim = 4096
        out = np.zeros((len(rows), dim), dtype=np.float32)
        for i, key in enumerate(rows):
            out[i, self.known[key] % dim] = 1.0
        return out


def _case_with_transcript(db):
    import uuid

    case = Case(canonical_title="Originality", slug=f"o-{uuid.uuid4().hex[:8]}", language="en", status="researched")
    db.add(case)
    db.commit()
    video = VideoSource(case_id=case.id, video_id=f"v{uuid.uuid4().hex[:6]}", url="https://example.invalid/v",
                        title="t", channel_name="c")
    db.add(video)
    db.commit()
    db.add(TranscriptSegment(video_source_id=video.id, segment_index=0, start_seconds=0, end_seconds=5,
                             text_original=TRANSCRIPT, canonical_text_en=TRANSCRIPT, language="en",
                             content_hash=uuid.uuid4().hex))
    db.commit()
    return case


def test_longest_shared_run_counts_consecutive_words():
    source = words("a b c d e f g h i j k l m n")
    index = {}
    for j, g in enumerate(ngrams(source, 3)):
        index.setdefault(g, []).append(j)
    assert longest_shared_run(words("x y c d e f g h z"), index, 3) == 6
    assert longest_shared_run(words("q r s t"), index, 3) == 0


def test_a_copied_story_fails_and_a_rewritten_one_passes(db_session):
    case = _case_with_transcript(db_session)
    guard = OriginalityGuard(_Embedder())
    copied = asyncio.run(guard.check(db_session, case.id, COPY))
    assert not copied["pass"] and "longest_run" in copied["failures"]
    assert copied["worst"]["longest_run"] >= 30
    rewritten = asyncio.run(guard.check(db_session, case.id, REWRITTEN))
    assert rewritten["pass"] and rewritten["near_verbatim_count"] == 0


def test_near_verbatim_sentences_are_found_by_embedding_and_listed(db_session):
    case = _case_with_transcript(db_session)
    sentence = "On the morning of August fourth the maid came downstairs and found the old man dead on the sofa in the sitting room while the house stayed strangely quiet."
    report = asyncio.run(OriginalityGuard(_Embedder()).check(db_session, case.id, sentence))
    assert report["near_verbatim"][0]["sentence"].startswith("On the morning")
    assert report["near_verbatim"][0]["similarity"] == 1.0


def test_the_gate_sends_the_language_back_to_stage_four_with_the_sentences(db_session, monkeypatch):
    from app.core.ai_config import ai_config

    monkeypatch.setattr(ai_config.language_pipeline, "originality_min_words", 1)
    from app.language_pipeline.gates import FinalGates, GateResult

    case = _case_with_transcript(db_session)
    gates = FinalGates(embedder=_Embedder())
    result = GateResult()
    asyncio.run(gates._originality(result, db_session, case, (COPY + " ") * 3))
    failure = result.failures[0]
    assert failure["gate"] == "originality" and "your own words and your own order" in failure["problem"]
    assert failure["passages"]
