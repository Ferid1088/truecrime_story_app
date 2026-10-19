"""How much the host's hands move in a finished avatar video (the owner's "he moves his hands too
much"). Frames are sampled every host.hand_motion.every_seconds and each is compared with the
one before inside the hand region; a window with a large grey-level difference counts as visible
hand movement. A measurement, not a verdict: it compares videos (the old ones against the new)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin


def _frames(video: Path, every: float, width: int) -> tuple[np.ndarray, int, int]:
    """Grey frames every `every` seconds, shape (n, h, w)."""
    probe = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(video), "-frames:v", "1", "-vf", f"scale={width}:-2",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    size = len(probe.stdout)
    height = size // width
    raw = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(video), "-vf",
         f"fps=1/{every},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    count = len(raw) // (width * height)
    return np.frombuffer(raw[:count * width * height], dtype=np.uint8).reshape(count, height, width), width, height


def hand_motion(video: Path) -> dict:
    """{"windows": n, "moving": k, "share": k/n, "diffs": [...]} for one video."""
    cfg = ai_config.host.hand_motion
    frames, w, h = _frames(Path(video), cfg.every_seconds, cfg.sample_width_px)
    x0, x1, y0, y1 = cfg.region
    region = frames[:, int(h * y0):int(h * y1), int(w * x0):int(w * x1)].astype(np.int16)
    diffs = [float(np.abs(region[i + 1] - region[i]).mean()) for i in range(len(region) - 1)]
    moving = sum(1 for d in diffs if d > cfg.threshold)
    return {"windows": len(diffs), "moving": moving,
            "mean_diff": round(float(np.mean(diffs)), 2) if diffs else 0.0,
            "share": round(moving / len(diffs), 3) if diffs else 0.0,
            "diffs": [round(d, 2) for d in diffs], "every_seconds": cfg.every_seconds}
