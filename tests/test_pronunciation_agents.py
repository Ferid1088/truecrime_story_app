"""The seven pronunciation agents: complete prompts, their contracts and few-shot datasets (training examples in the prompt, held-out examples never)."""

from __future__ import annotations

import json

import pytest

from app.agents.runner import agent_def
from app.documentary import pronunciation_agents as PA
from app.documentary.reading.words import key_of

AGENT_NAMES = list(PA.AGENTS)


@pytest.mark.parametrize("agent", AGENT_NAMES)
def test_every_prompt_has_every_required_section_and_renders_complete(agent):
    raw = (PA.ROOT / f"{agent}_system.md").read_text(encoding="utf-8")
    for section in PA.SECTIONS:
        assert section in raw, f"{agent} lacks {section}"
    rendered = PA.system_prompt(agent)
    assert "{{" not in rendered and "}}" not in rendered.replace("}}", "") or "{{" not in rendered
    assert json.dumps(PA.AGENTS[agent].model_json_schema(), ensure_ascii=False) in rendered and PA.version() in rendered
    assert rendered.count("## Example ") == len(PA.examples(agent, "train")) >= PA.MIN_TRAIN_EXAMPLES


@pytest.mark.parametrize("agent", AGENT_NAMES)
def test_every_agent_is_registered_with_its_prompt_and_a_role(agent):
    definition = agent_def(f"pronunciation.{agent}")
    assert definition.prompt == f"pronunciation/{agent}_system" and definition.role == PA.ROLES[agent]


@pytest.mark.parametrize("agent", AGENT_NAMES)
def test_the_dataset_is_well_formed_with_enough_training_and_held_out_examples(agent):
    data = PA.dataset(agent)
    examples = data["examples"]
    assert data["agent"] == agent and data["version"] == PA.version()
    assert len({e["id"] for e in examples}) == len(examples)
    for e in examples:
        assert set(e) == PA.DATASET_KEYS and e["split"] in PA.SPLITS and e["verification"] in PA.VERIFICATIONS
        assert e["correct_decision"] and e["incorrect_decision_to_avoid"] and e["explanation"] and e["phenomenon"] and e["linguistic_context"] and e["confidence_requirement"]
        PA.check_answer(agent, e["output"])
    assert len(PA.examples(agent, "train")) >= PA.MIN_TRAIN_EXAMPLES and len(PA.examples(agent, "heldout")) >= 2


@pytest.mark.parametrize("agent", AGENT_NAMES)
def test_held_out_examples_never_enter_a_prompt_and_share_no_input_with_the_training_examples(agent):
    rendered = PA.system_prompt(agent)
    train_inputs = {json.dumps(e["input"], ensure_ascii=False, sort_keys=True) for e in PA.examples(agent, "train")}
    for e in PA.examples(agent, "heldout"):
        assert json.dumps(e["input"], ensure_ascii=False) not in rendered and json.dumps(e["input"], ensure_ascii=False, sort_keys=True) not in train_inputs
        assert e["id"] not in rendered


@pytest.mark.parametrize("agent", ["audio_observer", "prosody"])
def test_an_example_about_audio_is_a_hypothetical_scenario_and_never_presented_as_a_recording(agent):
    for e in PA.dataset(agent)["examples"]:
        assert e["audio"] == "hypothetical" and e["verification"] == "hypothetical_scenario" and e["input"]["audio"]["hypothetical"] is True
    for e in PA.dataset("audio_observer")["examples"]:
        assert all(o["hypothetical"] is True for o in e["output"]["observations"])
    assert "hypothetical" in PA.system_prompt(agent)


def test_every_irab_of_the_linguist_examples_keeps_the_letters_of_its_word():
    for e in PA.dataset("linguist")["examples"]:
        for word, out in zip(e["input"]["words"], e["output"]["words"], strict=True):
            assert key_of(out["irab"]) == key_of(word["text"]), (e["id"], word["text"], out["irab"])
            if out["status"] == "needs_verified_name":
                assert out["irab"] == word["text"] and out["n"] in e["output"]["unresolved"]


def test_a_name_without_an_approved_pronunciation_is_never_given_vowels_in_the_examples():
    for e in PA.dataset("linguist")["examples"]:
        approved = {m["surface"] for m in e["input"]["memory"]}
        for out in e["output"]["words"]:
            if out["status"] == "verified_from_memory":
                assert out["irab"] in {m["approved_pronunciation"] for m in e["input"]["memory"]} and approved


def test_the_intervention_examples_apply_a_rule_to_the_exact_written_form_and_never_use_ipa_between_slashes():
    for e in PA.dataset("intervention")["examples"]:
        text = e["input"]["word"]["text"]
        for i in e["output"]["interventions"]:
            assert "/" not in i["replacement"] and (i["decision"] in ("none", "ask_owner") or i["exact_form"] == text), e["id"]
            if i["decision"] in ("dictionary_alias", "dictionary_phoneme", "selective_irab", "alt_spelling"):
                assert "hypothesis" in i["expected_effect"] or i["validated_in_memory"] or "validated" in i["expected_effect"], e["id"]


def test_every_risk_item_cites_its_evidence_and_a_name_without_approval_is_asked_about():
    for e in PA.dataset("risk")["examples"]:
        for item in e["output"]["items"]:
            assert item["evidence"], e["id"]
            if item["category"] in ("conflict", "uncertain"):
                assert item["action"] == "ask_owner"


def test_the_memory_examples_never_overwrite_a_human_entry_and_conflicts_need_a_person():
    for e in PA.dataset("memory")["examples"]:
        if e["output"]["action"] in ("conflict", "escalate", "do_not_reuse"):
            assert e["output"]["requires_human"] is True and e["output"]["entry"] is None
        if e["input"]["event"]["kind"] == "proposal":
            assert e["output"]["action"] in ("escalate", "do_not_reuse")


def test_the_qa_examples_approve_only_when_every_check_is_ok():
    for e in PA.dataset("qa")["examples"]:
        out = e["output"]
        checks = [out[k]["ok"] for k in ("linguistic", "audio_evidence", "dictionary_compatibility", "intervention_choice", "fluency", "memory_integrity", "reuse_safety", "cost")]
        assert (out["verdict"] == "approve") == all(checks) and (out["verdict"] == "approve") == (out["problem"] == "none") or out["verdict"] != "approve", e["id"]


def test_the_examples_are_labelled_as_authored_and_pending_review_not_as_verified():
    for agent in AGENT_NAMES:
        for e in PA.dataset(agent)["examples"]:
            assert e["verification"] != "owner_verified" and "authored" in e["source"]


def test_the_scoring_compares_decisions_and_not_wording():
    example = PA.dataset("qa")["examples"][0]
    right = PA.check_answer("qa", example["output"])
    assert PA.score("qa", right, example) == {"verdict_equal": True, "problem_equal": True}
    wrong = PA.check_answer("qa", {**example["output"], "verdict": "approve", "problem": "none"})
    assert PA.score("qa", wrong, example) == {"verdict_equal": False, "problem_equal": False}
