"""The editorial reviewer agent: a vision model, independent of the visual planner, judges one
picture with unclear rights. The strict decision rules stay in
app.documentary.visuals.editorial_review."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.prompts import prompt
from app.db.models import Case, EditorialReviewDecision, VisualAsset
from app.documentary.visuals import editorial_review as ER
from app.documentary.visuals import images as IM
from app.documentary.visuals.verification import image_for_check
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run


class EditorialReviewAgent:
    def __init__(self):
        self.gen = get_generation_provider()

    async def review(self, db: Session, case: Case, asset: VisualAsset, entities: list[dict],
                     facts: list[dict]) -> EditorialReviewDecision:
        payload = {
            "case": case.canonical_title,
            "found_for": asset.found_for, "title": asset.title, "caption": asset.caption,
            "source": asset.source_name, "page_url": asset.page_url,
            "claimed_entities": json.loads(asset.entities_json or "[]"),
            "entity_list": [{"key": e.get("key"), "name": e.get("name"), "type": e.get("type"),
                             "period": e.get("period")} for e in entities],
            "facts": [{"id": f["id"], "claim": f["claim"]} for f in facts],
        }
        with track_run(db, case.id, "Editorial Reviewer", input_summary=asset.asset_code) as run:
            v, res = await run_agent(
                "visuals.editorial_review", self.gen, json.dumps(payload, ensure_ascii=False),
                system=prompt("documentary/visuals/editorial_review/reviewer_system"),
                images=[IM.data_url(image_for_check(asset))])
            stamp_run(run, res, "visual_editorial_reviewer")
        model = getattr(res, "model", None)
        approved, reasons = ER.decide(v)
        shown = [str(r) for r in (v.get("reasons") if isinstance(v, dict) else None) or []][:4]
        if approved:
            ER.approve(db, asset, model)
        row = ER.record(db, asset, "approved" if approved else "rejected",
                        shown if approved else reasons + shown, v if isinstance(v, dict) else {}, model)
        db.commit()
        return row
