"""The estimate against what a case's ledger shows. The ledger is the only spend; this measures how
far the planning figures are from it, per stage and language, and measures the factors again from
any case (every call counts: failed drafts, repaired finals, gate repairs)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import StoryVersion
from app.language_pipeline.materials import SharedMaterial
from app.language_pipeline.stages import LanguageStages
from app.language_pipeline.state import ledger_table
from app.language_pipeline.store import FINAL, REWRITE

_STAGES = {"language_draft_review": "draft_review", "language_draft": "draft", "language_rewrite": "rewrite", "language_inspect": "inspect",
           "language_final": "final", "language_gates": "gates", "language_glossary": "glossary",
           "english_audit_fix": "audit_fix", "english_audit": "audit", "language_review": "review",
           "language_fidelity": "fidelity", "length_correction": "length_correction"}


def _stage(ledger_stage: str) -> str | None:
    for prefix, name in _STAGES.items():
        if ledger_stage.startswith(prefix):
            return name
    return None


def actual_by_stage(db: Session, case_id: int) -> dict[tuple[str, str | None], dict]:
    """Calls, tokens and EUR per estimate stage and language. The draft and the glossary are
    shared (language None); the draft includes its experiments."""
    out: dict[tuple[str, str | None], dict] = {}
    for row in ledger_table(db, case_id):
        name = _stage(f"{row['stage']}")
        if name is None:
            continue
        language = None if name in ("draft", "draft_review", "glossary", "audit", "audit_fix") else row["language"]
        item = out.setdefault((name, language), {"calls": 0, "tokens_in": 0, "tokens_cached": 0,
                                                 "tokens_reasoning": 0, "tokens_out": 0, "eur": 0.0})
        for key in item:
            item[key] += row[key if key != "eur" else "eur"]
    return out


def compare(db: Session, case_id: int, estimate: dict) -> list[dict]:
    """The estimate's lines beside the ledger's actual numbers (a stage the estimate does not
    plan, or the ledger does not show yet, has no number on that side)."""
    actual = actual_by_stage(db, case_id)
    rows = []
    for line in estimate["lines"]:
        key = (line["stage"], line["language"])
        real = actual.pop(key, None)
        rows.append(_row(line["stage"], line["language"], line, real))
    for (stage, language), real in sorted(actual.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        rows.append(_row(stage, language, None, real))
    return rows


def _row(stage: str, language: str | None, line: dict | None, real: dict | None) -> dict:
    row = {"stage": stage, "language": language,
           "estimated_calls": line["calls"] if line else None,
           "estimated_eur": line["eur"] if line else None,
           "actual_calls": real["calls"] if real else None,
           "actual_eur": round(real["eur"], 4) if real else None,
           "actual_tokens_in": real["tokens_in"] if real else None,
           "actual_tokens_cached": real["tokens_cached"] if real else None,
           "actual_tokens_out": real["tokens_out"] if real else None, "ratio": None}
    if line and real and line["eur"]:
        row["ratio"] = round(real["eur"] / line["eur"], 2)
    return row


def measure_cache_share(db: Session, gap_seconds: int = 600) -> dict:
    """The share of the draft's input tokens the provider served from its cache, measured on the ledger
    of every case: per run (calls of one case with no gap longer than `gap_seconds`) and overall. The
    worst run is the conservative figure. Nothing in the ledger means no figure."""
    from app.db.models import ProviderCall

    rows = (db.query(ProviderCall.case_id, ProviderCall.created_at, ProviderCall.tokens_in, ProviderCall.tokens_cached)
            .filter(ProviderCall.host == "budget-ledger", ProviderCall.stage == "language_draft",
                    ProviderCall.is_test_data.is_(False), ProviderCall.tokens_in.isnot(None))
            .order_by(ProviderCall.case_id, ProviderCall.id).all())
    runs: list[list[int]] = []
    last = None
    for case, at, tin, cached in rows:
        if last is None or case != last[0] or (at - last[1]).total_seconds() > gap_seconds:
            runs.append([0, 0])
        runs[-1][0] += int(tin)
        runs[-1][1] += int(cached or 0)
        last = (case, at)
    shares = [cached / tin for tin, cached in runs if tin]
    total_in = sum(tin for tin, _ in runs)
    return {"runs": len(shares),
            "overall": round(sum(c for _, c in runs) / total_in, 3) if total_in else None,
            "worst_run": round(min(shares), 3) if shares else None,
            "best_run": round(max(shares), 3) if shares else None}


def measure(db: Session, case_id: int, material: SharedMaterial, languages: list[str]) -> dict:
    """The calibration factors this case's ledger shows, for config.language_pipeline.estimate."""
    actual = actual_by_stage(db, case_id)
    scenes = max(sum(len(LanguageStages.scenes(a)) for a in material.acts), 1)
    draft = actual.get(("draft", None))
    versions = (db.query(StoryVersion).filter(StoryVersion.case_id == case_id,
                                              StoryVersion.kind.in_((REWRITE, FINAL)))
                .order_by(StoryVersion.id).all())
    latest = {(v.kind, v.language): v for v in versions}
    per_word: dict[str, float] = {}
    reasoning: dict[str, float] = {}
    for stage, kind in (("rewrite", REWRITE), ("final", FINAL)):
        for lg in languages:
            real, version = actual.get((stage, lg)), latest.get((kind, lg))
            if real and version and version.story_text and real["calls"]:
                visible = (real["tokens_out"] - real["tokens_reasoning"]) / real["calls"]
                per_word.setdefault(lg, round(visible / len(version.story_text.split()), 2))
                reasoning.setdefault(stage, round(real["tokens_reasoning"] / max(real["tokens_out"] - real["tokens_reasoning"], 1), 2))
    return {
        "draft_attempt_factor": round(draft["calls"] / scenes, 2) if draft else None,
        "tokens_per_word": per_word, "reasoning_ratio": reasoning,
        "ledger_eur": round(sum(v["eur"] for v in actual.values()), 4),
    }
