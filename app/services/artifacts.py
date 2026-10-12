"""Write/read service for the shadow artifact graph."""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from pathlib import PurePath

from sqlalchemy.orm import Session

from app.core.artifact_dependencies import ARTIFACT_DEPENDENCIES
from app.core.fingerprint import canonical_json, input_fingerprint
from app.db.models import Artifact, ArtifactDep
from app.utils import utc_now


class ArtifactGraphError(ValueError):
    pass


class ArtifactRegistry:
    """Domain service; callers may write in shadow mode but never use it to skip work."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _id(artifact_type: str, scope: dict) -> str:
        digest = input_fingerprint(artifact_type, {"scope": scope}, "artifact-id-v1")[:32]
        return f"artifact_{artifact_type}_{digest}"

    @staticmethod
    def _check_output_ref(output_ref: str | None) -> None:
        if not output_ref:
            return
        if PurePath(output_ref).is_absolute() or output_ref.startswith(("/", "\\")):
            raise ArtifactGraphError("artifact output_ref must be relative or a row reference")

    def register(self, artifact_type: str, scope: dict, inputs, depends_on: Iterable[str] = (),
                 output_ref: str | None = None, *, output_hash: str | None = None,
                 created_by: str | None = None, case_id: int | None = None,
                 production_id: int | None = None, language: str | None = None,
                 stage_job_id: int | None = None, edit_id: str | None = None) -> Artifact:
        if artifact_type not in ARTIFACT_DEPENDENCIES:
            raise ArtifactGraphError(f"unknown artifact type: {artifact_type}")
        self._check_output_ref(output_ref)
        artifact_id = self._id(artifact_type, scope)
        input_hash = input_fingerprint(artifact_type, inputs, "artifact-input-v1")
        row = self.db.get(Artifact, artifact_id)
        if row is None:
            row = Artifact(id=artifact_id, case_id=case_id, production_id=production_id,
                           language=language, type=artifact_type,
                           scope_json=canonical_json(scope), input_hash=input_hash,
                           output_hash=output_hash or input_hash, output_ref=output_ref,
                           status="fresh", version=1, created_by=created_by,
                           edit_id=edit_id, stage_job_id=stage_job_id,
                           created_at=utc_now(), updated_at=utc_now())
            self.db.add(row)
            self.db.flush()
        elif row.input_hash != input_hash:
            row.input_hash = input_hash
            row.output_hash = output_hash or input_hash
            row.output_ref = output_ref
            row.version += 1
            row.status = "fresh"
            row.updated_at = utc_now()
        else:
            # A later audit/approval stage may mutate the same persisted row
            # without changing its declared inputs.  Re-registration is the
            # single write choke point for that case: keep the input version,
            # but make the registry describe the final stored output.
            if output_hash is not None and row.output_hash != output_hash:
                row.output_hash = output_hash
                row.output_ref = output_ref
            row.status = "fresh"
            row.updated_at = utc_now()
        for dependency_id in dict.fromkeys(depends_on):
            if self.db.get(Artifact, dependency_id) is None:
                raise ArtifactGraphError(f"unknown dependency artifact: {dependency_id}")
            if dependency_id == artifact_id or self._would_cycle(artifact_id, dependency_id):
                raise ArtifactGraphError("artifact dependency cycle")
            exists = self.db.query(ArtifactDep).filter_by(
                artifact_id=artifact_id, depends_on_id=dependency_id).first()
            if exists is None:
                self.db.add(ArtifactDep(artifact_id=artifact_id, depends_on_id=dependency_id))
        self.db.commit()
        self.db.refresh(row)
        return row

    def _would_cycle(self, artifact_id: str, dependency_id: str) -> bool:
        pending = deque([dependency_id])
        seen: set[str] = set()
        while pending:
            current = pending.popleft()
            if current in seen:
                continue
            seen.add(current)
            if current == artifact_id:
                return True
            pending.extend(x.depends_on_id for x in self.db.query(ArtifactDep).filter_by(artifact_id=current))
        return False

    def invalidate(self, artifact_ids: Iterable[str], reason: str) -> dict[str, list[str]]:
        pending = deque(dict.fromkeys(artifact_ids))
        affected: set[str] = set()
        while pending:
            artifact_id = pending.popleft()
            if artifact_id in affected:
                continue
            row = self.db.get(Artifact, artifact_id)
            if row is None:
                raise ArtifactGraphError(f"unknown artifact: {artifact_id}")
            affected.add(artifact_id)
            if row.status != "stale":
                row.status = "stale"
                row.updated_at = utc_now()
            pending.extend(x.artifact_id for x in self.db.query(ArtifactDep).filter_by(depends_on_id=artifact_id))
        self.db.commit()
        grouped: dict[str, list[str]] = defaultdict(list)
        for artifact_id in sorted(affected):
            row = self.db.get(Artifact, artifact_id)
            if row is not None:
                grouped[row.type].append(artifact_id)
        return dict(grouped)

    def scope_preview(self, artifact_ids: Iterable[str]) -> dict:
        ids = list(dict.fromkeys(artifact_ids))
        rows = [self.db.get(Artifact, artifact_id) for artifact_id in ids]
        found = [row for row in rows if row is not None]
        counts: dict[str, int] = defaultdict(int)
        for row in found:
            counts[row.type] += 1
        return {"counts": dict(sorted(counts.items())), "total": len(ids),
                "found": len(found), "label": f"{len(found)} of {len(ids)}"}

    def reconcile(self, case_id: int, *, stage_job_id: int | None = None,
                  repair: bool = False) -> list[dict]:
        """Compare registered output hashes with persisted content.

        This is intentionally an explicit shadow-mode operation: it never
        changes pipeline scheduling or skips work.  ``repair=True`` updates
        only the registry's output hash/status, preserving the stored artifact
        and making out-of-band edits visible in the returned report.
        """
        from app.services.artifact_shadow import registry_consistency
        from app.services.policy_events import record_decision
        from app.core.policy import PolicyDecision

        drift = registry_consistency(self.db, stage_job_id=stage_job_id, case_id=case_id)
        if not drift:
            return []
        if repair:
            for item in drift:
                expected = item.get("expected")
                if expected is None:
                    continue
                row = self.db.get(Artifact, item["artifact_id"])
                if row is not None:
                    row.output_hash = expected
                    row.status = "fresh"
                    row.updated_at = utc_now()
            self.db.commit()
        record_decision(
            self.db,
            PolicyDecision(
                policy="artifact_registry", decision="repaired" if repair else "drift",
                reason=f"{len(drift)} registered artifact output hash mismatch(es)",
                rule_id="ARTIFACT_REGISTRY_DRIFT", severity="warning", recoverable=True,
                actions=("continue", "review", "redo"),
                context={"case_id": case_id, "stage_job_id": stage_job_id,
                         "artifact_ids": [item["artifact_id"] for item in drift]},
                code="ARTIFACT_REGISTRY_DRIFT"),
            case_id=case_id)
        return drift
