"""Best-effort write-only artifact registration for pipeline stages."""
from __future__ import annotations

import json
import logging
from pathlib import PurePath

from app.core.ai_config import ai_config
from app.core.artifact_dependencies import ARTIFACT_DEPENDENCIES, ARTIFACT_SCOPES
from app.core.policy import PolicyDecision
from app.db.models import (
    Artifact,
    AudioPlan,
    ChapterPlan,
    EditorialBlueprint,
    ProductionScript,
    StoryVersion,
    VisualAsset,
    VisualPlan,
)
from app.documentary.pipeline.base import record_warning
from app.services.artifacts import ArtifactRegistry
from app.services.policy_events import record_decision

log = logging.getLogger(__name__)

# Every current stage is listed, including bookkeeping-only stages.  The
# coverage test intentionally fails when plan_stages() gains a new name.
STAGE_ARTIFACT_TYPES: dict[str, tuple[str, ...]] = {
    "research": (), "master_story": ("master_story",), "master_approval": (),
    "blueprint": ("beats",), "audio_plan": ("audio_blocks",),
    "spoken": ("spoken_version",), "host_plan": (), "chapters": (
        "chapter_plan", "timeline_cards", "chapter_cards"),
    "naming": (), "film_length": (), "visual_needs": ("visual_needs",),
    "visual_research": ("visual_pieces",),
    "visual_check": ("visual_pieces",),
    "visual_plan": ("visual_asset", "visual_shots", "visual_pieces", "visual_plan"),
    "visual_gaps": (), "host": (), "performance": ("voice_takes",),
    "voice": ("voice_takes", "audio_timing", "music_cues"), "production": (
        "captions", "production_script"), "critique": (), "visual_audit": (),
    "render": ("intro", "final_render"),
}

# Kept as a compatibility alias for callers that imported the old private
# name during the first shadow implementation.
_STAGE_TYPES = STAGE_ARTIFACT_TYPES


def _relative_ref(detail: dict, artifact_type: str) -> str | None:
    value = detail.get("path") or detail.get("output_ref")
    if not isinstance(value, str) or not value or PurePath(value).is_absolute():
        return f"row:{artifact_type}:{detail.get('id', 'stage')}"
    return value


def _row_payload(row) -> dict:
    if row is None:
        return {}
    fields = {}
    for name in ("id", "version", "status", "language", "text_hash", "story_text",
                 "blueprint_json", "plan_json", "script_json", "validation_json",
                 "duration_seconds", "render_json", "generation_model"):
        if hasattr(row, name):
            fields[name] = getattr(row, name)
    return fields


def _latest_version(db, job, language: str | None):
    query = db.query(StoryVersion).filter(StoryVersion.case_id == job.case_id)
    if language:
        query = query.filter(StoryVersion.language == language)
    return query.order_by(StoryVersion.id.desc()).first()


def _source_payload(db, job, stage: str, language: str | None, detail: dict | None,
                    artifact_type: str) -> dict:
    """Capture the persisted data and config read by the producer.

    This is deliberately a conservative superset for shadow mode: a future
    registry consumer must not reuse an artifact because only a summary field
    happened to be unchanged.
    """
    detail = detail or {}
    source: dict = {"stage": stage, "language": language, "detail": detail}
    if artifact_type == "master_story":
        source["story"] = _row_payload(db.get(StoryVersion, detail.get("master_version_id")))
    elif artifact_type == "beats":
        source["blueprint"] = _row_payload(db.get(EditorialBlueprint, detail.get("blueprint_id")))
    elif artifact_type in ("audio_blocks", "music_cues"):
        audio_plan_id = detail.get("audio_plan_id")
        source["audio_plan"] = _row_payload(
            db.get(AudioPlan, audio_plan_id) if audio_plan_id is not None else None)
        if artifact_type == "music_cues":
            for key in ("music_history_snapshot", "music_library_identity",
                        "selection_reasons"):
                if key in detail:
                    source[key] = detail[key]
    elif artifact_type == "spoken_version":
        source["spoken"] = _row_payload(db.get(StoryVersion, detail.get("version_id")))
    elif artifact_type in ("voice_takes", "audio_timing"):
        version = _latest_version(db, job, language)
        source["voice"] = _row_payload(version)
        if version is not None:
            source["story_version_id"] = version.id
    elif artifact_type in ("visual_needs", "visual_shots", "visual_plan"):
        row = db.get(VisualPlan, detail.get("visual_plan_id"))
        source["visual_plan"] = _row_payload(row)
    elif artifact_type in ("chapter_plan", "chapter_cards", "timeline_cards"):
        row = db.get(ChapterPlan, detail.get("chapter_plan_id"))
        source["chapters"] = _row_payload(row)
    elif artifact_type in ("captions", "production_script"):
        production_id = detail.get("production_script_id")
        source["production"] = _row_payload(
            db.get(ProductionScript, production_id) if production_id is not None else None)
    elif artifact_type == "visual_asset":
        source["assets"] = [_row_payload(a) for a in db.query(VisualAsset)
                             .filter(VisualAsset.case_id == job.case_id).all()]
    source["config"] = {
        "documentary": ai_config.documentary.model_dump(mode="json"),
        "voice": ai_config.voice.model_dump(mode="json"),
        "audio_direction": ai_config.audio_direction.model_dump(mode="json"),
        "music_library": ai_config.music_library.model_dump(mode="json"),
        "render": ai_config.render.model_dump(mode="json"),
        "chapters": ai_config.chapters.model_dump(mode="json"),
        "visual_direction": ai_config.visual_direction.model_dump(mode="json"),
    }
    return source


def _language_rows(job, language: str | None) -> list[str | None]:
    if language:
        return [language]
    try:
        return [str(x) for x in json.loads(job.languages_json or "[]")]
    except (TypeError, ValueError):
        return [None]


def register_stage_outputs(db, job, stage: str, language: str | None, detail: dict | None,
                           result: dict) -> None:
    """Register a stage's outputs; registration failures become warnings only."""
    if not ai_config.artifacts.shadow_mode:
        return
    types = STAGE_ARTIFACT_TYPES.get(stage)
    if not types:
        return
    try:
        registry = ArtifactRegistry(db)
        for artifact_type in types:
            languages = _language_rows(job, language) if ARTIFACT_SCOPES[artifact_type] == "per_language" else [None]
            for artifact_language in languages:
                dependencies: list[str] = []
                for dep_type in ARTIFACT_DEPENDENCIES[artifact_type]:
                    query = db.query(Artifact).filter(
                        Artifact.stage_job_id == job.id, Artifact.type == dep_type)
                    if ARTIFACT_SCOPES[dep_type] == "per_language":
                        query = query.filter(Artifact.language == artifact_language)
                    else:
                        query = query.filter(Artifact.language.is_(None))
                    row = query.order_by(Artifact.created_at.desc()).first()
                    if row is not None:
                        dependencies.append(row.id)
                scope = {"job_id": job.id, "stage": stage, "language": artifact_language,
                         "type": artifact_type}
                registry.register(
                    artifact_type, scope,
                    _source_payload(db, job, stage, artifact_language, detail, artifact_type),
                    dependencies, _relative_ref(detail or {}, artifact_type),
                    created_by="shadow_stage", case_id=job.case_id,
                    language=artifact_language, stage_job_id=job.id)
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
