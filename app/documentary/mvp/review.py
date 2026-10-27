"""The owner-review gate as a persisted workflow state. A rendered film is a review candidate until the owner
approves its exact checksum; nothing runs past AWAITING_OWNER_REVIEW without an explicit owner decision."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import MvpFilmReview

TRANSITIONS = {
    "IMPLEMENTING": {"PRODUCING"},
    "PRODUCING": {"VALIDATING", "REVISION_REQUIRED"},
    "VALIDATING": {"AWAITING_OWNER_REVIEW", "PRODUCING"},
    "AWAITING_OWNER_REVIEW": {"APPROVED", "REVISION_REQUIRED", "REJECTED"},
    "REVISION_REQUIRED": {"PRODUCING"},
    "REJECTED": {"PRODUCING"},
    "APPROVED": set(),
}
OWNER_DECISIONS = {"APPROVED", "REVISION_REQUIRED", "REJECTED"}


class ReviewError(RuntimeError):
    pass


def states() -> list[str]:
    configured = ai_config.mvp().review.state_machine
    unknown = set(TRANSITIONS) ^ set(configured)
    if unknown:
        raise ReviewError(f"main_visual_planner.review.state_machine differs from the workflow: {sorted(unknown)}")
    return configured


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current(db: Session, case_id: int, language: str) -> MvpFilmReview | None:
    return (db.query(MvpFilmReview).filter_by(case_id=case_id, language=language)
            .order_by(MvpFilmReview.id.desc()).first())


def open_review(db: Session, case_id: int, language: str) -> MvpFilmReview:
    """The language's active review, or a new one after a finished (approved/rejected) cycle."""
    states()
    row = current(db, case_id, language)
    if row is None:
        row = MvpFilmReview(case_id=case_id, language=language, state="IMPLEMENTING")
        db.add(row)
        db.commit()
    return row


def supersede(db: Session, case_id: int, language: str) -> None:
    """A new run starts directing the film: a film still waiting for its checks or for the owner belongs to an older
    run (its file may not even match today's narration), so its review starts over instead of showing old findings.
    A decided review (approved, rejected, revision required) is never touched."""
    row = current(db, case_id, language)
    if row is not None and row.state in ("PRODUCING", "VALIDATING", "AWAITING_OWNER_REVIEW"):
        row.state, row.production_script_id, row.render_path, row.render_sha256 = "IMPLEMENTING", None, None, None
        row.validation_json = "{}"
        db.commit()


def advance(db: Session, review: MvpFilmReview, new_state: str) -> MvpFilmReview:
    if new_state in OWNER_DECISIONS:
        raise ReviewError(f"{new_state} is the owner's decision; use decide()")
    if new_state not in TRANSITIONS.get(review.state, set()):
        raise ReviewError(f"cannot move from {review.state} to {new_state}")
    if new_state == "AWAITING_OWNER_REVIEW":
        validation = json.loads(review.validation_json or "{}")
        if not validation.get("review_ready") or not review.render_sha256:
            raise ReviewError("a film reaches owner review only after it passed technical validation")
    review.state = new_state
    db.commit()
    return review


def submit_for_review(db: Session, review: MvpFilmReview, *, production_script_id: int, render_path: Path,
                      validation: dict) -> MvpFilmReview:
    """Record the exact rendered file and its validation; move VALIDATING -> AWAITING_OWNER_REVIEW only
    if the validation passed, otherwise the film goes back to PRODUCING with the findings kept."""
    review.production_script_id = production_script_id
    review.render_path = str(render_path)
    review.render_sha256 = sha256_of(render_path)
    review.validation_json = json.dumps(validation, ensure_ascii=False)
    db.commit()
    return advance(db, review, "AWAITING_OWNER_REVIEW" if validation.get("review_ready") else "PRODUCING")


def decide(db: Session, review: MvpFilmReview, decision: str, note: str | None = None,
           render_sha256: str | None = None) -> MvpFilmReview:
    """The owner's decision, valid only on the exact checksum under review."""
    if decision not in OWNER_DECISIONS:
        raise ReviewError(f"unknown decision {decision!r}")
    if review.state != "AWAITING_OWNER_REVIEW":
        raise ReviewError(f"the film is {review.state}, not awaiting review")
    if render_sha256 and render_sha256 != review.render_sha256:
        raise ReviewError("the decision names a different render than the one under review")
    review.state, review.owner_note = decision, note
    review.decided_at = datetime.now().astimezone()
    db.commit()
    return review


def invalidate_if_changed(db: Session, review: MvpFilmReview, render_path: Path) -> bool:
    """An approval holds only for the exact approved bytes: a different file voids it."""
    if review.state == "APPROVED" and review.render_sha256 != sha256_of(render_path):
        review.state = "REVISION_REQUIRED"
        review.owner_note = ((review.owner_note or "") + " [approval voided: render changed]").strip()
        db.commit()
        return True
    return False


def guard_no_progress_past_review(db: Session, case_id: int, language: str) -> None:
    """Called before any production step: while a film awaits the owner, nothing runs for that language."""
    row = current(db, case_id, language)
    if row is not None and row.state == "AWAITING_OWNER_REVIEW":
        raise ReviewError(f"the {language} film is awaiting owner review; no further production without an owner decision")


def english_approved(db: Session, case_id: int) -> bool:
    row = current(db, case_id, "en")
    return row is not None and row.state == "APPROVED"
