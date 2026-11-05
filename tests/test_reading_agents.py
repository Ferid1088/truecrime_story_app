"""The agents of the reading loop with fake models: the reader's merge, the editor asked again for an answer that is not the block's words, the
checker and its arbiter, and the whole stage with its doubts kept in the database until they are answered. The names are invented."""

from __future__ import annotations

import asyncio
import contextlib
import json
from uuid import uuid4

import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, ReadingApproval, ReadingDoubt, StoryVersion
from app.documentary.reading import ask as ASK
from app.documentary.reading import doubts as DB
from app.documentary.reading.answer import ReadingWord
from app.documentary.reading.ask import ReadingFailed
from app.documentary.reading.block import BlockInput, block_inputs
from app.documentary.reading.checker import Checker
from app.documentary.reading.glossary import Glossary, merge
from app.documentary.reading.table import FilmTable
from app.documentary.reading.text import ReadingStage
from app.documentary.reading.writer import Writer


@pytest.fixture(autouse=True)
def review_mode(monkeypatch):
    """These tests speak the review checker's answer format; the blind checker has its own tests below."""
    monkeypatch.setattr(ai_config.reading, "checker_mode", "review")


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(ASK, "track_run", lambda *a, **k: contextlib.nullcontext(object()))
    monkeypatch.setattr(ASK, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(ASK, "get_generation_provider", lambda: object())


READER = {"people": [{"id": "sara", "first": {"fa": "سارا", "latin": "sārā"}, "last": {"fa": "مرادی", "latin": "morādi"}}], "places": [], "foreign": []}


class Script:
    """A fake model: answers from a list, or from a function of the request, and keeps the requests."""

    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    async def __call__(self, gen, body):
        request = json.loads(body)
        self.seen.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return (answer(request) if callable(answer) else answer), None


def item(text="سارا بهم گفت", block_id="B1"):
    return BlockInput(block_id, "S1", text, english="Sara told them.", previous="قبل", next="بعد")


def answer_for(latins):
    return {"words": [{"n": n, "latin": latin, "why": "x", "confidence": "high"} for n, latin in enumerate(latins)]}


def test_a_person_the_reader_finds_again_keeps_one_id_and_gets_the_new_titles_and_places_are_kept_once():
    known = merge({}, {"people": [{"id": "reza", "first": {"fa": "رضا", "latin": "rezā"}, "last": {"fa": "کریمی", "latin": "karimi"}}],
                       "places": [{"fa": "تیپ‌سیتی", "latin": "tip siti"}]})
    later = merge(known, {"people": [{"id": "r2", "first": {"fa": "رضا", "latin": "rezā"}, "last": {"fa": "کریمی", "latin": "karimi"},
                                      "titles": [{"fa": "قاضی", "latin": "qāzi"}]}], "places": [{"fa": "تیپ‌سیتی", "latin": "tip siti"}]})
    assert len(later["people"]) == 1 and later["people"][0]["id"] == "reza" and later["people"][0]["titles"] == [{"fa": "قاضی", "latin": "qāzi"}]
    assert len(later["places"]) == 1


def test_the_reader_asks_a_chunk_at_a_time_and_tells_each_what_the_earlier_ones_found(monkeypatch):
    monkeypatch.setattr(ai_config.reading, "chunk_blocks", 1)
    script = Script(lambda r: READER if r["blocks"][0]["block"] == "B1" else {"people": [], "doubts": [{"word": "ملک", "block": "B2", "question": "?"}]})
    table, doubts = asyncio.run(Glossary(None, 1, script).build([{"block": "B1", "english": "one", "text": "x"}, {"block": "B2", "english": "two", "text": "y"}]))
    assert [p["id"] for p in table["people"]] == ["sara"] and len(script.seen) == 2
    assert script.seen[0]["known"]["people"] == [] and script.seen[1]["known"]["people"][0]["id"] == "sara" and script.seen[1]["english"] == "two"
    assert doubts == [{"word": "ملک", "block": "B2", "question": "?"}]


def test_the_editor_is_asked_again_with_the_problems_when_it_drops_a_word_and_a_name_is_read_as_the_table_says():
    script = Script(answer_for(["sara", "baham"]), answer_for(["sara", "baham", "goft"]))
    got, notes = asyncio.run(Writer(None, 1, FilmTable.build(READER), script).write(item()))
    assert [r.latin for r in got] == ["sārā", "baham", "goft"], "a name is read as the film table says"
    assert len(script.seen) == 2 and "problems" not in script.seen[0] and "3 words of the block" in script.seen[1]["problems"][0]
    assert script.seen[0]["film_table"] == [{"fa": "سارا", "latin": "sārā", "kind": "person"}] and script.seen[0]["english_source"] == "Sara told them."
    assert any("film table" in n for n in notes)


def test_an_editor_that_keeps_answering_wrong_stops_the_stage(monkeypatch):
    monkeypatch.setattr(ai_config.reading, "max_writer_retries", 1)
    script = Script({"words": []})
    with pytest.raises(ReadingFailed):
        asyncio.run(Writer(None, 1, FilmTable.build(READER), script).write(item()))
    assert len(script.seen) == 2


def readings(*latins):
    return [ReadingWord(n, latin, why="w") for n, latin in enumerate(latins)]


def check(findings, decision=None, text="سارا بهم گفت"):
    arbiter = Script(decision or {"decision": "doubt"})
    checker = Checker(None, 1, FilmTable.build(READER), Script({"words": findings}), arbiter)
    return asyncio.run(checker.check(item(text), readings(*["sārā", "beham", "goft"]))), arbiter


def test_a_word_the_checker_rejects_goes_to_the_arbiter_and_the_arbiters_choice_is_used():
    (out, doubts), arbiter = check([{"n": 1, "verdict": "reject", "proposed_latin": "baham", "proof": "spoken form"}], {"decision": "checker", "latin": "baham"})
    assert [r.latin for r in out] == ["sārā", "baham", "goft"] and doubts == []
    assert arbiter.seen[0]["word"] == "بهم" and arbiter.seen[0]["editor"]["latin"] == "beham" and arbiter.seen[0]["checker"]["proof"] == "spoken form"
    (out, _), _ = check([{"n": 1, "verdict": "reject", "proposed_latin": "baham", "proof": "p"}], {"decision": "editor"})
    assert out[1].latin == "beham"
    (out, _), _ = check([{"n": 1, "verdict": "reject", "proposed_latin": "baham", "proof": "p"}], {"decision": "other", "latin": "bāham"})
    assert out[1].latin == "bāham"


def test_a_word_nobody_can_settle_is_a_doubt_with_both_readings_and_the_arbiters_question():
    (out, doubts), _ = check([{"n": 1, "verdict": "reject", "proposed_latin": "baham", "proof": "p"}], {"decision": "doubt", "question": "which?"})
    assert out[1].latin == "beham" and len(doubts) == 1
    d = doubts[0]
    assert (d.n, d.surface, d.editor_latin, d.checker_latin, d.question) == (1, "بهم", "beham", "baham", "which?")
    (_, doubts), arbiter = check([{"n": 1, "verdict": "doubt", "question": "malek or melk?"}])
    assert doubts[0].question == "malek or melk?" and arbiter.seen == [], "a doubt of the checker needs no arbiter"


def test_the_checker_never_questions_a_name_the_table_fixes_or_a_proposal_equal_to_the_editors_reading():
    (out, doubts), arbiter = check([{"n": 0, "verdict": "reject", "proposed_latin": "sara", "proof": "p"},
                                    {"n": 1, "verdict": "reject", "proposed_latin": "beham", "proof": "p"}])
    assert [r.latin for r in out] == ["sārā", "beham", "goft"] and doubts == [] and arbiter.seen == []


def test_a_doubt_of_the_reader_about_digits_is_not_asked_because_code_reads_numbers():
    words = BlockInput("B1", "S1", "تماس ۹۱۱ رو شنید", "")
    found = [{"block": "B1", "word": "۹۱۱", "question": "English or Persian digits?"}, {"block": "B1", "word": "شنید", "question": "which?"}]
    got = ReadingStage.reader_doubts(words, found, readings("tamās", "NUM", "ro", "shenid"))
    assert [(d.surface, d.editor_latin) for d in got] == [("شنید", "shenid")]


def test_the_blocks_of_a_plan_get_their_neighbours_and_the_english_of_their_section():
    blocks = [{"block_id": "B1", "section_id": "B1.g01", "beats": [{"beat_id": "B1"}], "text": "[calm] یک دو سه"},
              {"block_id": "B2", "section_id": "B2.g01", "tts_text": "چهار"}]
    first, second = block_inputs(blocks, {"B1": "one", "B2": "two"}, 5)
    assert (first.previous, first.next, first.english) == ("", "چهار", "one") and (second.previous, second.next) == ("دو سه", "")
    assert second.english == "two", "a block without beats tells the beat its section is named after"
    assert [w.plain for w in first.words] == ["یک", "دو", "سه"]


@pytest.fixture
def stage_env(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    monkeypatch.setattr(ai_config.reading, "blind_check", "all")          # these tests are about the stage's bookkeeping: every block is checked
    case = Case(canonical_title="Reading", slug=f"rd-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.flush()
    version = StoryVersion(case_id=case.id, version=1, kind="spoken", language="zz", story_text="t", narrative_angle="a", status="ready")
    db_session.add(version)
    db_session.commit()
    yield case, version
    db_session.rollback()
    for model in (ReadingDoubt, ReadingApproval):
        db_session.query(model).filter(model.case_id == case.id).delete()
    db_session.query(StoryVersion).filter_by(case_id=case.id).delete()
    db_session.query(Case).filter_by(id=case.id).delete()
    db_session.commit()


def stage(db, case, version, items, calls):
    class Reader:
        async def build(self, blocks):
            calls["reader"] += 1
            return READER, []

    async def write(gen, body):
        calls["write"] += 1
        words = json.loads(body)["words"]
        return answer_for(["sara" if w["text"] == "سارا" else "x" for w in words]), None

    async def check(gen, body):
        calls["check"] += 1
        words = json.loads(body)["words"]
        return {"words": [{"n": 1, "verdict": "doubt", "question": "which?"}] if len(words) > 1 and words[1]["text"] == "بهم" else []}, None

    return ReadingStage(db, case.id, version, "zz", items, Reader(), write, check, Script({"decision": "doubt"}))


def test_the_stage_keeps_the_doubts_until_the_owner_answers_and_a_rerun_pays_for_nothing_it_has_done(db_session, stage_env):
    case, version = stage_env
    items = [item("سارا بهم گفت", "B1"), item("او رفت", "B2")]
    calls = {"reader": 0, "write": 0, "check": 0}
    result = asyncio.run(stage(db_session, case, version, items, calls).run())
    assert result.open_doubts == 1 and calls == {"reader": 1, "write": 2, "check": 2}
    assert result.readings["B1"][0].latin == "sārā" and result.readings["B1"][1].latin == "x"
    row = DB.open_rows(db_session, version.id)[0]
    assert (row.block_id, row.word_n, row.surface, row.question, row.status) == ("B1", 1, "بهم", "which?", "open")
    assert row.sentence == "سارا بهم گفت"

    again = asyncio.run(stage(db_session, case, version, items, calls).run())
    assert again.open_doubts == 1 and calls == {"reader": 1, "write": 2, "check": 2}, "nothing is asked twice"

    DB.answer(db_session, row, " baham ", everywhere=False, language="zz")
    done = asyncio.run(stage(db_session, case, version, items, calls).run())
    assert done.open_doubts == 0 and done.readings["B1"][1].latin == "baham" and done.readings["B1"][1].confidence == "high"
    assert calls == {"reader": 1, "write": 2, "check": 2}, "the owner's answer needs no new call"
    assert db_session.query(ReadingApproval).filter_by(language="zz").count() == 0


def test_an_answer_for_everywhere_is_kept_for_every_film_and_becomes_a_reading_code_enforces(db_session, stage_env):
    case, version = stage_env
    items = [item("سارا بهم گفت", "B1")]
    calls = {"reader": 0, "write": 0, "check": 0}
    asyncio.run(stage(db_session, case, version, items, calls).run())
    DB.answer(db_session, DB.open_rows(db_session, version.id)[0], "baham", everywhere=True, language="zz")
    saved = db_session.query(ReadingApproval).filter_by(language="zz").one()
    assert (saved.surface, saved.kind, saved.latin, saved.source) == ("بهم", "spoken_form", "baham", "owner")
    asyncio.run(stage(db_session, case, version, items, calls).run())
    assert calls["write"] == 2, "a new approval changes the block's table, so the block is written again, and the editor's own answer is overwritten by it"
    final = asyncio.run(stage(db_session, case, version, items, calls).run())
    assert final.readings["B1"][1].latin == "baham" and final.open_doubts == 0


def test_the_reading_stage_stops_the_run_before_the_voice_while_words_are_in_doubt(monkeypatch):
    from app.documentary.jobs import failure_details
    from app.documentary.pipeline.errors import AwaitingReadingReview
    from app.documentary.reading import stage as RS
    from app.documentary.reading.text import ReadingResult

    monkeypatch.setattr(RS, "performance_for_version", lambda db, version: {"blocks": [{"block_id": "B1", "section_id": "B1.g01", "text": "سارا آمد"}]})
    monkeypatch.setattr(RS.VP, "english_sources", lambda db, version: {"B1": "Sara came."})

    class Version:
        id = 7

    class Case:
        id = 1

    def fake_run(result):
        async def run(self):
            return result
        return run

    monkeypatch.setattr(RS.ReadingStage, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(RS.ReadingStage, "run", fake_run(ReadingResult({}, 3, {"B1": ["a note"]})))
    with pytest.raises(AwaitingReadingReview) as stopped:
        asyncio.run(RS.run(None, Case(), Version(), "fa"))
    assert "3 words" in str(stopped.value) and "Reading Review tab" in str(stopped.value)
    assert "Reading review" in failure_details(str(stopped.value))["next_action"]

    monkeypatch.setattr(RS.ReadingStage, "run", fake_run(ReadingResult({}, 0, {"B1": ["a note", "b note"]})))
    assert asyncio.run(RS.run(None, Case(), Version(), "fa")) == {"blocks": 1, "words": 2, "changed_by_code": 2, "open_doubts": 0, "doubts_in_run": 0}
    assert RS.required("fa") and not RS.required("en")


# --- the blind checker ---------------------------------------------------------------------------------------------------------------------

def blind(answer, monkeypatch, decision=None):
    monkeypatch.setattr(ai_config.reading, "checker_mode", "blind")
    checker_model, arbiter = Script(answer), Script(decision or {"decision": "editor"})
    out = asyncio.run(Checker(None, 1, FilmTable.build(READER), checker_model, arbiter).check(item(), readings("sārā", "baham", "goft")))
    return out, checker_model, arbiter


def test_the_blind_checker_never_sees_the_editors_answer_and_its_own_differing_reading_goes_to_the_arbiter(monkeypatch):
    (out, doubts), model, arbiter = blind(answer_for(["sārā", "beham", "goft"]), monkeypatch, {"decision": "checker", "latin": "beham"})
    assert "reading" not in model.seen[0] and [w["text"] for w in model.seen[0]["words"]] == ["سارا", "بهم", "گفت"]
    assert out[1].latin == "beham" and doubts == [], "an error of the editor is found by a reading made without it"
    assert arbiter.seen[0]["editor"]["latin"] == "baham" and arbiter.seen[0]["checker"]["latin"] == "beham"


def test_a_blind_reading_that_agrees_calls_no_arbiter_and_a_low_confidence_word_is_a_doubt(monkeypatch):
    (out, doubts), _, arbiter = blind(answer_for(["sārā", "baham", "goft"]), monkeypatch)
    assert arbiter.seen == [] and doubts == [] and out[1].latin == "baham"
    unsure = answer_for(["sārā", "baham", "goft"])
    unsure["words"][1].update(confidence="low", question="beham or baham?")
    (_, doubts), _, _ = blind(unsure, monkeypatch)
    assert [(d.n, d.question) for d in doubts] == [(1, "beham or baham?")]


def test_a_word_the_blind_checker_leaves_out_is_not_taken_as_agreement(monkeypatch):
    (out, doubts), _, arbiter = blind({"words": [{"n": 0, "latin": "sārā"}]}, monkeypatch)
    assert arbiter.seen == [] and doubts == [] and [r.latin for r in out] == ["sārā", "baham", "goft"]
    assert out[1].ezafe is None, "no judgement is filled in for a word the checker did not read"


def test_the_risk_gate_sends_only_a_block_the_editor_is_unsure_of_to_the_independent_checker(db_session, stage_env, monkeypatch):
    monkeypatch.setattr(ai_config.reading, "blind_check", "risk")
    case, version = stage_env
    calls = {"write": 0, "check": 0}

    class Reader:
        async def build(self, blocks):
            return READER, []

    async def write(gen, body):
        calls["write"] += 1
        words = json.loads(body)["words"]
        answer = answer_for(["x" for _ in words])
        if words[0]["text"] == "او":
            answer["words"][0]["confidence"] = "low"
        return answer, None

    async def check(gen, body):
        calls["check"] += 1
        return {"words": []}, None

    items = [item("سارا بهم گفت", "B1"), item("او رفت", "B2")]
    stage_run = ReadingStage(db_session, case.id, version, "zz", items, Reader(), write, check, Script({"decision": "doubt"}))
    asyncio.run(stage_run.run())
    assert calls == {"write": 2, "check": 1} and stage_run.stats == {"checked": 1, "skipped": 1}, "the sure block is validated by code only; the unsure one is checked"
