"""Visual verification (Part 22): what does this image ACTUALLY show?

A vision model (role visual_verifier) looks at the image itself next to
the claims made about it (caption, page, what it was found for) and the
case evidence. Filenames and page labels are never trusted blindly.
Deterministic rules then decide the status:
  rejected     — wrong person/place, unrelated, watermark, unusable;
  verified     — matches with confidence >= verified_min_confidence;
  needs_review — everything in between (a human decides in the UI).
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals import rights as R
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

VERIFIER_SYSTEM = """
You verify images for a factual true-crime documentary. Look at the
IMAGE ITSELF. The caption, page title and search query are claims to
check, not facts. Be strict: a wrong face or a wrong building shown as
"the" person or place is a serious error.

Decide:
- depicts: what is visibly in the image (neutral, one sentence).
- subject_type: person|place|building|vehicle|object|document|map|
  landscape|event|other
- matches_claim: yes|no|unclear — does the image show what it is
  claimed to show (the entity it was found for / its caption)?
- identity_evidence: why you think so (caption text, visible signs,
  context) — "unclear" when a person cannot be identified.
- role: evidence (genuine case material: the actual people, the actual
  house, police/coroner documents, case news photos) | context (real
  place/period related to the case but not case material) |
  illustration (generic/atmospheric).
- period_ok: yes|no|unclear — plausible for the case period.
- entities: keys from the given entity list that the image shows.
- reveals: evidence ids (from the given facts) the image would reveal
  to a viewer (e.g. a photo of a found object reveals that it was
  found). Empty if none.
- quality: 0–1 (sharpness, resolution, composition for a 16:9 frame).
- watermark: true if a stock watermark, a large logo, a TV/streaming
  title bar, burned-in headline or caption text, a decorative frame or
  any other branding is part of the picture (we need clean pictures).
- graphic_or_sensitive: true for gore, bodies, injuries, minors in
  distress — such images must not be used.
- confidence: 0–1 that your matches_claim/role judgement is right.

Return JSON only:
{"depicts": "...", "subject_type": "person", "matches_claim": "yes",
 "identity_evidence": "...", "role": "evidence", "period_ok": "yes",
 "entities": [], "reveals": [], "quality": 0.7, "watermark": false,
 "graphic_or_sensitive": false, "confidence": 0.8}
"""


def decide(v: dict) -> tuple[str, float, str | None]:
    """(verification_status, confidence, reason)."""
    cfg = ai_config.visual_verification
    try:
        conf = max(0.0, min(1.0, float(v.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    if v.get("watermark"):
        return "rejected", conf, "watermark"
    if v.get("graphic_or_sensitive"):
        return "rejected", conf, "graphic_or_sensitive"
    if v.get("matches_claim") == "no" or v.get("period_ok") == "no":
        return "rejected", conf, "does_not_match"
    if conf < cfg.reject_below_confidence:
        return "rejected", conf, "low_confidence"
    if v.get("matches_claim") == "yes" and conf >= cfg.verified_min_confidence:
        return "verified", conf, None
    return "needs_review", conf, "uncertain"


class VisualVerificationAgent:
    def __init__(self):
        self.gen = get_generation_provider()

    async def verify(self, db: Session, case: Case, asset: VisualAsset,
                     entities: list[dict], facts: list[dict]) -> VisualAsset:
        thumb = storage.resolve(asset.thumbnail_path or asset.local_path)
        claimed = json.loads(asset.entities_json or "[]")
        payload = {
            "case": case.canonical_title,
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
            v, res = await self.gen.generate_structured(
                "visual_verifier", VERIFIER_SYSTEM,
                json.dumps(payload, ensure_ascii=False), images=[IM.data_url(thumb)])
            stamp_run(run, res, "visual_verifier")
        v = v if isinstance(v, dict) else {}
        status, conf, reason = decide(v)
        known_ents = {e["key"] for e in entities}
        known_facts = {f["id"] for f in facts}
        asset.verification_status = status
        asset.verification_confidence = round(conf, 3)
        asset.verification_json = json.dumps({**v, "reason": reason,
                                              "model": getattr(res, "model", None)},
                                             ensure_ascii=False)
        asset.description = str(v.get("depicts") or asset.description or "")[:1000] or None
        if v.get("subject_type"):
            asset.subject_type = str(v["subject_type"])[:20]
        if v.get("role") in ("evidence", "context", "illustration"):
            asset.asset_role = v["role"]
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
