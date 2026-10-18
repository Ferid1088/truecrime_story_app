"""What the story page and the API show: the shared draft once (with its audit, its reports and its
approval), the rows of the retired fact-check loop, then one block per language with its stages,
critique, gates, models, effort, minutes and cost. Built from the stored rows and the ledger only."""

from __future__ import annotations

import json

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import ProviderCall, StoryVersion
from app.language_pipeline.review import OPEN_ISSUES
from app.language_pipeline.store import (
    DRAFT,
    FIDELITY,
    FINAL,
    INSPECTION,
    LENGTH_CORRECTION,
    RETIRED_KINDS,
    REVIEW,
    REWRITE,
    STAGE_KINDS,
    pipeline_of,
)
from app.services.cost_model import load_pricing


def _notes(row: StoryVersion) -> dict:
    try:
        return json.loads(row.critic_notes or "{}")
    except (ValueError, TypeError):
        return {}


def stage_dict(row: StoryVersion, with_text: bool = False) -> dict:
    p = pipeline_of(row)
    out = {
        "id": row.id, "stage": row.kind, "language": row.language, "status": row.status,
        "model": row.generation_model, "provider": row.generation_provider,
        "reasoning_effort": row.reasoning_effort, "minutes": row.minutes,
        "words": len((row.story_text or "").split()), "cost_eur": row.cost_eur,
        "prompt": row.writer_prompt_name, "prompt_sha256": row.writer_prompt_sha256,
        "derived_from": p.get("derived_from"), "usage": p.get("usage"), "created_at": row.created_at,
        "approved_by": row.approved_by, "approved_at": row.approved_at,
        "requested_minutes": p.get("requested_minutes"),
        "length_corrected": p.get("stage") == LENGTH_CORRECTION,
        "stage_kind": p.get("stage"),
        "acts_changed": p.get("acts_changed") or [],
        "corrects_version_id": p.get("corrects_version_id"),
    }
    if with_text:
        out["text"] = row.story_text
    return out


def _latest(rows: list[StoryVersion], kind: str, language: str) -> StoryVersion | None:
    found = [r for r in rows if r.kind == kind and r.language == language and r.status != "retired"]
    return found[-1] if found else None


def _saving_eur(model: str | None, cached_tokens: int) -> float:
    """What the cached input tokens did not cost, against paying the full input price."""
    pricing = load_pricing()
    spec = next((v for v in pricing["models"].values() if v.get("provider_model") == model), None)
    if not spec or not cached_tokens:
        return 0.0
    rate = float(pricing["exchange_rates"]["usd_to_eur"])
    gap = float(spec["input_usd_per_1m_tokens"]) - float(
        spec.get("cached_input_usd_per_1m_tokens", spec["input_usd_per_1m_tokens"]))
    return cached_tokens / 1_000_000 * gap * rate


def ledger_table(db: Session, case_id: int) -> list[dict]:
    """Per stage and language: calls, tokens in, cached (and what caching saved), reasoning, out
    and EUR (the draft once). Only the budget ledger's rows count as calls."""
    q = db.query(
        ProviderCall.stage, ProviderCall.model,
        func.count(ProviderCall.id).filter(ProviderCall.host == "budget-ledger"),
        func.coalesce(func.sum(ProviderCall.tokens_in), 0),
        func.coalesce(func.sum(ProviderCall.tokens_cached), 0),
        func.coalesce(func.sum(ProviderCall.cache_write_tokens), 0),
        func.coalesce(func.sum(ProviderCall.tokens_reasoning), 0),
        func.coalesce(func.sum(ProviderCall.tokens_out), 0),
        func.coalesce(func.sum(ProviderCall.cost_eur), 0.0),
    ).filter(ProviderCall.case_id == case_id, ProviderCall.is_test_data.is_(False),
             or_(ProviderCall.stage.like("language_%"), ProviderCall.stage.like("english_%"),
                 ProviderCall.stage.like("length_correction%"))
             ).group_by(
        ProviderCall.stage, ProviderCall.model).order_by(ProviderCall.stage)
    merged: dict[str, dict] = {}
    for stage, model, calls, tin, cached, written, reasoning, tout, eur in q.all():
        row = merged.setdefault(stage, {
            "stage": stage.partition(":")[0], "language": stage.partition(":")[2] or None,
            "calls": 0, "tokens_in": 0, "tokens_cached": 0, "tokens_cache_write": 0, "tokens_reasoning": 0,
            "tokens_out": 0, "eur": 0.0, "cache_saving_eur": 0.0})
        row["calls"] += int(calls)
        row["tokens_in"] += int(tin)
        row["tokens_cached"] += int(cached)
        row["tokens_cache_write"] += int(written)
        row["tokens_reasoning"] += int(reasoning)
        row["tokens_out"] += int(tout)
        row["eur"] += float(eur)
        row["cache_saving_eur"] += _saving_eur(model, int(cached))
    for row in merged.values():
        row["eur"] = round(row["eur"], 4)
        row["cache_saving_eur"] = round(row["cache_saving_eur"], 4)
        row["cache_share"] = round(row["tokens_cached"] / row["tokens_in"], 3) if row["tokens_in"] else 0.0
    return list(merged.values())


