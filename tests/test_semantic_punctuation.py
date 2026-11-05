"""Semantic punctuation and ezafe boundaries of a Persian narration: meaning first (the same words punctuated by their meaning, or not at all when
the meaning is open), ezafe required / forbidden / context-dependent, punctuation changes only in an ephemeral layer traced to the approved text,
the independent checker's bounded loop, the planner's use of the boundaries, and no paid call without the agents switched on. The agents are fakes."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.core.ai_config import ai_config
from app.documentary.pronunciation_planner import Planner
from app.documentary.punctuation import boundary as BD
from app.documentary.punctuation import stage as PS
from app.documentary.punctuation.layer import Edit, Layer, LayerError, check
from app.documentary.reading import ask as ASK
from app.documentary.reading.voicing import Voicing, WordSignal

DETECTIVE = "کارآگاه آرام وارد اتاق شد."


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(ASK, "track_run", lambda *a, **k: contextlib.nullcontext(object()))
    monkeypatch.setattr(ASK, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(ASK, "get_generation_provider", lambda: object())


def agent(*answers):
    """A fake model answering in turn; it keeps what it was asked."""
    seen = []

    async def run(gen, body):
        seen.append(body)
        return answers[min(len(seen), len(answers)) - 1], None

    run.seen = seen
    return run


def never():
    async def run(gen, body):
        raise AssertionError("no model may be called")
    return run


def block(text: str, tts: str | None = None, block_id: str = "b") -> dict:
    return {"block_id": block_id, "text": text, **({"tts_text": tts} if tts else {})}


def staged(text: str, syntax: dict, edits: list, *verdicts: dict, tts: str | None = None):
    stage = PS.PunctuationStage(None, None, use_agents=True, syntax_runner=agent(syntax), punctuate_runner=agent(*[{"edits": e} for e in edits]),
                                check_runner=agent(*verdicts) if verdicts else never())
    return asyncio.run(stage.block(block(text, tts))), stage


# --- L1: the meaning decides ---------------------------------------------------------------------------------------------------------------

def test_the_adverbial_reading_is_made_clear_with_a_comma_the_checker_passed():
    result, _ = staged(DETECTIVE, {"boundaries": [{"n": 0, "ezafe": "forbidden", "relation": "subject_adverb", "reason": "he entered quietly",
                                                    "comma_ok": True}]},
                       [[{"after": 0, "new": "،", "reason": "the subject ends: آرام tells how he entered"}]], {"decision": "PASS"})
    assert result.layer.text == "کارآگاه، آرام وارد اتاق شد." and result.review == "PASS"
    assert result.boundaries[0].status == BD.FORBIDDEN and result.boundaries[0].punctuation_can_clarify
    assert result.layer.canonical == DETECTIVE, "the approved text is never changed"


def test_the_adjectival_reading_keeps_its_ezafe_and_a_comma_there_is_refused():
    result, _ = staged(DETECTIVE, {"boundaries": [{"n": 0, "ezafe": "required", "relation": "noun_adjective", "reason": "the calm detective"}]},
                       [[{"after": 0, "new": "،", "reason": "a pause"}]])
    assert result.layer.text == DETECTIVE and "cut the construction" in result.refused[0]["why"]
    assert (result.boundaries[0].status, result.boundaries[0].pronunciation, result.boundaries[0].needs_phonetic_guidance) == (BD.REQUIRED, "", True)


def test_an_open_meaning_is_flagged_and_nothing_is_chosen():
    result, _ = staged(DETECTIVE, {"boundaries": [{"n": 0, "ezafe": "context"}],
                                   "ambiguities": [{"n": 0, "readings": ["kārāgāh | ārām", "kārāgāh-e ārām"], "question": "quietly or the calm detective?"}]},
                       [[]])
    assert result.layer.text == DETECTIVE and result.boundaries[0].status == BD.CONTEXT and "quietly or the calm detective" in result.flags[0]
    rules_only = asyncio.run(PS.PunctuationStage(None, None, use_agents=False).block(block(DETECTIVE)))
    assert rules_only.layer.text == DETECTIVE and rules_only.boundaries[0].status == BD.CONTEXT and rules_only.review == "NOT_REVIEWED"


# --- L2: ezafe boundaries ------------------------------------------------------------------------------------------------------------------

def statuses(text: str, syntax: dict | None = None) -> list[tuple[str, str]]:
    layer = Layer("b", text, text)
    return [(d.status, d.pronunciation) for d in BD.analyze(layer.words, layer.gaps(), syntax)]


def test_a_written_ezafe_is_required_with_its_spoken_form_and_rules_forbid_what_never_takes_one():
    assert statuses("درِ بزرگِ خانه‌ی قدیمی باز بود")[:3] == [(BD.REQUIRED, "-e"), (BD.REQUIRED, "-e"), (BD.REQUIRED, "-ye")], "a chain"
    assert statuses("صدای خانهٔ ما")[1] == (BD.REQUIRED, "-ye")
    assert statuses("کتاب را برداشت")[0][0] == BD.FORBIDDEN, "no ezafe before را"
    assert statuses("او کتاب می‌خواند")[1][0] == BD.FORBIDDEN, "no ezafe before a verb"
    assert statuses("رفت. بعد آمد")[0][0] == BD.FORBIDDEN, "no ezafe across a sentence end"
    assert statuses("گفت «یه نفر آمد»")[0][0] == BD.FORBIDDEN, "no ezafe into a quotation"
    assert statuses("و رفت")[0] == (BD.NONE, "")
    assert statuses("کتاب بزرگ")[0][0] == BD.CONTEXT, "adjacency alone decides nothing"


def test_the_syntax_agent_settles_a_boundary_with_its_reason_and_a_name_title_takes_its_ezafe():
    found = statuses("قاضی پرت گفت", {0: {"ezafe": "required", "relation": "title_name", "reason": "title + name"}})
    assert found[0] == (BD.REQUIRED, "-ye"), "a final ی: -ye"
    assert statuses("دادگاه شهر", {0: {"ezafe": "required"}})[0] == (BD.REQUIRED, ""), "a final ه: the letters do not decide -e or -ye"
    assert statuses("کالب رفت", {0: {"ezafe": "forbidden", "relation": "subject_verb"}})[0] == (BD.FORBIDDEN, "")


# --- L3: punctuation -----------------------------------------------------------------------------------------------------------------------

def test_latin_punctuation_takes_its_persian_form_and_correct_punctuation_is_left_alone():
    rules = asyncio.run(PS.PunctuationStage(None, None, use_agents=False).block(block("او آمد, نشست و پرسید: کی رفت?")))
    assert rules.layer.text == "او آمد، نشست و پرسید: کی رفت؟" and {e.old for e in rules.layer.edits} == {",", "?"}
    unchanged = asyncio.run(PS.PunctuationStage(None, None, use_agents=False).block(block("او آمد، نشست و رفت.")))
    assert unchanged.layer.edits == []


def test_a_proposal_into_a_gap_with_punctuation_or_with_a_mark_not_allowed_is_refused():
    result, _ = staged("او آمد، نشست.", {"boundaries": []}, [[{"after": 1, "new": "،"}, {"after": 0, "new": "—"}]])
    assert [r["why"] for r in result.refused] == ["the gap already has punctuation", "'—' is not a mark the layer may insert"]
    assert result.layer.text == "او آمد، نشست." and result.review == "UNCERTAIN"


def test_the_checker_loop_drops_what_it_names_keeps_only_rule_changes_when_unsure_and_stops():
    text = "او آمد و نشست و بعد رفت"
    two = [{"after": 1, "new": "،", "reason": "a"}, {"after": 3, "new": "،", "reason": "b"}]
    revised, _ = staged(text, {"boundaries": []}, [two], {"decision": "REVISE", "drop": [1], "issues": ["too many pauses"]}, {"decision": "PASS"})
    assert revised.layer.text == "او آمد، و نشست و بعد رفت" and [c["decision"] for c in revised.checks] == ["REVISE", "PASS"]
    unsure, _ = staged(text, {"boundaries": []}, [two], {"decision": "UNCERTAIN", "issues": ["cannot tell"]})
    assert unsure.layer.text == text and unsure.review == "UNCERTAIN" and "only the rule changes are kept" in unsure.flags[0]
    rejected, _ = staged(text, {"boundaries": []}, [two], {"decision": "REJECT", "issues": ["changes the meaning"]})
    assert rejected.layer.text == text and rejected.review == "REJECT"
    vague, _ = staged(text, {"boundaries": []}, [two], {"decision": "REVISE", "issues": ["something"]})
    assert vague.review == "UNCERTAIN", "a REVISE without the edits to change is uncertain, never a pass"
    endless, _ = staged(text, {"boundaries": []}, [two], {"decision": "REVISE", "drop": [0]}, {"decision": "REVISE", "drop": [0]})
    assert endless.review == "UNRESOLVED" and endless.layer.text == text and endless.rounds == ai_config.semantic_punctuation.max_rounds


# --- L4: safety ----------------------------------------------------------------------------------------------------------------------------

def test_the_layer_changes_punctuation_only_keeps_tags_and_maps_every_word_back_to_the_approved_text():
    layer = Layer("b", "کالب آمد و رفت", "[calm] کالب آمد و رفت", story_span=(100, 114))
    layer.edits = [Edit(1, "", "،", "a pause")]
    assert layer.text == "[calm] کالب آمد، و رفت" and check(layer) == []
    third = layer.word_map()[2]
    assert (third["canonical"], third["story"]) == ([9, 10], [109, 110]) and layer.text[third["tts"][0]:third["tts"][1]] == "و"
    assert layer.describe()["edits"][0]["canonical_offset"] == 8
    with pytest.raises(LayerError):
        _ = Layer("b", "او آمد", "او آمد", [Edit(0, "", "x", "a letter")]).text
    tampered = Layer("b", "او آمد", "او آمد")
    tampered.base = "او رفت"
    assert "the base's words are not the approved text's" in check(tampered)


def test_without_the_agents_no_model_is_called_and_identical_blocks_are_analysed_once_per_run_only():
    stage = PS.PunctuationStage(None, None, use_agents=False, syntax_runner=never(), punctuate_runner=never(), check_runner=never())
    results, stats = asyncio.run(stage.run([block("او آمد", block_id="a"), block("او آمد", block_id="b")]))
    assert stats["agents"] is False and set(results) == {"a", "b"} and len(stage.memo) == 2, "the context differs: two analyses"
    syntax = agent({"boundaries": []})
    paid = PS.PunctuationStage(None, None, use_agents=True, syntax_runner=syntax, punctuate_runner=agent({"edits": []}), check_runner=never())
    asyncio.run(paid.block(block("او آمد")))
    asyncio.run(paid.block(block("او آمد")))
    assert len(syntax.seen) == 1, "identical work is done once within the run"
    assert PS.PunctuationStage(None, None).memo == {}, "a new run starts with nothing"


def test_the_planner_escalates_a_word_where_ppie_and_the_boundary_analysis_disagree(db_session):
    voicing = Voicing({}, {"b": ["kārāgāh-e", "ārām", "vārede", "otāgh", "shod"]}, {"b": [WordSignal()] * 5},
                      boundaries={"b": {0: {"status": BD.FORBIDDEN, "reason": "subject before adverb", "source": "syntax_agent"}}})
    plan = Planner(db_session, "fa", "v", "m").plan("b", DETECTIVE, voicing)
    assert plan.disagreements and plan.disagreements[0]["ppie"] == "E" and plan.decisions[0].strategy == "escalate"
    assert plan.text == DETECTIVE, "neither side wins: the word stays as written for a person"
    agree = Planner(db_session, "fa", "v", "m").plan("b", DETECTIVE, Voicing({}, {"b": ["kārāgāh", "ārām", "vārede", "otāgh", "shod"]},
                                                                                {"b": [WordSignal()] * 5}, boundaries=voicing.boundaries))
    assert agree.disagreements == []


def test_the_stage_gives_the_plan_its_layer_and_the_planner_its_boundaries_and_only_persian_has_one():
    plan = {"language": "fa", "blocks": [block("او آمد, رفت", block_id="a")]}
    results, _ = asyncio.run(PS.PunctuationStage(None, None, use_agents=False).run(plan["blocks"]))
    applied = PS.applied(plan, results)
    assert applied["blocks"][0]["tts_text"] == "او آمد، رفت" and applied["blocks"][0]["text"] == "او آمد, رفت"
    assert PS.boundaries_of(results)["a"][1]["status"] == BD.FORBIDDEN
    assert ai_config.ppie.fresh("fa") and not ai_config.ppie.fresh("en"), "the stage runs only in the Persian path"


def test_a_run_that_stopped_continues_from_its_own_memo_without_asking_the_models_again(tmp_path):
    text = "او آمد و نشست"
    first, _ = staged(text, {"boundaries": []}, [[]])
    memo = tmp_path / "memo.pkl"
    one = PS.PunctuationStage(None, None, use_agents=True, syntax_runner=agent({"boundaries": []}), punctuate_runner=agent({"edits": []}), check_runner=never(), memo_path=memo)
    done = asyncio.run(one.block(block(text)))
    again = PS.PunctuationStage(None, None, use_agents=True, syntax_runner=never(), punctuate_runner=never(), check_runner=never(), memo_path=memo)
    assert asyncio.run(again.block(block(text))).layer.text == done.layer.text == first.layer.text, "the second stage reads the finished block from the file"
    elsewhere = PS.PunctuationStage(None, None, use_agents=True, syntax_runner=agent({"boundaries": []}), punctuate_runner=agent({"edits": []}), check_runner=never())
    asyncio.run(elsewhere.block(block(text)))
    assert elsewhere.memo, "without a memo file nothing is kept between runs"


def test_a_voice_text_that_joined_two_words_has_no_word_map_and_is_reported_not_crashed():
    rejected = asyncio.run(PS.PunctuationStage(None, None, use_agents=False).block(block("با باتنر، بدون اشلی.", tts="با باتنر—بدون اشلی.")))
    assert rejected.review == "REJECT" and any("source integrity" in f for f in rejected.flags)
    assert rejected.describe()["word_map"] == [], "the record is written; the block is flagged"
