"""What the scenes already written have told, as a compact deterministic ledger for the next
scene call (the draft prompt's `told_so_far`). It is built from the stored scene texts and the
plan only: no model call. It does not replace the story so far; it names what a writer must not
tell again."""

from __future__ import annotations

import re

from app.core.ai_config import ai_config
from app.language_pipeline.glossary import entities_of
from app.language_pipeline.materials import SharedMaterial

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def told_so_far(material: SharedMaterial, scenes: list[dict], extra_by_act: dict[str, list[str]],
                current_act: str) -> dict:
    """`scenes` are the written scenes in story order ({"act", "index", "text"}). The ledger
    lists the names already introduced, the evidence told in the acts before `current_act`
    (the plan's own assignment plus the extra facts given to those acts), the sentences that
    already explained an evidentiary limitation, and the last paragraph written."""
    cfg = ai_config.language_pipeline.ledger
    text = "\n\n".join(s["text"] for s in scenes)
    folded = text.casefold()
    names = [e.name for e in entities_of(material) if e.name.casefold() in folded]
    earlier = material.act_ids[:material.act_ids.index(current_act)]
    assigned = {a["id"]: [*(a.get("evidence_ids") or []), *extra_by_act.get(str(a["id"]), [])]
                for a in material.acts}
    patterns = [re.compile(p, re.I) for p in cfg.limitation_patterns]
    limitations = []
    for sentence in _SENTENCE.split(text):
        sentence = " ".join(sentence.split())
        if sentence and len(sentence.split()) <= cfg.max_limitation_words \
                and any(p.search(sentence) for p in patterns):
            limitations.append(sentence)
    paragraphs = _paragraphs(text)
    return {
        "names_introduced": names,
        "facts_told_in_earlier_acts": {str(a): assigned.get(a, []) for a in earlier},
        "limitations_already_explained": limitations[-cfg.max_limitations:],
        "last_paragraph": paragraphs[-1] if paragraphs else "",
    }
