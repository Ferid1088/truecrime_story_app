"""The owner's two buttons of the picture review: clear every rejected picture, or every rejected video.

A picture rejected everywhere loses its files (see storage_budget). A picture that is rejected for one requirement
but kept for another keeps its files; its rejected entry is only cleared from the review list. Every verdict stays,
so the planner never judges the same picture for the same requirement again."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import MvpCandidate, MvpRequirement, VisualAsset
from app.documentary.visuals import storage_budget as SB
from app.utils import utc_now


def _listed_rejected(db: Session, case_id: int, kind: str) -> list[MvpCandidate]:
    """The rejected entries of this kind that the review still lists."""
    rows = (db.query(MvpCandidate, VisualAsset)
            .join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
            .join(VisualAsset, VisualAsset.id == MvpCandidate.visual_asset_id)
            .filter(MvpRequirement.case_id == case_id, MvpCandidate.final_status == "rejected",
                    MvpCandidate.dismissed_at.is_(None), VisualAsset.file_removed_at.is_(None)).all())
    return [cand for cand, asset in rows if (asset.asset_type in SB.VIDEO_TYPES) == (kind == "video")]


def preview(db: Session, case_id: int) -> dict:
    """What each button would clear: the pictures (or videos) involved, the entries it hides, the bytes that go."""
    files = SB.rejected_media(db, case_id)
    out = {}
    for kind in SB.KINDS:
        entries = _listed_rejected(db, case_id, kind)
        out[kind] = {"assets": max(files[kind]["assets"], len({c.visual_asset_id for c in entries})),
                     "entries": len(entries), "bytes": files[kind]["bytes"]}
    return out


def clear(db: Session, case_id: int, kind: str) -> dict:
    entries = _listed_rejected(db, case_id, kind)       # before the files go: those of a deleted picture count too
    removed = SB.remove_rejected(db, case_id, kind)
    now = utc_now()
    for cand in entries:
        cand.dismissed_at = now
    db.commit()
    return {**removed, "dismissed": len(entries)}
