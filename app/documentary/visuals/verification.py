"""Visual verification (Part 22): what does this image ACTUALLY show?

A vision model (role visual_verifier) looks at the image itself next to
the claims made about it (caption, page, what it was found for) and the
case evidence. Filenames and page labels are never trusted blindly.
Deterministic rules then decide the status:
  rejected     — wrong person/place, unrelated, watermark, unusable;
  verified     — matches with confidence >= verified_min_confidence;
  needs_review — everything in between (a human decides in the UI).
and the relevance tier (visuals/tiers.py): what the image shows decides
how close to the case it really is, starting from what the search
claimed when it was found.

Footage is judged by three frames of the stored window — near its start,
middle and end, side by side in one picture — so what the clip shows a
few seconds later is checked too, by the same rules.
"""

from __future__ import annotations

from app.core.prompts import prompt

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals import rights as R
from app.documentary.visuals import tiers as T
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

VERIFIER_SYSTEM = prompt("documentary/visuals/verification/verifier_system")


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
    if v.get("tone_ok") is False:
        return "rejected", conf, "tone"
    if v.get("matches_claim") == "no" or v.get("period_ok") == "no":
        return "rejected", conf, "does_not_match"
    if conf < cfg.reject_below_confidence:
        return "rejected", conf, "low_confidence"
    if v.get("matches_claim") == "stand_in":
        # the same kind of thing, shown as an illustration (tier 4/5) —
        # never someone else's face
        if str(v.get("subject_type") or "") == "person":
            return "rejected", conf, "stand_in_person"
        if conf >= cfg.verified_min_confidence:
            return "verified", conf, "stand_in"
        return "needs_review", conf, "uncertain"
    if v.get("matches_claim") == "yes" and conf >= cfg.verified_min_confidence:
        return "verified", conf, None
    return "needs_review", conf, "uncertain"


def _keyframe_thumb(asset: VisualAsset):
    """The clip's keyframe thumbnail (made now if missing)."""
    from app.documentary.visuals.footage import extract_keyframe

    thumb = storage.resolve(asset.thumbnail_path)
    if thumb is not None and thumb.exists():
        return thumb
    clip = storage.resolve(asset.local_path)
    out = storage.thumbs_dir(asset.case_id) / f"{asset.asset_code}.jpg"
    length = asset.duration_seconds or ((asset.clip_end or 0) - (asset.clip_start or 0))
    frame = extract_keyframe(clip, (asset.clip_start or 0) + max(length, 0) / 2,
                             out.with_suffix(".key.jpg"))
    IM.save_thumbnail(IM.open_image(frame.read_bytes()), out)
    frame.unlink(missing_ok=True)
    asset.thumbnail_path = storage.rel(out)
    return out


def image_for_check(asset: VisualAsset):
    """The still the verifier looks at: the thumbnail; for footage the
    start/middle/end sheet of the stored window (made now if missing;
    the keyframe only when no sheet can be made) — a video file is never
    sent as an image."""
    if asset.asset_type != "video":
        thumb = storage.resolve(asset.thumbnail_path)
        if thumb is not None and thumb.exists():
            return thumb
        return storage.resolve(asset.local_path)
    from app.documentary.visuals.footage import FootageError, clip_sheet

    thumb = _keyframe_thumb(asset)  # the library's thumbnail stays the keyframe
    try:
        sheet = clip_sheet(asset)
    except (FootageError, OSError, IM.ImageError):
        sheet = None
    return sheet if sheet is not None else thumb


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
            v, res = await self.gen.generate_structured(
                "visual_verifier", VERIFIER_SYSTEM,
                json.dumps(payload, ensure_ascii=False), images=[IM.data_url(thumb)])
            stamp_run(run, res, "visual_verifier")
        v = v if isinstance(v, dict) else {}
        status, conf, reason = decide(v)
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
