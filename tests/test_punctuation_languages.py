"""The punctuation layer in Arabic, German and English: each language has its own profile (marks, normalisation), its own agents and prompts, no ezafe
analysis, and a construction the syntax agent calls one is never cut. The words, letters, vowel marks and tags never change. The agents are fakes."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import pytest

from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.documentary.punctuation import boundary as BD
from app.documentary.punctuation import stage as PS
from app.documentary.punctuation.layer import Edit, Layer, LayerError, check
from app.documentary.reading import ask as ASK


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(ASK, "track_run", lambda *a, **k: contextlib.nullcontext(object()))
    monkeypatch.setattr(ASK, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(ASK, "get_generation_provider", lambda: object())


def agent(*answers):
    seen = []

    async def run(gen, body):
        seen.append(body)
        return answers[min(len(seen), len(answers)) - 1], None

    run.seen = seen
    return run


def staged(language: str, text: str, syntax: dict, edits: list, *verdicts: dict):
    stage = PS.PunctuationStage(None, None, use_agents=True, syntax_runner=agent(syntax), punctuate_runner=agent(*[{"edits": e} for e in edits]),
                                check_runner=agent(*verdicts) if verdicts else agent({"decision": "PASS"}), language=language)
    return asyncio.run(stage.block({"block_id": "b", "text": text})), stage


@pytest.mark.parametrize("language", ["fa", "ar", "de", "en"])
def test_every_language_has_its_profile_agents_and_prompts(language):
    profile = ai_config.semantic_punctuation.profile(language)
    for kind in ("syntax", "punctuate", "check"):
        definition = ai_config.agents[f"{profile.agents}.{kind}"]
        assert prompt(definition.prompt), f"{language} {kind} has no prompt"
        assert Path("config/agents.json").exists()
    assert profile.ezafe == (language == "fa")


def test_a_language_without_a_profile_has_no_layer():
    assert not ai_config.semantic_punctuation.covers("es")
    with pytest.raises(ValueError, match="no punctuation profile"):
        ai_config.semantic_punctuation.profile("es")


def test_the_prompts_are_of_their_language():
    assert "Arabic" in prompt("documentary/punctuation/punctuate_system_ar") and "idafa" in prompt("documentary/punctuation/syntax_system_ar")
    assert "subordinate" in prompt("documentary/punctuation/punctuate_system_de") and "Nebensatz" not in prompt("documentary/punctuation/punctuate_system_en")
    assert "never carry over the comma positions of the German or the Persian version" in prompt("documentary/punctuation/punctuate_system_en")
    for lang in ("ar", "de", "en"):
        assert "ezafe" not in prompt(f"documentary/punctuation/punctuate_system_{lang}").lower().replace("no ezafe", "")


# --- Arabic -------------------------------------------------------------------------------------------------------------------------------

def test_arabic_latin_commas_become_arabic_commas_and_the_words_stay():
    result = asyncio.run(PS.PunctuationStage(None, None, use_agents=False, language="ar").block({"block_id": "b", "text": "جاء الرجل, ثم رحل? نعم"}))
    assert result.layer.text == "جاء الرجل، ثم رحل؟ نعم" and not check(result.layer)


def test_arabic_agent_comma_between_clauses_is_kept_and_inside_an_idafa_is_refused():
    syntax = {"boundaries": [{"n": 0, "relation": "idafa", "reason": "مدير الشرطة", "comma_ok": False},
                             {"n": 2, "relation": "clause_boundary", "reason": "new clause", "comma_ok": True}]}
    result, _ = staged("ar", "مدير الشرطة قال إنه رحل", syntax,
                       [[{"after": 0, "new": "،", "reason": "pause"}, {"after": 2, "new": "،", "reason": "the clause ends"}]], {"decision": "PASS"})
    assert result.layer.text == "مدير الشرطة قال، إنه رحل"
    refused = [r for r in result.refused if r["after_word"] == 0]
    assert refused and "one construction" in refused[0]["why"]
    assert all(b.status in (BD.COHESIVE, BD.NONE) for b in result.boundaries), "an Arabic block has no ezafe analysis"


def test_a_latin_comma_is_not_an_arabic_mark_the_agent_may_insert():
    result, _ = staged("ar", "جاء الرجل ثم رحل", {"boundaries": []}, [[{"after": 1, "new": ",", "reason": "wrong script"}]])
    assert result.layer.text == "جاء الرجل ثم رحل" and "not a mark the layer may insert" in result.refused[0]["why"]


def test_arabic_vowel_marks_are_part_of_the_word_a_change_of_them_is_refused():
    layer = Layer("b", "كَتَبَ الرجل", "كَتَبَ الرجل", language="ar")
    assert not check(layer)
    layer.base = "كتب الرجل"
    assert check(layer)


# --- German -------------------------------------------------------------------------------------------------------------------------------

def test_german_comma_before_the_subordinate_clause_and_a_dash_for_one_held_beat():
    text = "Er wusste dass sie lügte und schwieg"
    syntax = {"boundaries": [{"n": 1, "relation": "main_subordinate", "reason": "dass starts a subordinate clause", "comma_ok": True}]}
    result, _ = staged("de", text, syntax, [[{"after": 1, "new": ",", "reason": "Nebensatz"}, {"after": 4, "new": "—", "reason": "one held beat"}]])
    assert result.layer.text == "Er wusste, dass sie lügte— und schwieg"
    assert not check(result.layer)


def test_an_ellipsis_written_as_three_dots_can_be_replaced_by_a_comma_and_a_word_is_never_a_mark():
    layer = Layer("b", "Er kam... und ging", "Er kam... und ging", [Edit(1, "...", ",", "a held beat, not a trailing off")], language="de")
    assert layer.text == "Er kam, und ging" and check(layer) == []
    with pytest.raises(LayerError):
        _ = Layer("b", "Er kam und ging", "Er kam und ging", [Edit(0, "und", ",", "x")], language="de").text


def test_german_text_has_no_persian_marks_and_no_normalisation():
    assert ai_config.semantic_punctuation.profile("de").normalize == {}
    with pytest.raises(LayerError):
        _ = Layer("b", "Er kam und ging", "Er kam und ging", [Edit(0, "", "،", "wrong script")], language="de").text


def test_german_quotation_marks_are_punctuation_not_part_of_a_word():
    layer = Layer("b", "Er sagte „Komm“ und ging", "Er sagte „Komm“ und ging", language="de")
    assert [w.surface for w in layer.words] == ["Er", "sagte", "Komm", "und", "ging"] and not check(layer)


# --- English ------------------------------------------------------------------------------------------------------------------------------

def test_english_needs_no_reference_text_and_keeps_its_own_commas():
    syntax = agent({"boundaries": []})
    stage = PS.PunctuationStage(None, None, use_agents=True, syntax_runner=syntax, punctuate_runner=agent({"edits": [
        {"after": 3, "new": ",", "reason": "after the introductory phrase"}]}), language="en")
    result = asyncio.run(stage.block({"block_id": "b", "text": "In the early hours he left the house"}))
    assert result.layer.text == "In the early hours, he left the house" and result.review == "NOT_REVIEWED"
    assert json.loads(syntax.seen[0])["english"] == "", "an English narration is its own meaning reference"


def test_english_agent_comma_between_subject_and_verb_is_refused_when_the_syntax_says_one_construction():
    syntax = {"boundaries": [{"n": 1, "relation": "subject_verb", "reason": "the man left", "comma_ok": False}]}
    result, _ = staged("en", "The man left quickly", syntax, [[{"after": 1, "new": ",", "reason": "a pause"}]])
    assert result.layer.text == "The man left quickly" and "one construction" in result.refused[0]["why"]


@pytest.mark.parametrize("language,text", [("ar", "جاء الرجل ثم رحل"), ("de", "Er kam und ging"), ("en", "He came and left")])
def test_a_layer_never_changes_a_word_whatever_the_language(language, text):
    layer = Layer("b", text, text, [Edit(0, "", ",", "x")] if language != "ar" else [Edit(0, "", "،", "x")], language=language)
    assert not check(layer) and [w.surface for w in layer.words] == text.split()


@pytest.mark.parametrize("language,lowercase,capital", [("en", "He left and she stayed", "He left She stayed"), ("de", "Er ging und sie blieb", "Er ging Sie blieb")])
def test_a_sentence_mark_before_a_lowercase_word_is_refused_the_layer_never_changes_a_letter(language, lowercase, capital):
    edit = [[{"after": 1, "new": ".", "reason": "two sentences"}]]
    result, _ = staged(language, lowercase, {"boundaries": []}, edit)
    assert result.layer.text == lowercase and "starts in lowercase" in result.refused[0]["why"]
    result, _ = staged(language, capital, {"boundaries": []}, edit)
    assert result.layer.text == capital.replace(" She", ". She").replace(" Sie", ". Sie")


def test_arabic_has_no_capitals_so_a_period_is_never_refused_for_that():
    result, _ = staged("ar", "جاء الرجل ثم رحل", {"boundaries": []}, [[{"after": 1, "new": ".", "reason": "two sentences"}]])
    assert result.layer.text == "جاء الرجل. ثم رحل"


def test_only_persian_and_its_locale_variants_have_an_ezafe_every_other_language_has_none():
    cfg = ai_config.semantic_punctuation
    assert [cfg.has_ezafe(code) for code in ("fa", "fa-IR", "fa_AF", "FA")] == [True] * 4
    assert [cfg.has_ezafe(code) for code in ("ar", "ar-SA", "de", "de-AT", "en", "en-GB", "tr", "", None)] == [False] * 9
    assert cfg.profile("fa-IR") is cfg.profile("fa")


@pytest.mark.parametrize("language,text,edit", [("de", "Er kam und ging", {"after": 1, "new": ",", "reason": "clause"}),
                                                ("ar", "جاء الرجل ثم رحل", {"after": 1, "new": "،", "reason": "clause"}),
                                                ("en", "He came and left", {"after": 1, "new": ",", "reason": "clause"})])
def test_a_non_persian_proposal_goes_from_the_punctuate_agent_through_the_code_filter_without_the_checker(language, text, edit):
    checker = agent({"decision": "REJECT"})
    stage = PS.PunctuationStage(None, None, use_agents=True, syntax_runner=agent({"boundaries": []}), punctuate_runner=agent({"edits": [edit]}),
                                check_runner=checker, language=language)
    result = asyncio.run(stage.block({"block_id": "b", "text": text}))
    assert checker.seen == [] and result.checks == [] and result.review == "NOT_REVIEWED" and result.layer.edits, "the checker is Persian only"
    assert not check(result.layer)


@pytest.mark.parametrize("language", ["fa", "fa-IR", "fa_AF"])
def test_persian_and_its_locale_variants_still_pass_through_the_checker(language):
    checker = agent({"decision": "PASS"})
    stage = PS.PunctuationStage(None, None, use_agents=True, syntax_runner=agent({"boundaries": []}),
                                punctuate_runner=agent({"edits": [{"after": 1, "new": "،", "reason": "clause"}]}), check_runner=checker, language=language)
    result = asyncio.run(stage.block({"block_id": "b", "text": "مرد آمد و رفت"}))
    assert len(checker.seen) == 1 and result.review == "PASS" and result.checks
