"""Judge the stored verdicts of a case again under the current rules — no model call.

When a rule changes (a threshold, a new kind of verdict) the stored answers of the vision model
are still what they were; only the decision made from them changes. This applies the decision
again and reports the counts before and after. A picture the second pass verified, one a human
decided, a generated picture and a video are left alone."""

from __future__ import annotations

import json
from collections import Counter

from sqlalchemy.orm import Session

from app.db.models import VisualAsset
from app.documentary.visuals import tiers as T
from app.documentary.visuals.verification import CONTEXT_VERDICTS, decide

SECOND_PASS = "second_pass"


def _judgeable(a: VisualAsset) -> bool:
    return (a.asset_type == "photo" and a.provider != "generated" and not a.human_override
            and bool(a.verification_json))


def run(db: Session, case_id: int, apply: bool = False) -> dict:
    """{"before", "after": status counts, "changed": [{"asset", "from", "to", "reason"}]}."""
    before: Counter = Counter()
    after: Counter = Counter()
    changed = []
    for a in db.query(VisualAsset).filter(VisualAsset.case_id == case_id).order_by(VisualAsset.id):
        if not _judgeable(a):
            continue
        stored = json.loads(a.verification_json or "{}")
        before[a.verification_status] += 1
        if stored.get("reason") == SECOND_PASS:
            after[a.verification_status] += 1
            continue
        status, conf, reason = decide(stored)
        after[status] += 1
        if status == a.verification_status and reason == stored.get("reason"):
            continue
        if status != a.verification_status:
            changed.append({"asset": a.asset_code, "from": a.verification_status, "to": status,
                            "reason": reason})
        if apply:
            _apply(a, stored, status, conf, reason)
    if apply:
        db.commit()
    return {"before": dict(before), "after": dict(after), "changed": changed}


def _apply(a: VisualAsset, stored: dict, status: str, conf: float, reason: str | None) -> None:
    a.verification_status = status
    if status != "unverified":
        a.verification_confidence = round(conf, 3)
        tier, why = T.verified_why(a, stored, stored.get("provisional_tier"))
        a.relevance_tier, a.case_relevance = tier, T.tier_label(tier)
        stored["tier"], stored["tier_reason"] = tier, why
        if stored.get("matches_claim") in CONTEXT_VERDICTS:
            a.asset_role = "context"
    stored["reason"] = reason
    a.verification_json = json.dumps(stored, ensure_ascii=False)
