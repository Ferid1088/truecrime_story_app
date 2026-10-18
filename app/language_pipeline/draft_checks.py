"""The deterministic partners of the evidence audit, computed on the English draft: the numbers,
dates and names the draft states that the evidence does not, and which evidence items the draft
tells. Neither uses a language model, neither blocks or changes anything; they are reports."""

from __future__ import annotations

from app.language_pipeline import figures
from app.language_pipeline.coverage import EvidenceCoverage
from app.language_pipeline.fact_report import _fold, _present, proper_names
from app.language_pipeline.materials import SharedMaterial


def numbers_and_names(material: SharedMaterial, text: str) -> dict:
    """Figures (years, dates, amounts, counts) that no evidence item or the plan states, and
    capitalised names that stand in no fact, excerpt or source title."""
    allowed = figures.supported_figures(material.pack, material.plan, material.draft_pack)
    unsupported = figures.unsupported(text, allowed)
    evidence = _fold(" ".join(
        [str(f.get(k) or "") for f in material.draft_pack.get("facts") or []
         for k in ("claim", "original_claim", "supporting_text", "speaker")]
        + [s["title"] or "" for s in material.sources]))
    names = sorted(n for n in proper_names(text) if not _present(n, evidence))
    return {"model_calls": 0, "blocking": False,
            "unsupported_figures": [{"figure": u["figure"], "context": u["context"]} for u in unsupported],
            "names_not_in_evidence": names}


async def coverage(engine: EvidenceCoverage | None, material: SharedMaterial,
                   sections: list[dict], key_fact_count: int) -> dict:
    """Which facts of the 100-fact pack the draft tells (by embedding similarity), and which of
    the key facts it does not. Embeddings only; no language model."""
    if engine is None:
        return {"skipped": "no embedding client", "model_calls": 0, "blocking": False}
    tellings = await engine.tellings(material, sections)
    packed = [f["id"] for f in material.pack.get("facts") or []]
    key = material.key_fact_ids(key_fact_count)
    return {"model_calls": 0, "blocking": False, "embeddings_only": True,
            "facts_in_pack": len(packed), "told": len(tellings),
            "not_told": [i for i in packed if i not in tellings],
            "key_facts_not_told": [i for i in key if i not in tellings][:60]}
