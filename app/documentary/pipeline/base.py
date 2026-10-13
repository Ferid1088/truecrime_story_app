"""Stage + runner: the bookkeeping every pipeline stage shares.

A stage is a small class: a `name`, whether its failure may be survived
(`optional`), and `run()` returning its result dict. The runner saves each
stage's attempt, timing, errors and result on the job before the next stage
starts, honours cancellation, and skips stages that are already done."""

from __future__ import annotations

import json
import logging
from typing import ClassVar

from app.core.ai_config import ai_config
from app.core.artifact_dependencies import ARTIFACT_DEPENDENCIES
from app.core.concurrency import gather_limited
from app.core.policy import PolicyDecision, warning_for
from app.db.models import Artifact, DocumentaryJob
from app.documentary.pipeline.errors import JobCancelled
from app.services.tracking import documentary_run_context
from app.services.budget import BudgetService, BudgetStopped
from app.services.cost_model import estimate_stage_eur
from app.utils import utc_now

log = logging.getLogger(__name__)


def record_warning(result: dict, stage: str, reason: str, language: str | None = None,
                   code: str | None = None) -> dict:
    """Append a user-visible structured warning, preserving old dict results."""
    warnings = result.get("warnings")
    if isinstance(warnings, dict):
        warnings = [{"stage": name, "reason": value} for name, value in warnings.items()]
        result["warnings"] = warnings
    elif not isinstance(warnings, list):
        warnings = []
        result["warnings"] = warnings
    warning = warning_for(code or "STAGE_WARNING", reason[:500],
                          {"stage": stage, **({"language": language} if language else {})})
    # Keep the Prompt 2 aliases while clients migrate to the policy contract.
    warning["stage"] = stage
    warning["reason"] = reason[:500]
    if language:
        warning["language"] = language
    warnings.append(warning)
    return warning


class Stage:
    name: ClassVar[str]
    optional: ClassVar[bool] = False

    def __init__(self, ctx, lang: str | None = None):
        self.ctx = ctx
        self.lang = lang

    @property
    def stage_name(self) -> str:
        return f"{self.name}:{self.lang}" if self.lang else self.name

    async def run(self) -> dict | None:
        raise NotImplementedError


