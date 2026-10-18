"""Storing, editing, redoing and approving story plans (Part 5a).

Pure plan logic lives in app.services.story_plans; this module is the
database side and the one director call."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.agents.story import (
    StoryDirector,
    build_evidence_pack,
    choose_opening,
    editorial_context,
    evidence_fingerprint,
    normalize_act_budgets,
)
from app.core.ai_config import ai_config
from app.db.models import Case, Contradiction, Fact, Source, StoryPlan
from app.services import story_plans as SP
from app.services.tracking import stamp_run, track_run
from app.utils import utc_now


class StoryPlanError(ValueError):
    """The requested plan action cannot be applied safely."""


def _pack(db: Session, case: Case) -> dict:
    facts = db.query(Fact).filter(Fact.case_id == case.id).all()
    if not facts:
        raise StoryPlanError("No facts found. Run research first.")
    contradictions = db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
    sources = db.query(Source).filter(Source.case_id == case.id).all()
    return build_evidence_pack(facts, contradictions, sources)


def _hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


class StoryPlanService:
    def __init__(self, db: Session):
        self.db = db

    # -- reading -----------------------------------------------------------
    def latest(self, case_id: int, language: str | None = None) -> StoryPlan | None:
        q = self.db.query(StoryPlan).filter(
            StoryPlan.case_id == case_id, StoryPlan.status != "superseded")
        if language:
            q = q.filter(StoryPlan.language == language)
        return q.order_by(StoryPlan.id.desc()).first()

    def usable_approved(self, case: Case, language: str, target_minutes: float) -> StoryPlan | None:
        """The approved plan for this film, only while the evidence is the same
        one it was planned from."""
        plan = (self.db.query(StoryPlan).filter(
            StoryPlan.case_id == case.id, StoryPlan.status == "approved",
            StoryPlan.language == language)
            .order_by(StoryPlan.id.desc()).first())
        if plan is None or abs(plan.target_minutes - float(target_minutes)) > 1e-6:
            return None
        if plan.evidence_fingerprint != evidence_fingerprint(_pack(self.db, case)):
            return None
        return plan

    # -- creating ----------------------------------------------------------
    def _store(self, case: Case, plan: dict, pack: dict, language: str, target_minutes: float,
               origin: str, instruction: str | None, supersedes: StoryPlan | None,
               model: str | None) -> StoryPlan:
        wpm = ai_config.words_per_minute_for(language)
        plan = SP.enrich(plan, pack)
        report = SP.validate_plan(plan, pack, int(target_minutes * wpm))
        count = self.db.query(StoryPlan).filter(StoryPlan.case_id == case.id).count()
        if supersedes is not None:
            supersedes.status = "superseded"
        row = StoryPlan(
            case_id=case.id, version=count + 1, language=language,
            target_minutes=float(target_minutes), status="draft",
            plan_json=json.dumps(plan, ensure_ascii=False),
            validation_json=json.dumps(report, ensure_ascii=False),
            evidence_fingerprint=evidence_fingerprint(pack), instruction=instruction,
            origin=origin, supersedes_id=supersedes.id if supersedes else None,
            generation_model=model)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    async def create(self, case: Case, target_minutes: float, language: str,
                     instruction: str | None = None, follow_up: dict | None = None,
                     supersedes: StoryPlan | None = None) -> StoryPlan:
        pack = _pack(self.db, case)
        context = editorial_context(self.db, case, follow_up)
        if instruction:
            context["user_instruction"] = instruction
        role = "story_director"
        with track_run(self.db, case.id, "Story Plan") as run:
            plan, res = await StoryDirector().design(
                case, pack, int(target_minutes), language, role=role, context=context)
            stamp_run(run, res, role)
            if not isinstance(plan, dict):
                plan = {}
            plan["opening_strategy"], plan["opening_reason"] = choose_opening(plan, context)
            plan["case_status"] = context["case_status"]
            if follow_up:
                plan["follow_up"] = {k: follow_up.get(k) for k in (
                    "follow_up_id", "original_title", "episode_number", "original_published")}
            run.output_summary = f"beats={len(plan.get('acts', []))}"
        normalize_act_budgets(
            plan, int(target_minutes * ai_config.words_per_minute_for(language)))
        return self._store(case, plan, pack, language, target_minutes,
                           "redo" if supersedes else "director", instruction, supersedes,
                           getattr(res, "model", None))

    # -- editing -----------------------------------------------------------
    PLAN_LEVEL_KEYS = ("central_question", "ending_strategy", "reveal_map", "open_loops", "human_threads")

    def preview(self, plan: StoryPlan, action: str, instruction: str | None = None,
                beats: list[dict] | None = None, changes: dict | None = None) -> dict:
        """The exact scope of a redo or an edit, shown before it is applied."""
        if action not in ("redo", "edit"):
            raise StoryPlanError("action must be redo or edit")
        if plan.status == "superseded":
            raise StoryPlanError("this plan was already replaced")
        from app.db.models import StoryVersion

        written = [v.id for v in self.db.query(StoryVersion).filter(
            StoryVersion.story_plan_id == plan.id)]
        return {
            "plan_id": plan.id, "version": plan.version, "action": action,
            "replaces_status": plan.status, "approval_is_lost": plan.status == "approved",
            "masters_written_from_this_plan": written,
            "director_calls": 1 if action == "redo" else 0,
            "changes": [b.get("id") for b in beats or []] if action == "edit" else None,
            "plan_changes": sorted(changes or {}) if action == "edit" else None,
            "token": _hash(plan.id, plan.plan_json, action, instruction, beats, changes),
        }

    def edit(self, plan: StoryPlan, beats: list[dict], token: str, changes: dict | None = None) -> StoryPlan:
        """Edit acts (title, purpose, key_beats, target_words, evidence_ids, do_not_reveal, open_loops,
        resolved_loops) and, with `changes`, the plan-level fields in PLAN_LEVEL_KEYS. The result is a new
        version of the plan (origin "edit"); the old one stays as history."""
        case = self.db.get(Case, plan.case_id)
        if self.preview(plan, "edit", beats=beats, changes=changes)["token"] != token:
            raise StoryPlanError("the plan changed since the preview; preview again")
        unknown = sorted(set(changes or {}) - set(self.PLAN_LEVEL_KEYS))
        if unknown:
            raise StoryPlanError(f"cannot edit plan fields {unknown}")
        data = json.loads(plan.plan_json)
        by_id = {str(a.get("id")): a for a in data.get("acts") or []}
        for patch in beats:
            act = by_id.get(str(patch.get("id")))
            if act is None:
                raise StoryPlanError(f"unknown beat {patch.get('id')!r}")
            for key in ("title", "purpose", "key_beats", "target_words", "evidence_ids",
                        "do_not_reveal", "open_loops", "resolved_loops"):
                if key in patch:
                    act[key] = patch[key]
        data.update(changes or {})
        data.pop("host_slots", None)
        return self._store(case, data, _pack(self.db, case), plan.language, plan.target_minutes,
                           "edit", None, plan, plan.generation_model)

    async def redo(self, plan: StoryPlan, instruction: str, token: str) -> StoryPlan:
        case = self.db.get(Case, plan.case_id)
        if self.preview(plan, "redo", instruction=instruction)["token"] != token:
            raise StoryPlanError("the plan changed since the preview; preview again")
        return await self.create(case, plan.target_minutes, plan.language,
                                 instruction=instruction, supersedes=plan)

    # -- approving ---------------------------------------------------------
    def approve(self, plan: StoryPlan, by: str) -> StoryPlan:
        if plan.status != "draft":
            raise StoryPlanError(f"a {plan.status} plan cannot be approved")
        if json.loads(plan.validation_json).get("status") == "invalid":
            raise StoryPlanError("an invalid plan cannot be approved")
        plan.status, plan.approved_by, plan.approved_at = "approved", by, utc_now()
        self.db.commit()
        return plan

    async def ensure_approved(self, case: Case, target_minutes: float, language: str,
                              follow_up: dict | None = None,
                              instruction: str | None = None) -> StoryPlan:
        """The plan a pipeline run writes to: the approved one, else (when the
        config allows a one-button run to plan by itself) a new plan approved as
        "automatic"."""
        plan = None if instruction else self.usable_approved(case, language, target_minutes)
        if plan is not None:
            return plan
        if not ai_config.story_plan.automatic_approval_in_pipeline:
            raise StoryPlanError("an approved story plan is required before the writer runs")
        draft = self.latest(case.id, language)
        if (draft is None or draft.status != "draft"
                or abs(draft.target_minutes - float(target_minutes)) > 1e-6):
            draft = await self.create(case, target_minutes, language, follow_up=follow_up,
                                      instruction=instruction,
                                      supersedes=draft if draft and draft.status == "draft" else None)
        return self.approve(draft, "automatic")


def plan_dict(plan: StoryPlan) -> dict:
    data = json.loads(plan.plan_json or "{}")
    return {
        "id": plan.id, "case_id": plan.case_id, "version": plan.version,
        "language": plan.language, "target_minutes": plan.target_minutes,
        "status": plan.status, "origin": plan.origin, "instruction": plan.instruction,
        "approved_by": plan.approved_by,
        "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
        "title": data.get("title"), "central_question": data.get("central_question"),
        "opening_strategy": data.get("opening_strategy"),
        "beats": SP.beats_view(data), "host_slots": data.get("host_slots") or [],
        "validation": json.loads(plan.validation_json or "{}"),
        "evidence_fingerprint": plan.evidence_fingerprint,
    }
