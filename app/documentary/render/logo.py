"""The channel logo in a corner of the film.

One logo per language (the channel's `logo` in config/parameters/identity.json). It sits in the top corner at the END of
the reading direction - top right for English and German, top left for Persian and Arabic - the opposite corner to the
"illustration" label, clear of the subtitles, the date and place, and the map credit. It is on screen for the whole film, over
every kind of shot (the cards draw no logo and keep that corner free); only the channel intro, which is the logo, has none.
The production script decides when (`script["logo"]["windows"]`); this module draws.
"""

from __future__ import annotations

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
    """When the logo is on screen: every shot except the kinds the config leaves out, joined into stretches."""
    cfg = ai_config.branding_settings().logo
    language = script.get("language")
    if not cfg.enabled or logo_path(language) is None:
        return {"windows": []}
    out: list[list[float]] = []
    for shot in sorted(script.get("shots") or [], key=lambda s: s["start"]):
        if shot.get("kind") in cfg.except_kinds:
            continue
        if out and shot["start"] - out[-1][1] <= JOIN_SECONDS:
            out[-1][1] = round(shot["end"], 3)
        else:
            out.append([round(shot["start"], 3), round(shot["end"], 3)])
    return {"windows": out}


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


def ease(u: float) -> float:
    """Slow out of the corner, slow back into it."""
    return u * u * (3.0 - 2.0 * u)


class LogoBug:
    """Composites the language's logo into frames inside the script's windows (fading in and out), and once every
    `lap.every_seconds` of the film for the whole of a lap around the screen (`lap.seconds`)."""

    def __init__(self, script: dict, W: int, H: int):
        cfg = ai_config.branding_settings().logo
        self.cfg = cfg
        self.windows = [tuple(w) for w in (script.get("logo") or {}).get("windows") or []]
        self.duration = float(script.get("duration") or 0.0)
        path = logo_path(script.get("language", ""))
        self.box = self.rgb = self.alpha = None
        self.route: list[tuple[float, float]] = []
        if not (cfg.enabled and path is not None):
            self.windows = []
            return
        rgb, alpha = _bug(str(path), max(8, round(H * cfg.height_fraction)), cfg.black_key_below, cfg.plate_alpha)
        margin = round(H * cfg.margin_fraction)
        h, w = alpha.shape
        x = margin if script["language"] in RTL else W - margin - w
        self.box = (x, margin, x + w, margin + h)
        self.rgb, self.alpha = rgb, alpha[..., None]
        low = round(H * (1.0 - cfg.lap.bottom_clear_fraction)) - h
        corners = [(margin, margin), (W - margin - w, margin), (W - margin - w, low), (margin, low)]    # clockwise
        first = corners.index((x, margin))
        self.route = corners[first:] + corners[:first] + [corners[first]]

    def position(self, u: float) -> tuple[float, float]:
        """Where the logo is `u` (0..1) of the way round its lap: constant speed along the route, eased at both ends."""
        legs = [abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(self.route, self.route[1:], strict=False)]
        at = ease(min(max(u, 0.0), 1.0)) * sum(legs)
        for (a, b), leg in zip(zip(self.route, self.route[1:], strict=False), legs, strict=True):
            if at <= leg or (a, b) == (self.route[-2], self.route[-1]):
                k = min(at / leg, 1.0) if leg else 1.0
                return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
            at -= leg
        return self.route[-1]

    def lap_at(self, t: float) -> tuple[float, float, float] | None:
        """(x, y, fade) while a lap is under way: it starts at every multiple of `every_seconds` (not at 0) and only
        when it ends before the film does. It fades in and out only where the logo is not already on screen."""
        lap = self.cfg.lap
        k = int(t // lap.every_seconds)
        start = k * lap.every_seconds
        if k < 1 or t - start >= lap.seconds or start + lap.seconds > self.duration:
            return None
        x, y = self.position((t - start) / lap.seconds)
        return x, y, min(1.0, (t - start) / self.cfg.fade_seconds, (start + lap.seconds - t) / self.cfg.fade_seconds)

    def window_fade(self, t: float) -> float:
        for start, end in self.windows:
            if start <= t < end:
                return min(1.0, (t - start) / self.cfg.fade_seconds, (end - t) / self.cfg.fade_seconds)
        return 0.0

    def apply(self, frame: np.ndarray, t: float) -> np.ndarray:
        if self.box is None:
            return frame
        fade, (x, y) = self.window_fade(t), self.box[:2]
        lap = self.lap_at(t)
        if lap is not None:
            x, y, lap_fade = lap
            fade = max(fade, lap_fade)
        if fade <= 0.0:
            return frame
        h, w = self.alpha.shape[:2]
        x, y = round(x), round(y)
        a = self.alpha * self.cfg.opacity * fade
        out = frame.copy()
        region = out[y:y + h, x:x + w].astype(np.float32)
        out[y:y + h, x:x + w] = (region * (1 - a) + self.rgb * a).astype(np.uint8)
        return out
