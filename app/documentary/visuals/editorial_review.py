"""Automatic editorial review of pictures with unclear rights (no person approves them).

A news or web picture is "editorial_review_required": usable only after an approval is recorded on
the asset. With the owner setting auto_editorial_review on, an independent vision reviewer judges
each picture against the case facts and this module decides STRICTLY from its answers: a picture
is approved only on a clear pass, and never when a person's identity is uncertain or wrong. The
rights classification and the verification of a picture stay as they are: a picture that is not
"editorial_review_required", or that verification already rejected, is not touched. Every decision
is stored (editorial_review_decisions) so it can be audited later."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import AppSetting, EditorialReviewDecision, VisualAsset
from app.utils import utc_now

SETTING_KEY = "auto_editorial_review"
IDENTITY_OK = ("not_a_person", "certain")


def auto_review_enabled(db: Session) -> bool:
    """The owner's setting; the config default until one is made."""
    row = db.get(AppSetting, SETTING_KEY)
    if row is not None:
        try:
            value = json.loads(row.value_json).get("enabled")
        except (ValueError, AttributeError):
            value = None
        if isinstance(value, bool):
            return value
    return ai_config.editorial_review.auto_default


def set_auto_review(db: Session, enabled: bool) -> bool:
    row = db.get(AppSetting, SETTING_KEY)
    if row is None:
        row = AppSetting(key=SETTING_KEY)
        db.add(row)
    row.value_json = json.dumps({"enabled": bool(enabled)})
    row.updated_at = utc_now()
    db.commit()
    return bool(enabled)


def decide(v: dict) -> tuple[bool, list[str]]:
    """(approved, reasons) from the reviewer's answers. Approval needs every point to pass; any
    doubt, a wrong or unverifiable identity, the wrong period or a caption that does not match is
    a rejection."""
    v = v if isinstance(v, dict) else {}
    problems: list[str] = []
    if v.get("verdict") != "approve":
        problems.append("the reviewer did not approve")
    identity = v.get("identity")
    if identity == "wrong_person":
        problems.append("it shows a different person than the one it is claimed to show")
    elif identity not in IDENTITY_OK:
        problems.append("the identity of the person shown is not supported by caption, sign or source")
    if v.get("right_subject") != "yes":
        problems.append("it is not clearly the person, place or object it is claimed to show")
    if v.get("period") != "yes":
        problems.append("it is not clearly from the period of the case")
    if v.get("caption_matches") != "yes":
        problems.append("the caption's claim does not clearly match the picture")
    for flag, text in (("other_case", "it belongs to another case or event"),
                       ("watermark", "it carries a watermark, logo or burned-in text"),
                       ("graphic", "it is graphic or sensitive")):
        if v.get(flag) is not False:
            problems.append(text)
    try:
        confidence = float(v.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < ai_config.editorial_review.min_confidence:
        problems.append(f"the reviewer's confidence {confidence:.2f} is below "
                        f"{ai_config.editorial_review.min_confidence:.2f}")
    return (not problems), problems


def pending(db: Session, case_id: int, redo: bool = False) -> list[VisualAsset]:
    """Pictures with unclear rights that no approval and no earlier decision covers, best first."""
    reviewed = {a for (a,) in db.query(EditorialReviewDecision.asset_id)
                .filter(EditorialReviewDecision.case_id == case_id).all()}
    rows = (db.query(VisualAsset)
            .filter(VisualAsset.case_id == case_id,
                    VisualAsset.rights_status == "editorial_review_required",
                    VisualAsset.editorial_approved_at.is_(None))
            .order_by(VisualAsset.relevance_tier.asc().nullslast(), VisualAsset.id).all())
    return [a for a in rows if redo or a.id not in reviewed]


def record(db: Session, asset: VisualAsset, verdict: str, reasons: list[str], details: dict,
           model: str | None) -> EditorialReviewDecision:
    row = EditorialReviewDecision(
        case_id=asset.case_id, asset_id=asset.id, asset_code=asset.asset_code, verdict=verdict,
        reasons_json=json.dumps(reasons, ensure_ascii=False),
        details_json=json.dumps(details, ensure_ascii=False), model=model, mode="auto")
    db.add(row)
    return row


def approve(db: Session, asset: VisualAsset, model: str | None) -> None:
    asset.editorial_approved_by = f"{ai_config.editorial_review.approver_label} ({model or 'model'})"[:120]
    asset.editorial_approved_at = utc_now()


async def review_pending(db: Session, case, redo: bool = False) -> dict:
    """Review every pending picture of the case with the independent reviewer. Returns the counts."""
    from app.agents.story import build_evidence_pack
    from app.agents.visual_editorial_review import EditorialReviewAgent
    from app.core.concurrency import gather_limited
    from app.db.models import Contradiction, Fact, Source

    todo = pending(db, case.id, redo)[:ai_config.editorial_review.max_per_run]
    if not todo:
        return {"reviewed": 0, "approved": 0, "rejected": 0, "kept": 0}
    pack = build_evidence_pack(
        db.query(Fact).filter(Fact.case_id == case.id).all(),
        db.query(Contradiction).filter(Contradiction.case_id == case.id).all(),
        db.query(Source).filter(Source.case_id == case.id).all())
    agent = EditorialReviewAgent()
    entities = _entities(db, case)
    results = await gather_limited(
        None, [agent.review(db, case, a, entities, pack["facts"]) for a in todo],
        return_exceptions=True)
    counts = {"reviewed": 0, "approved": 0, "rejected": 0, "kept": 0, "errors": 0}
    for _asset, result in zip(todo, results, strict=True):
        if isinstance(result, Exception):
            counts["errors"] += 1
            continue
        counts["reviewed"] += 1
        counts[{"approved": "approved", "rejected": "rejected"}.get(result.verdict, "kept")] += 1
    return counts


def _entities(db: Session, case) -> list[dict]:
    from app.db.models import VisualPlan

    plan = (db.query(VisualPlan).filter(VisualPlan.case_id == case.id)
            .order_by(VisualPlan.id.desc()).first())
    if plan is None:
        return []
    try:
        return json.loads(plan.requirements_json or "{}").get("entities") or []
    except ValueError:
        return []
