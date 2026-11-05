"""The repair of a word the voice said wrong, and the independent check of a repair.

Diagnosis: code names the likely cause from the evidence it has (the independent expected reading against PPIE's reading and voice form, a number,
an unvalidated recognizer, an uncertain alignment); the corrector agent (billable, config) may name another and rank the candidates. A cause the
evidence does not decide is `unknown`.

Candidates change only how the word is written in the text the voice is given, never the story: targeted marks on the word's own letters (its
validated REFERENCE_IRAB, minimal form), inline IPA between slashes (eleven_v4 reads it; no SSML phoneme tag), or a respelling in Persian letters.
A form that failed in this run is not proposed again; after the configured attempts the word goes to the owner.

Verification is not done by the agent that proposed the repair: code compares the recognizer's review of the original take and of the new take,
the target word and every other word of the block, and the take's length. While the recognizer is unvalidated the verdict stays UNCERTAIN and the
owner listens and decides."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.documentary.reading import reference as REF
from app.documentary.reading import scheme, spelling
from app.documentary.reading.ask import ask
from app.documentary.reading.words import tokenize



@dataclass
class Proposal:
    block_id: str
    n: int
    word: str
    cause: str
    evidence: list[str]
    target: str                                   # the verified target reading (Latin scheme)
    strategies: list[dict] = field(default_factory=list)       # {type, form, tts_text, reason}
    status: str = "PROPOSED"                      # PROPOSED | AUTHORIZED | GENERATED | ACCEPTED | REJECTED | OWNER_REVIEW
    estimated_characters: int = 0
    attempts: list[dict] = field(default_factory=list)

    def describe(self) -> dict:
        return {"block_id": self.block_id, "word_id": self.n, "word": self.word, "suspected_root_cause": self.cause, "evidence": self.evidence,
                "verified_target_reading": self.target, "verified_target_phonemes": scheme.to_ipa(self.target) if self.target else "",
                "strategies": self.strategies, "owner_approval_required": True, "estimated_characters": self.estimated_characters,
                "status": self.status, "attempts": self.attempts}


def diagnose(review: dict, expected: str, ppie: dict | None, alignment_status: str, number: bool) -> tuple[str, list[str]]:
    """The likely cause by code, with what supports it. `review`: the word's acoustic review; `ppie`: PPIE's reading and voice form of the word in
    this run (None when the take was not voiced by a PPIE run)."""
    evidence = []
    heard = next((c for c in review.get("candidates") or [] if review.get("suggested") == "FAIL" and c["kind"] != "expected"
                  and c["loss"].get("1.0") == min(x["loss"]["1.0"] for x in review["candidates"] if x["loss"].get("1.0") is not None)), None)
    if review.get("advisory"):
        evidence.append("the recognizer is not validated: this may be a false alarm")
    if alignment_status not in ("ALIGNED", "PARTIAL"):
        evidence.append(f"the English passage is {alignment_status}: the expected reading rests on the Persian context alone")
    if number:
        return "number_normalization", evidence + ["the word is a number: its words come from the number reader"]
    if ppie and ppie.get("reading") and not scheme.same_reading(ppie["reading"], expected):
        return "ppie_wrong_reading", evidence + [f"PPIE read it {ppie['reading']}, the independent reading is {expected}"]
    if heard and heard["kind"] in ("ezafe_ye", "ezafe_added", "epenthesis") and ppie and ppie.get("tts_input") != review.get("word"):
        return "inserted_vowel_or_ezafe", evidence + [f"heard {heard['reading']} and PPIE changed the word to {ppie['tts_input']}"]
    if heard:
        return "tts_misreading", evidence + [f"the reading was right; the voice said {heard['reading']} ({heard['kind']})"]
    return "unknown", evidence


def forms(surface: str, target: str) -> dict[str, str]:
    """The voice forms of the target reading on this word, by strategy (only those that can be made and differ from the word)."""
    out: dict[str, str] = {}
    word = tokenize(surface)[0] if tokenize(surface) else None
    if word is not None:
        ref = REF.reference_of(word, target)
        if ref is not None and ref.valid and ref.irab and not ref.added_carrier:       # None: a number, or a reading that is not of the scheme
            out["targeted_diacritics"] = REF.minimal(ref.irab)
    with contextlib.suppress(KeyError, ValueError):
        out["inline_ipa"] = f"/{' '.join(scheme.to_ipa(p) for p in target.split())}/"
    with contextlib.suppress(ValueError):
        out["alternative_spelling"] = spelling.persian(target)
    allowed = ai_config.ppie_review.correction.strategies
    return {k: v for k, v in out.items() if k in allowed and v and v != surface}


def propose(block_id: str, n: int, surface: str, target: str, cause: str, evidence: list[str], voice_text: str, char_span: tuple[int, int],
            tried: list[dict]) -> Proposal:
    """The candidates for one word, best first by the configured order, without a form that failed in this run."""
    failed = {a["form"] for a in tried if a.get("outcome") == "failed"}
    proposal = Proposal(block_id, n, surface, cause, evidence, target)
    if cause in ("reading_agent_wrong", "recognizer_false_positive"):
        proposal.status = "OWNER_REVIEW"
        return proposal
    if len(tried) >= ai_config.ppie_review.correction.max_attempts_per_word:
        proposal.status, proposal.evidence = "OWNER_REVIEW", evidence + ["every allowed attempt of this run was made"]
        return proposal
    c0, c1 = char_span
    for kind in ai_config.ppie_review.correction.strategies:
        form = forms(surface, target).get(kind)
        if form and form not in failed:
            proposal.strategies.append({"type": kind, "form": form, "tts_text": voice_text[:c0] + form + voice_text[c1:],
                                        "reason": {"targeted_diacritics": "short vowels written on the word's own letters",
                                                   "inline_ipa": "the sounds themselves, for a word marks did not steer",
                                                   "alternative_spelling": "a spelling the voice reads as the target"}[kind]})
    if not proposal.strategies:
        proposal.status = "OWNER_REVIEW"
    proposal.estimated_characters = len(proposal.strategies[0]["tts_text"]) if proposal.strategies else 0
    return proposal


async def rank(db, case_id: int, proposal: Proposal, context: dict, runner=None) -> Proposal:
    """The corrector agent's diagnosis and ranking (billable); its cause replaces code's only when it names one, and it can only order the
    candidates code made (never add one)."""
    raw = await ask(db, case_id, f"PPIE review: correct {proposal.block_id} {proposal.n}", {**context, "candidates": proposal.strategies,
                    "code_cause": proposal.cause}, "ppie_corrector", runner or (lambda gen, body: run_agent("documentary.ppie_review.correct", gen, body)))
    cause = str(raw.get("cause") or "")
    if cause and cause != "unknown":
        proposal.cause = cause
        proposal.evidence.append(f"corrector: {raw.get('evidence') or ''}".strip())
    if proposal.cause in ("reading_agent_wrong", "recognizer_false_positive"):
        proposal.status, proposal.strategies = "OWNER_REVIEW", []
        return proposal
    order = [str(r.get("strategy")) for r in raw.get("ranking") or [] if isinstance(r, dict)]
    proposal.strategies.sort(key=lambda s: order.index(s["type"]) if s["type"] in order else len(order))
    return proposal


def verify(original: list[dict], candidate: list[dict], target_n: int, original_seconds: float, candidate_seconds: float) -> dict:
    """The check of a new take against the original, by the recognizer's reviews of both (never by the proposer)."""
    before = {w["n"]: w for w in original}
    after = {w["n"]: w for w in candidate}
    target = after.get(target_n, {})
    resolved = None if target.get("suggested") in (None, "UNCERTAIN", "NOT_EVALUABLE") else target.get("suggested") == "PASS"
    new_errors = [w["word"] for n, w in after.items() if n != target_n and w.get("suggested") == "FAIL" and before.get(n, {}).get("suggested") != "FAIL"]
    change = abs(candidate_seconds - original_seconds) / original_seconds if original_seconds else 0.0
    issues = ([f"the take is {round(change * 100)}% {'longer' if candidate_seconds > original_seconds else 'shorter'}"]
              if change > ai_config.ppie_review.correction.max_length_change else [])
    from app.documentary.ppie_review.controlled import read_aloud

    if read_aloud(target.get("heard") or ""):
        issues.append("the voice read the notation aloud (\"slash ...\")")
    validated = all(w.get("model_validation") == "VALIDATED" for w in candidate) and bool(candidate)
    if any("notation aloud" in i for i in issues):
        decision = "REJECTED"                   # a gross defect of the take: the original is kept whatever the recognizer's validation
    elif not validated:
        decision = "UNCERTAIN"
    elif resolved and not new_errors and not issues:
        decision = "ACCEPTABLE"
    else:
        decision = "REJECTED"
    return {"target_error_resolved": resolved, "new_errors_detected": new_errors, "issues": issues, "final_decision": decision,
            "advisory": not validated, "original_seconds": round(original_seconds, 3), "candidate_seconds": round(candidate_seconds, 3)}