class StageRunner:
    def __init__(self, db, job: DocumentaryJob, execution_scope: dict | None = None):
        self.db = db
        self.job = job
        self.stages = json.loads(job.stages_json or "[]")
        self.result = json.loads(job.result_json or "{}")
        self.execution_scope = execution_scope or {}
        self._scoped_execution = execution_scope is not None
        self._allowed_stage_names = set(self.execution_scope.get("stage_names", []))
        self.executed_stage_names: list[str] = []
        self.running: list[str] = []
        self.lang_errors: dict[str, str] = {}
        self._reran_artifact_types: set[str] = set()

    # -- bookkeeping ---------------------------------------------------------
    def _save(self):
        self.job.stages_json = json.dumps(self.stages, ensure_ascii=False, default=str)
        self.job.result_json = json.dumps(self.result, ensure_ascii=False, default=str)
        done = sum(1 for s in self.stages if s["status"] in ("done", "skipped", "degraded"))
        self.job.progress = done / max(len(self.stages), 1)
        self.job.stage = (", ".join(self.running) or None) if self.running else None
        if self.job.stage and len(self.job.stage) > 60:
            self.job.stage = self.job.stage[:57] + "..."
        self.job.updated_at = utc_now()
        self.db.commit()

    def _check_cancel(self):
        self.db.refresh(self.job)
        if self.job.status == "cancelling":
            raise JobCancelled()

    def _entry(self, name: str) -> dict:
        st = next((s for s in self.stages if s["name"] == name), None)
        if st is None:  # jobs created before this stage existed
            st = {"name": name, "status": "pending", "detail": None}
            self.stages.append(st)
        return st

    async def _stage(self, name: str, fn):
        """Run one stage once: its result is saved on the job before the
        next stage starts. Every attempt is counted and timed, every
        failure kept (type + message) — a retry does not erase the last
        one. A result marked {"degraded": reason} is kept and shown as
        degraded (the film went on without that part; the reason is on
        the stage and in the job result) — a new run redoes it."""
        st = self._entry(name)
        if self._scoped_execution and name not in self._allowed_stage_names:
            st["status"] = "skipped"
            st["detail"] = {"skipped": True,
                            "reason": "outside stage-control preview"}
            self._save()
            return st["detail"]
        skip_result = None
        from app.services.artifact_shadow import STAGE_ARTIFACT_TYPES

        language = next((lang for lang in json.loads(self.job.languages_json or "[]")
                         if name.endswith(f":{lang}")), None)
        pipeline_stage = name[:-len(language) - 1] if language else name
        types = set(STAGE_ARTIFACT_TYPES.get(pipeline_stage, ()))
        if st["status"] in ("done", "skipped", "degraded"):
            from app.services.skip_policy import SkipPolicy

            policy = SkipPolicy(self.db)
            dependencies_reran = any(
                dep in self._reran_artifact_types
                for artifact_type in types
                for dep in ARTIFACT_DEPENDENCIES.get(artifact_type, ())
            )
            skip_result = policy.evaluate(
                self.job, name,
                pending_edit=policy.pending_edit(
                    [row.id for row in self.db.query(Artifact).filter(
                        Artifact.stage_job_id == self.job.id,
                        Artifact.type.in_(types),
                    ).all()]),
                dependencies_reran=dependencies_reran,
            )
            mode = ai_config.production_policies.incremental.mode
            if mode == "enforce" and skip_result.decision.decision == "skip":
                detail = dict(st.get("detail") or {})
                detail["reused"] = True
                detail["reuse_decision"] = skip_result.decision.to_dict()
                st["detail"] = detail
                st["status"] = "skipped"
                self._save()
                return detail
            if mode == "enforce":
                st["status"] = "pending"
                st["detail"] = None
            elif mode == "verify":
                # Verify mode deliberately runs the producer, even when all
                # hash gates say it could have been reused.
                st["status"] = "pending"
                st["detail"] = None
        if st["status"] in ("done", "skipped", "degraded"):
            return st.get("detail")
        self._check_cancel()
        budget_stage = (
            "translation" if pipeline_stage == "spoken" else
            "tts" if pipeline_stage in {"performance", "voice"} else
            "heygen" if pipeline_stage.startswith("host") else
            "shorts" if pipeline_stage.startswith("short") else
            pipeline_stage
        )
        estimate = estimate_stage_eur(
            self.db, self.job.case_id, name,
            self.job.pilot_seconds if self.job.mode == "pilot" else None,
        )
        try:
            BudgetService(self.db).check(self.job.case_id, budget_stage, estimate)
        except BudgetStopped as exc:
            st["status"] = "failed"
            st["detail"] = {"budget_stopped": True, "reason": exc.reason}
            st["error_type"] = type(exc).__name__
            st["finished_at"] = utc_now().isoformat()
            self.result["budget_stopped"] = {"stage": name, "reason": exc.reason}
            if name in self.running:
                self.running.remove(name)
            self._save()
            raise
        st["status"] = "running"
        st["attempts"] = int(st.get("attempts") or 0) + 1
        st["started_at"] = utc_now().isoformat()
        st.pop("finished_at", None)
        self.running.append(name)
        self.executed_stage_names.append(name)
        self._save()
        try:
            language = None
            pipeline_stage = name
            for candidate in json.loads(self.job.languages_json or "[]"):
                suffix = f":{candidate}"
                if name.endswith(suffix):
                    language = candidate
                    pipeline_stage = name[:-len(suffix)]
                    break
            with documentary_run_context(self.job.id, self.job.case_id, language, pipeline_stage):
                detail = await fn()
        except Exception as e:
            st["status"] = "failed"
            st["detail"] = str(e)[:500]
            st["error_type"] = type(e).__name__
            st["finished_at"] = utc_now().isoformat()
            st["errors"] = (st.get("errors") or [])[-4:] + [
                {"at": st["finished_at"], "attempt": st["attempts"],
                 "type": type(e).__name__, "message": str(e)[:300]}]
            if name in self.running:
                self.running.remove(name)
            if not self.db.is_active:
                self.db.rollback()
            self._save()
            raise
        degraded = isinstance(detail, dict) and detail.get("degraded")
        st["status"] = ("skipped" if isinstance(detail, dict) and detail.get("skipped")
                        else "degraded" if degraded else "done")
        st["detail"] = detail
        st["finished_at"] = utc_now().isoformat()
        st.pop("error_type", None)
        if degraded:
            self.result.setdefault("degraded", {})[name] = str(degraded)[:300]
            record_warning(self.result, pipeline_stage, str(degraded), language,
                           "AUDIO_PLAN_DEGRADED" if pipeline_stage == "audio_plan"
                           else "STAGE_DEGRADED")
        from app.services.artifact_shadow import register_stage_outputs
        register_stage_outputs(self.db, self.job, pipeline_stage, language,
                               detail if isinstance(detail, dict) else None, self.result)
        from app.services.skip_policy import SkipPolicy
        # A stage that was already classified ``would-run`` is not a verify
        # candidate.  Only an initial ``would-skip`` can produce a
        # verify_match/verify_mismatch result; otherwise every unproven or
        # invalidated stage would be reported as a false mismatch.
        if (skip_result is not None
                and skip_result.decision.decision == "skip"
                and ai_config.production_policies.incremental.mode == "verify"):
            current = SkipPolicy(self.db).evaluate(self.job, name)
            post_run_match = current.decision.decision == "skip"
            current_decision = PolicyDecision(
                policy="incremental", decision="verify_match" if post_run_match else "verify_mismatch",
                reason=("producer output matched reusable artifact" if post_run_match
                        else current.decision.reason),
                rule_id="INCREMENTAL_VERIFY",
                severity="info", recoverable=True, actions=("continue", "review"),
                context={"job_id": self.job.id, "stage": name,
                         "would_skip": skip_result.decision.decision == "skip",
                         "post_run_skip": current.decision.decision == "skip"},
            )
            SkipPolicy(self.db)._event(current_decision, self.job, language)
        self._reran_artifact_types.update(types if 'types' in locals() else set())
        if name in self.running:
            self.running.remove(name)
        self._save()
        return detail

    def _skip_language(self, lang: str, reason: str):
        for st in self.stages:
            if st["name"].endswith(f":{lang}") and st["status"] == "pending":
                st["status"] = "blocked"
                st["detail"] = reason[:300]
        self._save()

    # -- running stages --------------------------------------------------------
    async def execute(self, stage: Stage):
        """Run a stage object; an optional stage's failure is recorded but does
        not stop the job."""
        if not stage.optional:
            return await self._stage(stage.stage_name, stage.run)
        try:
            return await self._stage(stage.stage_name, stage.run)
        except JobCancelled:
            raise
        except Exception as e:
            log.warning("optional stage %s failed: %s", stage.stage_name, e)
            record_warning(self.result, stage.name, f"{type(e).__name__}: {e}", stage.lang)
            self._save()
            return None

    async def _parallel(self, coros: list, shared_from: int, shared_count: int | None = None):
        """Run branches together. Failures of language branches are
        recorded by the branches themselves; a cancelled job or a failed
        SHARED branch (indices shared_from .. +shared_count) ends the job
        after every branch has stopped."""
        results = await gather_limited(None, coros, return_exceptions=True)
        shared = range(shared_from, shared_from + (shared_count if shared_count is not None
                                                   else len(coros) - shared_from))
        for r in results:
            if isinstance(r, JobCancelled):
                raise r
        for k, r in enumerate(results):
            if isinstance(r, BaseException) and k in shared:
                raise r
