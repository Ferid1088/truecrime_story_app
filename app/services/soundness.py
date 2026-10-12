"""Read-only helpers for proving the shadow graph's prediction boundary."""
from __future__ import annotations

import json
from collections import defaultdict, deque

from sqlalchemy.orm import Session

from app.db.models import Artifact, ArtifactDep, DocumentaryJob
from app.services.artifact_shadow import _output_hash, _stage_detail

SNAPSHOT_EXCLUDED_FIELDS = frozenset({
    "id", "card_id", "piece_id", "created_at", "updated_at", "timestamp",
    "started_at", "finished_at",
})


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
