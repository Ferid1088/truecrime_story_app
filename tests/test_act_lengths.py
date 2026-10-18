# ruff: noqa: F811  (`setup` is the flow tests' fixture, requested by setup)
"""R1-R3: the inspector's "selection" kind, the draft's length discipline (a scene over budget or
retelling is written again once) and the act word targets of the film stages."""

import asyncio
import json
import re

import pytest
from test_language_pipeline_flow import ACTS, FakeGen, _pipeline, setup  # noqa: F401  (the flow tests' fixture)

from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import StoryVersion
from app.language_pipeline.lengths import act_length_report, act_word_targets, final_range
from app.language_pipeline.store import FINAL
from app.services.writer_metrics import trigram_overlap

LANGUAGES = ("en", "fa", "de", "ar")


def span(language="en", minutes=60):
    return final_range(language, minutes)


# ------------------------------------------------------------------------------------------- R1
def test_the_two_inspector_lines_are_identical_in_all_four_files():
    lines = {language: re.search(r"Allowed kinds:.*\n.*", prompt(f"language_pipeline/{language}/inspector")).group()
             for language in LANGUAGES}
    assert len(set(lines.values())) == 1
    assert lines["en"] == (
        "Allowed kinds: native, spoken, flow, engagement, repetition, selection, ending, production.\n"
        "Use selection for a passage that does not earn its place, or for names and minor theories that "
        "pile up without purpose.")
    for language in LANGUAGES:
        text = prompt(f"language_pipeline/{language}/inspector")
        assert text.index('"kind":"flow"') < text.index("Allowed kinds:")


# ------------------------------------------------------------------------------------------- R2
def test_the_draft_prompt_budgets_the_part_not_the_whole_script_and_checks_the_count_before_delivery():
    text = prompt("language_pipeline/draft_writer")
    section = text[text.index("LENGTH\n"):text.index("ENDING\n")]
    assert "The whole documentary is about {target_words} words." in section
    assert "its budget is unit_target_words in the input; never apply the whole-script number to this part" in section
    assert "Never exceed one and a quarter times the scene target" in section
    assert "If no useful supported material remains, stop rather than repeat or invent" in section
    assert "check the word count against the budget; if you are over it, cut the weakest repetition" in text
    assert "correction (only when an earlier attempt at this part was rejected: why)" in text
    assert set(re.findall(r"\{(\w+)\}", text)) == {"marker_instruction", "tone", "target_words"}


class LongThenShort(FakeGen):
    """Scene calls: the first answer of each scene is too long, the answer to a correction is on budget."""

    async def generate_text(self, role, system, user, images=None, **k):
        task = json.loads(user)
        if role == "language_draft_writer":
            self.calls.append({"role": role, "system": system, "user": task, **k})
            words = task["unit_target_words"] if "correction" in task else task["unit_target_words"] * 2
            return self._result(self.draft_words(words), role)
        return await super().generate_text(role, system, user, images, **k)


def test_a_scene_over_one_and_three_tenths_of_its_budget_is_written_again_once_with_the_reason(setup):
    gen = LongThenShort()
    asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    scenes = [c for c in gen.calls if c["role"] == "language_draft_writer"]
    assert len(scenes) == 2 * len(ACTS)                         # each scene twice, never a third time
    first, second = scenes[0], scenes[1]
    assert "correction" not in first["user"] and "previous attempt had" in second["user"]["correction"]
    assert str(first["user"]["unit_target_words"] * 2) in second["user"]["correction"]
    assert second["user"]["story_so_far"] == first["user"]["story_so_far"]


class Retelling(FakeGen):
    """The first answer repeats what the story already said; the second answer is new."""

    async def generate_text(self, role, system, user, images=None, **k):
        task = json.loads(user)
        if role == "language_draft_writer":
            self.calls.append({"role": role, "system": system, "user": task, **k})
            if task["story_so_far"] and "correction" not in task:
                return self._result(task["story_so_far"], role)
            return self._result(self.draft_words(task["unit_target_words"]), role)
        return await super().generate_text(role, system, user, images, **k)


def test_a_scene_that_retells_the_story_is_written_again_once(setup):
    gen = Retelling()
    asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    scenes = [c["user"] for c in gen.calls if c["role"] == "language_draft_writer"]
    assert len(scenes) == 3                                     # act1 once; act2 retold, then new
    assert "already stands earlier in the story" in scenes[2]["correction"]


def test_the_second_answer_is_taken_as_it_comes_even_when_it_is_still_too_long(setup, db_session):
    class AlwaysLong(FakeGen):
        async def generate_text(self, role, system, user, images=None, **k):
            task = json.loads(user)
            if role == "language_draft_writer":
                self.calls.append({"role": role, "system": system, "user": task, **k})
                return self._result(self.draft_words(task["unit_target_words"] * 3), role)
            return await super().generate_text(role, system, user, images, **k)

    gen = AlwaysLong()
    asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    assert len([c for c in gen.calls if c["role"] == "language_draft_writer"]) == 2 * len(ACTS)


def test_a_scene_within_budget_and_new_is_written_once(setup):
    gen = FakeGen()
    asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    assert gen.count("language_draft_writer") == len(ACTS)


