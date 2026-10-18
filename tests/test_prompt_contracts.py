"""The English prompts as the models receive them: the labels they may output are the labels the
application's schemas accept, the placeholders are the ones the callers fill, and the plan prompt does
not force what the writers are told to avoid."""

import json
import re
import string
import typing

import pytest

from app.agents.story import _MARKER_INSTRUCTION
from app.core.prompts import prompt
from app.language_pipeline.evidence_audit import AuditItem
from app.language_pipeline.film_schema import FilmItem

LANGUAGES = ("en", "fa", "de", "ar")


def literal_values(model, field):
    return set(typing.get_args(model.model_fields[field].annotation))


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_kind_an_inspector_may_name_is_a_kind_the_schema_accepts_and_the_reverse(language):
    text = prompt(f"language_pipeline/{language}/inspector")
    named = {k.strip() for k in re.search(r"Allowed kinds: (.*)\.", text).group(1).split(",")}
    assert named == literal_values(FilmItem, "kind")
    template = next(line for line in text.splitlines() if line.startswith('{{"id":1'))
    item = json.loads(template.replace("{{", "{").replace("}}", "}"))
    assert item["kind"] in named and set(item) == set(FilmItem.model_fields)


def test_the_english_inspector_output_template_validates_against_the_schema():
    from app.language_pipeline.film_schema import FilmInspection

    text = prompt("language_pipeline/en/inspector")
    verdict = next(line for line in text.splitlines() if line.startswith('{{"verdict"'))
    FilmInspection.model_validate(json.loads(verdict.replace("{{", "{").replace("}}", "}")))


def test_every_kind_the_audit_prompt_names_is_a_kind_the_schema_accepts():
    text = prompt("language_pipeline/audit/evidence_audit")
    section = text[text.index("WHAT TO REPORT"):text.index("WHAT NOT TO REPORT")]
    named = set(re.findall(r"^- (\w+):", section, re.M))
    assert named == literal_values(AuditItem, "kind")
    template = text[text.index("Item shape:"):].splitlines()[1]
    item = json.loads(template.replace("{{", "{").replace("}}", "}"))
    assert set(item) == set(AuditItem.model_fields) and item["kind"] in named
    assert set(re.findall(r"^(High|Medium|Low):", text, re.M)) == {"High", "Medium", "Low"}


EXPECTED_FIELDS = {
    "language_pipeline/draft_writer": {"marker_instruction", "tone", "target_words"},
    "language_pipeline/audit/evidence_audit": {"max_items"},
    "language_pipeline/en/rewrite_writer": {"marker_instruction", "style_guide", "target_words_min", "target_words_max"},
    "language_pipeline/en/final_writer": {"marker_instruction", "style_guide", "target_words_min", "target_words_max"},
    "language_pipeline/en/inspector": {"style_guide"},
}


@pytest.mark.parametrize("name", EXPECTED_FIELDS)
def test_each_english_prompt_asks_the_caller_for_exactly_the_placeholders_it_fills(name):
    text = prompt(name)
    assert {f for _, f, _, _ in string.Formatter().parse(text) if f} == EXPECTED_FIELDS[name]
    assert text.format(**{f: "X" for f in EXPECTED_FIELDS[name]})                # no stray brace, no missing field


def test_the_marker_instruction_the_writers_receive_is_the_one_the_application_defines():
    assert "[[ACT" in _MARKER_INSTRUCTION
    for name in ("language_pipeline/draft_writer", "language_pipeline/en/rewrite_writer", "language_pipeline/en/final_writer"):
        assert "{marker_instruction}" in prompt(name)


def test_the_inputs_the_prompts_name_are_the_inputs_the_callers_send():
    rewrite_fields = ("language", "plan", "target_minutes_min", "target_minutes_max", "target_words_min",
                      "target_words_max", "act_word_targets", "english_master")
    text = prompt("language_pipeline/en/rewrite_writer")
    assert all(f in text for f in rewrite_fields)
    final = prompt("language_pipeline/en/final_writer")
    for field in ("last_version", "critique", "act_word_targets", "last_version_word_counts", "english_master"):
        assert field in final
    draft = prompt("language_pipeline/draft_writer")
    for field in ("unit_target_words", "told_so_far", "story_so_far", "correction", "reveal_map", "do_not_reveal"):
        assert field in draft


def test_the_inspector_is_not_asked_for_what_it_is_not_given():
    text = prompt("language_pipeline/en/inspector")
    assert "You have no evidence" in text and "word_count" in text and "allowed word range" in text
    assert "You are not given per-act targets" in text
    assert "kind spoken" in text


def test_the_audit_prompt_names_what_the_audit_is_given_and_the_draft_prompt_forbids_narrating_the_structure():
    audit = prompt("language_pipeline/audit/evidence_audit")
    assert "the timeline, the contradictions and the approved context the writer also received" in audit
    draft = prompt("language_pipeline/draft_writer")
    assert "Never refer to this call, an assigned act, beat or scene, or the documentary's editorial structure" in draft


def test_the_plan_prompt_forces_neither_a_question_at_every_act_nor_equal_acts_nor_repeated_recaps():
    text = prompt("agents/story/design")
    assert "never on a summary" not in text and "ends on a fact or a question" not in text
    assert "Not every act needs a question, and none ends on a recap" in text
    assert "acts are not equal" in text and "choosing, not cataloguing" in text
    assert "so later acts do not re-explain what an earlier act told" in text
    assert "ONE MAIN PLACE, AND A PLAN THAT AGREES WITH ITSELF" in text
    assert "Assign each explanatory task once" in text and "never asks for a recap" in text
    assert "A hearing or trial act shows what happens there and nothing that was told before" in text
    assert "must agree with each other" in text and "never narrate the film itself" in text
    assert "max_evidence_ids_per_act" in text and "user_instruction" in text
    for key in ('"title"', '"central_question"', '"opening_strategy"', '"acts"', '"key_beats"', '"target_words"',
                '"evidence_ids"', '"open_loops"', '"resolved_loops"', '"do_not_reveal"', '"reveal_map"',
                '"ending_strategy"', '"human_threads"'):
        assert key in text


def test_the_english_guide_does_not_speak_to_later_versions_the_film_text_does_not_have():
    guide = prompt("language_pipeline/guides/en")
    assert "later versions" not in guide and guide.startswith("LANGUAGE GUIDE: ENGLISH (en)")
    for name in ("language_pipeline/en/rewrite_writer", "language_pipeline/en/final_writer", "language_pipeline/en/inspector"):
        assert "{style_guide}" in prompt(name)
