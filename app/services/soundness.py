"""Read-only helpers for proving the shadow graph's prediction boundary."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db.models import Artifact, ArtifactDep, DocumentaryJob
from app.services.artifact_shadow import _output_hash, _stage_detail

SNAPSHOT_EXCLUDED_FIELDS = frozenset({
    "id", "card_id", "piece_id", "created_at", "updated_at", "timestamp",
    "started_at", "finished_at", "production_script_id", "job_id", "video_id",
    "artifact_id", "stage_job_id", "host_segments_id", "script_sha256",
})
SNAPSHOT_VOLATILE_JSON_KEYS = frozenset({
    # Generated primary-key/FK references are not output identity.  Their
    # insertion order varies when language stages finish concurrently.
    "id", "card_id", "production_script_id", "job_id", "video_id",
    "artifact_id", "stage_job_id", "host_segments_id",
    "created_at", "updated_at", "timestamp", "started_at", "finished_at", "at",
    # Derived from the raw script serialization, which contains the volatile
    # ids above; the canonical script body remains snapshotted.
    "script_sha256",
    # This records the concurrent observation window used by music selection;
    # selected track identity and rendered bytes remain snapshotted separately.
    "music_history_snapshot",
})
SNAPSHOT_ORDER_INSENSITIVE_LIST_KEYS = frozenset()

# Explicit pipeline outputs. Provider-ledger, policy/audit, monitoring, and
# lifecycle history rows are operational telemetry rather than saved pipeline
# outputs; they are intentionally not part of this artifact snapshot.
SNAPSHOT_PIPELINE_TABLES = (
    "cases", "facts", "sources", "source_chunks", "story_versions",
    "editorial_blueprints", "audio_plans", "research_results",
    "research_source_links", "visual_assets", "visual_plans", "visual_audits",
    "chapter_plans", "host_plans", "host_segments", "host_scenes",
    "voice_performances", "music_tracks", "music_usages", "production_scripts",
    "videos", "thumbnails",
    "media_usages", "reveal_asset_links", "reveal_edges", "reveal_exposures",
)


def _snapshot_value(value, path_replacements: tuple[tuple[str, str], ...] = ()):
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, str):
        for prefix, replacement in path_replacements:
            if value.startswith(prefix):
                return replacement + value[len(prefix):]
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return value
        if isinstance(parsed, (dict, list)):
            return _snapshot_value(parsed, path_replacements)
        return value
    if isinstance(value, dict):
        normalized = {str(k): _snapshot_value(v, path_replacements)
                      for k, v in sorted(value.items(), key=lambda x: str(x[0]))
                      if str(k) not in SNAPSHOT_VOLATILE_JSON_KEYS}
        for key in SNAPSHOT_ORDER_INSENSITIVE_LIST_KEYS:
            entries = normalized.get(key)
            if isinstance(entries, list):
                entries = sorted(entries, key=lambda item: json.dumps(
                    {k: v for k, v in item.items() if k != "position"}
                    if isinstance(item, dict) else item,
                    sort_keys=True, ensure_ascii=False))
                normalized[key] = [
                    {**entry, "position": index} if isinstance(entry, dict) else entry
                    for index, entry in enumerate(entries)]
        return normalized
    if isinstance(value, (list, tuple)):
        return [_snapshot_value(v, path_replacements) for v in value]
    return value


def _row_snapshot(row: dict, path_replacements: tuple[tuple[str, str], ...] = ()) -> dict:
    return {key: _snapshot_value(value, path_replacements) for key, value in sorted(row.items())
            if key not in SNAPSHOT_EXCLUDED_FIELDS}


def saved_output_snapshot(db: Session, *, file_roots: Iterable[Path] = ()) -> dict[str, str]:
    """Hash every saved pipeline-table row and every file below ``file_roots``.

    This intentionally reads persisted values and file bytes only. Registry
    input/output hashes are not consulted, so this is suitable for the actual
    side of a snapshot-diff soundness run. Volatile columns are limited to the
    named ``SNAPSHOT_EXCLUDED_FIELDS`` contract above.
    """
    roots = tuple(Path(root) for root in file_roots)
    replacements = tuple((str(root), f"<{root.name}>") for root in roots)
    snapshot: dict[str, str] = {}
    debug_values: dict[str, object] = {}
    bind = db.get_bind()
    inspector = inspect(bind)
    for table_name in SNAPSHOT_PIPELINE_TABLES:
        if table_name not in inspector.get_table_names():
            continue
        rows = db.execute(text(f'SELECT * FROM "{table_name}"')).mappings().all()
        normalized = sorted((_row_snapshot(dict(row), replacements) for row in rows),
                            key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False))
        for ordinal, row in enumerate(normalized):
            encoded = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            key = f"db:{table_name}:{ordinal}"
            snapshot[key] = hashlib.sha256(encoded.encode()).hexdigest()
            if os.environ.get("SOUNDNESS_SNAPSHOT_VALUES"):
                debug_values[key] = row
    for root in file_roots:
        root = Path(root)
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            raw = path.read_bytes()
            # JSON output files contain generated ids and absolute paths just
            # like JSON DB columns.  Apply the same named volatile-field and
            # root canonicalization contract before hashing; binary outputs
            # remain byte-hashed.
            if path.suffix.lower() == ".json":
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    parsed = None
                if isinstance(parsed, (dict, list)):
                    normalized = _snapshot_value(parsed, replacements)
                    raw = json.dumps(normalized, sort_keys=True,
                                     ensure_ascii=False, separators=(",", ":")) \
                        .encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            key = f"file:{root.name}/{path.relative_to(root).as_posix()}"
            snapshot[key] = digest
    debug_path = os.environ.get("SOUNDNESS_SNAPSHOT_VALUES")
    if debug_path:
        Path(debug_path).write_text(
            json.dumps(debug_values, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return snapshot


@dataclass(frozen=True)
class SnapshotTemplate:
    database_path: Path
    files_path: Path


def create_snapshot_template(database_path: Path, files_path: Path, destination: Path) -> SnapshotTemplate:
    """Create a restorable DB-copy + file-directory baseline template."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    db_copy = destination / "pipeline.db"
    file_copy = destination / "files"
    shutil.copy2(database_path, db_copy)
    shutil.copytree(files_path, file_copy, dirs_exist_ok=True)
    return SnapshotTemplate(db_copy, file_copy)


