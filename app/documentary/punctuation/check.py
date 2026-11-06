"""The independent check of a block's proposed punctuation (billable: another model than the punctuation agent's). It sees the approved text,
the proposed text for the voice, the syntax and ezafe analysis, the English reference and the neighbours, never the proposer's reasoning as a
verdict. PASS, REVISE (with the edits to drop or change, the smallest change), UNCERTAIN or REJECT; an answer it does not give in that form is
UNCERTAIN, never PASS."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.documentary.reading.ask import ask

DECISIONS = ("PASS", "REVISE", "UNCERTAIN", "REJECT")


@dataclass
class Verdict:
    decision: str
    issues: list[str] = field(default_factory=list)
    drop: set[int] = field(default_factory=set)            # the proposed edits (by their place in the proposal) to drop

    def describe(self) -> dict:
        return {"decision": self.decision, "issues": self.issues, "drop": sorted(self.drop)}


def parse(raw: dict, proposed: int) -> Verdict:
    decision = str(raw.get("decision") or "").upper()
    if decision not in DECISIONS:
        return Verdict("UNCERTAIN", ["the checker gave no decision"])
    drop = {int(i) for i in raw.get("drop") or [] if str(i).isdigit() and int(i) < proposed}
    if decision == "REVISE" and not drop:
        return Verdict("UNCERTAIN", [str(i) for i in raw.get("issues") or []] + ["REVISE without the edits to change"])
    return Verdict(decision, [str(i) for i in raw.get("issues") or []], drop)


async def check(db, case_id: int, block_id: str, canonical: str, proposed_text: str, edits: list[dict], analysis: dict, english: str, previous: str,
                following: str, runner=None, language: str = "fa") -> Verdict:
    body = {"original": canonical, "proposed": proposed_text, "edits": [{"i": i, **e} for i, e in enumerate(edits)], "analysis": analysis,
            "english": english, "previous": previous, "next": following}
    agent = f"{ai_config.semantic_punctuation.profile(language).agents}.check"
    raw = await ask(db, case_id, f"Punctuation: check {block_id}", body, "punctuation_checker", runner or (lambda gen, b: run_agent(agent, gen, b)))
    return parse(raw, len(edits))
