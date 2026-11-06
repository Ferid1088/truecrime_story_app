"""The copyright watermark: the language's logo, small and faint, always on screen inside a circle in the middle of the picture.

Its size is a fraction of the corner logo's (`watermark.size_of_logo`) and it is drawn over every frame at `watermark.opacity`. It never
rests and never jumps: it drifts at a constant, very slow speed along a smooth curve through random places of the circle (whose area is
`watermark.circle_area_share` of the picture), at sub-pixel precision so the movement is even. The curve is drawn from the config's seed, the
language and the film's length, so a film is rendered the same way twice.
"""

from __future__ import annotations

import hashlib
import math
import random

import cv2
import numpy as np

from app.core.ai_config import ai_config
from app.documentary.render.logo import _bug, logo_path

STEP = 0.5                  # pixels between the points of the curve


def circle_radius(W: int, H: int, area_share: float) -> float:
    """The radius of the circle that covers `area_share` of a W x H picture."""
    return math.sqrt(area_share * W * H / math.pi)


def _spline(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """A smooth (Catmull-Rom) curve through the points, as a dense polyline."""
    pts = [points[0], *points, points[-1]]
    out: list[tuple[float, float]] = []
    for a, b, c, d in zip(pts, pts[1:], pts[2:], pts[3:], strict=False):
        n = max(8, int(math.dist(b, c) / 4))
        for k in range(n):
            u = k / n
            out.append(tuple(0.5 * (2 * q1 + (-q0 + q2) * u + (2 * q0 - 5 * q1 + 4 * q2 - q3) * u * u + (-q0 + 3 * q1 - 3 * q2 + q3) * u ** 3)
                             for q0, q1, q2, q3 in zip(a, b, c, d, strict=True)))
    out.append(points[-1])
    return out


def path(W: int, H: int, w: int, h: int, length: float, cfg, language: str, duration: float) -> np.ndarray:
    """The top-left corners (one per STEP pixels of travel, `length` pixels at least) of a w x h logo along the curve, each with the whole logo
    inside the circle."""
    radius = circle_radius(W, H, cfg.circle_area_share)
    reach = radius - math.hypot(w, h) / 2
    seed = int(hashlib.sha256(f"{cfg.seed}:{language}:{round(duration)}".encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)

    def place() -> tuple[float, float]:
        r, a = reach * math.sqrt(rng.random()), rng.random() * 2 * math.pi
        return r * math.cos(a), r * math.sin(a)

    points, travelled = [(0.0, 0.0)], 0.0
    while travelled < length + reach:
        nxt = place()
        if math.dist(points[-1], nxt) < 0.6 * reach:          # a leg is long enough to be a drift, not a wiggle
            continue
        travelled += math.dist(points[-1], nxt)
        points.append(nxt)
    dense = np.array(_spline(points), dtype=np.float64)
    gaps = np.hypot(*np.diff(dense, axis=0).T)
    along = np.concatenate([[0.0], np.cumsum(gaps)])
    marks = np.arange(0.0, along[-1], STEP)
    xs, ys = np.interp(marks, along, dense[:, 0]), np.interp(marks, along, dense[:, 1])
    norm = np.hypot(xs, ys)
    scale = np.minimum(1.0, reach / np.maximum(norm, 1e-9))            # the curve may swing a little wide: pulled back inside
    return np.stack([W / 2 + xs * scale - w / 2, H / 2 + ys * scale - h / 2], axis=1)


class Watermark:
    """Composites the watermark into every frame of a film."""

    def __init__(self, script: dict, W: int, H: int):
        cfg = ai_config.branding_settings().watermark
        self.cfg = cfg
        language = script.get("language") or ""
        logo = logo_path(language)
        self.rgb = self.alpha = None
        if not (cfg.enabled and logo is not None):
            return
        corner = ai_config.branding_settings().logo
        rgb, alpha = _bug(str(logo), max(6, round(H * corner.height_fraction * cfg.size_of_logo)), corner.black_key_below, 0.0)
        self.rgb, self.alpha = rgb, alpha
        self.W, self.H = W, H
        h, w = alpha.shape
        duration = float(script.get("duration") or 0.0)
        self.speed = circle_radius(W, H, cfg.circle_area_share) * cfg.speed_radii_per_minute / 60.0          # pixels per second
        self.path = path(W, H, w, h, self.speed * duration, cfg, language, duration)

    def place_at(self, t: float) -> tuple[float, float]:
        """The logo's top-left corner `t` seconds into the film (fractions of a pixel)."""
        at = min(max(t, 0.0) * self.speed / STEP, len(self.path) - 1.0)
        i = min(int(at), len(self.path) - 2)
        k = at - i
        return tuple(float(self.path[i][n] * (1 - k) + self.path[i + 1][n] * k) for n in (0, 1))

    def apply(self, frame: np.ndarray, t: float) -> np.ndarray:
        if self.rgb is None:
            return frame
        x, y = self.place_at(t)
        ix, iy = math.floor(x), math.floor(y)
        h, w = self.alpha.shape
        shift = np.float32([[1, 0, x - ix + 1], [0, 1, y - iy + 1]])         # the sub-pixel part moves the logo smoothly
        size = (w + 2, h + 2)
        a = cv2.warpAffine(self.alpha, shift, size, flags=cv2.INTER_LINEAR)[..., None] * self.cfg.opacity
        c = cv2.warpAffine(self.rgb, shift, size, flags=cv2.INTER_LINEAR)
        x0, y0 = ix - 1, iy - 1
        out = frame.copy()
        region = out[y0:y0 + h + 2, x0:x0 + w + 2].astype(np.float32)
        out[y0:y0 + h + 2, x0:x0 + w + 2] = (region * (1 - a) + c * a).astype(np.uint8)
        return out
