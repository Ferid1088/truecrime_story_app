"""User-controlled stage state, previews, and bounded reruns."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.core.artifact_dependencies import ARTIFACT_SCOPES
from app.core.policy import PolicyDecision
from app.db.models import AppSetting, Artifact, DocumentaryJob, EditLog, StoryVersion
from app.services.artifact_shadow import STAGE_ARTIFACT_TYPES, _stage_detail
from app.services.policy_events import record_decision
from app.services.soundness import predicted_change_keys
from app.utils import utc_now

ACTIONS = ("continue", "redo_with_prompt", "edit", "run_automatically")


class StageControlError(ValueError):
    """A user action cannot be safely applied to the current saved state."""


class StageControlService:
    """Backend contract for one stage at a time.

    A preview stores the exact artifact ids and content hashes in ``EditLog``.
    Apply refuses stale previews, marks only that stored scope stale, and
    queues the existing job runner.  The runner remains responsible for
    producer execution and conservative treatment of unproven artifacts.
    """

    def __init__(self, db: Session):
        self.db = db

    def _job(self, case_id: int) -> DocumentaryJob:
        job = (self.db.query(DocumentaryJob).filter(DocumentaryJob.case_id == case_id)
               .order_by(DocumentaryJob.id.desc()).first())
        if job is None:
            raise StageControlError("case has no documentary job")
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
                                 "stage": json.loads(row.scope_json or "{}").get("stage")}
                                for row in scope],
            "estimated_provider_calls": {"total": len(scope), "by_stage": dict(sorted(by_stage.items()))},
            "bound_hashes": {row.id: {"input_hash": row.input_hash,
                                      "output_hash": row.output_hash}
                             for row in roots},
        }

    def state(self, case_id: int, stage_name: str) -> dict:
        job = self._job(case_id)
        entry = self._entry(job, stage_name)
        rows = self._rows(job, stage_name)
        pending = self._pending(rows)
        blocked = {
            "continue": "pending user edit or lock" if pending else (
                "stage is currently running" if entry.get("status") == "running" else None),
            "redo_with_prompt": "pending user edit or lock" if pending else (
                "stage is currently running" if entry.get("status") == "running" else None),
            "edit": "stage is currently running" if entry.get("status") == "running" else None,
            "run_automatically": None,
        }
        available = [action for action in ACTIONS if blocked[action] is None]
        setting = self.db.get(AppSetting, f"stage_control:auto:{case_id}:{stage_name}")
        automatic = json.loads(setting.value_json).get("enabled", False) if setting else False
        return {"case_id": case_id, "job_id": job.id, "stage": stage_name,
                "status": entry.get("status", "unknown"),
                "available_actions": available, "blocked_actions": blocked,
                "pending_user_edit": pending, "automatic": automatic,
                "artifacts": [{"id": row.id, "type": row.type, "language": row.language,
                               "status": row.status} for row in rows]}

    def preview(self, case_id: int, stage_name: str, action: str,
                prompt: str | None = None, edit: dict[str, Any] | None = None) -> dict:
        if action not in ACTIONS:
            raise StageControlError(f"unsupported stage action {action!r}")
        job = self._job(case_id)
        state = self.state(case_id, stage_name)
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

    def _apply_edit(self, payload: dict) -> None:
        edit = payload.get("edit") or {}
        for artifact in payload.get("estimated_scope", []):
            if artifact["type"] != "spoken_version":
                continue
            row = self.db.get(Artifact, artifact["id"])
            if row is None:
                continue
            detail = _stage_detail(self._job(payload["case_id"]), "spoken", artifact["language"])
            version_id = detail.get("version_id")
            version = self.db.get(StoryVersion, version_id) if version_id else None
            if version is None or artifact["language"] != edit.get("language", artifact["language"]):
                continue
            text = edit.get("story_text", edit.get("text"))
            chapter_text = edit.get("chapter_text")
            chapter_index = edit.get("chapter_index")
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

    def apply(self, preview_id: int, *, case_id: int | None = None) -> dict:
        row = self.db.get(EditLog, preview_id)
        if row is None or row.status != "previewed":
            raise StageControlError("preview is missing or already applied")
        payload = json.loads(row.scope_preview_json or "{}")
        if case_id is not None and int(payload["case_id"]) != case_id:
            raise StageControlError("preview does not belong to case")
        job = self._job(int(payload["case_id"]))
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
            self._apply_edit(payload)
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

    def set_automatic(self, case_id: int, stage_name: str, enabled: bool) -> dict:
        job = self._job(case_id)
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
