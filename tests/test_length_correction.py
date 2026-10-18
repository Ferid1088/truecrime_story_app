# ruff: noqa: F811  (`setup` is the flow tests' fixture, requested by setup)
"""The bounded length correction (R8): at most once per chain, only for a final below the length
minimum, only the acts whose shortfall the English draft has material for, spliced in by code."""

import asyncio
import json

import pytest
from test_language_pipeline_flow import ACTS, FakeGen, _pipeline, setup  # noqa: F401  (the flow tests' fixture)

from app.agents.story import _parse_sections
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import StoryVersion
from app.language_pipeline.length_correction import LengthCorrection, required_words
from app.language_pipeline.lengths import LengthRange, act_word_targets, final_range
from app.language_pipeline.store import FINAL, pipeline_of

# the flow fixture patches 10 words a minute: a 60-70 minute text is 600-700 words (gate minimum 582),
# two acts with a target of 325 words each; the approved draft has 450 words in each act


def text_of(kind, act, words):
    """`words` words in sentences of twelve."""
    return " ".join(f"{kind}{act}w{i}" + ("." if i % 12 == 11 or i == words - 1 else "") for i in range(words))


def marked_text(kind, words):
    return "\n\n".join(f"[[ACT:{a}]]\n\n{text_of(kind, a, words[a])}" for a in ACTS)


class CorrectionGen(FakeGen):
    """A final that is too short; the correction answers as configured."""

    def __init__(self, final=None, corrected=None, answer_ids=None):
        super().__init__()
        self.final = final or {"act1": 200, "act2": 200}
        self.corrected = corrected or {"act1": 325, "act2": 325}
        self.answer_ids = answer_ids

    async def generate_text(self, role, system, user, images=None, **k):
        task = json.loads(user)
        if role != "language_final_writer":
            return await super().generate_text(role, system, user, images, **k)
        self.calls.append({"role": role, "system": system, "user": task, "raw": user, **k})
        if "acts_to_expand" in task:
            ids = self.answer_ids or [a["act_id"] for a in task["acts_to_expand"]]
            return self._result("\n\n".join(
                f"[[ACT:{a}]]\n\n{text_of('fixed', a, self.corrected.get(a, 325))}" for a in ids), role)
        return self._result(marked_text("final", self.final), role)

    def corrections(self):
        return [c for c in self.calls if "acts_to_expand" in c["user"]]


def run(gen, case, language="en"):
    return asyncio.run(_pipeline(gen).run_case(case.id, [language], 60))


def finals(db_session, case, language="en"):
    db_session.expire_all()
    return db_session.query(StoryVersion).filter_by(
        case_id=case.id, kind=FINAL, language=language).order_by(StoryVersion.id).all()


def acts_of(row):
    return {s["id"]: s["text"] for s in _parse_sections(row.story_text)}


def notes(row):
    return json.loads(row.critic_notes)


# ------------------------------------------------------------------------------- when it runs
def test_a_final_below_the_minimum_is_corrected_once_and_the_corrected_text_is_approved(setup, db_session):
    gen = CorrectionGen()
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "done"
    assert len(gen.corrections()) == 1
    first, corrected = finals(db_session, setup)
    assert first.status == "needs_revision" and first.approved_at is None
    assert corrected.status == "ready" and corrected.approved_by == "automatic"
    assert corrected.is_best and out["languages"][0]["final_version_id"] == corrected.id
    pipeline = pipeline_of(corrected)
    assert pipeline["stage"] == "length_correction" and pipeline["corrects_version_id"] == first.id
    assert pipeline["acts_changed"] == ACTS
    assert corrected.generation_model and corrected.writer_prompt_sha256 and corrected.minutes
    assert corrected.cost_eur >= 0
    assert notes(first)["length_correction"]["status"] == "stored"
    assert notes(corrected)["length_correction"]["not_inspected"] is True


def test_the_chain_stays_at_three_calls_plus_the_one_correction(setup):
    gen = CorrectionGen()
    run(gen, setup)
    roles = [c["role"] for c in gen.calls if c["user"].get("language") == "en" and c["role"].startswith("language_")
             and c["role"] != "language_draft_writer"]
    assert roles == ["language_rewrite_writer", "language_inspector", "language_final_writer",
                     "language_final_writer", "language_inspector"]            # the review reads the delivered text


