"""The approved English story as a reference for the Persian text: the approved sources, the sentences, their alignment and the status of a block."""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.core.ai_config import ai_config
from app.db.models import StoryVersion
from app.documentary.english_reference import alignment as AL
from app.documentary.english_reference import sources as SRC


# --- sources ------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def scratch(db_session, monkeypatch):
    """A scratch case shaped like Case 15: an approved English final, an approved Persian final, its spoken version and a voice-prepare rewrite."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.db.models import Case

    case = Case(canonical_title="Scratch: PPIE review", slug=f"scratch-review-{uuid4().hex[:8]}", status="new", language="fa", origin="scratch")
    db_session.add(case)
    db_session.flush()
    made = lambda **kw: StoryVersion(case_id=case.id, version=1, narrative_angle="a", status="ready", **kw)  # noqa: E731
    english = made(kind="final", language="en", story_text="He came.", approved_at=datetime.now(UTC))
    final = made(kind="final", language="fa", story_text="او آمد.", approved_at=datetime.now(UTC))
    db_session.add_all([english, final])
    db_session.flush()
    spoken = made(kind="spoken", language="fa", story_text="او آمد.", master_version_id=final.id)
    rewrite = made(kind="spoken", language="fa", story_text="او آمَد.", master_version_id=final.id,
                   narrative_structure=json.dumps({"prepared": {"from_version_id": 0}}))
    db_session.add_all([spoken, rewrite])
    db_session.commit()
    monkeypatch.setattr(SRC, "english_final", lambda db, case_id: english if case_id == case.id else None)
    yield case, english, final, spoken, rewrite
    db_session.query(StoryVersion).filter(StoryVersion.case_id == case.id).delete()
    db_session.query(Case).filter_by(id=case.id).delete()
    db_session.commit()


def test_the_approved_sources_are_found_and_a_voice_prepare_rewrite_or_an_unapproved_final_is_refused(db_session, scratch):
    case, english, final, spoken, rewrite = scratch
    found = SRC.approved_sources(db_session, spoken)
    assert (found.narration.id, found.persian_final.id, found.english.id) == (spoken.id, final.id, english.id)
    assert "no approval date of its own" in found.notes[0] and found.describe()["english"]["approved_at"]
    with pytest.raises(SRC.SourceError, match="voice-prepare rewrite"):
        SRC.approved_sources(db_session, rewrite)
    with pytest.raises(SRC.SourceError, match="not a Persian spoken version"):
        SRC.approved_sources(db_session, english)
    final.approved_at = None
    db_session.commit()
    with pytest.raises(SRC.SourceError, match="not approved"):
        SRC.approved_sources(db_session, spoken)


# --- alignment ----------------------------------------------------------------------------------------------------------------------------

def unit_vectors(*rows):
    m = np.array(rows, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


class Encoder:
    """Vectors by a lookup of the sentence text (a fake of the local model)."""

    def __init__(self, table: dict[str, list[float]]):
        self.table = table

    def encode(self, texts):
        return unit_vectors(*[self.table[t] for t in texts])


def test_sentences_keep_their_offsets_and_markers_are_not_text():
    story = "[[ACT:act1]]\n\nاو آمد. [serious] سارا رفت؟ تمام"
    found = AL.segments(story)
    assert [u.text for u in found] == ["او آمد.", "سارا رفت؟", "تمام"] and all(story[u.start:u.end] == u.text for u in found)


@pytest.fixture
def table_config(monkeypatch):
    for k, v in {"band": 1.0, "max_group": 2, "match_floor": 0.5, "aligned_min": 0.8, "partial_min": 0.5, "ambiguous_margin": 0.05}.items():
        monkeypatch.setattr(ai_config.english_reference.alignment, k, v)


def test_one_to_one_many_to_one_and_a_persian_sentence_with_no_english(table_config):
    fa = "الف یک. الف دو. ب. ج اضافه. د."
    en = "A one and two. B. D."
    table = {"الف یک.": [1, 0.2, 0, 0, 0], "الف دو.": [1, -0.2, 0, 0, 0], "ب.": [0, 0, 1, 0, 0], "ج اضافه.": [0, 0, 0, 0, 1], "د.": [0, 0, 0, 1, 0],
             "A one and two.": [1, 0, 0, 0, 0], "B.": [0, 0, 1, 0, 0], "D.": [0, 0, 0, 1, 0]}
    persian, english, matches = AL.prepare(fa, en, Encoder(table))
    shape = [(m.persian, m.english, m.status) for m in matches]
    assert shape == [((0, 2), (0, 1), "ALIGNED"), ((2, 3), (1, 2), "ALIGNED"), ((3, 4), None, "MISSING_REFERENCE"), ((4, 5), (2, 3), "ALIGNED")]


def test_a_match_whose_numbers_differ_is_not_aligned_and_a_close_rival_makes_it_ambiguous(table_config):
    persian = AL.segments("در سال ۲۰۲۶ رفت. او آمد.")
    english = AL.segments("He left in 2025. He came. He came back.")
    m = AL.judge(AL.Match((0, 1), (0, 1), 0.95, 0.1, ""), persian, english)
    assert m.status == "PARTIAL" and "numbers differ" in m.issues[0]
    rival = AL.judge(AL.Match((1, 2), (1, 2), 0.9, 0.88, ""), persian, english)
    assert rival.status == "AMBIGUOUS"



def test_a_block_gets_the_worst_meaningful_status_of_its_sentences(table_config):
    persian, english = AL.segments("الف. ب."), AL.segments("A. B.")
    matches = [AL.Match((0, 1), (0, 1), 0.9, 0.1, "ALIGNED"), AL.Match((1, 2), (1, 2), 0.9, 0.1, "MISMATCH")]
    found = AL.blocks([{"block_id": "b1", "text": "الف."}, {"block_id": "b2", "text": "[calm] ب."}, {"block_id": "b3", "text": "نیست"}],
                      "الف. ب.", "A. B.", persian, english, matches)
    assert [(b.block_id, b.status) for b in found] == [("b1", "ALIGNED"), ("b2", "MISMATCH"), ("b3", "MISSING_REFERENCE")]
    assert found[0].english_text == "A." and found[0].persian_span == (0, 4)
    assert AL.block_status(["ALIGNED", "MISSING_REFERENCE"]) == "PARTIAL" and AL.block_status(["ALIGNED", "AMBIGUOUS"]) == "AMBIGUOUS"
