"""The independent reading of a block (billable: one model call per block): how each word should be said, from the approved Persian text, its
neighbours and the matched English passage alone. The agent never sees PPIE's reading, the text the voice was given or any dictionary, so its reading
can disagree with PPIE's. It names the other readings the letters allow when the context does not settle one, and why. A word it cannot settle keeps
its alternatives and stays unresolved; nothing is guessed. Its readings are this run's only: they are never stored for another run."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.runner import run_agent
from app.documentary.reading import scheme
from app.documentary.reading.ask import ask
from app.documentary.reading.words import tokenize


@dataclass(frozen=True)
class Expectation:
    n: int
    word: str
    reading: str                    # Latin scheme (reading/scheme.py); a number: the words it is said as
    alternatives: list[str] = field(default_factory=list)
    reason: str = ""
    settled: bool = True

    def describe(self) -> dict:
        return {"word_id": self.n, "canonical_text": self.word, "expected_reading": self.reading, "expected_phonemes": phonemes(self.reading),
                "alternative_readings": self.alternatives, "contextual_reason": self.reason, "settled": self.settled,
                "source": "independent_reading_agent"}


def phonemes(reading: str) -> str:
    try:
        return " ".join(scheme.to_ipa(part) for part in reading.split())
    except (KeyError, ValueError):
        return ""


def payload(text: str, previous: str, following: str, english: str, alignment_status: str) -> dict:
    return {"words": [{"n": w.n, "text": w.surface} for w in tokenize(text)], "previous": previous, "next": following,
            "english": english, "alignment_status": alignment_status}


def parse(raw: dict, text: str) -> list[Expectation]:
    """The agent's answer, one expectation per word of the block; a word it left out or answered in no reading is unsettled (no reading)."""
    given = {int(w["n"]): w for w in raw.get("words") or [] if isinstance(w, dict) and str(w.get("n", "")).isdigit()}
    out = []
    for w in tokenize(text):
        got = given.get(w.n) or {}
        reading = scheme.canonical(str(got.get("reading") or ""))
        alternatives = [scheme.canonical(str(a)) for a in got.get("alternatives") or [] if str(a).strip()]
        out.append(Expectation(w.n, w.surface, reading, [a for a in alternatives if a != reading], str(got.get("reason") or ""),
                               bool(reading) and bool(got.get("settled", True))))
    return out


async def expect(db, case_id: int, block_id: str, text: str, previous: str, following: str, english: str, alignment_status: str,
                 runner=None) -> list[Expectation]:
    raw = await ask(db, case_id, f"PPIE review: expected reading {block_id}", payload(text, previous, following, english, alignment_status),
                    "ppie_expectation", runner or (lambda gen, body: run_agent("documentary.ppie_review.expect", gen, body)))
    return parse(raw, text)
