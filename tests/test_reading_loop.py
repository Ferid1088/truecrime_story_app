"""The reading loop after the voice, with a fake inspector and a fake voice: who is told what, which blocks are voiced again, what is kept
for every film and what is reported. The names are invented."""

from __future__ import annotations

import asyncio

import pytest

from app.core.ai_config import ai_config
from app.db.models import ReadingApproval
from app.documentary.reading.block import BlockInput
from app.documentary.reading.inspector import Finding
from app.documentary.reading.loop import ReadingLoop
from app.documentary.reading.voicing import Voicing


@pytest.fixture(autouse=True)
def clean_approvals(db_session):
    yield
    db_session.query(ReadingApproval).delete()
    db_session.commit()


class FakeInspector:
    """Finds what the script says for a block in each round (the blocks listened to again are the ones voiced again)."""

    def __init__(self, rounds):
        self.rounds, self.round, self.asked = rounds, 0, []

    async def inspect(self, block_id, text, readings, wav, spoken):
        self.asked.append(block_id)
        step = self.rounds[min(self.round, len(self.rounds) - 1)]
        return step.get(block_id, []), step.get(f"{block_id}:weak", [])


def manifest(*ids):
    return {"timeline": {"blocks": [{"block_id": b, "start": 0.0, "wav": f"{b}.wav"} for b in ids], "words": []}}


def build(db_session, texts, readings, inspector, fixed=None):
    items = [BlockInput(b, "s", t) for b, t in texts.items()]
    return ReadingLoop(db_session, 1, "fa", items, inspector, Voicing(fixed or {}, readings))


def test_a_word_the_voice_said_wrong_is_voiced_again_as_ipa_and_kept_for_every_film_when_it_is_right(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.reading, "max_rounds", 3)
    finding = Finding("a", 1, "عهد", "'ahd", "'ohd", "'ohd", 9.0)
    inspector = FakeInspector([{"a": [finding]}, {}])
    loop = build(db_session, {"a": "از عهد", "b": "حرف زد"}, {"a": ["az", "'ahd"], "b": ["harf", "zad"]}, inspector)
    voiced = []

    async def revoice(block_ids, round_no):
        voiced.append((list(block_ids), round_no, loop.voicing.text("a", "از عهد")))
        inspector.round += 1
        return manifest("a", "b")

    asyncio.run(loop.run(manifest("a", "b"), revoice))
    assert voiced == [(["a"], 1, "از /ʔæhd/")]
    assert inspector.asked == ["a", "b", "a"], "the second listening is of the voiced block only"
    assert loop.report["fixed"] == [{"block": "a", "word": "عهد", "reading": "'ahd"}] and loop.report["unfixed"] == []
    kept = db_session.query(ReadingApproval).filter_by(language="fa", surface="عهد").one()
    assert (kept.latin, kept.kind, kept.source) == ("'ahd", "spoken_form", "listener")


def test_a_word_that_stays_wrong_after_every_round_is_reported_and_the_run_goes_on(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.reading, "max_rounds", 2)
    finding = Finding("a", 1, "پرت", "pat", "perat", "perat", 6.0)
    inspector = FakeInspector([{"a": [finding]}])
    loop = build(db_session, {"a": "قاضی پرت"}, {"a": ["qāzi", "pat"]}, inspector)
    calls = []

    async def revoice(block_ids, round_no):
        calls.append(round_no)
        return manifest("a")

    asyncio.run(loop.run(manifest("a"), revoice))
    assert calls == [1, 2] and [r["round"] for r in loop.report["rounds"]] == [0, 1, 2]
    assert [u["word"] for u in loop.report["unfixed"]] == ["پرت"] and loop.report["fixed"] == []
    assert db_session.query(ReadingApproval).filter_by(surface="پرت").count() == 0, "a word that is still wrong is not kept"
