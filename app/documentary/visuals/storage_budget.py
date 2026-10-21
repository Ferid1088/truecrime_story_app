"""Disk use of a case's pictures and clips: what it holds, the budget, and the removal of the
files of old rejected pictures (their metadata and the reason stay)."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import VisualAsset
from app.documentary import storage
from app.utils import utc_now

MB = 1024 * 1024


def _size(path: str | None) -> int:
    p = storage.resolve(path)
    return p.stat().st_size if p is not None and p.exists() else 0


def _files(a: VisualAsset, counted: set[str] | None = None) -> int:
    """Bytes of the asset's files; a file several assets point at (the pieces of one video share
    its proxy) is counted once when `counted` is given."""
    total = 0
    for path in (a.local_path, a.thumbnail_path):
        if path and (counted is None or path not in counted):
            total += _size(path)
            if counted is not None:
                counted.add(path)
    return total


def usage(db: Session, case_id: int) -> dict:
    """Bytes the case's stored pictures hold, by kind of asset and by verdict."""
    by_kind: Counter = Counter()
    by_status: Counter = Counter()
    files = 0
    counted: set[str] = set()
    for a in db.query(VisualAsset).filter(VisualAsset.case_id == case_id):
        size = _files(a, counted)
        if size:
            files += 1
        by_kind[a.asset_type if a.provider != "generated" else "generated"] += size
        by_status[a.verification_status] += size
    return {"bytes": sum(by_kind.values()), "files": files, "by_kind": dict(by_kind),
            "by_status": dict(by_status), "budget_mb": ai_config.storage_budget.max_case_mb}


def exceeded(db: Session, case_id: int) -> bool:
    return usage(db, case_id)["bytes"] >= ai_config.storage_budget.max_case_mb * MB


def purge_rejected(db: Session, case_id: int) -> dict:
    """Remove the files of rejected pictures older than the retention period."""
    cutoff = utc_now() - timedelta(days=ai_config.storage_budget.rejected_retention_days)
    removed = freed = 0
    for a in db.query(VisualAsset).filter(
            VisualAsset.case_id == case_id, VisualAsset.verification_status == "rejected",
            VisualAsset.file_removed_at.is_(None), VisualAsset.created_at < cutoff):
        size = _files(a)
        if not size and not (a.local_path or a.thumbnail_path):
            continue
        for path in (a.local_path, a.thumbnail_path):
            p = storage.resolve(path)
            if p is not None and p.exists():
                p.unlink()
        a.local_path = a.thumbnail_path = None
        a.file_removed_at = utc_now()
        removed += 1
        freed += size
    db.commit()
    return {"removed": removed, "bytes": freed}
