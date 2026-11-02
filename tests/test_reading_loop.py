"""The reading loop after the voice, with fake listeners, a fake judge and a fake voice: who is told what, which blocks are voiced again, what is kept
for every film and what is reported. The names are invented."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import subprocess

import pytest

from app.core.ai_config import ai_config
from app.db.models import ReadingApproval
from app.documentary.reading import ask as ASK
from app.documentary.reading.block import BlockInput
from app.documentary.reading.inspector import Finding
from app.documentary.reading.judge import Judge
from app.documentary.reading.listener import BlindListener
from app.documentary.reading.loop import ReadingLoop
from app.documentary.reading.table import FilmTable
from app.documentary.reading.voicing import Voicing


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(ASK, "track_run", lambda *a, **k: contextlib.nullcontext(object()))
    monkeypatch.setattr(ASK, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(ASK, "get_generation_provider", lambda: object())


@pytest.fixture(autouse=True)
def clean_approvals(db_session):
    yield
    db_session.query(ReadingApproval).delete()
    db_session.commit()


class Script:
    def __init__(self, answer):
        self.answer, self.requests = answer, []

    async def __call__(self, gen, body):
        self.requests.append(json.loads(body))
        return (self.answer(self.requests[-1]) if callable(self.answer) else self.answer), object()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_the_blind_listener_gets_only_the_plain_words_and_the_same_audio_is_never_heard_twice(tmp_path, db_session):
    wav = tmp_path / "b.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=0.5", str(wav)], check=True)
    runner = Script({"words": [{"n": 0, "latin": "az"}, {"n": 1, "latin": "ohd"}]})
    listener = BlindListener(db_session, 1, runner)
    words = BlockInput("b", "s", "از عَهْد").words
    assert asyncio.run(listener.hear(wav, words, "b")) == {0: "az", 1: "ohd"}
    assert asyncio.run(listener.hear(wav, words, "b")) == {0: "az", 1: "ohd"}
    assert runner.requests == [{"words": [{"n": 0, "text": "از"}, {"n": 1, "text": "عهد"}]}]


def test_the_judge_rules_for_every_finding_and_a_word_it_does_not_answer_or_a_reading_without_latin_stays_unclear(db_session):
    item = BlockInput("b", "s", "از عهد حرف زد", english="He spoke of vows.")
    findings = [Finding("b", 1, "عهد", "'ahd", "ohd", "o h d"), Finding("b", 2, "حرف", "harf", "harfe", "h a r f e"), Finding("b", 3, "زد", "zad", "zed", "z e d")]
    runner = Script({"words": [{"n": 1, "who": "voice", "why": "the vowel"}, {"n": 2, "who": "reading", "latin": ""}]})
    rulings = asyncio.run(Judge(db_session, 1, FilmTable.build({}), runner).rule(item, findings))
    assert [(r.who, r.latin) for r in rulings] == [("voice", ""), ("unclear", ""), ("unclear", "")]
    assert runner.requests[0]["english_source"] == "He spoke of vows." and runner.requests[0]["words"][0]["written_reading"] == "'ahd"


class FakeInspector:
    """Finds what the script says for a block in each round (the blocks listened to again are the ones voiced again)."""

    def __init__(self, rounds):
        self.rounds, self.round, self.asked = rounds, 0, []

    async def inspect(self, block_id, text, readings, wav, spoken):
        self.asked.append(block_id)
        step = self.rounds[min(self.round, len(self.rounds) - 1)]
        return step.get(block_id, []), step.get(f"{block_id}:weak", [])


class FakeJudge:
    def __init__(self, who, latin=""):
        self.who, self.latin = who, latin

    async def rule(self, item, findings):
        from app.documentary.reading.judge import Ruling

        return [Ruling(f, self.who, self.latin, "because") for f in findings]


def manifest(*ids):
    return {"timeline": {"blocks": [{"block_id": b, "start": 0.0, "wav": f"{b}.wav"} for b in ids], "words": []}}


def build(db_session, texts, readings, inspector, judge, fixed=None):
    items = [BlockInput(b, "s", t) for b, t in texts.items()]
    return ReadingLoop(db_session, 1, "fa", items, inspector, judge, Voicing(fixed or {}, readings))


def test_a_word_the_voice_said_wrong_is_voiced_again_as_ipa_and_kept_for_every_film_when_it_is_right(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.reading, "max_rounds", 3)
    finding = Finding("a", 1, "عهد", "'ahd", "ohd", "o h d")
    inspector = FakeInspector([{"a": [finding]}, {}])
    loop = build(db_session, {"a": "از عهد", "b": "حرف زد"}, {"a": ["az", "'ahd"], "b": ["harf", "zad"]}, inspector, FakeJudge("voice"))
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
    finding = Finding("a", 1, "پرت", "pat", "perat", "p r a t")
    inspector = FakeInspector([{"a": [finding]}])
    loop = build(db_session, {"a": "قاضی پرت"}, {"a": ["qāzi", "pat"]}, inspector, FakeJudge("voice"))
    calls = []

    async def revoice(block_ids, round_no):
        calls.append(round_no)
        return manifest("a")

    asyncio.run(loop.run(manifest("a"), revoice))
    assert calls == [1, 2] and [r["round"] for r in loop.report["rounds"]] == [0, 1, 2]
    assert [u["word"] for u in loop.report["unfixed"]] == ["پرت"] and loop.report["fixed"] == []
    assert db_session.query(ReadingApproval).filter_by(surface="پرت").count() == 0, "a word that is still wrong is not kept"


def test_a_wrong_reading_is_corrected_in_the_reading_and_a_missing_ezafe_is_not_kept_for_every_film(db_session):
    findings = [Finding("a", 0, "گاراژ", "garāzh", "gārāzh", "g ā r ā zh"), Finding("a", 1, "اداره", "edāre", "edāre-ye", "e d ā r e j e")]
    loop = build(db_session, {"a": "گاراژ اداره"}, {"a": ["garāzh", "edāre"]}, FakeInspector([{"a": findings}]), FakeJudge("reading", "gārāzh"))

    async def revoice(block_ids, round_no):
        raise AssertionError("nothing is voiced again for a wrong reading")

    asyncio.run(loop.run(manifest("a"), revoice))
    assert [r["new_reading"] for r in loop.report["reading_fixed"]] == ["gārāzh", "gārāzh"]
    assert db_session.query(ReadingApproval).filter_by(surface="گاراژ").one().latin == "gārāzh"
    assert loop.voicing.readings["a"][0] == "gārāzh", "the voice is given the corrected reading"


def test_what_the_judge_cannot_tell_is_reported_and_a_block_whose_words_do_not_match_its_reading_is_not_listened_to(db_session):
    finding = Finding("a", 1, "جایگاه", "jāygāh", "jāygāh-e", "j ā y g ā h e")
    inspector = FakeInspector([{"a": [finding]}])
    loop = build(db_session, {"a": "این جایگاه", "b": "یک دو سه"}, {"a": ["in", "jāygāh"], "b": ["yek", "do"]}, inspector, FakeJudge("unclear"))

    async def revoice(block_ids, round_no):
        raise AssertionError("an unclear word is not voiced again")

    asyncio.run(loop.run(manifest("a", "b"), revoice))
    assert [u["word"] for u in loop.report["unclear"]] == ["جایگاه"]
    assert loop.report["unchecked_blocks"] == ["b"] and inspector.asked == ["a"]


def test_a_word_only_one_listener_doubts_is_a_weak_flag_in_the_report_and_is_never_voiced_again(db_session):
    weak = Finding("a", 1, "حرف", "harf", "harfe", "h a r f")
    loop = build(db_session, {"a": "از حرف"}, {"a": ["az", "harf"]}, FakeInspector([{"a:weak": [weak]}]), FakeJudge("voice"))

    async def revoice(block_ids, round_no):
        raise AssertionError("a weak flag is only reported")

    asyncio.run(loop.run(manifest("a"), revoice))
    assert loop.report["weak_flags"] == [{"block": "a", "word": "حرف", "reading": "harf", "listener": "harfe", "recogniser": "h a r f"}]


def test_a_judgement_is_kept_and_not_paid_twice_and_a_block_the_judge_fails_on_stays_unclear(db_session, tmp_path, monkeypatch):
    item = BlockInput("b", "s", "از عهد")
    finding = Finding("b", 1, "عهد", "'ahd", "ohd", "o h d")
    runner = Script({"words": [{"n": 1, "who": "voice"}]})
    kept = tmp_path / "judge.json"
    first = asyncio.run(Judge(db_session, 1, FilmTable.build({}), runner, kept).rule(item, [finding]))
    again = asyncio.run(Judge(db_session, 1, FilmTable.build({}), runner, kept).rule(item, [finding]))
    assert [r.who for r in first] == [r.who for r in again] == ["voice"] and len(runner.requests) == 1

    async def broken(gen, body):
        raise ASK.GenerationError("invalid_output", "truncated")

    monkeypatch.setattr(ai_config.reading, "call_retries", 0)
    stuck = asyncio.run(Judge(db_session, 1, FilmTable.build({}), broken, None).rule(item, [finding]))
    assert [r.who for r in stuck] == ["unclear"] and "judge failed" in stuck[0].why
