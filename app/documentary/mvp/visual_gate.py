"""The case-level visual approval: one owner decision on the shared picture package, required before any language's film is directed or rendered.

The pictures and videos of a case are the English film's (mvp/reference.py): every other language uses what the English plan approved. So the owner reviews that package once.
Automated validation never counts as this approval. The approval names a checksum of the approved items; when they change (a picture is withdrawn, replaced or added) the approval no
longer matches and the gate closes until the owner approves the new package. The record lives in the English plan's `validation_json` (`mvp.owner_visual_approval`)."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.db.models import MvpCandidate, MvpRequirement, VisualPlan
from app.documentary.mvp import reference as REF
from app.documentary.pipeline.errors import AwaitingPictureReview
from app.utils import utc_now

WAITING_MARK = "the case's visual approval"          # in the message of a job that waits here: the autopilot resumes it once the gate is open


class VisualApprovalError(ValueError):
    pass


def package_sha(db: Session, plan: VisualPlan) -> str:
    """A checksum of exactly what the film may show: the approved item of every requirement."""
    rows = (db.query(MvpRequirement.requirement_key, MvpCandidate.visual_asset_id)
            .join(MvpCandidate, MvpCandidate.requirement_id == MvpRequirement.id)
            .filter(MvpRequirement.visual_plan_id == plan.id, MvpCandidate.final_status == "approved").all())
    return hashlib.sha256(json.dumps(sorted((str(k), int(a)) for k, a in rows)).encode()).hexdigest()


def _record(plan: VisualPlan) -> dict | None:
    return (json.loads(plan.validation_json or "{}").get("mvp") or {}).get("owner_visual_approval")


def status(db: Session, case_id: int) -> dict:
    """`none`: there is no English picture plan yet; `awaiting_owner`: not approved; `changed`: it was approved, the package changed since; `approved`."""
    plan = REF.english_needs_plan(db, case_id)
    if plan is None:
        return {"state": "none", "open": False, "reason": "The English picture plan is not made yet."}
    sha, record = package_sha(db, plan), _record(plan)
    if record is None:
        return {"state": "awaiting_owner", "open": False, "plan_id": plan.id, "package_sha256": sha, "reason": "The owner has not approved the case's pictures and videos."}
    if record.get("package_sha256") != sha:
        return {"state": "changed", "open": False, "plan_id": plan.id, "package_sha256": sha, "approved_at": record.get("approved_at"),
                "reason": "The approved pictures and videos changed after the approval: approve the new package."}
    return {"state": "approved", "open": True, "plan_id": plan.id, "package_sha256": sha, "approved_at": record.get("approved_at"), "note": record.get("note")}


def approve(db: Session, case_id: int, note: str | None = None) -> dict:
    """The owner approves the package as it is now. This is the owner's picture review 1 as well (the English film does not stop for it again)."""
    plan = REF.english_needs_plan(db, case_id)
    if plan is None:
        raise VisualApprovalError("The English picture plan is not made yet: there is nothing to approve.")
    validation = json.loads(plan.validation_json or "{}")
    mvp = validation.setdefault("mvp", {})
    mvp["owner_visual_approval"] = {"package_sha256": package_sha(db, plan), "approved_at": utc_now().isoformat(), "note": note}
    mvp["reviews"] = max(int(mvp.get("reviews") or 0), 1)
    plan.validation_json = json.dumps(validation, ensure_ascii=False)
    db.commit()
    return status(db, case_id)


def withdraw(db: Session, case_id: int) -> dict:
    plan = REF.english_needs_plan(db, case_id)
    if plan is not None:
        validation = json.loads(plan.validation_json or "{}")
        (validation.get("mvp") or {}).pop("owner_visual_approval", None)
        plan.validation_json = json.dumps(validation, ensure_ascii=False)
        db.commit()
    return status(db, case_id)


def guard(db: Session, case_id: int, language: str) -> None:
    """Stops the run (resumable) before the film is directed and rendered until the owner has approved the case's picture package."""
    state = status(db, case_id)
    if not state["open"]:
        raise AwaitingPictureReview(f"Awaiting picture review: the {language} film waits for {WAITING_MARK}. {state['reason']} "
                                    "Open Review pictures and approve the package; the film goes on by itself.")
