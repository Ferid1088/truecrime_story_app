"""Read-only helpers for proving the shadow graph's prediction boundary."""
from __future__ import annotations

from collections import defaultdict, deque

from sqlalchemy.orm import Session

from app.db.models import Artifact, ArtifactDep
from app.services.artifact_shadow import registry_consistency


def artifact_snapshot(db: Session, case_id: int) -> dict[str, str]:
    """Return hashes of the currently saved, registered outputs.

    The consistency check recomputes row/file content; a drifted artifact is
    represented by its recomputed hash so the comparison cannot trust the
    registry's stored output hash alone.
    """
    drift = {item["artifact_id"]: item.get("expected")
             for item in registry_consistency(db, case_id=case_id)
             if item.get("expected")}
    snapshot: dict[str, str] = {}
    for row in db.query(Artifact).filter(Artifact.case_id == case_id).all():
        snapshot[row.id] = str(drift.get(row.id) or row.output_hash or "")
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