def restore_snapshot_template(template: SnapshotTemplate, database_path: Path, files_path: Path) -> None:
    """Restore a template without consulting registry state."""
    shutil.copy2(template.database_path, database_path)
    if files_path.exists():
        shutil.rmtree(files_path)
    shutil.copytree(template.files_path, files_path)


def artifact_snapshot(db: Session, case_id: int) -> dict[str, str]:
    """Hash saved outputs directly, without consulting registry hashes.

    This is the ``actual`` side of the soundness harness.  It deliberately
    does not call ``registry_consistency`` and never falls back to
    ``Artifact.output_hash``; otherwise prediction and observation would be
    circular.
    """
    snapshot: dict[str, str] = {}
    for row in db.query(Artifact).filter(Artifact.case_id == case_id).all():
        if row.stage_job_id is None:
            snapshot[row.id] = ""
            continue
        job = db.get(DocumentaryJob, row.stage_job_id)
        if job is None:
            snapshot[row.id] = ""
            continue
        scope = json.loads(row.scope_json or "{}")
        stage = scope.get("stage") or ""
        detail = _stage_detail(job, stage, row.language)
        snapshot[row.id] = _output_hash(db, job, stage, row.language, detail, row.type)
    return snapshot


def downstream_closure(db: Session, artifact_ids: list[str]) -> set[str]:
    """Return a read-only transitive closure, including the changed roots."""
    pending = deque(dict.fromkeys(artifact_ids))
    found: set[str] = set()
    while pending:
        artifact_id = pending.popleft()
        if artifact_id in found:
            continue
        found.add(artifact_id)
        pending.extend(
            edge.artifact_id for edge in db.query(ArtifactDep).filter(
                ArtifactDep.depends_on_id == artifact_id
            ).all()
        )
    return found


def changed_artifacts(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {artifact_id for artifact_id in set(before) | set(after)
            if before.get(artifact_id) != after.get(artifact_id)}


def matrix_row(source: str, predicted: set[str], actual: set[str]) -> dict:
    hidden = sorted(actual - predicted)
    over = sorted(predicted - actual)
    return {
        "source": source,
        "predicted_changed": sorted(predicted),
        "actual_changed": sorted(actual),
        "hidden_inputs": hidden,
        "over_invalidation": over,
        "status": "SOUND" if not hidden else "UNPROVEN",
    }


def group_by_type(rows: list[dict]) -> dict[str, str]:
    statuses: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for artifact_id in row["predicted_changed"]:
            statuses[artifact_id].append(row["status"])
    return {artifact_id: "SOUND" if all(s == "SOUND" for s in values) else "UNPROVEN"
            for artifact_id, values in statuses.items()}
