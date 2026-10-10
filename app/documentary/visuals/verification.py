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



from app.core.ai_config import ai_config
from app.db.models import VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM

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
    if v.get("matches_claim") == "composite":
        if conf >= cfg.verified_min_confidence:
            return "verified", conf, "composite_match"
        return "needs_review", conf, "composite_uncertain"
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


def normalize_composite_match(verdict: dict, known_entity_keys: set[str]) -> dict:
    """Preserve a valid multi-entity depiction as a composite match."""
    out = dict(verdict)
    entities = [e for e in out.get("entities") or [] if e in known_entity_keys]
    if (out.get("matches_claim") == "no" and len(set(entities)) >= 2
            and out.get("period_ok") != "no"):
        out["matches_claim"] = "composite"
        out["composite_entities"] = sorted(set(entities))
    return out


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


def __getattr__(name: str):
    # the agent class lives in app/agents/visual_verification.py (imported when first asked for)
    if name == "VisualVerificationAgent":
        from app.agents.visual_verification import VisualVerificationAgent

        return VisualVerificationAgent
    raise AttributeError(name)
