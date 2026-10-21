"""The VisualVerificationAgent agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.visuals.verification."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary.visuals import images as IM
from app.documentary.visuals import rights as R
from app.documentary.visuals import tiers as T
from app.documentary.visuals.verification import (
    CONTEXT_VERDICTS,
    VERIFIER_SYSTEM,
    decide,
    image_for_check,
    normalize_composite_match,
    normalize_fact_match,
    normalize_other_entity_match,
)
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)


class VisualVerificationAgent:
    def __init__(self):
        self.gen = get_generation_provider()

    def _payload(self, case: Case, asset: VisualAsset, entities: list[dict], facts: list[dict]) -> dict:
        return {
            "case": case.canonical_title,
            "media": ("video: start, middle and end frames, left to right"
                      if asset.asset_type == "video" else "image"),
            "found_for": asset.found_for,
            "claimed_entities": json.loads(asset.entities_json or "[]"),
            "title": asset.title, "caption": asset.caption,
            "page_url": asset.page_url, "source": asset.source_name,
            "entity_list": [{"key": e["key"], "name": e["name"], "type": e["type"],
                             "period": e.get("period")} for e in entities],
            "facts": [{"id": f["id"], "claim": f["claim"]} for f in facts],
        }

    async def _judge(self, db: Session, case: Case, asset: VisualAsset, entities: list[dict],
                     facts: list[dict], role: str, label: str) -> tuple[dict, str, float, str | None, object]:
        """One model verdict on the picture, normalized by the same rules whichever model gave it."""
        payload = self._payload(case, asset, entities, facts)
        with track_run(db, case.id, label, input_summary=asset.asset_code) as run:
            v, res = await run_agent("visuals.verify", self.gen, json.dumps(payload, ensure_ascii=False),
                                     system=VERIFIER_SYSTEM, role=role,
                                     images=[IM.data_url(image_for_check(asset))])
            stamp_run(run, res, role)
        v = v if isinstance(v, dict) else {}
        known_ents = {e["key"] for e in entities}
        v = normalize_composite_match(v, known_ents)
        v = normalize_other_entity_match(v, known_ents)
        v = normalize_fact_match(v, {f["id"] for f in facts})
        status, conf, reason = decide(v)
        return v, status, conf, reason, res

    async def verify(self, db: Session, case: Case, asset: VisualAsset,
                     entities: list[dict], facts: list[dict]) -> VisualAsset:
        if asset.asset_type == "video" and ai_config.footage.pieces:
            # a video is judged as video: frame by frame, by the video auditor
            from app.documentary.visuals.video_auditor import VideoAuditor

            return await VideoAuditor(gen=self.gen).describe(db, case, asset, entities, facts)
        claimed = json.loads(asset.entities_json or "[]")
        previous = json.loads(asset.verification_json or "{}") if asset.verification_json else {}
        # what the search claimed when the asset was found (kept across re-checks)
        provisional = previous.get("provisional_tier") or asset.relevance_tier
        v, status, conf, reason, res = await self._judge(
            db, case, asset, entities, facts, "visual_verifier", "Visual Verifier")
        if status == "unverified":
            # the model did not answer in the schema: the picture stays unchecked and is checked again
            asset.verification_json = json.dumps({**previous, "reason": reason, "raw": v,
                                                  "model": getattr(res, "model", None)},
                                                 ensure_ascii=False)
            db.commit()
            return asset
        known_ents = {e["key"] for e in entities}
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
        if v.get("matches_claim") in CONTEXT_VERDICTS:
            # a real picture of the same kind: background, never evidence of a specific fact
            asset.asset_role = "context"
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

    @staticmethod
    def second_pass_candidates(assets: list[VisualAsset]) -> list[VisualAsset]:
        """needs_review pictures of places, objects, documents and maps (the subjects with a lower
        bar) that the second model has not seen: a person is never sent, never auto-accepted."""
        kinds = set(ai_config.visual_verification.verified_min_confidence_by_subject)
        return [a for a in assets
                if a.verification_status == "needs_review" and a.asset_type == "photo"
                and (a.subject_type or "") in kinds and a.entity_type != "person"
                and "second_pass" not in json.loads(a.verification_json or "{}")]

    async def second_pass(self, db: Session, case: Case, assets: list[VisualAsset],
                          entities: list[dict], facts: list[dict]) -> dict:
        """A second, independent model (role visual_second_verifier) looks at the same picture with
        the same inputs. The picture becomes verified only when it, too, calls it verified, shows no
        person, nothing graphic and no watermark; anything else stays with the owner."""
        todo = self.second_pass_candidates(assets)
        checked = verified = errors = 0
        for asset in todo:
            try:
                v, status, conf, reason, res = await self._judge(
                    db, case, asset, entities, facts, "visual_second_verifier", "Visual Second Verifier")
            except GenerationError as e:
                log.warning("second pass for %s failed: %s", asset.asset_code, e)
                errors += 1          # stays in needs_review, asked again next run
                continue
            first = json.loads(asset.verification_json or "{}")
            second = {**v, "status": status, "reason": reason, "confidence": round(conf, 3),
                      "model": getattr(res, "model", None)}
            checked += 1
            passed = (status == "verified" and str(v.get("subject_type") or "") != "person"
                      and not v.get("watermark") and not v.get("graphic_or_sensitive"))
            if passed:
                asset.verification_status = "verified"
                asset.verification_confidence = round(min(conf, asset.verification_confidence or conf), 3)
                first["reason"] = "second_pass"
                verified += 1
            asset.verification_json = json.dumps({**first, "second_pass": second}, ensure_ascii=False)
            db.commit()
        return {"second_checked": checked, "second_verified": verified,
                **({"second_errors": errors} if errors else {})}
