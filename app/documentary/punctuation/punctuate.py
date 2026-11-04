"""The punctuation changes of one block: the deterministic ones (Latin punctuation in Persian text in its Persian form) and, when the agents run,
the punctuation agent's proposals (billable). A proposal is kept only when code finds it safe: a mark the layer may insert, at a boundary that is
not a required ezafe (a comma never cuts an ezafe construction), into a gap that has no punctuation yet (or a replacement of the mark that is
there). Everything refused is reported with why."""

from __future__ import annotations

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.documentary.punctuation.boundary import REQUIRED, BoundaryDecision
from app.documentary.punctuation.layer import PUNCTUATION, Edit, Layer
from app.documentary.reading.ask import ask
from app.documentary.reading.words import TAG


def normalized(layer: Layer) -> list[Edit]:
    """Latin punctuation the text writes between Persian words, in its Persian form (the story keeps its own)."""
    table = ai_config.semantic_punctuation.normalize
    out = []
    for n, gap in enumerate(layer.gaps()[1:]):              # the gap after each word, the last one too
        for ch in TAG.sub("", gap):
            if ch in table:
                out.append(Edit(n, ch, table[ch], f"{ch!r} written in Persian text: its Persian form {table[ch]!r}"))
                break
    return out


def safe(edit: Edit, layer: Layer, boundaries: list[BoundaryDecision]) -> str:
    """Why a proposed edit is refused ("" when it is safe)."""
    words = layer.words
    if not 0 <= edit.after < len(words) - 1:
        return "not a boundary between two words of the block"
    if edit.new and edit.new not in ai_config.semantic_punctuation.marks:
        return f"{edit.new!r} is not a mark the layer may insert"
    decision = boundaries[edit.after]
    if edit.new in ("،", "؛", ":", ".") and decision.status == REQUIRED:
        return f"the boundary needs its ezafe ({decision.reason}): a mark there would cut the construction"
    gap = TAG.sub("", layer.gaps()[edit.after + 1]).strip()
    if not edit.old and any(ch in PUNCTUATION for ch in gap):
        return "the gap already has punctuation"
    if edit.old and edit.old not in gap:
        return f"the gap has no {edit.old!r}"
    return ""


def payload(layer: Layer, boundaries: list[BoundaryDecision], syntax: dict, english: str) -> dict:
    words = layer.words
    return {"words": [{"n": w.n, "text": w.surface} for w in words], "text": layer.base, "boundaries": [b.describe(words) for b in boundaries],
            "syntax": syntax, "english": english, "marks": ai_config.semantic_punctuation.marks}


def parse(raw: dict) -> list[Edit]:
    out = []
    for e in raw.get("edits") or []:
        if isinstance(e, dict) and str(e.get("after", "")).isdigit():
            out.append(Edit(int(e["after"]), str(e.get("old") or ""), str(e.get("new") or ""), str(e.get("reason") or ""), "agent"))
    return out


async def propose(db, case_id: int, layer: Layer, boundaries: list[BoundaryDecision], syntax: dict, english: str, feedback: list[str],
                  runner=None) -> list[Edit]:
    body = {**payload(layer, boundaries, syntax, english), **({"checker_feedback": feedback} if feedback else {})}
    raw = await ask(db, case_id, f"Punctuation: propose {layer.block_id}", body, "punctuation_editor",
                    runner or (lambda gen, b: run_agent("documentary.punctuation.punctuate", gen, b)))
    return parse(raw)
