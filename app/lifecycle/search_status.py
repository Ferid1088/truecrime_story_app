"""D3: per-case/per-language status for a discovered case, and the per-Search
(discovery job) counts built from it. Always computed live from Case/Video/
DiscoveryCandidate — never a stored snapshot, so a publish or an archive is
reflected the instant it happens, with no extra write of its own.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Case, DiscoveryCandidate, Video

LANGUAGES = ("en", "de", "fa", "ar")

# The states counted toward a Search's totals: everything the user was
# actually shown. Duplicates/filtered/ignored candidates were never
# suggested and are reported separately (ResearchJob.result.selection_stats).
_COUNTED_CANDIDATE_STATES = ("suggested", "accepted", "archived")


def candidate_status(db: Session, candidate: DiscoveryCandidate) -> str:
    """The furthest state across every language: free (no case created),
    created_not_published, created_published, or archived."""
    if candidate.state == "archived":
        return "archived"
    if candidate.case_id is None:
        return "free"
    case = db.get(Case, candidate.case_id)
    if case is None:
        return "free"
    if case.status == "archived":
        return "archived"
    published = (
        db.query(Video)
        .filter(Video.case_id == case.id, Video.state == "published")
        .first()
    )
    return "created_published" if published is not None else "created_not_published"


def language_badges(db: Session | None, case_id: int | None) -> dict[str, str]:
    """Per-language video state (en/de/fa/ar): the real `Video.state`
    (rendered/published/archived) or "not_created" when no Video row
    exists yet for that language."""
    badges = dict.fromkeys(LANGUAGES, "not_created")
    if db is None or case_id is None:
        return badges
    for video in db.query(Video).filter(Video.case_id == case_id).all():
        if video.language in badges:
            badges[video.language] = video.state
    return badges


def search_counts(db: Session, job_id: int) -> dict[str, int]:
    """Total/free/created_not_published/created_published/archived for one
    Search (discovery job), counted live over its suggested candidates."""
    candidates = (
        db.query(DiscoveryCandidate)
        .filter(DiscoveryCandidate.discovery_job_id == job_id,
                DiscoveryCandidate.state.in_(_COUNTED_CANDIDATE_STATES))
        .all()
    )
    counts = {"total": len(candidates), "free": 0, "created_not_published": 0,
              "created_published": 0, "archived": 0}
    for candidate in candidates:
        counts[candidate_status(db, candidate)] += 1
    return counts
