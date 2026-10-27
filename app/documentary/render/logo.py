"""The channel logo in a corner of the film.

One logo per language (the channel's `logo` in config/parameters/identity.json). It sits in the top corner at the END of
the reading direction - top right for English and German, top left for Persian and Arabic - the opposite corner to the
"illustration" label, clear of the subtitles, the date and place, and the map credit. It is shown over pictures and clips
only: graphics, maps, host scenes, chapter and title cards have their own layouts, and the channel intro is the logo.
The production script decides when (`script["logo"]["windows"]`); this module draws.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from app.core.ai_config import ai_config
from app.documentary.visuals.typography import RTL

ROOT = Path(__file__).resolve().parents[3]
JOIN_SECONDS = 0.05          # two shots this close are one stretch


def logo_path(language: str) -> Path | None:
    channel = ai_config.channels.get(language)
    rel = getattr(channel, "logo", None) if channel else None
    path = (Path(rel) if Path(rel).is_absolute() else ROOT / rel) if rel else None
    return path if path is not None and path.exists() else None


def windows_of(script: dict) -> dict:
    """When the logo is on screen: the stretches of picture and clip shots, at least `min_window_seconds` long."""
    cfg = ai_config.branding_settings().logo
    language = script.get("language")
    if not cfg.enabled or logo_path(language) is None:
        return {"windows": []}
    out: list[list[float]] = []
    for shot in sorted(script.get("shots") or [], key=lambda s: s["start"]):
        if shot.get("kind") not in cfg.shot_kinds:
            continue
        if out and shot["start"] - out[-1][1] <= JOIN_SECONDS:
            out[-1][1] = round(shot["end"], 3)
        else:
            out.append([round(shot["start"], 3), round(shot["end"], 3)])
    return {"windows": [w for w in out if w[1] - w[0] >= cfg.min_window_seconds]}


@lru_cache(maxsize=8)
def _bug(path: str, height: int, black_below: int, plate: float) -> tuple[np.ndarray, np.ndarray]:
    """(rgb, alpha) of the logo cut to its content at `height` pixels. A file without transparency has a black
    background, which becomes see-through; a soft dark plate under it keeps it readable on bright pictures."""
    arr = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float32)
    rgb, alpha = arr[..., :3], arr[..., 3] / 255.0
    if alpha.min() > 0.98:
        luma = rgb.max(axis=2)
        alpha = np.clip((luma - black_below) / max(2.0 * black_below, 1.0), 0.0, 1.0)
    ys, xs = np.nonzero(alpha > 0.05)
    pad = int(0.06 * max(alpha.shape))
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad, alpha.shape[0])
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad, alpha.shape[1])
    rgb, alpha = rgb[y0:y1, x0:x1], alpha[y0:y1, x0:x1]
    width = max(1, round(height * alpha.shape[1] / alpha.shape[0]))
    rgb = np.asarray(Image.fromarray(rgb.astype(np.uint8)).resize((width, height), Image.LANCZOS), dtype=np.float32)
    alpha = np.asarray(Image.fromarray((alpha * 255).astype(np.uint8)).resize((width, height), Image.LANCZOS),
                       dtype=np.float32) / 255.0
    yy, xx = np.mgrid[0:height, 0:width]
    d = np.sqrt(((xx - width / 2) / (width / 2)) ** 2 + ((yy - height / 2) / (height / 2)) ** 2)
    base = plate * np.clip((1.0 - d) / 0.35, 0.0, 1.0)
    total = alpha + base * (1.0 - alpha)
    return rgb * (alpha / np.maximum(total, 1e-6))[..., None], total


class LogoBug:
    """Composites the language's logo into frames inside the script's windows (fading in and out)."""

    def __init__(self, script: dict, W: int, H: int):
        cfg = ai_config.branding_settings().logo
        self.cfg = cfg
        self.windows = [tuple(w) for w in (script.get("logo") or {}).get("windows") or []]
        path = logo_path(script.get("language", ""))
        self.box = self.rgb = self.alpha = None
        if not (cfg.enabled and self.windows and path is not None):
            self.windows = []
            return
        rgb, alpha = _bug(str(path), max(8, round(H * cfg.height_fraction)), cfg.black_key_below, cfg.plate_alpha)
        margin = round(H * cfg.margin_fraction)
        h, w = alpha.shape
        x = margin if script["language"] in RTL else W - margin - w
        self.box = (x, margin, x + w, margin + h)
        self.rgb, self.alpha = rgb, alpha[..., None]

    def apply(self, frame: np.ndarray, t: float) -> np.ndarray:
        for start, end in self.windows:
            if start <= t < end:
                fade = min(1.0, (t - start) / self.cfg.fade_seconds, (end - t) / self.cfg.fade_seconds)
                x0, y0, x1, y1 = self.box
                a = self.alpha * self.cfg.opacity * fade
                out = frame.copy()
                region = out[y0:y1, x0:x1].astype(np.float32)
                out[y0:y1, x0:x1] = (region * (1 - a) + self.rgb * a).astype(np.uint8)
                return out
        return frame
