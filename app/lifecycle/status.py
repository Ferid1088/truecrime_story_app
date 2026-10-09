"""Resolution status — SOLVED / UNSOLVED / UNKNOWN / STATUS_UNDER_REVIEW.

A first-class case attribute with a full history: every change records
who changed it (discovery, verifier, monitor, research, user), why, with
which confidence and from which sources. Nothing reads "solved" out of
research notes.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import RESOLUTION_STATUSES
from app.db.models import Case, CaseStatusHistory
from app.utils import utc_now

SOLVED, UNSOLVED, UNKNOWN, UNDER_REVIEW = RESOLUTION_STATUSES

_ALIASES = {
    "solved": SOLVED, "resolved": SOLVED, "closed": SOLVED, "convicted": SOLVED,
    "unsolved": UNSOLVED, "open": UNSOLVED, "cold": UNSOLVED, "cold_case": UNSOLVED,
    "unresolved": UNSOLVED,
    "under_review": UNDER_REVIEW, "status_under_review": UNDER_REVIEW, "review": UNDER_REVIEW,
    "partially_solved": UNDER_REVIEW, "pending": UNDER_REVIEW, "pending_trial": UNDER_REVIEW,
    "unknown": UNKNOWN, "": UNKNOWN,
}


def normalize_status(value: str | None) -> str:
    v = (value or "").strip()
    if v.upper() in RESOLUTION_STATUSES:
        return v.upper()
    return _ALIASES.get(v.lower().replace(" ", "_").replace("-", "_"), UNKNOWN)


def history(db: Session, case_id: int) -> list[CaseStatusHistory]:
    return (db.query(CaseStatusHistory).filter(CaseStatusHistory.case_id == case_id)
            .order_by(CaseStatusHistory.id).all())


def set_resolution(db: Session, case: Case, status: str, *, changed_by: str,
                   reason: str | None = None, confidence: float | None = None,
                   sources: list | None = None, summary: str | None = None,
                   check_id: int | None = None, commit: bool = True
                   ) -> CaseStatusHistory | None:
    """Set the case's status. Writes a history row when the status
    changes (or for the very first status); a confirmation of the same
    status only refreshes confidence and the check time."""
    status = normalize_status(status)
    previous = case.resolution_status or UNKNOWN
    first = not db.query(CaseStatusHistory.id).filter(
        CaseStatusHistory.case_id == case.id).first() if case.id else True
    case.resolution_status = status
    case.resolution_checked_at = utc_now()
    if confidence is not None:
        case.resolution_confidence = round(float(confidence), 3)
    if summary:
        case.resolution_summary = summary[:2000]
    row = None
    if first or previous != status:
        if case.id is None:
            db.flush()
        row = CaseStatusHistory(
            case_id=case.id, previous_status=None if first else previous, new_status=status,
            confidence=confidence, reason=(reason or "")[:4000] or None,
            sources_json=json.dumps(sources or [], ensure_ascii=False),
            changed_by=changed_by, status_check_id=check_id)
        db.add(row)
    if commit:
        db.commit()
    return row


def history_dict(row: CaseStatusHistory) -> dict:
    return {
        "id": row.id, "case_id": row.case_id, "previous_status": row.previous_status,
        "new_status": row.new_status, "confidence": row.confidence, "reason": row.reason,
        "sources": json.loads(row.sources_json or "[]"), "changed_by": row.changed_by,
        "status_check_id": row.status_check_id, "created_at": row.created_at,
    }
