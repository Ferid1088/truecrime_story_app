"""Photos from the scenes of a downloaded video: the sharp, well-lit, distinct frames of a video, one for each scene, as photo
candidates for a person, place or object of the film (at most `youtube_frames.max_per_identity` for an identity).

A frame is chosen at the start of a scene (found by the change of picture), kept only when it is sharp, neither dark nor
blown out and not like a frame already kept, and spread over the video. Registering the frames as pictures of a case goes
through the same rights gate as any clip from YouTube (`youtube_clips.check_allowed`); the frames are then judged by the
usual picture checks like every other candidate."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from app.core.ai_config import ai_config
from app.documentary.visuals import images as IM


@dataclass(frozen=True)
class Frame:
    at: float
    path: Path
    sharpness: float
    brightness: float


def scene_times(video: Path, threshold: float, seconds: float) -> list[float]:
    """The moments the picture changes (the start of each scene), found by ffmpeg's scene score."""
    cmd = ["ffmpeg", "-v", "info", "-i", str(video), "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"]
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=max(120, seconds * 2), check=False)
    return [float(t) for t in re.findall(r"pts_time:([0-9.]+)", run.stderr)]


def sampled_times(seconds: float, count: int) -> list[float]:
    """Evenly spread moments, for a video with too few scene changes."""
    return [round(seconds * (n + 0.5) / count, 2) for n in range(count)]


def sharpness_of(image: Image.Image) -> float:
    """The variance of the Laplacian of the grey picture (higher is sharper)."""
    g = np.asarray(image.convert("L").resize((320, int(320 * image.height / image.width))), dtype=np.float32)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def brightness_of(image: Image.Image) -> float:
    return float(np.asarray(image.convert("L"), dtype=np.float32).mean())


def has_bars(image: Image.Image) -> bool:
    """A frame with black bars at the top and bottom (a letterboxed video) is no photo."""
    g = np.asarray(image.convert("L"), dtype=np.float32)
    band = max(image.height // 12, 2)
    return bool(g[:band].mean() < 8 and g[-band:].mean() < 8)


def probe_seconds(video: Path) -> float:
    run = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, timeout=60, check=False)
    return float(run.stdout.strip() or 0.0)


def grab(video: Path, at: float, out: Path, width: int) -> Path | None:
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(at, 0.0):.3f}", "-i", str(video), "-frames:v", "1",
           "-vf", f"scale={width}:-2", "-q:v", "2", str(out)]
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    return out if run.returncode == 0 and out.exists() and out.stat().st_size > 0 else None


def frames_of(video: Path, out_dir: Path, limit: int | None = None) -> list[Frame]:
    """At most `limit` (default: the configured maximum for an identity) good frames of the video, spread over it."""
    cfg = ai_config.youtube_frames
    limit = cfg.max_per_identity if limit is None else limit
    seconds = probe_seconds(video)
    if seconds <= 0 or limit <= 0:
        return []
    times = [t + cfg.into_scene_seconds for t in scene_times(video, cfg.scene_threshold, seconds) if t < seconds - 1]
    if len(times) < limit:
        times += sampled_times(seconds, limit * 2)
    out_dir.mkdir(parents=True, exist_ok=True)
    kept: list[Frame] = []
    hashes: list[str] = []
    for at in sorted(set(round(t, 2) for t in times)):
        if kept and at - kept[-1].at < cfg.min_gap_seconds:
            continue
        path = grab(video, at, out_dir / f"frame_{int(at * 100):07d}.jpg", cfg.width)
        if path is None:
            continue
        image = Image.open(path).convert("RGB")
        sharp, bright = sharpness_of(image), brightness_of(image)
        if sharp < cfg.min_sharpness or not cfg.min_brightness <= bright <= cfg.max_brightness or has_bars(image):
            path.unlink(missing_ok=True)
            continue
        h = IM.dhash(image)
        if any(IM.hamming(h, other) <= cfg.max_hash_distance for other in hashes):
            path.unlink(missing_ok=True)
            continue
        hashes.append(h)
        kept.append(Frame(at, path, round(sharp, 1), round(bright, 1)))
    if len(kept) > limit:                              # more than needed: the sharpest of each stretch of the video
        step = len(kept) / limit
        groups = [kept[int(i * step):int((i + 1) * step)] for i in range(limit)]
        keep = {max(g, key=lambda f: f.sharpness).path for g in groups if g}
        for f in kept:
            if f.path not in keep:
                f.path.unlink(missing_ok=True)
        kept = [f for f in kept if f.path in keep]
    return kept


def register_frames(db, case, video, frames: list[Frame], identity: str, credit: str, digest: str) -> list:
    """The frames as photo candidates of the case, found for `identity`, at most the identity's configured maximum in all
    (frames already registered for it count). Each is a picture of another channel's video: its rights stay with the owner's
    decision, and the usual checks (identity, scene, marks to cut away) judge it like every other candidate."""
    import json

    from app.db.models import VisualAsset
    from app.documentary import storage
    from app.documentary.visuals import tiers as T
    from app.documentary.visuals.research import next_asset_code

    cfg = ai_config.youtube_frames
    have = db.query(VisualAsset).filter(VisualAsset.case_id == case.id, VisualAsset.provider == "youtube_frame",
                                        VisualAsset.found_for == identity).count()
    made = []
    for frame in frames[:max(cfg.max_per_identity - have, 0)]:
        image = Image.open(frame.path).convert("RGB")
        code = next_asset_code(db)
        master = IM.save_master(image, storage.visuals_dir(case.id, "photo") / f"{code}.jpg")
        thumb = IM.save_thumbnail(image, storage.thumbs_dir(case.id) / f"{code}.jpg")
        minutes, seconds = divmod(int(frame.at), 60)
        tier = T.provisional_tier("youtube_frame", "evidence")
        asset = VisualAsset(
            case_id=case.id, asset_code=code, asset_type="photo", provider="youtube_frame",
            title=f"{video.title} ({minutes}:{seconds:02d})"[:500], caption=f"A frame of {credit} at {minutes}:{seconds:02d}",
            source_url=video.url, page_url=video.url, source_name=video.channel_name or "YouTube", found_for=identity,
            rights_status="editorial_review_required", rights_reason="a frame of another channel's video: the owner decides",
            asset_role="evidence", local_path=storage.rel(master), thumbnail_path=storage.rel(thumb), width=image.width,
            height=image.height, phash=IM.dhash(image), sha256=IM.sha256_file(master), verification_status="unverified",
            relevance_tier=tier, case_relevance=T.tier_label(tier), found_during="youtube_frames",
            spec_json=json.dumps({"video_sha256": digest, "frame_at": frame.at, "credit": credit}))
        db.add(asset)
        made.append(asset)
    db.commit()
    return made