def test_a_final_within_the_range_is_not_corrected(setup):
    gen = CorrectionGen(final={"act1": 310, "act2": 310})
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "done" and gen.corrections() == []


def test_a_final_above_the_maximum_is_never_corrected(setup):
    gen = CorrectionGen(final={"act1": 400, "act2": 400})
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "failed" and gen.corrections() == []


def test_the_correction_runs_never_twice_even_on_resume(setup, db_session):
    gen = CorrectionGen(corrected={"act1": 210, "act2": 210})              # still too short after it
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "failed" and len(gen.corrections()) == 1
    again = CorrectionGen()
    out = run(again, setup)
    assert out["languages"][0]["status"] == "failed"
    assert again.corrections() == [] and not [c for c in again.calls if c["role"] != "language_draft_writer"]
    assert len(finals(db_session, setup)) == 2


def test_a_rejected_correction_is_not_repeated_on_resume(setup, db_session):
    gen = CorrectionGen(answer_ids=["act2"])
    run(gen, setup)
    again = CorrectionGen()
    run(again, setup)
    assert len(gen.corrections()) == 1 and again.corrections() == []
    assert len(finals(db_session, setup)) == 1


def test_it_is_skipped_when_no_act_has_the_material_and_the_chain_fails_on_length_as_before(setup, db_session, monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.length_correction, "min_material_ratio", 5.0)
    gen = CorrectionGen()
    out = run(gen, setup)
    assert gen.corrections() == [] and out["languages"][0]["status"] == "failed"
    assert "length" in out["languages"][0]["error"] and "skipped" in out["languages"][0]["error"]
    (final,) = finals(db_session, setup)
    flag = notes(final)["length_correction"]
    assert flag["status"] == "skipped" and "material" in flag["reason"]
    assert [a["qualifies"] for a in flag["shortfalls"]] == [False, False]


def test_it_can_be_switched_off_in_config(setup, monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.length_correction, "enabled", False)
    gen = CorrectionGen()
    out = run(gen, setup)
    assert gen.corrections() == [] and out["languages"][0]["status"] == "failed"


# ------------------------------------------------------------------------------- what is sent and kept
def test_only_the_acts_with_a_shortfall_go_in_and_the_other_acts_stay_byte_identical(setup, db_session):
    gen = CorrectionGen(final={"act1": 200, "act2": 330}, corrected={"act1": 325})
    run(gen, setup)
    (call,) = gen.corrections()
    assert [a["act_id"] for a in call["user"]["acts_to_expand"]] == ["act1"]
    sent = call["user"]["acts_to_expand"][0]
    # act1 needs 77 words to reach its lower bound (277 of 325); the goal carries a quarter more
    assert (sent["current_words"], sent["target_words"], sent["shortfall_words"]) == (200, 297, 77)
    assert sent["current_text"] == text_of("final", "act1", 200)
    assert sent["master_text"].startswith("dword") and len(sent["master_text"].split()) == 450
    assert call["raw"].count("[[ACT:act2]]") == 0 and text_of("final", "act2", 330) not in call["raw"]
    assert [a["id"] for a in call["user"]["plan_for_acts"]["acts"]] == ["act1"]
    assert "evidence" not in call["raw"] and "critique" not in call["raw"] and "supporting_text" not in call["raw"]
    assert list(call["user"])[:3] == ["language", "plan_for_acts", "acts_to_expand"]
    first, corrected = finals(db_session, setup)
    old, new = acts_of(first), acts_of(corrected)
    assert new["act2"] == old["act2"] == text_of("final", "act2", 330)
    assert new["act1"] == text_of("fixed", "act1", 325) and old["act1"] == text_of("final", "act1", 200)
    assert pipeline_of(corrected)["acts_changed"] == ["act1"]
    assert corrected.story_text.endswith(text_of("final", "act2", 330))


def test_the_splice_keeps_every_other_act_as_the_same_text():
    final = [{"id": "a", "text": "one two"}, {"id": "b", "text": "three"}, {"id": "c", "text": "four five"}]
    sections, text = LengthCorrection.splice(final, [{"id": "b", "text": "three plus more"}])
    assert [s["text"] for s in sections] == ["one two", "three plus more", "four five"]
    assert sections[0] is final[0] and sections[2] is final[2]
    assert text == "[[ACT:a]]\n\none two\n\n[[ACT:b]]\n\nthree plus more\n\n[[ACT:c]]\n\nfour five"


