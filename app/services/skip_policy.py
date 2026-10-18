"""Hash-aware reuse decisions.

This module is deliberately separate from the pipeline runner.  It can prove
that a saved artifact is reusable, but it never changes a producer's output.
The runner is the only caller that may turn an approved decision into a skip.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.core.ai_config import ai_config
from app.core.artifact_dependencies import ARTIFACT_SCOPES
from app.core.policy import PolicyDecision, PolicyError
from app.db.models import Artifact, DocumentaryJob, EditLog
from app.services.artifact_shadow import (
    STAGE_ARTIFACT_TYPES,
    _output_hash,
    _source_payload,
    _stage_detail,
)
from app.services.policy_events import record_decision

_STATUS_PATH = Path(__file__).resolve().parents[2] / "config" / "soundness_status.json"


def load_soundness_status() -> dict[str, str]:
    payload = json.loads(_STATUS_PATH.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in payload["statuses"].items()}


# Keeping the conservative status as data makes it impossible for the skip
# policy to silently promote a type in Python code. The file must be replaced
# by a completed snapshot-diff run before any type becomes SOUND.
SOUNDNESS = load_soundness_status()


@dataclass(frozen=True, slots=True)
class SkipResult:
    decision: PolicyDecision
    artifacts: tuple[str, ...] = ()


class SkipPolicy:
    """Evaluate all safety gates required before a stage may be reused."""

    def __init__(self, db, *, soundness: dict[str, str] | None = None):
        self.db = db
        self.soundness = soundness or SOUNDNESS

    def validate_configuration(self) -> None:
        cfg = ai_config.production_policies.incremental
        unknown = set(cfg.enforced_types) - set(ARTIFACT_SCOPES)
        if unknown:
            raise PolicyError("INCREMENTAL_UNPROVEN",
                              f"Unknown incremental artifact type(s): {sorted(unknown)}")
        unproven = [name for name in cfg.enforced_types
                    if self.soundness.get(name) != "SOUND"]
        if cfg.mode == "enforce" and unproven:
            raise PolicyError(
                "INCREMENTAL_UNPROVEN",
                f"Cannot enforce hash-aware skipping for unproven type(s): {unproven}",
                {"types": unproven},
            )

    def _event(self, decision: PolicyDecision, job: DocumentaryJob,
               language: str | None) -> PolicyDecision:
        record_decision(self.db, decision, case_id=job.case_id,
                        production_id=job.id, language=language)
        return decision

    def reusable_reasons(self, row: Artifact, job: DocumentaryJob) -> list[str]:
        """Pure gate inspection used by read-only scope preview."""
        reasons: list[str] = []
        if row.status not in ("fresh", "ok"):
            reasons.append(f"status is {row.status}")
        stage = json.loads(row.scope_json or "{}").get("stage", "")
        detail = _stage_detail(job, stage, row.language)
        if not detail and stage in ("master_story", "language_pipeline"):
            detail = _stage_detail(job, "master_approval" if stage == "master_story" else "final_approval",
                                   row.language)
        expected_inputs = _source_payload(self.db, job,
                                           stage,
                                           row.language, detail, row.type)
        from app.core.fingerprint import input_fingerprint

        if input_fingerprint(row.type, expected_inputs, "artifact-input-v1") != row.input_hash:
            reasons.append("input hash changed")
        if _output_hash(self.db, job, stage, row.language, detail, row.type) != row.output_hash:
            reasons.append("output hash changed")
        if self.soundness.get(row.type) != "SOUND":
            reasons.append("type is unproven")
        if self.pending_edit([row.id]):
            reasons.append("pending user edit or lock")
        return reasons

    def evaluate(self, job: DocumentaryJob, stage_name: str, *,
                 pending_edit: bool = False, dependencies_reran: bool = False) -> SkipResult:
        self.validate_configuration()
        cfg = ai_config.production_policies.incremental
        language = next((lang for lang in json.loads(job.languages_json or "[]")
                         if stage_name.endswith(f":{lang}")), None)
        stage = stage_name[:-len(language) - 1] if language else stage_name
        types = STAGE_ARTIFACT_TYPES.get(stage, ())
        rows = self.db.query(Artifact).filter(
            Artifact.stage_job_id == job.id, Artifact.type.in_(types))
        if language:
            rows = rows.filter((Artifact.language == language) | Artifact.language.is_(None))
        rows = rows.all()
        pending_edit = pending_edit or self.pending_edit([row.id for row in rows])
        reasons: list[str] = []
        if cfg.mode == "off":
            reasons.append("incremental mode is off")
        if not rows:
            reasons.append("no registered artifacts for stage")
        if dependencies_reran:
            reasons.append("a dependency ran in this job")
        if pending_edit:
            reasons.append("pending user edit or lock")
        for row in rows:
            reasons.extend(f"{row.id}: {reason}" for reason in self.reusable_reasons(row, job))
        allowed = cfg.mode in ("verify", "enforce") and bool(rows) and not reasons
        if cfg.mode == "enforce" and rows and any(
                self.soundness.get(row.type) != "SOUND" or row.type not in cfg.enforced_types
                for row in rows):
            reasons.append("artifact type is not in enforced_types")
            allowed = False
        decision = PolicyDecision(
            policy="incremental", decision="skip" if allowed else "run",
            reason="; ".join(reasons) if reasons else "all hash and policy gates passed",
            rule_id="INCREMENTAL_SKIP" if allowed else "INCREMENTAL_RUN",
            severity="info", recoverable=True,
            actions=("continue", "review"),
            context={"job_id": job.id, "stage": stage_name,
                     "artifact_ids": [row.id for row in rows], "mode": cfg.mode},
        )
        self._event(decision, job, language)
        return SkipResult(decision, tuple(row.id for row in rows))

    def pending_edit(self, artifact_ids: list[str]) -> bool:
        if not artifact_ids:
            return False
        return self.db.query(EditLog).filter(
            EditLog.scope_id.in_(artifact_ids),
            EditLog.status.in_(("previewed", "running")),
        ).first() is not None
