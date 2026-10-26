"""Mechanics the Main Visual Planner shares: the still a vision model looks at, and the gateway preflight."""

from __future__ import annotations

from app.db.models import VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM


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


async def gateway_available(gen) -> tuple[bool, str | None]:
    """Whether the vision/generation gateway can answer right now: a free preflight (GET
    /models, no billed call) before a job downloads or checks pictures (P8). Returns
    (ok, status) — status is the provider_status() status word (e.g. "forbidden" for an
    "Insufficient balance" account) when not ok, so a failed stage can show it. This does not
    prove a billed vision call itself will succeed (no provider documents a free balance
    endpoint) — only that the gateway answers at all (configured, reachable, authorized)."""
    status = await gen.provider_status()
    ok = status.get("status") in ("ok", "models_missing")
    return ok, (None if ok else status.get("status"))