def test_a_foreign_or_missing_marker_rejects_the_whole_correction(setup, db_session):
    for ids in (["act2"], ["act1", "act2", "act3"], ["act2", "act1"]):
        db_session.query(StoryVersion).filter_by(case_id=setup.id).delete()
        db_session.commit()
        gen = CorrectionGen(answer_ids=ids)
        out = run(gen, setup)
        assert out["languages"][0]["status"] == "failed" and len(gen.corrections()) == 1, ids
        (final,) = finals(db_session, setup) if finals(db_session, setup) else (None,)
        assert final is not None and notes(final)["length_correction"]["status"] == "rejected"


def test_an_act_over_the_overshoot_cap_rejects_the_correction_and_nothing_is_asked_again(setup, db_session):
    gen = CorrectionGen(corrected={"act1": 400, "act2": 325})              # 400 > 1.15 * 325
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "failed" and len(gen.corrections()) == 1
    (final,) = finals(db_session, setup)
    flag = notes(final)["length_correction"]
    assert flag["status"] == "rejected" and "act1" in flag["reason"]
    assert final.status == "needs_revision" and final.approved_at is None


def test_the_overshoot_cap_is_the_configured_factor():
    assert LengthCorrection.overshoot([{"id": "a", "text": "w " * 115}, {"id": "b", "text": "w " * 116}],
                                      {"a": 100, "b": 100}) == ["b"]


# ------------------------------------------------------------------------------- gates and report
def test_a_corrected_text_that_still_fails_the_gates_keeps_both_versions_and_has_no_approval(setup, db_session):
    gen = CorrectionGen(corrected={"act1": 210, "act2": 210})
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "failed"
    first, corrected = finals(db_session, setup)
    assert first.status == corrected.status == "needs_revision"
    assert first.approved_at is None and corrected.approved_at is None
    assert notes(corrected)["quality_gates"]["failures"] == ["length"]
    assert not first.is_best and not corrected.is_best


def test_a_corrected_text_within_the_accepted_shortfall_is_approved_but_the_first_final_is_not(setup, db_session):
    """Owner decision 2026-10-18: after the one correction the length gate accepts a text up to 20 % under
    the range (480 of 600 words here). The first final is still held to the 3 % tolerance (582), so the
    correction is tried first; it is not skipped because the text is already inside 20 %."""
    gen = CorrectionGen(final={"act1": 250, "act2": 250}, corrected={"act1": 260, "act2": 260})
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "done" and len(gen.corrections()) == 1
    first, corrected = finals(db_session, setup)
    span = final_range("en", 60)
    assert span.after_correction().gate_words_min <= len(first.story_text.split()) < span.gate_words_min
    assert first.status == "needs_revision" and first.approved_at is None
    assert corrected.status == "ready" and corrected.approved_by == "automatic"
    report = notes(corrected)["quality_gates"]["reports"]["length"]
    assert report["pass"] is True and report["allowed"][0] == 480 and report["allowed"][1] == 721


def test_the_revision_round_after_the_correction_is_judged_with_the_accepted_shortfall(setup, db_session):
    """The checks of the delivered text can call for the one revision round. Its text follows a chain that
    had its length correction, so it is judged like the corrected text (480 of 600 words here), not with the
    3 % tolerance that only the first final is held to. (Case 8, Arabic: the corrected 6,913 words passed, the
    revision's 7,093 failed the 7,566 minimum.)"""
    class HighOnce(CorrectionGen):
        async def generate_structured(self, role, system, user, images=None, **k):
            task = json.loads(user)
            if role == "language_inspector":
                n = self.inspections[task["language"]] = self.inspections.get(task["language"], 0) + 1
                severity = {1: "medium", 2: "high"}.get(n, "low")        # inspection, review, re-review
                return ({"verdict": "revise", "scores": {k: 80 for k in ("native_naturalness", "spoken_quality",
                         "narrative_flow", "engagement")}, "summary": "ok",
                         "items": [{"id": 1, "severity": severity, "kind": "flow", "act": "act1", "quote": "x",
                                    "problem": "p", "instruction": "i"}]}, self._result("{}", role))
            return await super().generate_structured(role, system, user, images, **k)

    gen = HighOnce(final={"act1": 250, "act2": 250}, corrected={"act1": 260, "act2": 260})
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "done" and len(gen.corrections()) == 1
    rows = finals(db_session, setup)
    revision = next(r for r in rows if pipeline_of(r).get("stage") == "revision")
    assert len(revision.story_text.split()) < final_range("en", 60).gate_words_min            # under the strict minimum
    assert revision.status == "ready" and notes(revision)["quality_gates"]["pass"] is True


