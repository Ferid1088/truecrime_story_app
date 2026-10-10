"""Generate a review-only case-6 short-form publishing plan."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date

from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import ShortFormConcept
from app.shortform.operations import build_publish_plan
from scripts.phase10_repetition_verify import _candidates


def main() -> None:
    candidates = _candidates()
    with SessionLocal() as db:
        persisted = db.query(ShortFormConcept).filter_by(case_id=6, status="approved").count()
    counts = {
        platform: cfg.count
        for platform, cfg in ai_config.short_form.distribution.items()
        if cfg.enabled and cfg.count
    }
    slots = build_publish_plan(date.today(), counts, days=14)
    plan = [
        {
            "day": slot.day,
            "platform": slot.platform,
            "concept_id": candidates[index % len(candidates)]["id"],
            "status": slot.status,
            "approved_only": slot.approved_only,
        }
        for index, slot in enumerate(slots)
    ]
    output = {
        "case_id": 6,
        "source": "phase10_verification_candidates" if persisted == 0 else "approved_persisted_concepts",
        "persisted_approved_concepts": persisted,
        "configured_counts": counts,
        "scheduled_counts": dict(Counter(item["platform"] for item in plan)),
        "total_slots": len(plan),
        "days_used": sorted({item["day"] for item in plan}),
        "max_slots_on_one_day": max(Counter(item["day"] for item in plan).values(), default=0),
        "all_require_human_approval": all(item["approved_only"] and item["status"] == "planned" for item in plan),
        "plan": plan,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
