"""The seven agents of the Persian pronunciation system: their output schemas (the contract of every answer), the few-shot dataset each prompt is built
from, and the rendering of a complete system prompt. The prompts are files (prompts/pronunciation/<agent>_system.md, with a changelog) and the examples
are a dataset organised by linguistic phenomenon (prompts/pronunciation/fewshot/<agent>.json): training examples go into the prompt, held-out examples
never do and are what an agent is evaluated on. An example without a real audio is labelled hypothetical and is never evidence about a recording."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2] / "prompts" / "pronunciation"
VERSION_FILE = ROOT / "CHANGELOG.md"
MIN_TRAIN_EXAMPLES = 8
SECTIONS = ("# Role", "# Input", "# Output", "# Procedure", "# Rules", "# Prohibitions", "# Ambiguity and uncertainty", "# Escalation",
            "# Independent validation", "# Acceptance criteria", "# Examples")


# --- the contracts ---------------------------------------------------------------------------------------------------------------------

class IrabWord(BaseModel):
    n: int
    irab: str
    speech_register: Literal["formal", "colloquial", "neutral"]
    pos: str
    ezafe: bool
    status: Literal["resolved", "verified_from_memory", "needs_verified_name", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    note: str = ""


class LinguistOut(BaseModel):
    words: list[IrabWord]
    unresolved: list[int]


class RiskItem(BaseModel):
    n: int
    word: str
    risk: float = Field(ge=0, le=1)
    category: Literal["none", "proper_name", "known_failure", "vowel_ambiguity", "ezafe", "context_specific", "conflict", "uncertain"]
    action: Literal["none", "dictionary", "selective_irab", "ask_owner"]
    evidence: list[str]


class RiskOut(BaseModel):
    items: list[RiskItem]


class Intervention(BaseModel):
    n: int
    surface: str
    decision: Literal["none", "dictionary_alias", "dictionary_phoneme", "selective_irab", "alt_spelling", "ask_owner"]
    exact_form: str
    replacement: str
    reason: str
    rejected_alternatives: list[str]
    ezafe_kept: bool
    validated_in_memory: bool
    expected_effect: str


class InterventionOut(BaseModel):
    interventions: list[Intervention]


class Observation(BaseModel):
    n: int | None
    word: str
    expected: str
    observed: str | None
    evidence_kind: Literal["acoustic", "asr", "model", "none"]
    acoustic_evidence: str
    verdict: Literal["match", "mismatch", "uncertain", "missing_word", "unclear_audio"]
    confidence: float = Field(ge=0, le=1)
    uncertainty: str
    next_action: Literal["accept", "retake_with_intervention", "ask_owner", "recheck_audio", "none"]
    hypothetical: bool


class ObservationOut(BaseModel):
    audio_quality: Literal["clear", "distorted", "unclear"]
    observations: list[Observation]


class Rating(BaseModel):
    rating: Literal["good", "acceptable", "poor"]
    note: str


class ProsodyIssue(BaseModel):
    kind: Literal["robotic", "pauses", "emphasis", "excess_diacritics", "emotion", "identity", "artifact", "colloquial_ok"]
    where: str
    note: str


class ProsodyOut(BaseModel):
    flow: Rating
    rhythm: Rating
    pauses: Rating
    emotion: Rating
    stress_intonation: Rating
    identity: Rating
    artifacts: Rating
    issues: list[ProsodyIssue]
    verdict: Literal["natural", "needs_retake", "acceptable_with_note"]
    more_diacritics_recommended: bool
    reason: str


class MemoryEntryOut(BaseModel):
    surface: str
    context: str
    pronunciation: str
    scope: Literal["context", "global", "voice_specific"]
    voice: str


class MemoryOut(BaseModel):
    action: Literal["store_new", "already_known", "store_context_variant", "conflict", "record_validated_intervention", "record_failed_intervention",
                    "keep_for_voice_only", "do_not_reuse", "escalate"]
    entry: MemoryEntryOut | None
    intervention_validated: bool | None
    requires_human: bool
    reason: str


class QACheck(BaseModel):
    ok: bool
    note: str


class QAOut(BaseModel):
    verdict: Literal["approve", "reject", "escalate"]
    linguistic: QACheck
    audio_evidence: QACheck
    dictionary_compatibility: QACheck
    intervention_choice: QACheck
    fluency: QACheck
    memory_integrity: QACheck
    reuse_safety: QACheck
    cost: QACheck
    problem: Literal["none", "false_positive", "false_negative", "conflicting_evidence", "invalid_rule", "unsupported_confidence", "naturalness_loss",
                     "unsafe_reuse", "cost_waste"]
    reason: str


AGENTS: dict[str, type[BaseModel]] = {
    "linguist": LinguistOut, "risk": RiskOut, "intervention": InterventionOut, "audio_observer": ObservationOut,
    "prosody": ProsodyOut, "memory": MemoryOut, "qa": QAOut,
}
ROLES = {"linguist": "reading_editor", "risk": "reading_checker", "intervention": "reading_editor", "audio_observer": "reading_listener",
         "prosody": "reading_listener", "memory": "reading_arbiter", "qa": "reading_checker"}
DATASET_KEYS = {"id", "split", "phenomenon", "original_text", "linguistic_context", "expected_pronunciation", "correct_decision", "incorrect_decision_to_avoid",
                "explanation", "confidence_requirement", "source", "verification", "audio", "input", "output"}
SPLITS = ("train", "heldout")
VERIFICATIONS = ("assistant_authored_pending_linguistic_review", "hypothetical_scenario", "owner_verified")


# --- the dataset and the prompts -------------------------------------------------------------------------------------------------------

def dataset(agent: str) -> dict:
    return json.loads((ROOT / "fewshot" / f"{agent}.json").read_text(encoding="utf-8"))


def examples(agent: str, split: str = "train") -> list[dict]:
    return [e for e in dataset(agent)["examples"] if e["split"] == split]


def version() -> str:
    found = re.search(r"^## (\S+)", VERSION_FILE.read_text(encoding="utf-8"), re.M)
    return found.group(1) if found else "0"


def render_examples(agent: str) -> str:
    """The training examples as demonstrations: the input an agent gets and the exact JSON it answers, with the decision to avoid and why."""
    parts = []
    for i, e in enumerate(examples(agent), 1):
        parts.append(f"## Example {i} — {e['phenomenon']}" + (" (hypothetical: no real audio)" if e["audio"] == "hypothetical" else "") + "\n"
                     f"Input:\n{json.dumps(e['input'], ensure_ascii=False)}\n"
                     f"Output:\n{json.dumps(e['output'], ensure_ascii=False)}\n"
                     f"Correct decision: {e['correct_decision']}\nDecision to avoid: {e['incorrect_decision_to_avoid']}\nWhy: {e['explanation']}")
    return "\n\n".join(parts)


def system_prompt(agent: str) -> str:
    """The complete system prompt of an agent: its file with the output schema and the demonstrations in place."""
    text = (ROOT / f"{agent}_system.md").read_text(encoding="utf-8")
    schema = json.dumps(AGENTS[agent].model_json_schema(), ensure_ascii=False)
    return text.replace("{{OUTPUT_SCHEMA}}", schema).replace("{{EXAMPLES}}", render_examples(agent)).replace("{{VERSION}}", version())


def check_answer(agent: str, answer: dict) -> BaseModel:
    """The answer of an agent, validated against its contract (raises pydantic's ValidationError)."""
    return AGENTS[agent].model_validate(answer)


# --- running and evaluating an agent ---------------------------------------------------------------------------------------------------

async def run(agent: str, payload: dict, gen=None) -> BaseModel:
    """Asks an agent (its complete system prompt, the payload as the user message) and validates the answer against its contract."""
    from app.agents.runner import run_agent
    from app.providers.generation import get_generation_provider

    data, _ = await run_agent(f"pronunciation.{agent}", gen or get_generation_provider(), json.dumps(payload, ensure_ascii=False), system=system_prompt(agent))
    return check_answer(agent, data)


def score(agent: str, answer: BaseModel, example: dict) -> dict:
    """How an answer compares with the expected output of an example, on the decision fields (not on the wording of notes)."""
    got, want = answer.model_dump(), example["output"]
    if agent == "linguist":
        pairs = list(zip(got["words"], want["words"], strict=False))
        return {"irab_match": sum(1 for a, b in pairs if a["irab"] == b["irab"]) / max(1, len(want["words"])),
                "status_match": sum(1 for a, b in pairs if a["status"] == b["status"]) / max(1, len(want["words"])), "unresolved_match": got["unresolved"] == want["unresolved"]}
    if agent == "risk":
        flagged = {i["n"] for i in got["items"] if i["action"] != "none"}
        expected = {i["n"] for i in want["items"] if i["action"] != "none"}
        return {"flagged_equal": flagged == expected, "extra": sorted(flagged - expected), "missed": sorted(expected - flagged)}
    if agent == "intervention":
        return {"decisions_equal": [(i["n"], i["decision"]) for i in got["interventions"]] == [(i["n"], i["decision"]) for i in want["interventions"]]}
    if agent == "memory":
        return {"action_equal": got["action"] == want["action"], "human_equal": got["requires_human"] == want["requires_human"]}
    if agent == "qa":
        return {"verdict_equal": got["verdict"] == want["verdict"], "problem_equal": got["problem"] == want["problem"]}
    return {"schema_only": True}