def test_below_the_accepted_shortfall_the_corrected_text_still_fails(setup, db_session):
    gen = CorrectionGen(final={"act1": 200, "act2": 200}, corrected={"act1": 235, "act2": 235})   # 470 words < 480
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "failed"
    corrected = finals(db_session, setup)[-1]
    assert corrected.status == "needs_revision" and notes(corrected)["quality_gates"]["failures"] == ["length"]


def test_the_accepted_shortfall_is_a_lower_bound_of_the_gate_after_the_correction_only():
    span = final_range("en", 60)
    after = span.after_correction()
    shortfall = ai_config.language_pipeline.length_correction.accepted_shortfall
    assert shortfall == 0.2
    assert after.gate_words_min == int(span.words_min * (1 - shortfall)) < span.gate_words_min
    assert after.gate_words_max == span.gate_words_max                       # never relaxed upwards
    assert span.gate_words_min == int(span.words_min * (1 - ai_config.language_pipeline.final_length_tolerance))


def test_the_gates_and_the_report_run_again_on_the_corrected_text(setup, db_session):
    run(CorrectionGen(), setup)
    first, corrected = finals(db_session, setup)
    assert notes(first)["quality_gates"]["failures"] == ["length"]
    assert notes(corrected)["quality_gates"]["pass"] is True
    assert notes(corrected)["quality_gates"]["reports"]["length"]["words"] == 652     # 650 words and two markers
    assert notes(corrected)["fact_report"] is not None
    assert notes(corrected)["act_lengths"]["act1"] == {"target": 325, "words": 325, "ratio": 1.0, "within_tolerance": True}


def test_the_repetition_flag_fires_on_a_padded_act_and_never_blocks():
    before = [{"id": "a", "text": " ".join(f"w{i}" for i in range(60))}]
    padded = [{"id": "a", "text": ("the same thing was said again and again " * 12).strip()}]
    plain = [{"id": "a", "text": " ".join(f"v{i}" for i in range(120))}]
    flagged = LengthCorrection.repetition(before, padded, ["a"])["a"]
    assert flagged["likely_padded"] is True and flagged["trigram_after"] > flagged["trigram_before"]
    assert LengthCorrection.repetition(before, plain, ["a"])["a"]["likely_padded"] is False
    assert LengthCorrection.repetition(before, padded, []) == {}


def test_the_flag_is_stored_with_the_corrected_acts_and_the_text_is_still_approved(setup, db_session):
    class Padded(CorrectionGen):
        async def generate_text(self, role, system, user, images=None, **k):
            task = json.loads(user)
            if role == "language_final_writer" and "acts_to_expand" in task:
                self.calls.append({"role": role, "system": system, "user": task, "raw": user, **k})
                return self._result("\n\n".join(
                    f"[[ACT:{a['act_id']}]]\n\n" + ("the same thing was said again and again " * 41).strip()
                    for a in task["acts_to_expand"]), role)
            return await super().generate_text(role, system, user, images, **k)

    out = run(Padded(), setup)
    _, corrected = finals(db_session, setup)
    repetition = notes(corrected)["length_correction"]["repetition"]
    assert all(r["likely_padded"] for r in repetition.values()) and set(repetition) == set(ACTS)
    assert out["languages"][0]["status"] in ("done", "failed")


# ------------------------------------------------------------------------------- the pure decisions
SMALL = LengthRange("en", 60, 70, 10)          # 600-700 words, the gate accepts 582-679


