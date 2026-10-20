"""Stage outputs as story version rows, and what a resumed run finds in them.

kind is the stage: draft (shared), rewrite, inspection, final (one set per language).
A row records the draft it derives from, the plan and evidence it was written to, its
model and prompt hash, the reasoning effort, the narration minutes and its actual cost."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents.story import _norm_ws, clean_sections, is_structured
from app.core.ai_config import ai_config
from app.core.prompts import prompt_sha256
from app.db.models import ProviderCall, StoryPlan, StoryVersion
from app.language_pipeline.lengths import minutes_of
from app.language_pipeline.materials import SharedMaterial
from app.language_pipeline.stages import (
    AUDIT_FIX_STAGE,
    DRAFT_STAGE,
    FIDELITY_STAGE,
    FINAL_STAGE,
    INSPECT_STAGE,
    LENGTH_CORRECTION_STAGE,
    REVIEW_STAGE,
    REWRITE_STAGE,
    StageOutput,
    stage_name,
)
from app.utils import text_hash, utc_now

DRAFT, REWRITE, INSPECTION, FINAL = "draft", "rewrite", "inspection", "final"
# The checks of a delivered final text: the inspector's review and the meaning check against the English.
REVIEW, FIDELITY = "review", "fidelity"
STAGE_KINDS = (DRAFT, REWRITE, INSPECTION, FINAL, REVIEW, FIDELITY)
LENGTH_CORRECTION = "length_correction"
AUDIT_FIX = "audit_fix"
LEDGER_STAGE = {DRAFT: DRAFT_STAGE, REWRITE: REWRITE_STAGE, INSPECTION: INSPECT_STAGE,
                FINAL: FINAL_STAGE, LENGTH_CORRECTION: LENGTH_CORRECTION_STAGE, REVIEW: REVIEW_STAGE,
                FIDELITY: FIDELITY_STAGE, AUDIT_FIX: AUDIT_FIX_STAGE}
STAGE_STATUS = "stage_output"
# The retired fact-check loop (docs/english_pipeline_checklist.md): its rows stay as history.
RETIRED = "retired"
RETIRED_KINDS = ("master_inspect", "master_revise", "english_master")


def structure_of(version: StoryVersion) -> dict:
    try:
        return json.loads(version.narrative_structure or "{}")
    except (ValueError, TypeError):
        return {}


def draft_sections(scenes: list[dict]) -> list[dict]:
    """The act sections of a draft: each act's scenes, in order, one paragraph break apart."""
    by_act: dict[str, list[str]] = {}
    for scene in scenes:
        by_act.setdefault(scene["act"], []).append(scene["text"])
    return [{"id": act, "text": "\n\n".join(texts)} for act, texts in by_act.items()]


def pipeline_of(version: StoryVersion) -> dict:
    return structure_of(version).get("pipeline") or {}




def ledger_usage(db: Session, case_id: int, kind: str, language: str | None,
                 since: datetime) -> dict:
    stage = stage_name(LEDGER_STAGE[kind], None if kind in (DRAFT, AUDIT_FIX) else language)
    row = db.query(
        func.count(ProviderCall.id).filter(ProviderCall.host == "budget-ledger"),
        func.coalesce(func.sum(ProviderCall.tokens_in), 0),
        func.coalesce(func.sum(ProviderCall.tokens_cached), 0),
        func.coalesce(func.sum(ProviderCall.tokens_reasoning), 0),
        func.coalesce(func.sum(ProviderCall.tokens_out), 0),
        func.coalesce(func.sum(ProviderCall.cost_eur), 0.0),
    ).filter(ProviderCall.case_id == case_id, ProviderCall.stage == stage,
             ProviderCall.is_test_data.is_(False), ProviderCall.created_at >= since).one()
    return {"calls": int(row[0]), "tokens_in": int(row[1]), "tokens_cached": int(row[2]),
            "tokens_reasoning": int(row[3]), "tokens_out": int(row[4]), "cost_eur": float(row[5])}


