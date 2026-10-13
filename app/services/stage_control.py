"""User-controlled stage state, previews, and bounded reruns."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.artifact_dependencies import ARTIFACT_SCOPES
from app.core.policy import PolicyDecision
from app.db.models import (
    AppSetting,
    Artifact,
    Case,
    CaseTitleCandidate,
    DocumentaryJob,
    EditLog,
    PolicyEvent,
    StoryVersion,
    Thumbnail,
)
from app.services.artifact_shadow import STAGE_ARTIFACT_TYPES, _stage_detail
from app.services.policy_events import record_decision
from app.services.skip_policy import SOUNDNESS
from app.services.soundness import predicted_change_keys
from app.utils import utc_now

ACTIONS = ("continue", "redo_with_prompt", "edit", "run_automatically")


class StageControlError(ValueError):
    """A user action cannot be safely applied to the current saved state."""


class StageControlService:
    """Backend contract for one stage at a time.

    A preview stores the exact artifact ids and content hashes in ``EditLog``.
    Apply refuses stale previews, marks only that stored scope stale, and
    queues the existing job runner. The runner receives and executes only the
    stored scope; any conservative inclusion is visible in the preview first.
    """

    def __init__(self, db: Session):
        self.db = db

    def _job(self, case_id: int, job_id: int | None = None) -> DocumentaryJob:
        query = self.db.query(DocumentaryJob).filter(DocumentaryJob.case_id == case_id)
        if job_id is not None:
            query = query.filter(DocumentaryJob.id == job_id)
        job = query.order_by(DocumentaryJob.id.desc()).first()
        if job is None:
            raise StageControlError("selected run is not available for this case")
        return job

    @staticmethod
    def _entry(job: DocumentaryJob, stage_name: str) -> dict:
        stages = json.loads(job.stages_json or "[]")
        entry = next((item for item in stages if item.get("name") == stage_name), None)
        if entry is None:
            raise StageControlError(f"unknown stage {stage_name!r}")
        return entry

    @staticmethod
    def _base_stage(stage_name: str) -> tuple[str, str | None]:
        if ":" in stage_name:
            stage, language = stage_name.rsplit(":", 1)
            return stage, language
        return stage_name, None

    def _rows(self, job: DocumentaryJob, stage_name: str) -> list[Artifact]:
        stage, language = self._base_stage(stage_name)
        types = set(STAGE_ARTIFACT_TYPES.get(stage, ()))
        if not types:
            raise StageControlError(f"stage {stage_name!r} has no registered artifact types")
        rows = (self.db.query(Artifact).filter(
            Artifact.stage_job_id == job.id, Artifact.type.in_(types)).all())
        if language:
            rows = [row for row in rows
                    if ARTIFACT_SCOPES[row.type] == "shared" or row.language == language]
        return sorted(rows, key=lambda row: row.id)

    def _pending(self, rows: list[Artifact], *, exclude: int | None = None) -> bool:
        ids = {row.id for row in rows}
        for edit in self.db.query(EditLog).all() if rows else []:
            if edit.id == exclude or edit.status not in ("previewed", "running"):
                continue
            try:
                scope = json.loads(edit.scope_preview_json or "{}")
            except (TypeError, ValueError):
                scope = {}
            if ids.intersection(scope.get("artifact_ids", [])):
                return True
        return False

    def _audit(self, job: DocumentaryJob, action: str, decision: str, reason: str,
               context: dict[str, Any]) -> None:
        record_decision(
            self.db,
            PolicyDecision(policy="stage_control", decision=decision, reason=reason,
                           rule_id=f"STAGE_CONTROL_{decision.upper()}", severity="info",
                           recoverable=True, actions=("continue", "redo", "review"), context=context),
            case_id=job.case_id, production_id=job.id,
        )

    def _scope(self, job: DocumentaryJob, roots: list[Artifact]) -> list[Artifact]:
        predicted = predicted_change_keys(self.db, [row.id for row in roots])
        all_rows = self.db.query(Artifact).filter(Artifact.stage_job_id == job.id).all()
        return sorted(
            [row for row in all_rows
             if f"{row.type}:{row.language or 'shared'}" in predicted],
            key=lambda row: row.id,
        )

    def _preview_payload(self, job: DocumentaryJob, stage_name: str, action: str,
                         prompt: str | None, edit: dict[str, Any] | None) -> dict:
        roots = self._rows(job, stage_name)
        scope = self._scope(job, roots) if action != "run_automatically" else []
        by_stage: dict[str, int] = defaultdict(int)
        for row in scope:
            stage = json.loads(row.scope_json or "{}").get("stage", "unknown")
            by_stage[f"{stage}:{row.language}" if row.language else stage] += 1
        stage_names = sorted({
            f"{json.loads(row.scope_json or '{}').get('stage')}:{row.language}"
            if row.language else json.loads(row.scope_json or "{}").get("stage", "unknown")
            for row in scope
        })
        languages = sorted({row.language for row in scope if row.language})
        return {
            "case_id": job.case_id, "job_id": job.id, "stage": stage_name,
            "action": action, "prompt": prompt, "edit": edit or {},
            "artifact_ids": [row.id for row in scope],
            "stage_names": stage_names,
            "languages": languages,
            "estimated_scope": [{"id": row.id, "type": row.type,
                                 "language": row.language,
                                 "stage": json.loads(row.scope_json or "{}").get("stage"),
                                 "will_change": row.id in {root.id for root in roots},
                                 "may_change": SOUNDNESS.get(row.type) != "SOUND"}
                                for row in scope],
            "estimated_provider_calls": {"total": len(scope), "by_stage": dict(sorted(by_stage.items()))},
            "bound_hashes": {row.id: {"input_hash": row.input_hash,
                                      "output_hash": row.output_hash}
                             for row in roots},
        }

    def state(self, case_id: int, stage_name: str, job_id: int | None = None) -> dict:
        job = self._job(case_id, job_id)
        entry = self._entry(job, stage_name)
        rows = self._rows(job, stage_name)
        pending = self._pending(rows)
        status = entry.get("status", "unknown")
        if status == "running":
            allowed: tuple[str, ...] = ()
        elif status == "failed":
            allowed = ("continue", "redo_with_prompt", "run_automatically")
        elif status in ("done", "skipped", "degraded"):
            allowed = ("redo_with_prompt", "edit", "run_automatically")
        else:
            allowed = ("continue", "run_automatically")
        blocked = {
            "continue": "pending user edit or lock" if pending else (
                "stage is currently running" if status == "running" else None),
            "redo_with_prompt": "pending user edit or lock" if pending else (
                "stage is currently running" if status == "running" else None),
            "edit": "stage is currently running" if status == "running" else None,
            "run_automatically": None,
        }
        available = [action for action in allowed if blocked[action] is None]
        setting = self.db.get(AppSetting, f"stage_control:auto:{case_id}:{stage_name}")
        automatic = json.loads(setting.value_json).get("enabled", False) if setting else False
        return {"case_id": case_id, "job_id": job.id, "stage": stage_name,
                "status": status,
                "available_actions": available, "blocked_actions": blocked,
                "pending_user_edit": pending, "automatic": automatic,
                "artifacts": [{"id": row.id, "type": row.type, "language": row.language,
                               "status": row.status} for row in rows]}

    def preview(self, case_id: int, stage_name: str, action: str,
                prompt: str | None = None, edit: dict[str, Any] | None = None,
                job_id: int | None = None) -> dict:
        if action not in ACTIONS:
            raise StageControlError(f"unsupported stage action {action!r}")
        job = self._job(case_id, job_id)
        state = self.state(case_id, stage_name, job_id=job.id)
        if action in state["blocked_actions"] and state["blocked_actions"][action]:
            raise StageControlError(state["blocked_actions"][action])
        if action == "edit" and not edit:
            raise StageControlError("edit action requires an edit payload")
        payload = self._preview_payload(job, stage_name, action, prompt, edit)
        row = EditLog(actor="api", scope_type="stage", scope_id=(payload["artifact_ids"] or [stage_name])[0],
                      instruction_text=prompt, intent_json=json.dumps({"action": action}),
                      scope_preview_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                      status="previewed")
        self.db.add(row)
        self.db.flush()
        payload["preview_id"] = row.id
        row.scope_preview_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.db.commit()
        self._audit(job, action, "previewed", "stage action previewed",
                    {"preview_id": row.id, "stage": stage_name,
                     "artifact_ids": payload["artifact_ids"]})
        return payload

    def _apply_edit(self, payload: dict, job: DocumentaryJob) -> None:
        edit = payload.get("edit") or {}
        applied = False
        for artifact in payload.get("estimated_scope", []):
            if artifact["type"] != "spoken_version":
                row = self.db.get(Artifact, artifact["id"])
                text = edit.get("text") or edit.get("chapter_text")
                if row is not None and isinstance(text, str) and text:
                    row.output_ref = text
                    row.output_hash = hashlib.sha256(text.encode()).hexdigest()
                    row.status = "fresh"
                    applied = True
                continue
            row = self.db.get(Artifact, artifact["id"])
            if row is None:
                continue
            detail = _stage_detail(job, "spoken", artifact["language"])
            version_id = detail.get("version_id")
            version = self.db.get(StoryVersion, version_id) if version_id else None
            if version is None or artifact["language"] != edit.get("language", artifact["language"]):
                continue
            text = edit.get("story_text", edit.get("text"))
            chapter_text = edit.get("chapter_text")
            chapter_index = edit.get("chapter_index")
            chapter_title = edit.get("chapter_title")
            if chapter_text is not None and isinstance(chapter_title, str) and chapter_title:
                try:
                    structure = json.loads(version.narrative_structure or "{}")
                    sections = structure["sections"]
                    chapter_index = next(
                        index for index, section in enumerate(sections)
                        if section.get("title") == chapter_title
                        or section.get("heading") == chapter_title
                    )
                except (KeyError, StopIteration, TypeError, ValueError) as exc:
                    raise StageControlError("chapter title does not match the spoken structure") from exc
            if chapter_text is not None and isinstance(chapter_index, int):
                try:
                    structure = json.loads(version.narrative_structure or "{}")
                    section = structure["sections"][chapter_index]
                    old_text = section.get("text", "")
                    section["text"] = chapter_text
                    version.narrative_structure = json.dumps(structure, ensure_ascii=False)
                    text = version.story_text.replace(old_text, chapter_text, 1)
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    raise StageControlError("chapter edit does not match the spoken structure") from exc
            if not isinstance(text, str) or not text:
                raise StageControlError("spoken edit requires non-empty story_text or chapter_text")
            version.story_text = text
            version.text_hash = hashlib.sha256(text.encode()).hexdigest()
            applied = True
        if not applied:
            raise StageControlError("edit did not target a supported artifact")

    def apply(self, preview_id: int, *, case_id: int | None = None) -> dict:
        row = self.db.get(EditLog, preview_id)
        if row is None or row.status != "previewed":
            raise StageControlError("preview is missing or already applied")
        payload = json.loads(row.scope_preview_json or "{}")
        if case_id is not None and int(payload["case_id"]) != case_id:
            raise StageControlError("preview does not belong to case")
        job = self._job(int(payload["case_id"]), int(payload["job_id"]))
        roots: list[Artifact] = []
        for artifact_id in payload.get("artifact_ids", []):
            artifact = self.db.get(Artifact, artifact_id)
            if artifact is not None:
                roots.append(artifact)
        for artifact_id, hashes in payload.get("bound_hashes", {}).items():
            current = self.db.get(Artifact, artifact_id)
            if current is None or {"input_hash": current.input_hash,
                                   "output_hash": current.output_hash} != hashes:
                self._audit(job, payload["action"], "blocked", "inputs changed since preview",
                            {"preview_id": preview_id})
                raise StageControlError("inputs changed since preview")
        if payload["action"] != "edit" and self._pending(roots, exclude=preview_id):
            self._audit(job, payload["action"], "blocked", "pending user edit or lock",
                        {"preview_id": preview_id})
            raise StageControlError("pending user edit or lock")
        if payload["action"] == "edit":
            self._apply_edit(payload, job)
        for artifact_id in payload.get("artifact_ids", []):
            artifact = self.db.get(Artifact, artifact_id)
            if artifact is not None:
                artifact.status = "stale"
                artifact.updated_at = utc_now()
        stages = json.loads(job.stages_json or "[]")
        scope_stages = set(payload.get("stage_names", []))
        for stage in stages:
            if stage.get("name") in scope_stages:
                stage["status"] = "pending"
                stage["detail"] = None
        job.stages_json = json.dumps(stages, ensure_ascii=False)
        job.status, job.error = "queued", None
        row.status = "applied"
        row.result_json = json.dumps({
            "status": "queued",
            "previewed_artifact_ids": payload.get("artifact_ids", []),
            "executed_artifact_ids": [],
            "executed_stage_names": [],
            "changed_outside_scope": [],
        })
        self.db.commit()
        self._audit(job, payload["action"], "applied", "stage action queued",
                    {"preview_id": preview_id, "artifact_ids": payload.get("artifact_ids", [])})
        from app.documentary import jobs as documentary_jobs
        documentary_jobs.launch(job.id)
        return {"preview_id": preview_id, "job_id": job.id,
                "status": "queued", "previewed_artifact_ids": payload.get("artifact_ids", []),
                "executed_artifact_ids": [], "changed_outside_scope": []}

    def set_automatic(self, case_id: int, stage_name: str, enabled: bool,
                      job_id: int | None = None) -> dict:
        job = self._job(case_id, job_id)
        key = f"stage_control:auto:{case_id}:{stage_name}"
        row = self.db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key)
            self.db.add(row)
        row.value_json = json.dumps({"enabled": enabled, "mode": "auto_continue"})
        row.updated_at = utc_now()
        self.db.commit()
        self._audit(job, "run_automatically", "updated", "automatic mode changed",
                    {"stage": stage_name, "enabled": enabled})
        return {"case_id": case_id, "stage": stage_name, "automatic": enabled,
                "mode": "auto_continue"}

    def result(self, case_id: int, preview_id: int) -> dict:
        """Return the durable result of one preview/apply operation.

        Applying a preview queues the job, so the browser needs a read-only
        endpoint for the runner's eventual scope check.  The result is kept
        on the EditLog together with the original preview and is therefore
        stable across page reloads.
        """
        row = self.db.get(EditLog, preview_id)
        if row is None:
            raise StageControlError("preview not found")
        payload = json.loads(row.scope_preview_json or "{}")
        if int(payload.get("case_id", -1)) != case_id:
            raise StageControlError("preview does not belong to case")
        result = json.loads(row.result_json or "{}")
        return {
            "preview_id": preview_id,
            "case_id": case_id,
            "status": row.status,
            "preview": payload,
            "result": result,
        }

    # ------------------------------------------------------------------
    # Case-level editorial actions.  These use the same preview/apply
    # contract as documentary stages, but do not require a documentary job.
    # ------------------------------------------------------------------

    @staticmethod
    def _case_action_hash(row: object, action: str) -> str:
        if action.startswith("naming_"):
            assert isinstance(row, CaseTitleCandidate)
            payload = {"id": row.id, "title": row.title, "status": row.status,
                       "critic": row.critic_json, "language": row.language}
        elif action.startswith("thumbnail_"):
            assert isinstance(row, Thumbnail)
            payload = {"id": row.id, "status": row.status, "sha256": row.sha256,
                       "critic": row.critic_json, "language": row.language}
        else:
            assert isinstance(row, StoryVersion)
            payload = {"id": row.id, "version": row.version, "status": row.status,
                       "text_hash": row.text_hash, "story_text": row.story_text,
                       "language": row.language}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _case_action_target(self, case_id: int, action: str, target_id: int) -> tuple[object, str]:
        case = self.db.get(Case, case_id)
        if case is None:
            raise StageControlError("case not found")
        if action.startswith("naming_"):
            row = self.db.get(CaseTitleCandidate, target_id)
            if row is None or row.case_id != case_id:
                raise StageControlError("naming candidate does not belong to case")
            return row, row.language
        if action.startswith("thumbnail_"):
            row = self.db.get(Thumbnail, target_id)
            if row is None or row.case_id != case_id:
                raise StageControlError("thumbnail does not belong to case")
            return row, row.language
        row = self.db.get(StoryVersion, target_id)
        if row is None or row.case_id != case_id or row.kind != "localized":
            raise StageControlError("localization does not belong to case")
        return row, row.language

    def preview_case_action(self, case_id: int, action: str, target_id: int) -> dict:
        allowed = {"naming_approve", "thumbnail_approve", "thumbnail_reject",
                   "localization_regenerate"}
        if action not in allowed:
            raise StageControlError(f"unsupported case action {action!r}")
        target, language = self._case_action_target(case_id, action, target_id)
        scope_id = f"{action}:{target_id}"
        payload = {
            "case_id": case_id,
            "action": action,
            "target_id": target_id,
            "scope": [{"id": scope_id, "type": action, "language": language,
                       "may_change": False}],
            "estimated_provider_calls": {
                "total": 1 if action == "localization_regenerate" else 0,
                "by_stage": {"localization": 1} if action == "localization_regenerate" else {},
            },
            "bound_hashes": {scope_id: self._case_action_hash(target, action)},
        }
        row = EditLog(
            actor="api", scope_type="case_action", scope_id=scope_id,
            intent_json=json.dumps({"action": action, "target_id": target_id}),
            scope_preview_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            status="previewed",
        )
        self.db.add(row)
        self.db.flush()
        payload["preview_id"] = row.id
        row.scope_preview_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.db.commit()
        self._audit_case(case_id, action, "previewed", "case action previewed", payload)
        return payload

    def _audit_case(self, case_id: int, action: str, decision: str, reason: str,
                    context: dict[str, Any]) -> None:
        record_decision(
            self.db,
            PolicyDecision(policy="stage_control", decision=decision, reason=reason,
                           rule_id=f"STAGE_CONTROL_{decision.upper()}", severity="info",
                           recoverable=True, actions=("continue", "redo", "review"),
                           context={"action": action, **context}),
            case_id=case_id,
        )

    async def apply_case_action(self, preview_id: int, *, case_id: int | None = None) -> dict:
        row = self.db.get(EditLog, preview_id)
        if row is None or row.status != "previewed" or row.scope_type != "case_action":
            raise StageControlError("case action preview is missing or already applied")
        payload = json.loads(row.scope_preview_json or "{}")
        actual_case_id = int(payload["case_id"])
        if case_id is not None and actual_case_id != case_id:
            raise StageControlError("preview does not belong to case")
        action = str(payload["action"])
        target, _language = self._case_action_target(actual_case_id, action, int(payload["target_id"]))
        scope_id = next(iter(payload.get("bound_hashes", {})), "")
        if self._case_action_hash(target, action) != payload.get("bound_hashes", {}).get(scope_id):
            self._audit_case(actual_case_id, action, "blocked", "inputs changed since preview", payload)
            raise StageControlError("inputs changed since preview")
        try:
            if action == "naming_approve":
                from app.naming import agent as naming_agent
                case = self.db.get(Case, actual_case_id)
                assert case is not None
                naming_agent.approve_candidate(self.db, case, int(payload["target_id"]))
            elif action in {"thumbnail_approve", "thumbnail_reject"}:
                from app.thumbnails import service as thumbnail_service
                assert isinstance(target, Thumbnail)
                thumbnail_service.decide(
                    self.db, target, action == "thumbnail_approve",
                    override=action == "thumbnail_approve" and
                    json.loads(target.critic_json or "{}").get("verdict") == "fail",
                )
            elif action == "localization_regenerate":
                from app.agents.localization import LocalizationPipeline
                from app.api.masters import best_master
                case = self.db.get(Case, actual_case_id)
                assert case is not None and isinstance(target, StoryVersion)
                master = best_master(self.db, actual_case_id)
                if master is None:
                    raise StageControlError("no master story found")
                await LocalizationPipeline().localize(self.db, case, master, target.language)
            else:
                raise StageControlError(f"unsupported case action {action!r}")
        except StageControlError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._audit_case(actual_case_id, action, "failed", str(exc), payload)
            raise StageControlError(str(exc)) from exc
        result = {
            "status": "completed",
            "previewed_scope": payload.get("scope", []),
            "executed_scope": payload.get("scope", []),
            "changed_outside_scope": [],
        }
        row.status = "applied"
        row.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
        self.db.commit()
        self._audit_case(actual_case_id, action, "applied", "case action completed", result)
        return {"preview_id": preview_id, "case_id": actual_case_id, **result}

    def audit(self, case_id: int, *, stage: str | None = None,
              language: str | None = None, job_id: int | None = None) -> list[dict]:
        """Read stage-control audit records for the case timeline."""
        production_id = job_id if job_id is not None and self._job(case_id, job_id) else None
        query = (self.db.query(PolicyEvent)
                .filter(PolicyEvent.case_id == case_id,
                        PolicyEvent.rule_id.like("STAGE_CONTROL_%")))
        if production_id is not None:
            query = query.filter(PolicyEvent.production_id == production_id)
        rows = query.order_by(desc(PolicyEvent.created_at), desc(PolicyEvent.id)).limit(200).all()
        events = []
        for row in rows:
            try:
                context = json.loads(row.context_json or "{}")
            except (TypeError, ValueError):
                context = {}
            event_stage = context.get("stage")
            event_language = row.language or context.get("language")
            if stage and event_stage != stage:
                continue
            if language and event_language != language:
                continue
            events.append({
                "id": row.id,
                "created_at": row.created_at,
                "stage": event_stage,
                "language": event_language,
                "rule_id": row.rule_id,
                "decision": row.decision,
                "reason": row.reason,
                "context": context,
            })
        return events
