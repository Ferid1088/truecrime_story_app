"""Which cases are archived: the owner archived them, or every one of their shorts is published."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Case, RenderedShort
from app.utils import utc_now

MANUAL = "manual"
ALL_PUBLISHED = "all_published"


@dataclass(frozen=True)
class ShortsProgress:
    total: int
    published: int

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.published == self.total


def shorts_progress(db: Session, case_id: int) -> ShortsProgress:
    """A rejected short is never published, so it does not hold the case back."""
    rows = db.query(RenderedShort).filter(
        RenderedShort.case_id == case_id, RenderedShort.approval_state != "rejected").all()
    return ShortsProgress(len(rows), sum(1 for row in rows if row.published_at is not None))


def archive_reason(db: Session, case: Case) -> str | None:
    if case.archived_at is not None:
        return MANUAL
    return ALL_PUBLISHED if shorts_progress(db, case.id).complete else None


def archive_dict(db: Session, case: Case) -> dict:
    reason = archive_reason(db, case)
    return {"archived": reason is not None, "archive_reason": reason}


def archive(db: Session, case: Case) -> None:
    case.archived_at = utc_now()
    db.commit()


def unarchive(db: Session, case: Case) -> None:
    case.archived_at = None
    db.commit()


def set_published(db: Session, short: RenderedShort, published: bool) -> None:
    short.published_at = utc_now() if published else None
    db.commit()
