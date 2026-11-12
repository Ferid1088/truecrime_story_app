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


VIDEO_TYPES = ("video", "video_source")
KINDS = ("photo", "video")


def _of_kind(a: VisualAsset, kind: str) -> bool:
    return (a.asset_type in VIDEO_TYPES) == (kind == "video")


def _rejected_with_files(db: Session, case_id: int) -> list[VisualAsset]:
    return db.query(VisualAsset).filter(
        VisualAsset.case_id == case_id, VisualAsset.verification_status == "rejected",
        VisualAsset.file_removed_at.is_(None),
        (VisualAsset.local_path.isnot(None)) | (VisualAsset.thumbnail_path.isnot(None))).all()


def _remove_files(db: Session, case_id: int, assets: list[VisualAsset]) -> dict:
    """Delete the files of these assets and mark them removed (metadata and verdict stay, so a picture is
    never fetched or judged again). A file that an asset which stays still points at (the pieces of one video
    share its proxy) is kept."""
    going = {a.id for a in assets}
    kept = {path for a in db.query(VisualAsset).filter(VisualAsset.case_id == case_id)
            if a.id not in going for path in (a.local_path, a.thumbnail_path) if path}
    removed = freed = 0
    deleted: set[str] = set()
    for a in assets:
        for path in (a.local_path, a.thumbnail_path):
            p = storage.resolve(path)
            if path and path not in kept and path not in deleted and p is not None and p.exists():
                freed += p.stat().st_size
                p.unlink()
                deleted.add(path)
        a.local_path = a.thumbnail_path = None
        a.file_removed_at = utc_now()
        removed += 1
    db.commit()
    return {"removed": removed, "bytes": freed}


def purge_rejected(db: Session, case_id: int) -> dict:
    """Remove the files of rejected pictures older than the retention period."""
    cutoff = utc_now() - timedelta(days=ai_config.storage_budget.rejected_retention_days)
    return _remove_files(db, case_id, [a for a in _rejected_with_files(db, case_id) if a.created_at < cutoff])


def rejected_media(db: Session, case_id: int) -> dict:
    """What the owner's delete buttons would remove now: the rejected pictures and videos still holding files."""
    out = {kind: {"assets": 0, "bytes": 0} for kind in KINDS}
    counted: set[str] = set()
    for a in _rejected_with_files(db, case_id):
        kind = "video" if a.asset_type in VIDEO_TYPES else "photo"
        out[kind]["assets"] += 1
        out[kind]["bytes"] += _files(a, counted)
    return out


def remove_rejected(db: Session, case_id: int, kind: str) -> dict:
    """The owner's button: delete the files of every rejected picture (kind 'photo') or video (kind 'video')."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    return _remove_files(db, case_id, [a for a in _rejected_with_files(db, case_id) if _of_kind(a, kind)])
