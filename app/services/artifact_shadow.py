"""Best-effort write-only artifact registration for pipeline stages."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path, PurePath

from app.core.ai_config import ai_config
from app.core.artifact_dependencies import ARTIFACT_DEPENDENCIES, ARTIFACT_SCOPES
from app.core.fingerprint import canonical_json
from app.core.policy import PolicyDecision
from app.db.models import (
    Artifact,
    AudioPlan,
    ChapterPlan,
    DocumentaryJob,
    EditorialBlueprint,
    HostPlan,
    HostSegments,
    ProductionScript,
    StoryVersion,
    VisualAsset,
    VisualPlan,
)
from app.documentary.pipeline.base import record_warning
from app.services.artifacts import ArtifactRegistry
from app.services.policy_events import record_decision

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]

# Every current stage is listed, including bookkeeping-only stages.  The
# coverage test intentionally fails when plan_stages() gains a new name.
STAGE_ARTIFACT_TYPES: dict[str, tuple[str, ...]] = {
    "research": ("research_result",), "master_story": ("master_story",),
    "master_approval": ("master_story",),
    "blueprint": ("beats",), "audio_plan": ("audio_blocks",),
    "spoken": ("spoken_version",), "host_plan": ("host_plan",), "chapters": (
        "chapter_plan", "timeline_cards", "chapter_cards"),
    "naming": ("naming",), "film_length": ("film_length",),
    "visual_needs": ("visual_needs",),
    "visual_research": ("visual_pieces",),
    "visual_check": ("visual_pieces",),
    "visual_plan": ("visual_asset", "visual_shots", "visual_pieces", "visual_plan"),
    "visual_gaps": ("visual_plan",), "host": ("host_segments",),
    "performance": ("voice_takes",),
    "voice": ("voice_takes", "audio_timing", "music_cues"), "production": (
        "captions", "production_script"), "critique": ("production_script",),
    "visual_audit": ("production_script",),
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
    if isinstance(row, VisualAsset):
        fields.update({name: getattr(row, name) for name in (
            "asset_code", "asset_type", "asset_role", "rights_status",
            "verification_status", "verification_confidence", "sha256", "duration_seconds")})
    return fields


def _get(db, model, primary_key):
    return db.get(model, primary_key) if primary_key is not None else None


def _asset_bytes_sha256(asset: VisualAsset) -> str | None:
    value = asset.local_path
    if not isinstance(value, str) or not value:
        return asset.sha256
    path = Path(value) if Path(value).is_absolute() else ROOT / value
    if not path.is_file():
        return asset.sha256
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _asset_codes(value) -> set[str]:
    """Find the editorial asset identities actually referenced by a plan."""
    found: set[str] = set()
    if isinstance(value, dict):
        asset_id = value.get("asset_id")
        if asset_id not in (None, ""):
            found.add(str(asset_id))
        for child in value.values():
            found.update(_asset_codes(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_asset_codes(child))
    return found


def _visual_asset_rows(db, job, detail: dict) -> list[VisualAsset]:
    row = _get(db, VisualPlan, detail.get("visual_plan_id"))
    if row is None:
        return []
    try:
        used = _asset_codes(json.loads(row.plan_json or "{}"))
    except (TypeError, ValueError):
        used = set()
    assets = db.query(VisualAsset).filter(VisualAsset.case_id == job.case_id).all()
    return [asset for asset in assets if asset.asset_code in used]


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
    source: dict = {"stage": stage, "language": language, "detail": detail,
                    "job_languages": _language_rows(job, None)}
    if artifact_type in ("master_story", "master_approval"):
        source["story"] = _row_payload(_get(db, StoryVersion, detail.get("master_version_id")))
    elif artifact_type == "beats":
        source["blueprint"] = _row_payload(_get(db, EditorialBlueprint, detail.get("blueprint_id")))
    elif artifact_type in ("audio_blocks", "music_cues"):
        audio_plan_id = detail.get("audio_plan_id")
        source["audio_plan"] = _row_payload(
            _get(db, AudioPlan, audio_plan_id))
        if artifact_type == "music_cues":
            for key in ("music_history_snapshot", "music_library_identity",
                        "selection_reasons"):
                if key in detail:
                    source[key] = detail[key]
    elif artifact_type == "spoken_version":
        source["spoken"] = _row_payload(_get(db, StoryVersion, detail.get("version_id")))
    elif artifact_type in ("voice_takes", "audio_timing"):
        version = _latest_version(db, job, language)
        source["voice"] = _row_payload(version)
        if version is not None:
            source["story_version_id"] = version.id
    elif artifact_type in ("visual_needs", "visual_shots", "visual_plan", "visual_gaps"):
        row = _get(db, VisualPlan, detail.get("visual_plan_id"))
        if row is None:
            row = (db.query(VisualPlan).filter(VisualPlan.case_id == job.case_id)
                   .order_by(VisualPlan.id.desc()).first())
        source["visual_plan"] = _row_payload(row)
        if row is not None and artifact_type != "visual_plan":
            try:
                plan = json.loads(row.plan_json or "{}")
            except (TypeError, ValueError):
                plan = {}
            source["visual_member"] = (
                plan.get("requirements") if artifact_type == "visual_needs"
                else plan.get("shots") if artifact_type == "visual_shots"
                else plan.get("pieces"))
    elif artifact_type == "host_plan":
        source["host_plan"] = _row_payload(_get(db, HostPlan, detail.get("host_plan_id")))
    elif artifact_type == "host_segments":
        source["host_segments"] = _row_payload(
            _get(db, HostSegments, detail.get("host_segments_id")))
    elif artifact_type in ("chapter_plan", "chapter_cards", "timeline_cards"):
        row = _get(db, ChapterPlan, detail.get("chapter_plan_id"))
        source["chapters"] = _row_payload(row)
    elif artifact_type in ("captions", "production_script"):
        production_id = detail.get("production_script_id")
        source["production"] = _row_payload(
            _get(db, ProductionScript, production_id))
    elif artifact_type == "visual_asset":
        asset = _get(db, VisualAsset, detail.get("asset_id"))
        source["asset"] = _row_payload(asset)
        if asset is not None:
            source["asset_file_sha256"] = _asset_bytes_sha256(asset)
    # Shadow mode intentionally uses a complete typed-config snapshot.  This
    # is conservative: every section read by a producer (including agent
    # prompts/routing, pronunciation and channel/studio identity) is an
    # explicit input instead of an accidental hidden default.
    source["config"] = ai_config.model_dump(mode="json")
    channel_identity = {}
    for lang, channel in ai_config.channels.items():
        item = channel.model_dump(mode="json")
        logo = item.get("logo")
        if isinstance(logo, str):
            path = Path(logo) if Path(logo).is_absolute() else ROOT / logo
            if path.is_file():
                item["logo_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        channel_identity[lang] = item
    source["channel_identity"] = channel_identity
    try:
        from app.documentary import intros

        source["intro_design"] = {
            "version": intros.VERSION,
            "concepts": {key: value.__dict__ for key, value in intros.CONCEPTS.items()},
        }
    except Exception:  # noqa: BLE001 - shadow capture must stay best effort
        source["intro_design"] = {"version": None}
    return source


def _language_rows(job, language: str | None) -> list[str | None]:
    if language:
        return [language]
    try:
        return [str(x) for x in json.loads(job.languages_json or "[]")]
    except (TypeError, ValueError):
        return [None]


def _output_payload(db, job, stage: str, language: str | None, detail: dict,
                    artifact_type: str) -> dict:
    """Canonical representation of the content persisted by a stage."""
    payload: dict = {"artifact_type": artifact_type}
    if artifact_type == "visual_asset":
        payload["row"] = _row_payload(_get(db, VisualAsset, detail.get("asset_id")))
    elif artifact_type in ("master_story", "master_approval"):
        payload["row"] = _row_payload(_get(db, StoryVersion, detail.get("master_version_id")))
    elif artifact_type == "beats":
        payload["row"] = _row_payload(_get(db, EditorialBlueprint, detail.get("blueprint_id")))
    elif artifact_type in ("audio_blocks", "music_cues"):
        payload["row"] = _row_payload(_get(db, AudioPlan, detail.get("audio_plan_id")))
    elif artifact_type in ("visual_needs", "visual_shots", "visual_plan", "visual_gaps"):
        row = _get(db, VisualPlan, detail.get("visual_plan_id"))
        if row is None:
            row = (db.query(VisualPlan).filter(VisualPlan.case_id == job.case_id)
                   .order_by(VisualPlan.id.desc()).first())
        payload["row"] = _row_payload(row)
        if row is not None and artifact_type != "visual_plan":
            try:
                plan = json.loads(row.plan_json or "{}")
            except (TypeError, ValueError):
                plan = {}
            payload["visual_member"] = (
                plan.get("requirements") if artifact_type == "visual_needs"
                else plan.get("shots") if artifact_type == "visual_shots"
                else plan.get("pieces"))
    elif artifact_type == "host_plan":
        payload["row"] = _row_payload(_get(db, HostPlan, detail.get("host_plan_id")))
    elif artifact_type == "host_segments":
        payload["row"] = _row_payload(_get(db, HostSegments, detail.get("host_segments_id")))
    elif artifact_type in ("chapter_plan", "chapter_cards", "timeline_cards"):
        payload["row"] = _row_payload(_get(db, ChapterPlan, detail.get("chapter_plan_id")))
    elif artifact_type in ("captions", "production_script"):
        payload["row"] = _row_payload(_get(db, ProductionScript, detail.get("production_script_id")))
    else:
        # Stages without a dedicated persisted row expose their saved result
        # through the stage detail.  Row-backed artifacts intentionally omit
        # bookkeeping detail so a later approval/audit can re-register the
        # same final row without hashing transient stage metadata.
        payload["detail"] = detail
    return payload


def _output_hash(db, job, stage: str, language: str | None, detail: dict,
                 artifact_type: str) -> str:
    output_ref = detail.get("path") or detail.get("output_ref")
    if isinstance(output_ref, str) and not PurePath(output_ref).is_absolute():
        path = ROOT / output_ref
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    payload = _output_payload(db, job, stage, language, detail, artifact_type)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _stage_detail(job, stage: str, language: str | None) -> dict:
    try:
        entries = json.loads(job.stages_json or "[]")
    except (TypeError, ValueError):
        return {}
    if not isinstance(entries, list):
        return {}
    name = f"{stage}:{language}" if language else stage
    names = {name}
    if stage == "production" and language:
        names.update({f"critique:{language}", f"visual_audit:{language}",
                      f"render:{language}"})
    matches = [item for item in entries
               if isinstance(item, dict) and item.get("name") in names]
    if stage == "production":
        matches = [item for item in matches
                   if isinstance(item.get("detail"), dict)
                   and item["detail"].get("production_script_id")] or matches
    entry: dict = matches[-1] if matches else {}
    detail = entry.get("detail")
    return detail if isinstance(detail, dict) else {}


def registry_consistency(db, stage_job_id: int | None = None,
                         case_id: int | None = None) -> list[dict]:
    """Read-only check that registered output hashes match saved content."""
    query = db.query(Artifact)
    if stage_job_id is not None:
        query = query.filter(Artifact.stage_job_id == stage_job_id)
    if case_id is not None:
        query = query.filter(Artifact.case_id == case_id)
    mismatches = []
    for artifact in query.all():
        if artifact.stage_job_id is None:
            mismatches.append({"artifact_id": artifact.id, "type": artifact.type,
                               "language": artifact.language, "stage": None,
                               "reason": "no stage job", "actual": artifact.output_hash,
                               "expected": None})
            continue
        job = db.get(DocumentaryJob, artifact.stage_job_id)
        if job is None:
            mismatches.append({"artifact_id": artifact.id, "type": artifact.type,
                               "language": artifact.language, "stage": None,
                               "reason": "missing stage job", "actual": artifact.output_hash,
                               "expected": None})
            continue
        scope = json.loads(artifact.scope_json or "{}")
        stage = scope.get("stage") or ""
        detail = _stage_detail(job, stage, artifact.language)
        if artifact.type == "visual_asset" and scope.get("asset_id"):
            asset = (db.query(VisualAsset)
                     .filter(VisualAsset.case_id == job.case_id,
                             VisualAsset.asset_code == scope["asset_id"])
                     .first())
            if asset is not None:
                detail = {**detail, "asset_id": asset.id,
                          "asset_code": asset.asset_code}
        expected = _output_hash(db, job, stage, artifact.language, detail, artifact.type)
        if expected != artifact.output_hash:
            mismatches.append({"artifact_id": artifact.id, "type": artifact.type,
                               "language": artifact.language, "stage": stage,
                               "expected": expected, "actual": artifact.output_hash})
    return mismatches


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
            asset_rows = (db.query(VisualAsset)
                          .filter(VisualAsset.case_id == job.case_id).all()
                          if artifact_type == "visual_asset" else [None])
            if artifact_type == "visual_asset" and not asset_rows:
                continue
            for asset in asset_rows:
                artifact_detail = dict(detail or {})
                if asset is not None:
                    artifact_detail["asset_id"] = asset.id
                    artifact_detail["asset_code"] = asset.asset_code
                languages = (_language_rows(job, language)
                             if ARTIFACT_SCOPES[artifact_type] == "per_language" else [None])
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
                    if artifact_type in {"visual_shots", "visual_pieces", "visual_plan"}:
                        used_assets = _visual_asset_rows(db, job, detail or {})
                        for asset_row in used_assets:
                            asset_rows_query = db.query(Artifact).filter(
                                Artifact.stage_job_id == job.id,
                                Artifact.type == "visual_asset")
                            for candidate in asset_rows_query.all():
                                try:
                                    scope = json.loads(candidate.scope_json or "{}")
                                except (TypeError, ValueError):
                                    scope = {}
                                if scope.get("asset_id") == asset_row.asset_code:
                                    dependencies.append(candidate.id)
                    scope_stage = {"master_approval": "master_story",
                                   "visual_gaps": "visual_plan",
                                   "critique": "production",
                                   "visual_audit": "production"}.get(stage, stage)
                    scope = {"job_id": job.id, "stage": scope_stage,
                             "language": artifact_language,
                             "type": artifact_type,
                             **({"asset_id": asset.asset_code} if asset is not None else {})}
                    registry.register(
                        artifact_type, scope,
                        _source_payload(db, job, scope_stage, artifact_language, artifact_detail,
                                        artifact_type),
                        dependencies, _relative_ref(detail or {}, artifact_type),
                        output_hash=_output_hash(db, job, scope_stage, artifact_language,
                                                 artifact_detail, artifact_type),
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
