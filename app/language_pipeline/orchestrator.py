"""Runs the pipeline of a case: the shared English draft once (written by Sonnet from the plan and
all the evidence), the one-pass evidence audit of it (a report), the draft's automatic approval
record, then stages 2-4 per language from the approved draft, in parallel and independently. Each
language makes exactly three model calls (rewrite, inspection, final); a stage that already has its
row is not run again; a failed language leaves the others alone."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agents.story import strip_section_markers
from app.core.ai_config import ai_config
from app.core.prompts import prompt_sha256
from app.db.base import SessionLocal
from app.db.models import Case, StoryVersion
from app.language_pipeline import draft_checks, fact_report, ledger, script_hygiene
from app.language_pipeline.assign import extra_evidence_by_act, related_to_contradictions
from app.language_pipeline.audit_fix import AuditFix
from app.language_pipeline.evidence_audit import EvidenceAudit
from app.language_pipeline.gates import FinalGates
from app.language_pipeline.length_correction import (
    REJECTED,
    SKIPPED,
    STARTED,
    STORED,
    LengthCorrection,
)
from app.language_pipeline.lengths import (
    act_length_report,
    check_requested_minutes,
    draft_range,
    final_range,
    LengthRange,
)
from app.language_pipeline.materials import SharedMaterial, load_material
from app.language_pipeline.review import (
    OPEN_ISSUES,
    blocking_items,
    critique_from,
    open_issues,
    revision_items,
)
from app.language_pipeline.review import counts as review_counts
from app.language_pipeline.stages import (
    AUDIT_FIX_STAGE,
    DRAFT_REVIEW_STAGE,
    FIDELITY_STAGE,
    REVIEW_STAGE,
    REWRITE_STAGE,
    LanguageStages,
    StageFailed,
    StageOutput,
    check_draft,
    marked,
)
from app.language_pipeline.store import (
    AUDIT_FIX,
    DRAFT,
    FIDELITY,
    FINAL,
    INSPECTION,
    LENGTH_CORRECTION,
    REVIEW,
    REWRITE,
    StageStore,
    draft_sections,
    pipeline_of,
    structure_of,
)
from app.services import writer_metrics
from app.services.budget import BudgetStopped
from app.utils import utc_now

log = logging.getLogger(__name__)
Progress = Callable[[dict], None]

@dataclass
class LanguageOutcome:
    language: str
    status: str = "pending"          # done | open | failed | budget_stopped
    stage: str | None = None         # the last stage reached
    error: str | None = None
    final_id: int | None = None
    calls: int = 0
    reused: list[str] = field(default_factory=list)
    open_issues: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"language": self.language, "status": self.status, "stage": self.stage,
                "error": self.error, "final_version_id": self.final_id, "reused": self.reused,
                "open_issues": len(self.open_issues)}


def _sections_of(version: StoryVersion) -> list[dict]:
    return [{"id": s["id"], "text": s["text"]}
            for s in structure_of(version).get("sections") or []]


def _source_text(base: StoryVersion) -> str:
    """The text a language re-tells, in act markers (its english_master): the English draft for
    English, the approved English final for every other language."""
    return marked(_sections_of(base)) if base.kind == DRAFT else base.story_text


def _draft_review_items(base: StoryVersion) -> list[dict] | None:
    """The items of the draft's editorial review, for the language told from the draft; None when the
    base is not the draft or the draft has no review that ran."""
    if base.kind != DRAFT:
        return None
    review = json.loads(base.critic_notes or "{}").get("draft_review") or {}
    return review["items"] if review.get("status") == "ok" else None


def _plain(base: StoryVersion) -> str:
    """The base text as the number and name report reads it: without act markers."""
    return strip_section_markers(base.story_text)


class LanguagePipeline:
    def __init__(self, gen=None, embedder=None, progress: Progress | None = None):
        self.stages = LanguageStages(gen)
        self.audit = EvidenceAudit(self.stages)
        self.fixer = AuditFix(self.stages, embedder)
        self.gates = FinalGates(embedder)
        self.coverage = self.gates.coverage
        self.embedder = embedder
        self.progress = progress or (lambda event: None)

    def _emit(self, **event) -> None:
        self.progress(event)

    # ---------------------------------------------------------------- the shared draft
    async def ensure_draft(self, db: Session, case: Case, material: SharedMaterial,
                           tone: str, requested_minutes: float) -> tuple[StoryVersion, bool]:
        """The shared draft of this plan and evidence, written scene by scene (one call per key
        beat of each act) and stored once; never written again because a language failed. A draft
        that stopped half way (an error, a restart) goes on from the scenes it has.
        Returns (draft, reused)."""
        store = StageStore(db)
        existing = store.draft_for(material)
        if existing is not None:
            return existing, True
        row = store.draft_for(material, status="needs_revision")
        have = {(s["act"], s["index"]): s["text"]
                for s in (pipeline_of(row).get("scenes") or [])} if row is not None else {}
        extras = await extra_evidence_by_act(material, self.embedder)
        packaging = ai_config.language_pipeline.draft_packaging
        related = (await related_to_contradictions(material, self.embedder, packaging.related_facts_per_contradiction)
                   if packaging.mode == "act" else {})
        started = utc_now()
        scenes: list[dict] = []
        output = last = None
        for act in material.acts:
            act_id = str(act["id"])
            prefix = material.act_package(act, extras[act_id], related) if packaging.mode == "act" else None
            for index, count in self.stages.scene_groups(act, packaging.scenes_per_call):
                target = self.stages.scene_target_words(material, act, count)
                text = have.get((act_id, index))
                if text is None:
                    text, output = await self._write_scene(
                        db, case, material, tone, act, index, scenes, extras, count, prefix)
                scenes.append({"act": act_id, "index": index, "text": text})
                if output is not None:
                    row = self._store_draft(store, row, case, material, scenes, output, started,
                                            requested_minutes)
                    last, output = output, None
                    self._emit(event="scene_done", stage=DRAFT, act=act_id, scene=index + 1,
                               words=len(text.split()), target=target, version_id=row.id)
        problems = check_draft(draft_sections(scenes), requested_minutes)
        row = store.update_draft(row, scenes, last, started,
                                 "needs_revision" if problems else "ready", problems)
        if problems:
            raise StageFailed("draft", "; ".join(problems))
        self._emit(event="stage_done", stage=DRAFT, language=None, version_id=row.id,
                   words=len(row.story_text.split()), cost_eur=row.cost_eur)
        return row, False

    async def _write_scene(self, db, case, material, tone, act, index, scenes, extras, count=1, prefix=None):
        """One scene (one key beat), checked at once: a scene far over its budget or mostly retelling
        what the draft already says is written once more with that reason, and the second answer is
        taken as it comes. Both attempts are in the ledger."""
        act_id = str(act["id"])
        story_so_far = "\n\n".join(s["text"] for s in scenes)
        told = ledger.told_so_far(material, scenes, extras, act_id)
        self._emit(event="stage_started", stage=DRAFT, language=None, act=act_id, scene=index + 1)
        output = await self.stages.draft_scene(
            db, case, material, tone, act, index, story_so_far, told, extras[act_id], count=count, prefix=prefix)
        problem = self._scene_problem(material, act, output.text, story_so_far, count)
        if problem:
            self._emit(event="scene_regenerated", stage=DRAFT, act=act_id, scene=index + 1,
                       words=len(output.text.split()), reason=problem)
            output = await self.stages.draft_scene(
                db, case, material, tone, act, index, story_so_far, told, extras[act_id],
                correction=problem, count=count, prefix=prefix)
        return output.text, output

    def _scene_problem(self, material: SharedMaterial, act: dict, text: str, story_so_far: str,
                       count: int = 1) -> str | None:
        """Why a scene is written again (None when it stands): too long, or retelling."""
        check = ai_config.language_pipeline.draft_scene_check
        budget = self.stages.scene_target_words(material, act, count)
        words = len(text.split())
        overlap = writer_metrics.trigram_overlap(text, story_so_far)
        reasons = []
        if words > check.max_words_ratio * budget:
            reasons.append(f"The previous attempt had {words} words against a budget of {budget}; "
                           "write the scene again within the budget.")
        if overlap > check.max_trigram_overlap:
            reasons.append(f"{round(overlap * 100)} percent of the previous attempt's wording already stands "
                           "earlier in the story; write only what told_so_far does not yet contain, "
                           "and stop when the beat is told.")
        return " ".join(reasons) or None

    @staticmethod
    def _store_draft(store: StageStore, row: StoryVersion | None, case: Case,
                     material: SharedMaterial, scenes: list[dict], output: StageOutput,
                     started, requested_minutes: float) -> StoryVersion:
        if row is not None:
            return store.update_draft(row, scenes, output, started, "needs_revision", [])
        sections = draft_sections(scenes)
        whole = StageOutput(
            stage=output.stage, language=output.language,
            text="\n\n".join(s["text"] for s in sections), sections=sections, markers_ok=True,
            result=output.result, prompt_name=output.prompt_name,
            prompt_sha256=output.prompt_sha256)
        row = store.save(
            case_id=case.id, kind=DRAFT, language=output.language, output=whole, material=material,
            started=started, requested_minutes=requested_minutes, base=None,
            status="needs_revision")
        return store.update_draft(row, scenes, output, started, "needs_revision", [])

    # ------------------------------------------------------------ the evidence audit
    async def ensure_audit(self, db: Session, case: Case, material: SharedMaterial,
                           draft: StoryVersion) -> dict | None:
        """The one-pass evidence audit of the draft, once, with its two deterministic partners
        (numbers and names, coverage). All three are reports stored with the draft: they block
        nothing, repair nothing and change no text. An audit that fails is recorded as failed and
        the run goes on; only the budget stop ends it."""
        cfg = ai_config.language_pipeline.evidence_audit
        notes = json.loads(draft.critic_notes or "{}")
        if "draft_checks" not in notes:
            sections = _sections_of(draft)
            notes["draft_checks"] = {
                "numbers_and_names": draft_checks.numbers_and_names(material, draft.story_text),
                "coverage": await draft_checks.coverage(self.coverage, material, sections, cfg.key_fact_count)}
            draft.critic_notes = json.dumps(notes, ensure_ascii=False)
            db.commit()
        if not cfg.enabled or (notes.get("evidence_audit") or {}).get("status") == "ok":
            return notes.get("evidence_audit")
        self._emit(event="stage_started", stage="evidence_audit", language=None)
        try:
            report = await self.audit.run(db, case, material, draft)
        except BudgetStopped:
            raise
        except Exception as exc:  # noqa: BLE001 - the audit never blocks the pipeline
            log.exception("the evidence audit failed")
            report = {"status": "failed", "report_only": True, "error": f"{type(exc).__name__}: {exc}"[:500]}
            EvidenceAudit._store(db, draft, report)
        self._emit(event="stage_done", stage="evidence_audit", language=None, status=report["status"],
                   items=len(report.get("items") or []), cost_eur=(report.get("usage") or {}).get("cost_eur"))
        return report

    async def audit_final(self, case_id: int, language: str) -> dict:
        """The same one-pass evidence audit, on a language's current final text (report only,
        stored with that final version): what the final added or changed that the evidence does not
        carry. The draft's own audit is left as it is."""
        with SessionLocal() as db:
            case = db.get(Case, case_id)
            final = (db.query(StoryVersion).filter(
                StoryVersion.case_id == case_id, StoryVersion.kind == FINAL,
                StoryVersion.language == language).order_by(StoryVersion.id.desc()).first())
            if final is None:
                raise StageFailed("audit", f"there is no {language} final version to audit")
            material = await self._material(db, case)
            sections, ok = self.stages._sections(final.story_text, material)
            if not ok:
                raise StageFailed("audit", f"the {language} final does not keep the plan's act markers")
            return await self.audit.run(db, case, material, final, sections)

    # ------------------------------------------------------------ the draft's editorial review
    async def ensure_draft_review(self, db: Session, case: Case, material: SharedMaterial,
                                  draft: StoryVersion, requested_minutes: float) -> dict | None:
        """The English inspector on the draft, once, before any language is told from it: how the
        draft works as a listening text (selection, flow, repetition, paragraph endings), stored with
        the draft and handed to the English rewrite. The inspector is given the draft's own range (it
        is written long on purpose), never the final text's; length and marker findings (kind
        production) are left out, since the rewrite is held to its own range. A stored review is
        used again only when it was made with the same inspector prompt and range. A report: it
        changes no text, and a review that fails is recorded and the run goes on."""
        if not ai_config.language_pipeline.draft_review.enabled:
            return None
        english = ai_config.language_pipeline.draft_language
        span = draft_range(requested_minutes)
        made_with = {"prompt_sha256": prompt_sha256(ai_config.agents[
            ai_config.language_pipeline.agents_for(english).inspect].prompt),
            "allowed_word_range": {"min": span.words_min, "max": span.words_max}}
        stored = json.loads(draft.critic_notes or "{}").get("draft_review") or {}
        if stored.get("status") == "ok" and all(stored.get(k) == v for k, v in made_with.items()):
            return stored
        self._emit(event="stage_started", stage=DRAFT_REVIEW_STAGE, language=None)
        try:
            output = await self.stages.inspect(db, case, material, _source_text(draft), english, span,
                                               DRAFT_REVIEW_STAGE)
            critique = output.data
            report = {"status": "ok", "report_only": True, **made_with, "verdict": critique["verdict"],
                      "scores": critique["scores"], "summary": critique["summary"],
                      "items": [i for i in critique["items"] if i["kind"] != "production"]}
        except BudgetStopped:
            raise
        except Exception as exc:  # noqa: BLE001 - the review never blocks the pipeline
            log.exception("the draft review failed")
            report = {"status": "failed", "report_only": True, "error": f"{type(exc).__name__}: {exc}"[:500]}
        notes = json.loads(draft.critic_notes or "{}")
        notes["draft_review"] = report
        draft.critic_notes = json.dumps(notes, ensure_ascii=False)
        db.commit()
        self._emit(event="stage_done", stage=DRAFT_REVIEW_STAGE, language=None, status=report["status"],
                   items=len(report.get("items") or []))
        return report

    # ------------------------------------------------------------ the draft's approval
    def ensure_draft_approved(self, db: Session, draft: StoryVersion) -> StoryVersion:
        """The draft is the source of every language. Its approval is automatic and does not wait
        for the evidence audit (a report); the record is written here, through the store."""
        StageStore(db).approve_draft_automatically(draft)
        self._emit(event="draft_approved", by=draft.approved_by, version_id=draft.id)
        return draft

    # ------------------------------------------------------------ one language
    async def run_language(self, case_id: int, language: str, material: SharedMaterial,
                           base_id: int, requested_minutes: float,
                           on_rewrite_done: Callable[[], None] | None = None) -> LanguageOutcome:
        """Independent session, independent failure. `base_id` is the approved text the language is
        re-told from: the English draft for English, the approved English final for every other
        language. `on_rewrite_done` is called when stage 2 has its row (or the language stopped before
        it), once."""
        outcome = LanguageOutcome(language)
        with SessionLocal() as db:
            case = db.get(Case, case_id)
            base = db.get(StoryVersion, base_id)
            try:
                await self._chain(db, case, language, material, base, requested_minutes, outcome,
                                  on_rewrite_done)
            except BudgetStopped as stop:
                outcome.status, outcome.error = "budget_stopped", stop.reason
            except Exception as exc:  # noqa: BLE001 - one language's failure must not stop the rest
                log.exception("language %s failed at %s", language, outcome.stage)
                outcome.status = "failed"
                outcome.error = f"{type(exc).__name__}: {exc}"[:600]
            if on_rewrite_done is not None:
                on_rewrite_done()
            self._emit(event="language_finished", **outcome.as_dict())
        return outcome

    async def _chain(self, db: Session, case: Case, language: str, material: SharedMaterial,
                     base: StoryVersion, requested: float, outcome: LanguageOutcome,
                     on_rewrite_done: Callable[[], None] | None = None) -> None:
        """Stages 2-4 from the approved base, the bounded length correction when the final is too short,
        then the checks of the delivered text and the decision on its approval."""
        if base.approved_at is None:
            raise StageFailed(base.kind, f"the {base.language} {base.kind} (id {base.id}) has no approval "
                              "record; no language starts before the text it is re-told from is approved")
        store = StageStore(db)
        span = final_range(language, requested)

        outcome.stage = REWRITE
        rewrite = store.stage_for(material, REWRITE, language, base, requested)
        if rewrite is None:
            rewrite = await self._rewrite(db, case, store, material, base, language, span, requested)
        else:
            outcome.reused.append(REWRITE)
        if on_rewrite_done is not None:
            on_rewrite_done()

        outcome.stage = INSPECTION
        inspection = store.stage_for(material, INSPECTION, language, base, requested, parent=rewrite)
        if inspection is None:
            inspection = await self._inspect(db, case, store, material, base, rewrite, language, span, requested)
        else:
            outcome.reused.append(INSPECTION)

        outcome.stage = FINAL
        final = store.stage_for(material, FINAL, language, base, requested, parent=inspection)
        if final is None:
            final = await self._finalize(db, case, store, material, base, rewrite, inspection,
                                         language, span, requested)
        else:
            outcome.reused.append(FINAL)
        outcome.final_id = final.id
        if final.status != "ready":
            final = await self._correct_length(db, case, store, material, base, rewrite, inspection,
                                               final, language, span, requested)
            outcome.final_id = final.id
        if final.status != "ready":
            notes = json.loads(final.critic_notes)
            correction = notes.get("length_correction") or {}
            raise StageFailed(FINAL, f"{language}: the blocking gates failed "
                              f"({', '.join(notes['quality_gates']['failures'])})"
                              + (f"; bounded length correction: {correction.get('status')}"
                                 f"{', ' + correction['reason'] if correction.get('reason') else ''}"
                                 if correction else ""))

        outcome.stage = "checks"
        final, review_items, check_items = await self._verify(
            db, case, store, material, base, rewrite, inspection, final, language, span, requested)
        outcome.final_id = final.id
        self._decide(store, final, rewrite, inspection, review_items, check_items, outcome)

    # -- stage 2
    async def _rewrite(self, db, case, store, material, base, language, span, requested):
        """One call. A text without the plan's act markers fails the stage (no second attempt)."""
        started = utc_now()
        self._emit(event="stage_started", stage=REWRITE, language=language)
        output = await self.stages.rewrite(db, case, material, _source_text(base), language, span,
                                           _draft_review_items(base))
        if not output.markers_ok:
            raise StageFailed(REWRITE_STAGE, f"{language}: the text does not keep the plan's act markers "
                              "(one attempt, no retry)")
        row = store.save(
            case_id=case.id, kind=REWRITE, language=language, output=output,
            material=material, started=started, requested_minutes=requested, base=base,
            extra={"range": span.as_dict(), **({"removed_lead_in": output.data["removed_lead_in"]}
                                               if output.data.get("removed_lead_in") else {})})
        self._emit(event="stage_done", stage=REWRITE, language=language, version_id=row.id,
                   words=len(row.story_text.split()), cost_eur=row.cost_eur)
        return row

    # -- stage 3
    async def _inspect(self, db, case, store, material, base, rewrite, language, span, requested):
        started = utc_now()
        self._emit(event="stage_started", stage=INSPECTION, language=language)
        output = await self.stages.inspect(db, case, material, rewrite.story_text, language, span)
        critique = output.data
        summary = critique.get("summary") or f"{len(critique['items'])} items"
        output.text = summary
        row = store.save(
            case_id=case.id, kind=INSPECTION, language=language, output=output, material=material,
            started=started, requested_minutes=requested, base=base, text=summary,
            critic_notes=critique, parents={"parent_id": rewrite.id},
            extra={"scores": critique["scores"], "items": len(critique["items"])})
        self._emit(event="stage_done", stage=INSPECTION, language=language, version_id=row.id,
                   items=len(critique["items"]), cost_eur=row.cost_eur)
        return row

    # -- stage 4
    async def _finalize(self, db, case, store, material, base, rewrite, inspection, language,
                        span, requested, previous: StoryVersion | None = None,
                        critique: dict | None = None, stage: str | None = None) -> StoryVersion:
        """Stage 4: one call, then the blocking gates. With `previous` and `critique` it starts from
        that text and that critique instead of the rewrite and the inspector's: the one revision round
        after the checks (`stage` "revision") or the owner's own instruction. A final is never approved
        here; that is decided after the checks of the delivered text."""
        started = utc_now()
        last_text = previous.story_text if previous is not None else rewrite.story_text
        used = critique or json.loads(inspection.critic_notes or "{}")
        self._emit(event="stage_started", stage=FINAL, language=language)
        output = await self.stages.finalize(db, case, material, language, span, last_text, used,
                                            _source_text(base))
        gates = await self.gates.run(db, case, material, language, output, self._gate_span(db, span, previous),
                                     gates=ai_config.language_pipeline.blocking_gates_for(language))
        failures = [f["gate"] for f in gates.failures]
        self._emit(event="gates", stage=FINAL, language=language, passed=gates.passed, failures=failures)
        notes = {"quality_gates": gates.as_dict(),
                 "gate_history": [{"words": len(output.text.split()), "failures": failures}],
                 "inspection_version_id": inspection.id, "rewrite_version_id": rewrite.id,
                 "act_lengths": act_length_report(self.stages.act_targets(material, language, span),
                                                  output.sections),
                 "fact_report": self._fact_report(material, language, base, output.text),
                 **({"removed_lead_in": output.data["removed_lead_in"]} if output.data.get("removed_lead_in") else {})}
        extra = {"owner_instruction": critique is not None and stage is None}
        if stage:
            extra.update({"stage": stage, "revises_version_id": previous.id})
        row = store.save(
            case_id=case.id, kind=FINAL, language=language, output=output, material=material,
            started=started, requested_minutes=requested, base=base, critic_notes=notes,
            status="ready" if gates.passed else "needs_revision",
            parents={"parent_id": inspection.id, "rewrite_id": rewrite.id},
            extra=extra, similarity=gates.similarity)
        self._emit(event="stage_done", stage=FINAL, language=language, version_id=row.id,
                   status=row.status, cost_eur=row.cost_eur)
        return row

    # -- stage 5: the bounded length correction
    async def _correct_length(self, db, case, store, material, base, rewrite, inspection, final,
                              language, span, requested) -> StoryVersion:
        """At most once per chain, and only for a final below the length minimum: the acts that need
        words (to their own lower bound first, then what the whole text still lacks) and that the
        English text has the material for are expanded in one call and spliced into the final by code.
        The editors' decisions about what was removed on purpose go with the call and bind it. The
        corrected text is a new final version (the old one stays); the gates and the report run again.
        A rejected or unusable answer leaves the final as it was. Never a second call."""
        notes = json.loads(final.critic_notes or "{}")
        if pipeline_of(final).get("stage") == LENGTH_CORRECTION or "length_correction" in notes:
            return final
        if self._chain_corrected(db, final):
            return final                       # the one correction of this chain was already made
        if not LengthCorrection.is_due(final.story_text, span):
            return final
        final_sections, markers_ok = self.stages._sections(final.story_text, material)
        if not markers_ok:
            return self._note_correction(db, final, notes, SKIPPED, {},
                                         "the final does not keep the plan's act markers")
        master_sections = _sections_of(base) if base.kind == DRAFT else self.stages._sections(base.story_text, material)[0]
        targets = self.stages.act_targets(material, language, span)
        overhead = len(final.story_text.split()) - sum(len(s["text"].split()) for s in final_sections)
        shortfalls = LengthCorrection.shortfalls(final_sections, master_sections, targets, span, overhead)
        report = {"shortfalls": [a.as_dict() for a in shortfalls]}
        rewrite_sections = self.stages._sections(rewrite.story_text, material)[0]
        decisions = LengthCorrection.decisions(
            json.loads(inspection.critic_notes or "{}").get("items") or [], rewrite_sections, final_sections)
        acts = LengthCorrection.request(shortfalls, final_sections, master_sections, decisions)
        if not any(a.needed_words for a in shortfalls):
            return self._note_correction(db, final, notes, SKIPPED, report,
                                         "the text needs no words: its acts and its total are inside the bounds")
        if not acts:
            return self._note_correction(db, final, notes, SKIPPED, report,
                                         "no act that needs words has enough material in the English text; "
                                         "the shortfall is reported, not padded")
        self._note_correction(db, final, notes, STARTED, report)
        started = utc_now()
        self._emit(event="stage_started", stage=LENGTH_CORRECTION, language=language,
                   acts=[a["act_id"] for a in acts])
        answer = await self.stages.correct_length(db, case, material, language, acts)
        changed = [a["act_id"] for a in acts]
        long = LengthCorrection.overshoot(answer.sections, targets) if answer.markers_ok else []
        if not answer.markers_ok or long:
            reason = ("the answer does not hold exactly the requested acts with their markers"
                      if not answer.markers_ok else
                      f"acts over {ai_config.language_pipeline.length_correction.max_act_overshoot} times "
                      f"their target: {', '.join(long)}")
            self._emit(event="stage_done", stage=LENGTH_CORRECTION, language=language, status=REJECTED,
                       reason=reason)
            return self._note_correction(db, final, notes, REJECTED, report, reason)
        # what the editors removed on purpose does not come back, even in a usable answer: those sentences
        # are taken out and the rest stands (the gates then say whether the text is long enough)
        answer.sections, brought_back = LengthCorrection.without_reintroduced(answer.sections, decisions)
        report["removed_as_reintroduced"] = brought_back
        sections, text = LengthCorrection.splice(final_sections, answer.sections)
        corrected = StageOutput(stage=LENGTH_CORRECTION, language=language, text=text, sections=sections,
                                markers_ok=True, result=answer.result, prompt_name=answer.prompt_name,
                                prompt_sha256=answer.prompt_sha256)
        gates = await self.gates.run(db, case, material, language, corrected, span.after_correction(),
                                     gates=ai_config.language_pipeline.blocking_gates_for(language))
        failures = [f["gate"] for f in gates.failures]
        self._emit(event="gates", stage=LENGTH_CORRECTION, language=language, passed=gates.passed,
                   failures=failures)
        report["repetition"] = LengthCorrection.repetition(final_sections, sections, changed)
        report.update({"status": STORED, "corrects_version_id": final.id, "acts_changed": changed,
                       "not_inspected": True})
        corrected_notes = {
            "quality_gates": gates.as_dict(),
            "gate_history": [{"words": len(text.split()), "failures": failures}],
            "inspection_version_id": inspection.id, "rewrite_version_id": rewrite.id,
            "act_lengths": act_length_report(targets, sections),
            "fact_report": self._fact_report(material, language, base, text),
            "length_correction": report}
        row = store.save(
            case_id=case.id, kind=FINAL, language=language, output=corrected, material=material,
            started=started, requested_minutes=requested, base=base, critic_notes=corrected_notes,
            status="ready" if gates.passed else "needs_revision",
            parents={"parent_id": inspection.id, "rewrite_id": rewrite.id},
            extra={"stage": LENGTH_CORRECTION, "corrects_version_id": final.id, "acts_changed": changed},
            similarity=gates.similarity, ledger_kind=LENGTH_CORRECTION)
        self._note_correction(db, final, notes, STORED, {"corrected_version_id": row.id})
        self._emit(event="stage_done", stage=LENGTH_CORRECTION, language=language, version_id=row.id,
                   status=row.status, words=len(text.split()), cost_eur=row.cost_eur, acts_changed=changed)
        return row

    def _gate_span(self, db: Session, span: LengthRange, source: StoryVersion | None) -> LengthRange:
        """A text written from a final of a chain that had its one length correction (the revision round, the
        owner's instruction, the audit fix) is judged like the corrected text: with the accepted shortfall."""
        if source is not None and self._chain_corrected(db, source):
            return span.after_correction()
        return span

    @staticmethod
    def _chain_corrected(db: Session, final: StoryVersion) -> bool:
        """The correction was already called in this chain: some final that came from the same rewrite
        carries a correction flag that says a call was made."""
        rewrite_id = pipeline_of(final).get("rewrite_id")
        for row in db.query(StoryVersion).filter(
                StoryVersion.case_id == final.case_id, StoryVersion.language == final.language,
                StoryVersion.kind == FINAL, StoryVersion.status != "retired"):
            if pipeline_of(row).get("rewrite_id") == rewrite_id and \
                    (json.loads(row.critic_notes or "{}").get("length_correction") or {}).get("status") in (
                        STARTED, STORED, REJECTED):
                return True
        return False

    @staticmethod
    def _note_correction(db: Session, final: StoryVersion, notes: dict, status: str, report: dict,
                         reason: str | None = None) -> StoryVersion:
        """The flag that the correction was decided on, stored with the final it was decided for."""
        notes["length_correction"] = {**notes.get("length_correction", {}), **report, "status": status,
                                      **({"reason": reason} if reason else {})}
        final.critic_notes = json.dumps(notes, ensure_ascii=False)
        db.commit()
        return final

    # -- the checks of the delivered text
    async def _verify(self, db, case, store, material, base, rewrite, inspection, final, language, span,
                      requested) -> tuple[StoryVersion, list[dict], list[dict]]:
        """The delivered text is checked, not the one before it: the inspector reads it; English then
        goes through the evidence audit and the correction of confirmed findings, every other language
        through the meaning check against the approved English text. One revision round at most, with
        the check repeated on its result. Returns the final that stands, the review's items and the
        check's unresolved items."""
        stage = ai_config.language_pipeline.stage_config_for(language)
        review = await self._review(db, case, store, material, final, language, span, requested)
        review_items = review["items"]
        if stage.check == "fidelity":
            fidelity = await self._fidelity(db, case, store, material, base, final, language)
            check_items = fidelity["items"]
            if blocking_items(review_items, check_items) and pipeline_of(final).get("stage") != "revision":
                final = await self._revise(db, case, store, material, base, rewrite, inspection, final,
                                           language, span, requested, review_items, check_items)
                if final.status == "ready":
                    review_items = (await self._review(db, case, store, material, final, language, span,
                                                       requested))["items"]
                    check_items = (await self._fidelity(db, case, store, material, base, final, language))["items"]
            return final, review_items, check_items
        if blocking_items(review_items, []) and pipeline_of(final).get("stage") != "revision":
            final = await self._revise(db, case, store, material, base, rewrite, inspection, final,
                                       language, span, requested, review_items, [])
            if final.status == "ready":
                review_items = (await self._review(db, case, store, material, final, language, span,
                                                   requested))["items"]
        if final.status != "ready":
            return final, review_items, []
        checked, unresolved = await self._factual(db, case, store, material, base, rewrite, inspection, final,
                                                  language, span, requested)
        if checked.id != final.id:
            review_items = (await self._review(db, case, store, material, checked, language, span,
                                               requested))["items"]
        return checked, review_items, unresolved

    async def _review(self, db, case, store, material, final, language, span, requested) -> dict:
        """The inspector on the delivered text: a review row made on exactly this version."""
        row = store.check_for(REVIEW, final)
        if row is None:
            started = utc_now()
            self._emit(event="stage_started", stage=REVIEW, language=language, version_id=final.id)
            output = await self.stages.inspect(db, case, material, final.story_text, language, span,
                                               stage=REVIEW_STAGE)
            critique = output.data
            output.text = critique.get("summary") or f"{len(critique['items'])} items"
            row = store.save(
                case_id=case.id, kind=REVIEW, language=language, output=output, material=material,
                started=started, requested_minutes=requested, base=final, text=output.text,
                critic_notes=critique, extra={"checks_version_id": final.id, "scores": critique["scores"],
                                              "items": len(critique["items"])})
            self._emit(event="stage_done", stage=REVIEW, language=language, version_id=row.id,
                       items=len(critique["items"]), counts=review_counts(critique["items"]), cost_eur=row.cost_eur)
        return json.loads(row.critic_notes)

    async def _fidelity(self, db, case, store, material, base, final, language) -> dict:
        """The meaning check of a translated text against the approved English text it came from."""
        row = store.check_for(FIDELITY, final)
        if row is None:
            started = utc_now()
            self._emit(event="stage_started", stage=FIDELITY, language=language, version_id=final.id)
            report, res = await self.stages.fidelity(db, case, material, language, base.story_text, final.story_text)
            data = {"verdict": report.verdict, "summary": report.summary,
                    "items": [i.model_dump() for i in report.items]}
            output = StageOutput(stage=FIDELITY_STAGE, language=language, text=report.summary or "no difference",
                                 sections=[], markers_ok=True, result=res,
                                 prompt_name=ai_config.agents["language.fidelity"].prompt,
                                 prompt_sha256=prompt_sha256(ai_config.agents["language.fidelity"].prompt))
            row = store.save(
                case_id=case.id, kind=FIDELITY, language=language, output=output, material=material,
                started=started, requested_minutes=0, base=base, text=output.text, critic_notes=data,
                extra={"checks_version_id": final.id, "items": len(report.items)})
            self._emit(event="stage_done", stage=FIDELITY, language=language, version_id=row.id,
                       items=len(report.items), counts=review_counts(data["items"]), cost_eur=row.cost_eur)
        return json.loads(row.critic_notes)

    async def _revise(self, db, case, store, material, base, rewrite, inspection, final, language, span,
                      requested, review_items, check_items) -> StoryVersion:
        """The one revision round: the final writer works on the delivered text with the review's and the
        check's items; the result passes the same gates and is checked again."""
        critique = critique_from(review_items, check_items)
        self._emit(event="stage_started", stage="revision", language=language, items=len(critique["items"]))
        revised = await self._finalize(db, case, store, material, base, rewrite, inspection, language, span,
                                       requested, previous=final, critique=critique, stage="revision")
        if revised.status != "ready":
            revised = await self._correct_length(db, case, store, material, base, rewrite, inspection, revised,
                                                 language, span, requested)
        return revised

    async def _factual(self, db, case, store, material, base, rewrite, inspection, final, language, span,
                       requested) -> tuple[StoryVersion, list[dict]]:
        """English: the evidence audit of the delivered text, each finding checked against the evidence,
        the confirmed ones corrected in place (smallest edit), and the groups of acts that changed audited
        again. Returns the final that stands and the confirmed findings that are still open."""
        if not ai_config.language_pipeline.evidence_audit.enabled:
            return final, []                       # the owner switched the audit off: there is no factual check
        notes = json.loads(final.critic_notes or "{}")
        sections = self.stages._sections(final.story_text, material)[0]
        if (notes.get("evidence_audit") or {}).get("status") != "ok":
            self._emit(event="stage_started", stage="evidence_audit", language=language, version_id=final.id)
            try:
                report = await self.audit.run(db, case, material, final, sections)
            except BudgetStopped:
                raise
            except Exception as exc:  # noqa: BLE001 - an audit that cannot run leaves the text open, not failed
                log.exception("the evidence audit of the delivered text failed")
                report = {"status": "failed", "report_only": True, "error": f"{type(exc).__name__}: {exc}"[:500],
                          "items": []}
                EvidenceAudit._store(db, final, report)
            self._emit(event="stage_done", stage="evidence_audit", language=language, status=report["status"],
                       items=len(report.get("items") or []), cost_eur=(report.get("usage") or {}).get("cost_eur"))
            notes = json.loads(final.critic_notes)
        report = notes["evidence_audit"]
        if report["status"] != "ok":
            return final, [{"severity": "high", "kind": "audit_unavailable", "decision": "confirmed",
                            "problem": "the evidence audit of the delivered text could not be completed "
                                       f"({report.get('status')}); the text has not been checked against the evidence"}]
        if pipeline_of(final).get("stage") == "audit_fix":
            return final, self._unresolved(notes)
        if "audit_fix" in notes:
            return final, self._unresolved(notes)
        self._emit(event="stage_started", stage=AUDIT_FIX_STAGE, language=language, version_id=final.id)
        fix_started = utc_now()
        fix = await self.fixer.run(db, case, material, report, sections)
        new_sections, fix_result = fix.pop("sections"), fix.pop("result", None)
        self._emit(event="stage_done", stage=AUDIT_FIX_STAGE, language=language, counts=fix["counts"],
                   applied=fix["applied"], skipped=fix["skipped"])
        notes["audit_fix"] = fix
        final.critic_notes = json.dumps(notes, ensure_ascii=False)
        db.commit()
        if not fix["applied"]:
            return final, self._unresolved(notes)
        text = marked(new_sections)
        output = StageOutput(stage=AUDIT_FIX_STAGE, language=language, text=text, sections=new_sections,
                             markers_ok=True, result=fix_result, prompt_name=ai_config.agents["english.audit_fix"].prompt,
                             prompt_sha256=prompt_sha256(ai_config.agents["english.audit_fix"].prompt))
        gates = await self.gates.run(db, case, material, language, output, self._gate_span(db, span, final),
                                     gates=ai_config.language_pipeline.blocking_gates_for(language))
        failures = [f["gate"] for f in gates.failures]
        targets = self.stages.act_targets(material, language, span)
        corrected_notes = {
            "quality_gates": gates.as_dict(), "gate_history": [{"words": len(text.split()), "failures": failures}],
            "inspection_version_id": inspection.id, "rewrite_version_id": rewrite.id,
            "act_lengths": act_length_report(targets, new_sections),
            "fact_report": self._fact_report(material, language, base, text),
            "audit_fix": {k: v for k, v in fix.items()}, "audit_fix_from": final.id}
        row = store.save(
            case_id=case.id, kind=FINAL, language=language, output=output, material=material,
            started=fix_started, requested_minutes=requested, base=base, critic_notes=corrected_notes,
            status="ready" if gates.passed else "needs_revision",
            parents={"parent_id": inspection.id, "rewrite_id": rewrite.id},
            extra={"stage": "audit_fix", "fixes_version_id": final.id, "acts_changed": fix["acts_changed"]},
            similarity=gates.similarity, ledger_kind=AUDIT_FIX)
        if row.status != "ready":
            return row, [{"severity": "high", "kind": "length", "problem": "the corrected text fails the gates: "
                          + ", ".join(failures), "decision": "confirmed"}]
        EvidenceAudit.carry_over(db, row, report, set(fix["acts_changed"]), material, new_sections)
        self._emit(event="stage_started", stage="evidence_audit", language=language, version_id=row.id,
                   acts=fix["acts_changed"])
        again = await self.audit.run(db, case, material, row, new_sections)
        self._emit(event="stage_done", stage="evidence_audit", language=language, status=again["status"],
                   items=len(again.get("items") or []), cost_eur=(again.get("usage") or {}).get("cost_eur"))
        return row, self._unresolved(json.loads(row.critic_notes), previous=report)

    @staticmethod
    def _unresolved(notes: dict, previous: dict | None = None) -> list[dict]:
        """The confirmed findings that no edit settled, and, after a correction, the new blocking findings
        of the repeated audit that the first audit did not have."""
        severities = ai_config.language_pipeline.review.blocking_severities
        open_items = [
            {"severity": d["severity"], "kind": d["kind"], "act": d["act"], "quote": d["quote"],
             "problem": d["reason"], "decision": "confirmed"}
            for d in (notes.get("audit_fix") or {}).get("decisions", [])
            if d["decision"] == "confirmed" and not d["applied"] and d["severity"] in severities]
        if previous is not None:
            seen = {" ".join(i["quote"].split()).lower() for i in previous.get("items") or []}
            for item in (notes.get("evidence_audit") or {}).get("items") or []:
                if item["severity"] in severities and " ".join(item["quote"].split()).lower() not in seen:
                    open_items.append({"severity": item["severity"], "kind": item["kind"], "act": item["act"],
                                       "quote": item["quote"], "problem": item["problem"], "decision": "new"})
        return open_items

    def _decide(self, store, final, rewrite, inspection, review_items, check_items, outcome) -> None:
        """Approval needs more than markers and length: the delivered text was reviewed, its check
        (meaning or evidence) was made, and no item of a blocking severity is left open. Otherwise the text
        stays open with its reasons; nothing is approved and nothing downstream starts from it."""
        problems = open_issues(review_items, check_items)
        notes = json.loads(final.critic_notes or "{}")
        notes["checks"] = {"review_counts": review_counts(review_items), "check_counts": review_counts(check_items),
                           "open_issues": problems, "revision": pipeline_of(final).get("stage") == "revision"}
        final.critic_notes = json.dumps(notes, ensure_ascii=False)
        if final.status == "ready" and not problems and \
                ai_config.language_pipeline.automatic_approval_for(final.language):
            store.mark_best_final(final)
            store.approve_automatically(final, {"rewrite": rewrite, "inspection": inspection})
            outcome.status = "done"
        elif final.status == "ready":
            final.status = OPEN_ISSUES
            store.db.commit()
            outcome.status = "open"
            outcome.open_issues = problems
            outcome.error = f"{len(problems)} open item(s) of a blocking severity; the text is not approved"
        else:
            store.db.commit()
            outcome.status = "failed"
        self._emit(event="decision", language=final.language, version_id=final.id, status=outcome.status,
                   open_issues=len(problems))

    @staticmethod
    def _fact_report(material: SharedMaterial, language: str, base: StoryVersion,
                     text: str) -> dict | None:
        """A film text against the text it was re-told from (the English draft for English, the approved
        English final for every other language): numbers both ways and, as the language's config says
        (report_names), names. A report only (config post_master_fact_report); it blocks nothing,
        repairs nothing and costs no model call."""
        cfg = ai_config.language_pipeline
        if not cfg.post_master_fact_report.enabled or cfg.blocking_gates_for(language) is None:
            return None
        stage = cfg.language_stages[language]
        report = fact_report.compare(_plain(base), text, material, stage.report_names)
        if stage.report_script_hygiene:
            report["script_hygiene"] = script_hygiene.check(text)
        return report

    async def revise_final(self, case_id: int, language: str,
                           requested_minutes: float | None = None) -> StoryVersion:
        """The one revision round of a language version, asked for explicitly: the final writer works on the
        delivered text with the items of its review (and of its meaning check) that are worth a revision, the
        result passes the gates (with the one length correction, if the chain has not used it) and is checked
        again like any delivered text. Once per version: a revised version is not revised again."""
        requested = float(requested_minutes or ai_config.language_pipeline.requested_minutes_default)
        with SessionLocal() as db:
            case = db.get(Case, case_id)
            store = StageStore(db)
            current = (db.query(StoryVersion).filter(
                StoryVersion.case_id == case_id, StoryVersion.kind == FINAL, StoryVersion.language == language,
                StoryVersion.status != "retired").order_by(StoryVersion.id.desc()).first())
            if current is None:
                raise StageFailed("revise", f"there is no {language} final version to revise")
            if pipeline_of(current).get("stage") == "revision":
                raise StageFailed("revise", f"this {language} version is already a revision; the round is used")
            p = pipeline_of(current)
            base = db.get(StoryVersion, current.draft_version_id)
            rewrite = db.get(StoryVersion, p["rewrite_id"])
            inspection = db.get(StoryVersion, p["parent_id"])
            material = await self._material(db, case)
            span = final_range(language, requested)
            review_row, fidelity_row = store.check_for(REVIEW, current), store.check_for(FIDELITY, current)
            review_items = json.loads(review_row.critic_notes)["items"] if review_row else []
            check_items = json.loads(fidelity_row.critic_notes)["items"] if fidelity_row else []
            if not revision_items(review_items, check_items):
                raise StageFailed("revise", f"the {language} version has no review item worth a revision")
            final = await self._revise(db, case, store, material, base, rewrite, inspection, current, language,
                                       span, requested, review_items, check_items)
            if final.status != "ready":
                return final
            outcome = LanguageOutcome(language)
            final, review_items, check_items = await self._verify(
                db, case, store, material, base, rewrite, inspection, final, language, span, requested)
            self._decide(store, final, rewrite, inspection, review_items, check_items, outcome)
            return final

    async def improve_final(self, case_id: int, language: str, instruction: str,
                            requested_minutes: float | None = None) -> StoryVersion:
        """The owner's instruction for one language: stage 4 again on its current final text, with the
        instruction as the critique, through the same gates and the same checks of the delivered text. A
        new final version; the earlier ones stay."""
        requested = float(requested_minutes or ai_config.language_pipeline.requested_minutes_default)
        with SessionLocal() as db:
            case = db.get(Case, case_id)
            store = StageStore(db)
            current = (db.query(StoryVersion).filter(
                StoryVersion.case_id == case_id, StoryVersion.kind == FINAL,
                StoryVersion.language == language).order_by(StoryVersion.id.desc()).first())
            if current is None:
                raise StageFailed("improve", f"there is no {language} final version to improve")
            p = pipeline_of(current)
            base = db.get(StoryVersion, current.draft_version_id)
            rewrite = db.get(StoryVersion, p["rewrite_id"])
            inspection = db.get(StoryVersion, p["parent_id"])
            material = await self._material(db, case)
            span = final_range(language, requested)
            critique = {"scores": {}, "summary": "The owner asked for this change.",
                        "items": [{"id": 1, "severity": "high", "kind": "owner", "act": "",
                                   "quote": "", "problem": instruction, "instruction": instruction}]}
            final = await self._finalize(db, case, store, material, base, rewrite, inspection, language, span,
                                         requested, previous=current, critique=critique)
            if final.status == "ready":
                outcome = LanguageOutcome(language)
                final, review_items, check_items = await self._verify(
                    db, case, store, material, base, rewrite, inspection, final, language, span, requested)
                self._decide(store, final, rewrite, inspection, review_items, check_items, outcome)
            return final

    # --------------------------------------------------------------------- the whole case
    async def run_case(self, case_id: int, languages: list[str] | None = None,
                       requested_minutes: float | None = None, tone: str = "documentary",
                       follow_up: dict | None = None, new_attempt: bool = False) -> dict:
        """The draft, its audit and approval; then English (the base of every other language) from the
        approved draft; then the other languages, in parallel, each from the approved English final. A
        language whose base is not approved does not start. With `new_attempt` the chain of each requested
        language that did not end approved is set aside first and written again."""
        cfg = ai_config.language_pipeline
        languages = languages or ai_config.multilingual.production_languages
        requested = float(requested_minutes or cfg.requested_minutes_default)
        check_requested_minutes(requested)
        with SessionLocal() as db:
            case = db.get(Case, case_id)
            material = await self._material(db, case, follow_up)
            draft, reused = await self.ensure_draft(db, case, material, tone, requested)
            await self.ensure_audit(db, case, material, draft)
            await self.ensure_draft_review(db, case, material, draft, requested)
            self.ensure_draft_approved(db, draft)
            draft_id = draft.id
        english = cfg.draft_language
        others = [lg for lg in languages if lg != english]
        outcomes: list[LanguageOutcome] = []
        base_id = None
        if english in languages or others:
            if new_attempt and english in languages:
                self._set_aside(case_id, english, draft_id)
            outcome = await self.run_language(case_id, english, material, draft_id, requested)
            if english in languages:
                outcomes.append(outcome)
            if outcome.status == "done":
                base_id = outcome.final_id
        if others:
            if base_id is None:
                reason = (f"no approved {english} final text to re-tell from "
                          f"({english}: {outcome.status}{', ' + outcome.error if outcome.error else ''})")
                for lg in others:
                    failed = LanguageOutcome(lg, status="failed", stage="base", error=reason)
                    self._emit(event="language_finished", **failed.as_dict())
                    outcomes.append(failed)
            else:
                if new_attempt:
                    for lg in others:
                        self._set_aside(case_id, lg, base_id)
                outcomes += await self.run_languages(case_id, others, material, base_id, requested)
        outcomes.sort(key=lambda o: languages.index(o.language))
        return {"draft_version_id": draft_id, "draft_reused": reused,
                "languages": [o.as_dict() for o in outcomes]}

    @staticmethod
    def _set_aside(case_id: int, language: str, base_id: int) -> None:
        with SessionLocal() as db:
            rows = StageStore(db).retire_attempt(case_id, language, base_id, "a new attempt was requested")
            log.info("language %s: %d rows of the earlier attempt set aside", language, len(rows))

    async def run_languages(self, case_id: int, languages: list[str], material: SharedMaterial,
                            base_id: int, requested: float) -> list[LanguageOutcome]:
        """All languages in parallel from the same approved English final. With the cache primer on,
        the first language writes its stage 2 text alone; the others start their stage 2 once it
        exists, so their calls read the shared prefix (plan, English text) from the provider's cache.
        The first language goes on to the later stages meanwhile."""
        gate = asyncio.Semaphore(ai_config.language_pipeline.language_concurrency)
        primed = asyncio.Event()
        primer = languages[0] if ai_config.language_pipeline.stage2_cache_primer else None
        if primer is None:
            primed.set()

        async def one(language: str) -> LanguageOutcome:
            async with gate:
                if language == primer:
                    return await self.run_language(case_id, language, material, base_id, requested,
                                                   on_rewrite_done=primed.set)
                await primed.wait()
                return await self.run_language(case_id, language, material, base_id, requested)

        return list(await asyncio.gather(*(one(lg) for lg in languages)))

    async def _material(self, db: Session, case: Case,
                        follow_up: dict | None = None) -> SharedMaterial:
        from app.services.story_plan_service import StoryPlanService

        cfg = ai_config.language_pipeline
        plan = await StoryPlanService(db).ensure_approved(
            case, cfg.long_draft_minutes, cfg.draft_language, follow_up=follow_up)
        return load_material(db, case, plan, follow_up)