def test_the_overlap_is_the_share_of_trigrams_the_story_already_has():
    assert trigram_overlap("a b c d e", "a b c x y") == pytest.approx(1 / 3, abs=1e-3)
    assert trigram_overlap("p q r s", "a b c d") == 0.0
    assert trigram_overlap("only two", "only two") == 0.0
    assert trigram_overlap("a b c d", "") == 0.0


# ------------------------------------------------------------------------------------------- R3
def test_the_act_targets_add_up_to_the_middle_of_the_range_and_no_act_exceeds_the_cap():
    plan = {"a": 500, "b": 1500, "c": 300, "d": 300, "e": 400, "f": 400, "g": 600}
    for language in LANGUAGES:
        sp = span(language)
        cap_ratio = ai_config.language_pipeline.stage_config_for(language).act_target_cap_ratio
        targets = act_word_targets(plan, sp, cap_ratio)
        total = round((sp.words_min + sp.words_max) / 2)
        assert sum(targets.values()) == total and list(targets) == list(plan)
        assert max(targets.values()) <= cap_ratio * total / len(plan)
        assert targets["b"] == max(targets.values())


def test_the_cap_gives_the_excess_to_the_other_acts_in_proportion():
    sp = span()
    plan = {"a": 100, "b": 100, "c": 10000}
    targets = act_word_targets(plan, sp, 1.5)
    total = round((sp.words_min + sp.words_max) / 2)
    assert targets["c"] == round(1.5 * total / 3) and abs(targets["a"] - targets["b"]) <= 1
    assert sum(targets.values()) == total


def test_an_empty_or_zero_plan_still_gives_targets_that_add_up():
    sp = span()
    assert act_word_targets({}, sp, 2.0) == {}
    zero = act_word_targets({"a": 0, "b": 0}, sp, 2.0)
    assert sum(zero.values()) == round((sp.words_min + sp.words_max) / 2) and zero["a"] == zero["b"]


def test_every_language_is_given_its_own_cap_in_config():
    for language in LANGUAGES:
        assert ai_config.language_pipeline.stage_config_for(language).act_target_cap_ratio >= 1.0


def test_the_act_report_marks_each_act_against_its_target_and_blocks_nothing():
    report = act_length_report({"a": 100, "b": 100, "c": 100},
                               [{"id": "a", "text": "w " * 100}, {"id": "b", "text": "w " * 60}])
    assert report["a"] == {"target": 100, "words": 100, "ratio": 1.0, "within_tolerance": True}
    assert report["b"]["ratio"] == 0.6 and report["b"]["within_tolerance"] is False
    assert report["c"]["words"] == 0 and report["c"]["within_tolerance"] is False


def act_lengths_section(text):
    start = text.index("ACT LENGTHS\n")
    return text[start:text.index("\n\n", start)]


def test_the_eight_writer_files_carry_one_act_length_text_per_stage_in_all_four_languages():
    """The owner's wording (2026-10-18) replaced the first ACT LENGTHS text; what is pinned is that the text is the
    same in every language, that it names the inputs, and where it stands."""
    sections = {stage: {language: act_lengths_section(prompt(f"language_pipeline/{language}/{stage}"))
                        for language in LANGUAGES} for stage in ("rewrite_writer", "final_writer")}
    for stage, by_language in sections.items():
        assert len(set(by_language.values())) == 1, stage
    assert "Use act_word_targets when supplied" in sections["rewrite_writer"]["en"]
    assert "Use act_word_targets and last_version_word_counts when supplied" in sections["final_writer"]["en"]
    for language in LANGUAGES:
        rewrite = prompt(f"language_pipeline/{language}/rewrite_writer")
        final = prompt(f"language_pipeline/{language}/final_writer")
        assert final.index("ACT LENGTHS\n") < final.index("HARD LIMITS")
        assert rewrite.index("\nLENGTH\n") < rewrite.index("ACT LENGTHS\n") < rewrite.index("{style_guide}")
        assert "act_word_targets; last_version_word_counts;" in final
        assert "act_word_targets" in rewrite.split("{marker_instruction}")[0]
        assert "re-expand" not in final
        assert set(re.findall(r"\{(\w+)\}", rewrite)) == {
            "marker_instruction", "style_guide", "target_words_min", "target_words_max"}
        assert set(re.findall(r"\{(\w+)\}", final)) == {
            "marker_instruction", "style_guide", "target_words_min", "target_words_max"}


def test_the_blocking_gates_stay_markers_and_total_length_only():
    for language in LANGUAGES:
        assert ai_config.language_pipeline.blocking_gates_for(language) == ["markers", "length"]


def test_the_final_stores_the_act_report_with_the_stage_row(setup, db_session):
    asyncio.run(_pipeline(FakeGen()).run_case(setup.id, ["en"], 60))
    db_session.expire_all()
    final = db_session.query(StoryVersion).filter_by(case_id=setup.id, kind=FINAL, language="en").one()
    report = json.loads(final.critic_notes)["act_lengths"]
    assert set(report) == set(ACTS) and report["act1"]["target"] == 325
    assert sum(r["target"] for r in report.values()) == 650