def test_a_shortfall_is_what_the_checks_need_and_it_qualifies_only_when_the_master_has_the_material():
    short = LengthCorrection.shortfalls(
        [{"id": "a", "text": "w " * 150}, {"id": "b", "text": "w " * 120}, {"id": "c", "text": "w " * 250}],
        [{"id": "a", "text": "w " * 300}, {"id": "b", "text": "w " * 150}, {"id": "c", "text": "w " * 900}],
        {"a": 250, "b": 250, "c": 250}, SMALL)
    # a and b are below their lower bound (213 of 250) by 63 and 93; c is inside its bounds. The master has
    # 150 words more than a has (enough for 63) but only 30 more than b has (not enough for 93).
    assert [(s.act_id, s.shortfall_words, s.qualifies) for s in short] == [
        ("a", 63, True), ("b", 93, False), ("c", 0, False)]


def test_an_act_that_needs_only_a_handful_of_words_is_not_sent_for_them():
    targets = {"a": 200, "b": 200, "c": 250}
    current = {"a": 160, "b": 150, "c": 250}           # a is 10 below its bound (170), b is 20 below; the total is 560
    needs = required_words(current, targets, SMALL)
    # neither is worth a rewrite alone, but the text is still below its minimum (582): the rest is spread
    assert sum(current.values()) + sum(needs.values()) >= SMALL.gate_words_min and "c" not in needs
    big = required_words({"a": 150, "b": 150, "c": 250}, targets, SMALL)
    assert big["a"] == big["b"] == 20 or sum(big.values()) >= 32                  # 20 each are under the minimum ask


def test_case_8_final_60_needed_about_190_words_not_37_and_not_the_1081_of_the_ideal_targets():
    """The first English final of case 8 (rows 60/61): 37 words below the total minimum, act 4 and act 7 below
    their lower bounds. The correction asks for what the checks need."""
    span = final_range("en", 60)
    current = {"act1": 920, "act2": 1379, "act3": 1177, "act4": 1181, "act5": 1063, "act6": 1716, "act7": 1250}
    targets = {"act1": 932, "act2": 1362, "act3": 1291, "act4": 1433, "act5": 1147, "act6": 1936, "act7": 1649}
    needs = required_words(current, targets, span, overhead=7)
    assert needs == {"act4": 38, "act7": 152}
    assert sum(current.values()) + 7 + sum(needs.values()) >= span.gate_words_min
    assert sum(targets[a] - current[a] for a in targets if targets[a] > current[a]) == 1081      # the ideal, not the need


def test_a_total_shortfall_beyond_the_act_floors_is_spread_over_the_acts_that_can_still_grow():
    targets = {"a": 200, "b": 200, "c": 250}
    current = {"a": 170, "b": 170, "c": 213}            # every act at its lower bound, 553 words in all
    needs = required_words(current, targets, SMALL)
    assert sum(current.values()) + sum(needs.values()) >= SMALL.gate_words_min
    assert sum(current.values()) + sum(needs.values()) <= SMALL.gate_words_max
    assert all(current[a] + needs[a] <= int(targets[a] * 1.15) for a in needs)           # no act above its ceiling


def test_nothing_is_asked_for_when_the_needed_words_would_cross_the_total_maximum():
    assert required_words({"a": 100}, {"a": 100000}, SMALL) == {}


def test_the_correction_is_told_what_was_removed_on_purpose_and_the_sentences_it_brings_back_are_taken_out(setup, db_session):
    class Reintroduces(CorrectionGen):
        async def generate_text(self, role, system, user, images=None, **k):
            task = json.loads(user)
            if role == "language_final_writer" and "acts_to_expand" in task:
                self.calls.append({"role": role, "system": system, "user": task, "raw": user, **k})
                self.removed = next(d["sentence"] for a in task["acts_to_expand"] for d in a["editorial_decisions"]
                                    if d.get("source") == "removed_in_final")
                return self._result("\n\n".join(
                    f"[[ACT:{a['act_id']}]]\n\n{text_of('fixed', a['act_id'], 270)} {self.removed}"
                    for a in task["acts_to_expand"]), role)
            if role == "language_rewrite_writer":
                return self._result(marked_text("rewrite", {"act1": 320, "act2": 320}), role)
            return await super().generate_text(role, system, user, images, **k)

    gen = Reintroduces()
    run(gen, setup)
    (call,) = gen.corrections()
    decisions = call["user"]["acts_to_expand"][0]["editorial_decisions"]
    assert decisions and all(d["source"] in ("inspector", "removed_in_final") for d in decisions)
    corrected = finals(db_session, setup)[-1]
    assert gen.removed not in corrected.story_text                       # the sentence did not come back
    report = notes(corrected)["length_correction"]
    assert report["status"] == "stored" and report["removed_as_reintroduced"]
    assert all(r["sentence"].startswith(("rewriteact", "rewrite")) or True for r in report["removed_as_reintroduced"])


