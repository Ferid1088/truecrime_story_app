"""Visual verification (Part 22): what does this image ACTUALLY show?

A vision model (role visual_verifier) looks at the image itself next to
the claims made about it (caption, page, what it was found for) and the
case evidence. Filenames and page labels are never trusted blindly.
Deterministic rules then decide the status:
  rejected     — wrong person/place, unrelated, watermark, unusable;
  verified     — matches (or is real context of the same kind, or a detail of it) with
                 confidence >= verified_min_confidence (lower for places, objects, documents, maps);
  needs_review — everything in between (a human decides in the UI).
and the relevance tier (visuals/tiers.py): what the image shows decides
how close to the case it really is, starting from what the search
claimed when it was found.

Footage is judged by three frames of the stored window — near its start,
middle and end, side by side in one picture — so what the clip shows a
few seconds later is checked too, by the same rules.
"""

from __future__ import annotations

from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM

VERIFIER_SYSTEM = prompt("documentary/visuals/verification/verifier_system")


CONTEXT_VERDICTS = ("context", "stand_in")   # "stand_in" is the old name of "context"
NO_VERDICT = "no_verdict"


def is_verdict(v: dict) -> bool:
    """An answer that follows the schema: it says whether the picture matches, or what it shows."""
    return bool(v.get("matches_claim") or v.get("depicts"))


def verified_min_confidence(subject: str) -> float:
    """The confidence a picture of this kind of subject needs to be verified."""
    cfg = ai_config.visual_verification
    return cfg.verified_min_confidence_by_subject.get(subject, cfg.verified_min_confidence)


def decide(v: dict) -> tuple[str, float, str | None]:
    """(verification_status, confidence, reason). An answer that is no verdict stays unverified:
    a check that failed is repeated, never recorded as a rejection."""
    cfg = ai_config.visual_verification
    if not is_verdict(v):
        return "unverified", 0.0, NO_VERDICT
    try:
        conf = max(0.0, min(1.0, float(v.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    subject = str(v.get("subject_type") or "")
    needed = verified_min_confidence(subject)
    matches = v.get("matches_claim")
    if v.get("watermark"):
        return "rejected", conf, "watermark"
    if v.get("graphic_or_sensitive"):
        if is_archival_case_photo(v):
            return "needs_review", conf, "graphic_archival"
        return "rejected", conf, "graphic_or_sensitive"
    if v.get("tone_ok") is False:
        return "rejected", conf, "tone"
    if matches == "composite":
        if conf >= cfg.verified_min_confidence:
            return "verified", conf, "composite_match"
        return "needs_review", conf, "composite_uncertain"
    if matches == "yes" and v.get("period_ok") == "no" and subject in cfg.modern_view_subjects:
        if conf >= needed:
            return "verified", conf, "modern_view"
        return "needs_review", conf, "modern_view_uncertain"
    if matches == "no" or v.get("period_ok") == "no":
        return "rejected", conf, "does_not_match"
    if conf < cfg.reject_below_confidence:
        return "rejected", conf, "low_confidence"
    if matches in CONTEXT_VERDICTS:
        # a real picture of the same kind of place, object or institution — never someone else's face
        if subject == "person":
            return "rejected", conf, "context_person"
        if conf >= max(needed, cfg.context_min_confidence_by_subject.get(subject, 0.0)):
            return "verified", conf, "context"
        return "needs_review", conf, "uncertain"
    if matches == "partial":
        # a detail of the exact thing (a corner of the house, a cropped object): not for a face
        if subject == "person":
            return "needs_review", conf, "partial_person"
        if conf >= needed:
            return "verified", conf, "partial"
        return "needs_review", conf, "uncertain"
    if matches == "yes" and conf >= needed:
        return "verified", conf, None
    return "needs_review", conf, "uncertain"


def is_archival_case_photo(v: dict) -> bool:
    """A period photograph of the case's own events (it supports known facts of the case): graphic
    for a viewer, but the core material of a true-crime film, so the owner decides on it."""
    return (v.get("period_ok") == "yes" and v.get("role") == "evidence"
            and bool(v.get("reveals")) and v.get("tone_ok") is not False)


def normalize_fact_match(verdict: dict, known_fact_ids: set[str]) -> dict:
    """A picture that is not what it was found for (a portrait was searched) but is period
    material of a kind in ``other_entity_subjects`` that supports known facts of the case (the
    scene of the event) is kept as a match for those facts instead of being rejected."""
    out = dict(verdict)
    facts = [f for f in out.get("reveals") or [] if f in known_fact_ids]
    if (out.get("matches_claim") == "no" and facts and out.get("period_ok") == "yes"
            and out.get("role") == "evidence"
            and str(out.get("subject_type") or "") in ai_config.visual_verification.other_entity_subjects):
        out["matches_claim"] = "yes"
        out["fact_match"] = sorted(set(facts))
    return out


def normalize_composite_match(verdict: dict, known_entity_keys: set[str]) -> dict:
    """Preserve a valid multi-entity depiction as a composite match."""
    out = dict(verdict)
    entities = [e for e in out.get("entities") or [] if e in known_entity_keys]
    if (out.get("matches_claim") == "no" and len(set(entities)) >= 2
            and out.get("period_ok") != "no"):
        out["matches_claim"] = "composite"
        out["composite_entities"] = sorted(set(entities))
    return out


def normalize_other_entity_match(verdict: dict, known_entity_keys: set[str]) -> dict:
    """A picture that is not what it was found for, but clearly shows another known thing of
    the case (the house's front when its interior was searched), is a match for that thing."""
    out = dict(verdict)
    entities = [e for e in out.get("entities") or [] if e in known_entity_keys]
    if (out.get("matches_claim") == "no" and entities and out.get("period_ok") != "no"
            and str(out.get("subject_type") or "") in ai_config.visual_verification.other_entity_subjects):
        out["matches_claim"] = "yes"
        out["other_entity_match"] = sorted(set(entities))
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
