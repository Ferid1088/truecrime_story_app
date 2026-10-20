"""D4: archive (permanent, fingerprint-matched exclusion from every later
Search) and delete (a soft delete of just this one suggestion, restorable
for a configured window, and never blocking a later Search) for a
discovered case.
"""
from __future__ import annotations

from app.core.ai_config import ai_config
from app.db.models import DiscoveryCandidate
from app.lifecycle.selection import reevaluate_candidate
from app.utils import ensure_utc, utc_now


class DeleteNotAllowed(Exception):
    """A case already created (or published) from this candidate cannot be
    deleted — only archived, from the discover list; the film is untouched."""


class RestoreWindowExpired(Exception):
    """The soft-delete restore window has passed."""


def archive_candidate(db, candidate: DiscoveryCandidate) -> None:
    candidate.state = "archived"
    candidate.archived_at = utc_now()
    candidate.deleted_at = None


def restore_candidate(db, candidate: DiscoveryCandidate) -> None:
    """Undo an archive or a soft delete. The candidate goes back through the
    discovery run's own duplicate and filter path, so it comes back
    suggested, filtered or duplicate exactly as a new run would decide
    now — "accepted" when a case was already created from it."""
    if candidate.state == "deleted" and not _delete_restore_window_open(candidate):
        raise RestoreWindowExpired
    candidate.archived_at = None
    candidate.deleted_at = None
    if candidate.case_id is not None:
        candidate.state = "accepted"
        return
    selection = reevaluate_candidate(db, candidate)
    candidate.state = selection["state"]
    duplicate = selection.get("duplicate") or {}
    is_duplicate = selection["state"] == "duplicate"
    candidate.duplicate_of_case_id = (
        duplicate.get("matched_id") if is_duplicate and duplicate.get("matched_kind") == "case" else None)
    candidate.duplicate_of_candidate_id = (
        duplicate.get("matched_id") if is_duplicate and duplicate.get("matched_kind") == "candidate" else None)
    candidate.duplicate_reason = selection.get("reason") if is_duplicate else None
    if selection["state"] != "suggested":
        candidate.suggestion_reason = selection.get("reason")


def soft_delete_candidate(db, candidate: DiscoveryCandidate) -> None:
    if candidate.case_id is not None:
        raise DeleteNotAllowed
    candidate.state = "deleted"
    candidate.deleted_at = utc_now()


def _delete_restore_window_open(candidate: DiscoveryCandidate) -> bool:
    if candidate.deleted_at is None:
        return False
    days = ai_config.discovery.soft_delete_restore_days
    deleted_at = ensure_utc(candidate.deleted_at)
    return deleted_at is not None and (utc_now() - deleted_at).days < days