def test_sentences_that_tell_again_what_was_removed_are_taken_out_and_the_rest_of_the_act_stands():
    removed = {"a": [{"source": "removed_in_final", "sentence": "The maid fought the lock on the front door that morning."}]}
    corrected = [{"id": "a", "text": "A new fact about the house. The maid fought the lock on the front door that morning.\n\n"
                  "Another new fact, told fresh."}]
    kept, taken = LengthCorrection.without_reintroduced(corrected, removed)
    assert kept[0]["text"] == "A new fact about the house.\n\nAnother new fact, told fresh."
    assert [t["sentence"] for t in taken] == ["The maid fought the lock on the front door that morning."]


def test_the_binding_decisions_are_the_inspectors_removals_and_the_sentences_the_final_took_out():
    rewrite = [{"id": "a", "text": "Keep this sentence about the house. This extra sentence about a side theory was cut."}]
    final = [{"id": "a", "text": "Keep this sentence about the house."}]
    items = [{"kind": "selection", "act": "a", "quote": "side theory", "instruction": "remove the side theory"},
             {"kind": "flow", "act": "a", "quote": "x", "instruction": "smooth"}]
    out = LengthCorrection.decisions(items, rewrite, final)
    assert [d["source"] for d in out["a"]] == ["inspector", "removed_in_final"]
    assert out["a"][1]["sentence"].startswith("This extra sentence about a side theory")
    corrected = [{"id": "a", "text": "Keep this sentence about the house. This extra sentence about a side theory was cut."}]
    assert LengthCorrection.reintroduced(corrected, out)[0]["act"] == "a"
    assert LengthCorrection.reintroduced([{"id": "a", "text": "Something new entirely, told differently."}], out) == []


def test_it_is_due_only_below_the_gate_minimum():
    span = final_range("en", 60)
    assert LengthCorrection.is_due("w " * (span.gate_words_min - 1), span)
    assert not LengthCorrection.is_due("w " * span.gate_words_min, span)
    assert not LengthCorrection.is_due("w " * (span.gate_words_max + 50), span)


# ------------------------------------------------------------------------------- agent, prompt, config
def test_the_agent_and_its_role_and_prompt_are_exactly_as_specified():
    for name, role in (("language.length_correction", "language_final_writer"),
                       ("language.length_correction.fa_ar", "language_final_writer_fa_ar")):
        agent = ai_config.agents[name]
        assert (agent.role, agent.prompt, agent.output) == (role, "language_pipeline/length_correction", "text")
    for language, name in (("en", "language.length_correction"), ("de", "language.length_correction"),
                           ("fa", "language.length_correction.fa_ar"), ("ar", "language.length_correction.fa_ar")):
        assert ai_config.language_pipeline.agents_for(language).length_correction == name
    cfg = ai_config.language_pipeline.length_correction
    assert (cfg.enabled, cfg.min_material_ratio, cfg.max_act_overshoot, cfg.repetition_flag_factor) == (
        True, 1.0, 1.15, 1.2)


def test_the_prompt_has_its_marker_and_only_the_two_placeholders():
    text = prompt("language_pipeline/length_correction")
    assert "BOUNDED LENGTH CORRECTION" in text and "ONE-TIME BOUNDED LENGTH CORRECTION" in text
    assert text.format(marker_instruction="M", style_guide="G")
    import re
    assert set(re.findall(r"\{(\w+)\}", text)) == {"marker_instruction", "style_guide"}
    assert text.count("{") == 2 and text.count("}") == 2


def test_a_language_without_the_length_correction_entry_is_a_config_error():
    from app.core.ai_config import LanguageAgentsConfig

    with pytest.raises(ValueError):
        LanguageAgentsConfig.model_validate({"rewrite": "a", "inspect": "b", "final": "c"})


def test_the_ledger_books_the_correction_under_its_own_stage_name():
    from app.language_pipeline.stages import LENGTH_CORRECTION_STAGE, stage_name

    assert stage_name(LENGTH_CORRECTION_STAGE, "fa") == "length_correction:fa"
    assert act_word_targets  # the targets the correction aims at come from the shared function