def retired_method(db: Session, case_id: int) -> list[dict]:
    """The rows of the retired fact-check loop. They stay as history and are never the source of
    a stage; the page labels them "retired method"."""
    rows = (db.query(StoryVersion).filter(
        StoryVersion.case_id == case_id, StoryVersion.kind.in_(RETIRED_KINDS))
        .order_by(StoryVersion.id).all())
    return [{"id": r.id, "kind": r.kind, "status": r.status, "cost_eur": r.cost_eur,
             "round": pipeline_of(r).get("round"), "retired": _notes(r).get("retired"),
             "created_at": r.created_at} for r in rows]


def _check_row(rows: list[StoryVersion], kind: str, final: StoryVersion | None) -> StoryVersion | None:
    """The newest check row (review or fidelity) made on exactly this final text."""
    if final is None:
        return None
    found = [r for r in rows if r.kind == kind and pipeline_of(r).get("checks_version_id") == final.id]
    return found[-1] if found else None


def _stale(final: StoryVersion | None, language: str, english: str, english_final: StoryVersion | None,
           approved_draft: StoryVersion | None) -> tuple[bool, str | None]:
    """A language version is out of date when the approved text it was re-told from is no longer the
    current one: its checks and its approval then no longer hold for the base the language now has."""
    if final is None:
        return False, None
    source = pipeline_of(final).get("derived_from") or {}
    if language == english:
        if approved_draft is not None and source.get("id") != approved_draft.id:
            return True, f"written from draft {source.get('id')}; the approved draft is now {approved_draft.id}"
        return False, None
    if english_final is None:
        return True, "there is no approved English final text any more"
    if source.get("kind") != FINAL or source.get("id") != english_final.id:
        return True, (f"re-told from {source.get('kind')} {source.get('id')}; the approved English final is "
                      f"now {english_final.id}")
    if source.get("text_hash") and source["text_hash"] != english_final.text_hash:
        return True, "the English text it was re-told from has changed"
    return False, None


def case_state(db: Session, case_id: int, with_text: bool = False) -> dict:
    drafts = (db.query(StoryVersion).filter(StoryVersion.case_id == case_id, StoryVersion.kind == DRAFT)
              .order_by(StoryVersion.id).all())
    languages = ai_config.multilingual.production_languages
    retired = retired_method(db, case_id)
    if not drafts:
        return {"draft": None, "retired_method": retired,
                "languages": [{"language": lg, "stages": {}} for lg in languages],
                "ledger": ledger_table(db, case_id), "cost_total_eur": 0.0}
    draft = drafts[-1]
    english = ai_config.language_pipeline.draft_language
    approved_draft = next((d for d in reversed(drafts) if d.approved_at is not None), None)
    rows = (db.query(StoryVersion).filter(
        StoryVersion.case_id == case_id, StoryVersion.kind.in_(STAGE_KINDS)).order_by(StoryVersion.id).all())
    english_final = next((r for r in reversed(rows) if r.kind == FINAL and r.language == english
                          and r.approved_at is not None and r.status == "ready"), None)
    blocks = []
    for language in languages:
        mine = [r for r in rows if r.language == language]
        final = _latest(mine, FINAL, language)
        links = pipeline_of(final) if final is not None else {}
        by_id = {r.id: r for r in mine}
        rewrite = by_id.get(links.get("rewrite_id")) if final is not None else None
        inspection = by_id.get(links.get("parent_id")) if final is not None else None
        review = _check_row(mine, REVIEW, final)
        fidelity = _check_row(mine, FIDELITY, final)
        stages = {}
        for kind, row in ((REWRITE, rewrite), (INSPECTION, inspection), (FINAL, final),
                          (REVIEW, review), (FIDELITY, fidelity)):
            if row is not None:
                stages[kind] = stage_dict(row, with_text)
        stale, reason = _stale(final, language, english, english_final, approved_draft)
        notes_f = _notes(final) if final else {}
        block = {"language": language, "stages": stages,
                 "direction": ai_config.multilingual.language_catalog[language].direction,
                 "critique": _notes(inspection) if inspection else None,
                 "review": _notes(review) if review else None,
                 "fidelity": _notes(fidelity) if fidelity else None,
                 "gates": notes_f.get("quality_gates"),
                 "gate_history": notes_f.get("gate_history"),
                 "blocking_gates": ai_config.language_pipeline.blocking_gates_for(language),
                 "automatic_approval": ai_config.language_pipeline.automatic_approval_for(language),
                 "fact_report": notes_f.get("fact_report"),
                 "act_lengths": notes_f.get("act_lengths"),
                 "evidence_audit": notes_f.get("evidence_audit"),
                 "audit_fix": notes_f.get("audit_fix"),
                 "checks": notes_f.get("checks"),
                 "length_correction": notes_f.get("length_correction"),
                 "originality": (notes_f.get("quality_gates") or {}).get("reports", {}).get("originality"),
                 "source": ai_config.language_pipeline.language_stages[language].source,
                 "derived_from": links.get("derived_from") if final is not None else None,
                 "stale": stale, "stale_reason": reason,
                 "approved": bool(final and final.approved_at and not stale),
                 "open": bool(final and final.status == OPEN_ISSUES),
                 "final_version_id": final.id if final else None}
        blocks.append(block)
    ledger = ledger_table(db, case_id)
    notes = _notes(draft)
    return {"draft": {**stage_dict(draft, with_text), "evidence_audit": notes.get("evidence_audit"),
                      "draft_checks": notes.get("draft_checks"), "approval": notes.get("approval")},
            "retired_method": retired, "languages": blocks, "ledger": ledger,
            "cost_total_eur": round(sum(r["eur"] for r in ledger), 4),
            "calls_total": sum(r["calls"] for r in ledger)}
