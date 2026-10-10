"""The VisualVerificationAgent agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.visuals.verification."""

from __future__ import annotations

from app.agents.runner import run_agent
import json
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary.visuals import images as IM
from app.documentary.visuals import rights as R
from app.documentary.visuals import tiers as T
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.documentary.visuals.verification import (
    VERIFIER_SYSTEM,
    decide,
    image_for_check,
    normalize_composite_match,
)


class VisualVerificationAgent:
    def __init__(self):
        self.gen = get_generation_provider()

    async def verify(self, db: Session, case: Case, asset: VisualAsset,
                     entities: list[dict], facts: list[dict]) -> VisualAsset:
        if asset.asset_type == "video" and ai_config.footage.pieces:
            # a video is judged as video: frame by frame, by the video auditor
            from app.documentary.visuals.video_auditor import VideoAuditor

            return await VideoAuditor(gen=self.gen).describe(db, case, asset, entities, facts)
        thumb = image_for_check(asset)
        claimed = json.loads(asset.entities_json or "[]")
        previous = json.loads(asset.verification_json or "{}") if asset.verification_json else {}
        # what the search claimed when the asset was found (kept across re-checks)
        provisional = previous.get("provisional_tier") or asset.relevance_tier
        payload = {
            "case": case.canonical_title,
            "media": ("video: start, middle and end frames, left to right"
                      if asset.asset_type == "video" else "image"),
            "found_for": asset.found_for,
            "claimed_entities": claimed,
            "title": asset.title, "caption": asset.caption,
            "page_url": asset.page_url, "source": asset.source_name,
            "entity_list": [{"key": e["key"], "name": e["name"], "type": e["type"],
                             "period": e.get("period")} for e in entities],
            "facts": [{"id": f["id"], "claim": f["claim"]} for f in facts],
        }
        with track_run(db, case.id, "Visual Verifier",
                       input_summary=asset.asset_code) as run:
            v, res = await run_agent("visuals.verify", self.gen, json.dumps(payload, ensure_ascii=False), system=VERIFIER_SYSTEM, images=[IM.data_url(thumb)])
            stamp_run(run, res, "visual_verifier")
        v = v if isinstance(v, dict) else {}
        known_ents = {e["key"] for e in entities}
        v = normalize_composite_match(v, known_ents)
        status, conf, reason = decide(v)
        known_facts = {f["id"] for f in facts}
        tier, tier_reason = T.verified_why(asset, v, provisional)
        asset.verification_status = status
        asset.verification_confidence = round(conf, 3)
        asset.relevance_tier = tier
        asset.case_relevance = T.tier_label(tier)
        asset.verification_json = json.dumps({**v, "reason": reason,
                                              "model": getattr(res, "model", None),
                                              "provisional_tier": provisional,
                                              "tier": tier, "tier_reason": tier_reason},
                                             ensure_ascii=False)
        asset.description = str(v.get("depicts") or asset.description or "")[:1000] or None
        if v.get("subject_type"):
            asset.subject_type = str(v["subject_type"])[:20]
        if v.get("role") in ("evidence", "context", "illustration"):
            asset.asset_role = v["role"]
        if v.get("matches_claim") == "stand_in":
            # a stand-in is never case material, and always shown with the
            # "symbolic image" label (the label follows role illustration)
            asset.asset_role = "illustration"
        ents = [e for e in v.get("entities") or [] if e in known_ents]
        if ents:
            asset.entities_json = json.dumps(sorted(set(ents) | set(claimed)), ensure_ascii=False)
        asset.reveals_json = json.dumps([r for r in v.get("reveals") or [] if r in known_facts])
        try:
            asset.quality_score = round(max(0.0, min(1.0, float(v.get("quality")))), 3)
        except (TypeError, ValueError):
            pass
        if v.get("watermark"):
            asset.rights_status, asset.rights_reason = R.classify(
                asset.provider, asset.license, asset.source_url, asset.page_url,
                watermark=True)
        db.commit()
        return asset
