"""The structure and meaning of one block (billable: one call of the syntax agent): its constituents and clauses, and for each word boundary
whether the two words are connected by an ezafe, separated, or open, with the relation and the reason. The block's neighbours and the English
passage (when the alignment verified it) are its context; the English is a reference for meaning, never a template for punctuation. A boundary the
agent cannot settle is an ambiguity, never a guess."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.documentary.reading.ask import ask
from app.documentary.reading.words import Word

EZAFE = ("required", "forbidden", "context")


@dataclass
class Syntax:
    boundaries: dict[int, dict] = field(default_factory=dict)        # word n -> {"ezafe", "relation", "reason", "comma_ok"}
    ambiguous: set[int] = field(default_factory=set)
    constituents: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)

    def describe(self) -> dict:
        return {"boundaries": {str(k): v for k, v in sorted(self.boundaries.items())}, "ambiguous": sorted(self.ambiguous),
                "constituents": self.constituents, "questions": self.questions}


def payload(words: list[Word], previous: str, following: str, english: str, alignment_status: str) -> dict:
    return {"words": [{"n": w.n, "text": w.surface} for w in words], "previous": previous, "next": following,
            "english": english if alignment_status in ("ALIGNED", "PARTIAL") else "", "alignment_status": alignment_status}


def parse(raw: dict, words: list[Word], has_ezafe: bool = True) -> Syntax:
    """The agent's answer, kept to the block's real boundaries; a boundary with an unknown verdict is ambiguous (only in a language with an ezafe)."""
    out, last = Syntax(), len(words) - 1
    for b in raw.get("boundaries") or []:
        if not isinstance(b, dict) or not str(b.get("n", "")).isdigit() or not 0 <= int(b["n"]) < last:
            continue
        n, said = int(b["n"]), str(b.get("ezafe") or "").lower()
        out.boundaries[n] = {"ezafe": said if said in EZAFE else "context", "relation": str(b.get("relation") or ""),
                             "reason": str(b.get("reason") or ""), "comma_ok": bool(b.get("comma_ok"))}
        if has_ezafe and said not in ("required", "forbidden"):
            out.ambiguous.add(n)
    for a in raw.get("ambiguities") or []:
        if isinstance(a, dict) and str(a.get("n", "")).isdigit() and int(a["n"]) < last:
            out.ambiguous.add(int(a["n"]))
            out.questions.append({"n": int(a["n"]), "readings": [str(r) for r in a.get("readings") or []], "question": str(a.get("question") or "")})
    out.constituents = [c for c in raw.get("constituents") or [] if isinstance(c, dict)]
    return out


async def analyze(db, case_id: int, block_id: str, words: list[Word], previous: str, following: str, english: str, alignment_status: str,
                  runner=None, language: str = "fa") -> Syntax:
    profile = ai_config.semantic_punctuation.profile(language)
    agent = f"{profile.agents}.syntax"
    raw = await ask(db, case_id, f"Punctuation: syntax {block_id}", payload(words, previous, following, english, alignment_status), "punctuation_syntax",
                    runner or (lambda gen, body: run_agent(agent, gen, body)))
    return parse(raw, words, profile.ezafe)