def _assignments(plan_json: str) -> list[tuple]:
    """What a draft is written to in a plan: each act's id, evidence and withheld evidence."""
    acts = json.loads(plan_json or "{}").get("acts") or []
    return [(str(a.get("id")), tuple(a.get("evidence_ids") or []), tuple(a.get("do_not_reveal") or []))
            for a in acts]


class StageStore:
    def __init__(self, db: Session):
        self.db = db

    def plan_still_fits(self, written_plan_id: int | None, current_plan_id: int) -> bool:
        """A draft written to an earlier plan is still the draft of the current plan when the current plan
        is that plan edited (titles, purposes, beats, weights) and the edits left every act's evidence and
        withheld evidence as they were. A plan redone by the director is another plan."""
        plan = self.db.get(StoryPlan, current_plan_id)
        while plan is not None and plan.id != written_plan_id:
            if plan.origin != "edit" or plan.supersedes_id is None:
                return False
            earlier = self.db.get(StoryPlan, plan.supersedes_id)
            if earlier is None or _assignments(earlier.plan_json) != _assignments(plan.plan_json):
                return False
            plan = earlier
        return plan is not None

    # -- writing ---------------------------------------------------------------
    def save(self, *, case_id: int, kind: str, language: str, output: StageOutput,
             material: SharedMaterial, started: datetime, requested_minutes: float,
             base: StoryVersion | None, text: str | None = None,
             critic_notes: dict | None = None, status: str = STAGE_STATUS,
             parents: dict | None = None, extra: dict | None = None,
             similarity: tuple[float | None, str] = (None, "not_evaluated"),
             ledger_kind: str | None = None) -> StoryVersion:
        text = text if text is not None else output.text
        stored = clean_sections(output.sections)
        if _norm_ws("\n\n".join(s["text"] for s in stored)) != _norm_ws(text):
            stored = [{"id": "full", "text": text}]
        usage = ledger_usage(self.db, case_id, ledger_kind or kind, language, started)
        plan = material.plan
        structure = {
            "title": plan.get("title"), "central_question": plan.get("central_question"),
            "acts": plan.get("acts"), "opening_strategy": plan.get("opening_strategy"),
            "case_status": plan.get("case_status"),
            "sections": [{"id": s["id"], "words": len(s["text"].split()), "text": s["text"]}
                         for s in stored],
            "structured": is_structured(stored),
            "evidence_fingerprint": material.fingerprint,
            "pipeline": {
                "stage": kind, "scope": "shared" if kind == DRAFT else "language",
                "requested_minutes": requested_minutes, "plan_id": material.plan_id,
                "derived_from": ({"id": base.id, "kind": base.kind, "language": base.language,
                                  "prompt_sha256": base.writer_prompt_sha256,
                                  "text_hash": base.text_hash} if base else None),
                "usage": usage, **(parents or {}), **(extra or {})},
        }
        count = self.db.query(func.coalesce(func.max(StoryVersion.version), 0)).filter(
            StoryVersion.case_id == case_id).scalar() or 0
        result = output.result
        row = StoryVersion(
            case_id=case_id, version=count + 1, kind=kind, language=language,
            narrative_angle=json.dumps(plan, ensure_ascii=False),
            narrative_structure=json.dumps(structure, ensure_ascii=False),
            story_text=text, text_hash=text_hash(text), status=status,
            story_plan_id=material.plan_id, similarity_score=similarity[0],
            similarity_status=similarity[1],
            critic_notes=json.dumps(critic_notes, ensure_ascii=False) if critic_notes else None,
            generation_provider=result.provider, generation_model=result.model,
            writer_prompt_name=output.prompt_name, writer_prompt_sha256=output.prompt_sha256,
            draft_version_id=base.id if base else None,
            reasoning_effort=result.reasoning_effort, minutes=minutes_of(text, language),
            cost_eur=round(usage["cost_eur"], 6))
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update_draft(self, row: StoryVersion, scenes: list[dict], output: StageOutput | None,
                     started: datetime, status: str, problems: list[str]) -> StoryVersion:
        """The draft grows scene by scene: the same row takes each new scene (it is the one shared
        draft in the making). It becomes `ready` when it is complete and long enough.
        `scenes` are {"act", "index", "text"} in story order."""
        sections = draft_sections(scenes)
        text = "\n\n".join(s["text"] for s in sections)
        structure = structure_of(row)
        structure["sections"] = [{"id": s["id"], "words": len(s["text"].split()), "text": s["text"]}
                                 for s in sections]
        usage = ledger_usage(self.db, row.case_id, DRAFT, None, started)
        structure["pipeline"]["usage"] = usage
        structure["pipeline"]["scenes"] = scenes
        row.narrative_structure = json.dumps(structure, ensure_ascii=False)
        row.story_text, row.text_hash = text, text_hash(text)
        row.minutes, row.cost_eur = minutes_of(text, row.language), round(usage["cost_eur"], 6)
        row.status = status
        row.critic_notes = json.dumps({"problems": problems}, ensure_ascii=False) if problems else None
        if output is not None:
            row.generation_model = output.result.model
        self.db.commit()
        return row

    def approve_automatically(self, final: StoryVersion, stages: dict[str, StoryVersion]) -> None:
        """The system approves a final text whose blocking gates passed: approved_by "automatic",
        the time, the stage versions and their cost in the notes (the same mechanism the story plan
        and the draft use)."""
        now = utc_now()
        notes = json.loads(final.critic_notes or "{}")
        versions = {**{k: v.id for k, v in stages.items()}, "final": final.id}
        cost = round(sum(v.cost_eur or 0.0 for v in (*stages.values(), final)), 6)
        notes["approval"] = {"by": "automatic", "at": now.isoformat(), "stage_version_ids": versions,
                             "cost_eur": cost}
        final.approved_by, final.approved_at = "automatic", now
        final.critic_notes = json.dumps(notes, ensure_ascii=False)
        self.db.commit()

    def approve_draft_automatically(self, draft: StoryVersion) -> StoryVersion:
        """The system approves the English draft, the source every language reads: approved_by
        "automatic", the time, the draft version and its cost. The evidence audit does not gate it.
        Approving again changes nothing."""
        if draft.approved_at is not None:
            return draft
        now = utc_now()
        notes = json.loads(draft.critic_notes or "{}")
        notes["approval"] = {"by": "automatic", "at": now.isoformat(), "draft_version_id": draft.id,
                             "cost_eur": draft.cost_eur,
                             "evidence_audit": (notes.get("evidence_audit") or {}).get("status")}
        draft.approved_by, draft.approved_at = "automatic", now
        draft.critic_notes = json.dumps(notes, ensure_ascii=False)
        self.db.commit()
        return draft

    def retire(self, row: StoryVersion, reason: str) -> StoryVersion:
        """Set a row of a method that is no longer used aside: it stays as history, is never the
        source of a stage, and records why and when (and the status it had)."""
        notes = json.loads(row.critic_notes or "{}")
        notes["retired"] = {"reason": reason, "at": utc_now().isoformat(), "was": row.status}
        row.status = RETIRED
        row.critic_notes = json.dumps(notes, ensure_ascii=False)
        self.db.commit()
        return row

    def retire_loop_rows(self, case_id: int, reason: str) -> list[StoryVersion]:
        """The rows of the retired fact-check loop (inspections, revisions, the stopped master)."""
        rows = (self.db.query(StoryVersion).filter(
            StoryVersion.case_id == case_id, StoryVersion.kind.in_(RETIRED_KINDS),
            StoryVersion.status != RETIRED).order_by(StoryVersion.id).all())
        return [self.retire(row, reason) for row in rows]

    def retire_attempt(self, case_id: int, language: str, base_id: int, reason: str) -> list[StoryVersion]:
        """Set aside the newest chain of a language that was started from this base and did not end
        approved: its rewrite, inspection, finals and checks stay as history and are no longer found as the
        language's rows, so the next run writes a new chain. An approved chain is never set aside; an older
        chain (of an earlier plan) is not touched."""
        rows = (self.db.query(StoryVersion).filter(
            StoryVersion.case_id == case_id, StoryVersion.language == language,
            StoryVersion.status != RETIRED).order_by(StoryVersion.id).all())
        rewrites = [r for r in rows if r.kind == REWRITE and r.draft_version_id == base_id]
        if not rewrites:
            return []
        rewrite = rewrites[-1]
        finals = [r for r in rows if r.kind == FINAL and pipeline_of(r).get("rewrite_id") == rewrite.id]
        final_ids = {r.id for r in finals}
        chain = [rewrite] + [r for r in rows if r.kind == INSPECTION and pipeline_of(r).get("parent_id") == rewrite.id] \
            + finals + [r for r in rows if r.kind in (REVIEW, FIDELITY)
                        and pipeline_of(r).get("checks_version_id") in final_ids]
        if any(r.approved_at is not None for r in chain):
            raise ValueError("an approved chain is not set aside; the base must change first")
        return [self.retire(row, reason) for row in sorted(chain, key=lambda r: r.id)]

    def mark_best_final(self, final: StoryVersion) -> None:
        if final.status != "ready":
            return
        self.db.query(StoryVersion).filter(
            StoryVersion.case_id == final.case_id, StoryVersion.kind == FINAL,
            StoryVersion.language == final.language, StoryVersion.id != final.id,
        ).update({"is_best": False})
        final.is_best = True
        self.db.commit()

    # -- reading / resuming ------------------------------------------------------
    def _latest(self, case_id: int, kind: str, language: str | None = None,
                base_id: int | None = None) -> list[StoryVersion]:
        q = self.db.query(StoryVersion).filter(
            StoryVersion.case_id == case_id, StoryVersion.kind == kind, StoryVersion.status != RETIRED)
        if language:
            q = q.filter(StoryVersion.language == language)
        if base_id is not None:
            q = q.filter(StoryVersion.draft_version_id == base_id)
        return q.order_by(StoryVersion.id.desc()).all()

    def check_for(self, kind: str, subject: StoryVersion) -> StoryVersion | None:
        """The newest check row (review or fidelity) made on exactly this text version."""
        for row in self._latest(subject.case_id, kind, subject.language):
            if pipeline_of(row).get("checks_version_id") == subject.id:
                return row
        return None

    def draft_for(self, material: SharedMaterial, status: str = "ready") -> StoryVersion | None:
        """The newest shared draft of this plan and this evidence with that status: "ready"
        (usable) or "needs_revision" (written, rejected: the next attempt starts from it). A draft
        written with another draft prompt is another draft: it is history, not a source."""
        prompt_sha = prompt_sha256(ai_config.prompt_of("language.draft"))
        for row in self._latest(material.case_id, DRAFT):
            p = pipeline_of(row)
            if row.status == status and self.plan_still_fits(p.get("plan_id"), material.plan_id) and \
                    structure_of(row).get("evidence_fingerprint") == material.fingerprint and \
                    row.writer_prompt_sha256 == prompt_sha:
                return row
        return None

    def approved_english_final(self, material: SharedMaterial) -> StoryVersion | None:
        """The English final text every other language is re-told from: the newest approved English
        final of this plan and evidence. Only a finished, approved text is a base; an open or
        unapproved English text is none."""
        language = ai_config.language_pipeline.draft_language
        for row in self._latest(material.case_id, FINAL, language):
            if row.approved_at is not None and row.status == "ready" and \
                    pipeline_of(row).get("plan_id") == material.plan_id and \
                    structure_of(row).get("evidence_fingerprint") == material.fingerprint:
                return row
        return None

    def stage_for(self, material: SharedMaterial, kind: str, language: str, base: StoryVersion,
                  requested_minutes: float, parent: StoryVersion | None = None) -> StoryVersion | None:
        """The newest row of a language's stage that derives from this base (the English master;
        the draft for the draft stage), plan, evidence, requested length and (when it has one)
        the stage before it."""
        for row in self._latest(material.case_id, kind, language, base.id):
            p = pipeline_of(row)
            if p.get("plan_id") != material.plan_id or \
                    structure_of(row).get("evidence_fingerprint") != material.fingerprint or \
                    float(p.get("requested_minutes") or 0) != float(requested_minutes):
                continue
            if parent is not None and p.get("parent_id") != parent.id:
                continue
            return row
        return None
