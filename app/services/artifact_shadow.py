"""Best-effort write-only artifact registration for pipeline stages."""
from __future__ import annotations

import logging
from pathlib import PurePath

from app.core.ai_config import ai_config
from app.core.policy import PolicyDecision
from app.db.models import Artifact
from app.documentary.pipeline.base import record_warning
from app.services.artifacts import ArtifactRegistry
from app.services.policy_events import record_decision

log = logging.getLogger(__name__)

_STAGE_TYPES: dict[str, tuple[str, ...]] = {
    "master_story": ("master_story",),
    "blueprint": ("beats",),
    "audio_plan": ("audio_blocks", "music_cues"),
    "spoken": ("spoken_version",),
    "performance": ("voice_takes",),
    "voice": ("voice_takes",),
    "visual_needs": ("visual_needs",),
    "visual_research": ("visual_pieces",),
    "visual_check": ("visual_pieces",),
    "visual_plan": ("visual_shots", "visual_plan"),
    "chapters": ("chapter_plan", "chapter_cards", "timeline_cards"),
    "production": ("captions", "production_script"),
    "render": ("final_render",),
}


def _relative_ref(detail: dict, artifact_type: str) -> str | None:
    value = detail.get("path") or detail.get("output_ref")
    if not isinstance(value, str) or not value or PurePath(value).is_absolute():
        return f"row:{artifact_type}:{detail.get('id', 'stage')}"
    return value


def register_stage_outputs(db, job, stage: str, language: str | None, detail: dict | None,
                           result: dict) -> None:
    """Register a stage's outputs; registration failures become warnings only."""
    if not ai_config.artifacts.shadow_mode:
        return
    types = _STAGE_TYPES.get(stage)
    if not types:
        return
    try:
        registry = ArtifactRegistry(db)
        for artifact_type in types:
            dependencies: list[str] = []
            for dep_type in __import__("app.core.artifact_dependencies", fromlist=["ARTIFACT_DEPENDENCIES"]).ARTIFACT_DEPENDENCIES[artifact_type]:
                row = (db.query(Artifact)
                       .filter(Artifact.stage_job_id == job.id, Artifact.type == dep_type)
                       .order_by(Artifact.created_at.desc()).first())
                if row is not None:
                    dependencies.append(row.id)
            scope = {"job_id": job.id, "stage": stage, "language": language,
                     "type": artifact_type}
            registry.register(
                artifact_type, scope, {"detail": detail or {}, "stage": stage,
                                       "language": language}, dependencies,
                _relative_ref(detail or {}, artifact_type), created_by="shadow_stage",
                case_id=job.case_id, language=language, stage_job_id=job.id)
    except Exception as exc:  # shadow registration must never fail the job
        reason = f"{type(exc).__name__}: {exc}"
        record_warning(result, stage, reason, language, "ARTIFACT_REGISTRATION_FAILED")
        try:
            record_decision(
                db,
                PolicyDecision(
                    policy="artifact_registry", decision="warning", reason=reason,
                    rule_id="ARTIFACT_REGISTRATION_FAILED", severity="warning",
                    recoverable=True, actions=("continue", "review"),
                    context={"stage": stage, "language": language, "job_id": job.id},
                    code="ARTIFACT_REGISTRATION_FAILED"),
                case_id=job.case_id, language=language)
        except Exception:
            db.rollback()
        log.warning("artifact registration failed for job %s stage %s: %s", job.id, stage, exc)
