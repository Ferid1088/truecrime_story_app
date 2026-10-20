"""The approved final texts of a case as UTF-8 files, one per language, each with a report of where it
comes from and what was checked. The text file holds the narration only (the act markers are turned
into blank-line separated acts); the report names the English source version, the words, the estimated
minutes and the results of the checks that were actually made. Minutes are words divided by the
configured speaking rate: an estimate, marked as one."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.story import strip_section_markers
from app.core.ai_config import ai_config
from app.db.models import Case, StoryVersion
from app.language_pipeline.lengths import minutes_of
from app.language_pipeline.state import case_state


def narration(text: str) -> str:
    return strip_section_markers(text).strip() + "\n"


def _checks(block: dict, final: StoryVersion) -> dict:
    notes = json.loads(final.critic_notes or "{}")
    gates = notes.get("quality_gates") or {}
    review = block.get("review") or {}
    fidelity = block.get("fidelity")
    audit = notes.get("evidence_audit")
    fix = notes.get("audit_fix")
    return {
        "blocking_gates": {"passed": gates.get("pass"), "failures": gates.get("failures"),
                           "gates": ai_config.language_pipeline.blocking_gates_for(final.language)},
        "review_of_the_delivered_text": {"verdict": review.get("verdict"), "items": len(review.get("items") or []),
                                         "by_severity": _counts(review.get("items") or [])},
        "meaning_check_against_the_english": (
            None if fidelity is None else {"verdict": fidelity.get("verdict"), "items": len(fidelity.get("items") or []),
                                           "by_severity": _counts(fidelity.get("items") or [])}),
        "evidence_audit_of_the_delivered_text": (
            None if audit is None else {"status": audit.get("status"), "items": len(audit.get("items") or []),
                                        "by_severity": _counts(audit.get("items") or [])}),
        "audit_findings_checked_and_corrected": (
            None if fix is None else {"decisions": fix.get("counts"), "edits_applied": fix.get("applied"),
                                      "edits_skipped": fix.get("skipped"), "acts_changed": fix.get("acts_changed")}),
        "open_issues": (notes.get("checks") or {}).get("open_issues"),
        "length_correction": (notes.get("length_correction") or {}).get("status"),
        "removed_lead_in": notes.get("removed_lead_in"),
    }


def _counts(items: list[dict]) -> dict:
    out: dict[str, int] = {}
    for item in items:
        out[item["severity"]] = out.get(item["severity"], 0) + 1
    return out


def export_case(db: Session, case_id: int, directory: Path) -> list[dict]:
    """Write the approved final of every language that has one (and the report beside it). A language
    without an approved, current final is listed with its status and not written."""
    case = db.get(Case, case_id)
    assert case is not None
    state = case_state(db, case_id)
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for block in state["languages"]:
        language = block["language"]
        final = db.get(StoryVersion, block["final_version_id"]) if block["final_version_id"] else None
        entry = {"language": language, "final_version_id": block["final_version_id"],
                 "approved": block["approved"], "open": block["open"], "stale": block["stale"], "file": None}
        if final is None or not block["approved"]:
            entries.append(entry)
            continue
        words = len(narration(final.story_text).split())
        name = f"case{case_id}_{case.slug}_{language}_final_v{final.id}.txt"
        (directory / name).write_text(narration(final.story_text), encoding="utf-8")
        derived = block["derived_from"] or {}
        entry.update({
            "file": name, "words": words, "minutes_estimated": minutes_of(final.story_text, language),
            "minutes_note": f"estimated: words / {ai_config.language_pipeline.words_per_minute_for(language)} "
                            "words per minute (configured rate, not a measured duration)",
            "model": final.generation_model, "reasoning_effort": final.reasoning_effort,
            "source": {"version_id": derived.get("id"), "kind": derived.get("kind"), "language": derived.get("language"),
                       "text_hash": derived.get("text_hash")},
            "approved_by": final.approved_by, "approved_at": final.approved_at.isoformat() if final.approved_at else None,
            "stage_versions": {k: v["id"] for k, v in block["stages"].items()},
            "checks": _checks(block, final)})
        entries.append(entry)
    (directory / f"case{case_id}_{case.slug}_final_texts_report.json").write_text(
        json.dumps({"case_id": case_id, "title": case.canonical_title, "texts": entries}, ensure_ascii=False,
                   indent=2, default=str), encoding="utf-8")
    return entries
