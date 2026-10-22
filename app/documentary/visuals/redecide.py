"""Judge the stored verdicts of a case again under the current rules — no model call.

When a rule changes (a threshold, a new kind of verdict) the stored answers of the vision model
are still what they were; only the decision made from them changes. This applies the decision
again and reports the counts before and after, for pictures AND video pieces (asset_type
"video" — a clip window the video auditor already described; "video_source", the whole raw
clip, is never itself verified, so it never carries a verification_json and is skipped by
construction). A picture or piece the second pass verified, one a human decided, a generated
asset, and a piece whose duration probe failed (a technical failure, not a confidence
judgement) are left alone."""

from __future__ import annotations

import json
from collections import Counter

from sqlalchemy.orm import Session

from app.db.models import VisualAsset
from app.documentary.visuals import tiers as T
from app.documentary.visuals.verification import CONTEXT_VERDICTS, decide
from app.documentary.visuals.video_auditor import describe_decide

SECOND_PASS = "second_pass"
PROBE_FAILED = "duration_probe_failed"


def _judgeable(a: VisualAsset) -> bool:
    return (a.asset_type in ("photo", "video") and a.provider != "generated"
            and not a.human_override and bool(a.verification_json))


def _decide_stored(a: VisualAsset, stored: dict) -> tuple[str, float, str | None]:
    """The picture auditor's decide() for a photo; for a video piece, the same rules plus the
    auditor's own cut/description check (a bad cut or wrong description stays rejected,
    whatever the confidence threshold)."""
    if a.asset_type != "video":
        return decide(stored)
    status, conf, reason = describe_decide(stored)
    cut_ok = stored.get("cut_ok") is not False
    desc_ok = stored.get("description_ok") is not False
    if status != "rejected" and not (cut_ok and desc_ok):
        return "rejected", conf, ("cut_not_meaningful" if not cut_ok else "description_wrong")
    return status, conf, reason


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
        if stored.get("reason") in (SECOND_PASS, PROBE_FAILED):
            after[a.verification_status] += 1
            continue
        status, conf, reason = _decide_stored(a, stored)
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
