"""A portrait drawn as a pencil drawing, stroke by stroke, the way an artist works: the outline, the masses of hair, light tones,
mid tones, the features, the deep tones. The drawing is made once for a photo and kept: a progress video of the paper (the graphite
growing, no pencil, no window) and the pencil's position for every frame; the film's compositor shows the video in its window and
puts the tiny black-and-white pencil where the file says. Nothing here calls a provider."""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.core.ai_config import ai_config

CW, CH = 704, 880                                                   # the paper: 4:5, even sides for the video codec
PAPER = np.array((210, 226, 232), np.float32)                      # BGR cream
PHASE_SECONDS = (("silhouette", 12), ("hair masses", 55), ("light tones", 50), ("mid tones", 60), ("features", 70), ("deep tones", 93))
LIFT_FAR, LIFT_NEAR = 0.10, 0.02                                   # seconds the pencil is in the air between two strokes (far, near)
STEP = 2.0                                                          # pixels between two points of a stroke
PROGRESS_FPS = 10


@dataclass
class Timeline:
    """What the pencil does: for each moment its position, the stroke's width and strength, and whether it draws."""

    t: np.ndarray
    pos: np.ndarray
    width: np.ndarray
    alpha: np.ndarray
    draws: np.ndarray

    @property
    def seconds(self) -> float:
        return float(self.t[-1])


class PortraitDrawing:
    """The drawing of one photo."""

    def __init__(self, photo: Path, seed: int = 7):
        self.rng = np.random.RandomState(seed)
        self.gray = self._crop(cv2.imread(str(photo)))
        yy, xx = np.mgrid[0:CH, 0:CW]
        self.oval = np.clip(1.5 - (((xx - CW * 0.5) / (CW * 0.5)) ** 2 + ((yy - CH * 0.47) / (CH * 0.56)) ** 2), 0, 1)
        self.dark = 1.0 - cv2.GaussianBlur(self.gray, (0, 0), 3).astype(np.float32) / 255.0
        person = np.clip(1.15 - (((xx - CW * 0.5) / (CW * 0.46)) ** 2 + ((yy - CH * 0.49) / (CH * 0.52)) ** 2) ** 1.4, 0, 1)
        self.dark = np.clip((self.dark - 0.12) / 0.8, 0, 1) * np.clip(person * 1.3, 0, 1)
        self._directions()
        self.paper = (PAPER + self.rng.randn(CH, CW, 1).astype(np.float32) * 2.0).clip(0, 255)
        grain = cv2.GaussianBlur(self.rng.rand(CH, CW).astype(np.float32), (0, 0), 0.7)
        self.grain = np.clip(0.84 + 0.17 * (grain - grain.mean()) / (grain.std() + 1e-6), 0.45, 1.0)
        self.graphite = np.zeros((CH, CW), np.float32)

    # -- the photo ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _crop(img: np.ndarray) -> np.ndarray:
        """The photo as the paper: a 4:5 crop around the middle of the picture (a little above it), in the tones of a drawing."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        crop_w = min(w, int(h * CW / CH))
        crop_h = int(crop_w * CH / CW)
        x0 = (w - crop_w) // 2
        y0 = max(0, min(h - crop_h, int(h * 0.45 - crop_h / 2)))
        gray = cv2.resize(gray[y0:y0 + crop_h, x0:x0 + crop_w], (CW, CH), interpolation=cv2.INTER_CUBIC)
        return cv2.createCLAHE(clipLimit=2.4, tileGridSize=(6, 6)).apply(gray)

    def _directions(self) -> None:
        """The way the lines of the picture run, so that the strokes follow the forms (hair, folds) and not a grid."""
        g = cv2.GaussianBlur(self.gray, (0, 0), 2.2).astype(np.float32)
        gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
        jxx, jyy, jxy = (cv2.GaussianBlur(a, (0, 0), 7) for a in (gx * gx, gy * gy, gx * gy))
        theta = 0.5 * np.arctan2(2 * jxy, jxx - jyy) + np.pi / 2
        self.coherence = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (jxx + jyy + 1e-6)
        d0 = np.array([math.cos(math.radians(50)), -math.sin(math.radians(50))], np.float32)
        vx, vy = np.cos(theta), np.sin(theta)
        flip = (vx * d0[0] + vy * d0[1]) < 0
        vx, vy = np.where(flip, -vx, vx), np.where(flip, -vy, vy)
        w = np.clip(self.coherence * 1.6, 0, 1)
        vx, vy = w * vx + (1 - w) * d0[0], w * vy + (1 - w) * d0[1]
        norm = np.sqrt(vx ** 2 + vy ** 2) + 1e-6
        self.vx, self.vy = vx / norm, vy / norm

    # -- strokes -----------------------------------------------------------------------------------------------------
    def _trace(self, seed, length: float, thr: float, jitter: float = 0.0) -> np.ndarray:
        p, prev = np.array(seed, np.float32), None
        pts = [p.copy()]
        for _ in range(int(length / STEP)):
            x, y = int(round(p[0])), int(round(p[1]))
            if not (0 <= x < CW and 0 <= y < CH) or self.dark[y, x] < thr * 0.8:
                break
            v = np.array([self.vx[y, x], self.vy[y, x]], np.float32)
            if prev is not None and float(v @ prev) < 0:
                v = -v
            v = v + jitter * self.rng.randn(2).astype(np.float32) * 0.15
            v /= np.linalg.norm(v) + 1e-6
            p = p + v * STEP
            pts.append(p.copy())
            prev = v
        return np.array(pts)

    def _flow(self, spacing: int, thr: float, length: float, min_coh: float = 0.0) -> list[np.ndarray]:
        out = []
        for y in range(4, CH - 4, spacing):
            for x in range(4, CW - 4, spacing):
                sx = x + self.rng.randint(-spacing // 2, spacing // 2 + 1)
                sy = y + self.rng.randint(-spacing // 2, spacing // 2 + 1)
                if 0 <= sx < CW and 0 <= sy < CH and self.dark[sy, sx] > thr and self.coherence[sy, sx] >= min_coh:
                    stroke = self._trace((sx, sy), length * (0.6 + 0.8 * self.rng.rand()), thr, 0.4)
                    if len(stroke) >= 5:
                        out.append(stroke)
        return out

    def _split(self, run: np.ndarray, maxlen: float) -> list[np.ndarray]:
        pieces, i = [], 0
        while i < len(run):
            n = max(int(maxlen / 2 * (0.6 + 0.8 * self.rng.rand())), 4)
            if len(run[i:i + n]) >= 4:
                pieces.append(run[i:i + n])
            i += n
        return pieces

    def _hatch(self, angle: float, spacing: int, thr: float, maxlen: float) -> list[np.ndarray]:
        """Straight hatching at one angle, only where the picture is darker than `thr`."""
        ang = math.radians(angle)
        d = np.array([math.cos(ang), -math.sin(ang)], np.float32)
        n = np.array([math.sin(ang), math.cos(ang)], np.float32)
        out, diag = [], int(math.hypot(CW, CH))
        for off in range(-diag // 2, diag // 2, spacing):
            base = np.array([CW / 2, CH / 2], np.float32) + n * off
            run: list[np.ndarray] = []
            for k in range(-diag // 2, diag // 2, 2):
                p = base + d * k
                x, y = int(round(p[0])), int(round(p[1]))
                if 0 <= x < CW and 0 <= y < CH and self.dark[y, x] > thr:
                    run.append(p)
                elif run:
                    if len(run) >= 4:
                        out.extend(self._split(np.array(run), maxlen))
                    run = []
            if len(run) >= 4:
                out.extend(self._split(np.array(run), maxlen))
        return out

    def _contours(self, sigma: float, lo: int, hi: int, min_len: int, inside: float) -> list[np.ndarray]:
        edges = cv2.Canny(cv2.GaussianBlur(self.gray, (0, 0), sigma), lo, hi)
        edges = (edges * (self.oval > inside)).astype(np.uint8)
        found, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        return [c[:len(c) // 2 + 1, 0, :].astype(np.float32) for c in found if len(c) >= min_len * 2]

    def _features(self) -> list[np.ndarray]:
        """The lines of the eyes, brows, nose and mouth (the middle of the picture, where a portrait has them)."""
        fx, fy, fw, fh = int(CW * 0.2), int(CH * 0.17), int(CW * 0.6), int(CH * 0.58)
        area = np.zeros((CH, CW), np.uint8)
        cv2.rectangle(area, (fx, fy + int(fh * 0.20)), (fx + fw, fy + int(fh * 0.55)), 255, -1)
        cv2.rectangle(area, (fx + int(fw * 0.28), fy + int(fh * 0.5)), (fx + int(fw * 0.72), fy + int(fh * 0.72)), 255, -1)
        cv2.rectangle(area, (fx + int(fw * 0.15), fy + int(fh * 0.66)), (fx + int(fw * 0.85), fy + int(fh * 0.9)), 255, -1)
        edges = (cv2.Canny(cv2.GaussianBlur(self.gray, (0, 0), 1.7), 45, 115) * (area > 0)).astype(np.uint8)
        found, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        strokes = [c[:len(c) // 2 + 1, 0, :].astype(np.float32) for c in found if len(c) >= 34]
        return sorted(strokes, key=lambda c: -len(c))[:90]

    @staticmethod
    def _smooth(s: np.ndarray, k: int = 5) -> np.ndarray:
        if len(s) < k * 2:
            return s
        pad = np.pad(s, ((k, k), (0, 0)), mode="edge")
        kernel = np.ones(k * 2 + 1) / (k * 2 + 1)
        return np.c_[np.convolve(pad[:, 0], kernel, "valid"), np.convolve(pad[:, 1], kernel, "valid")]

    @staticmethod
    def _resample(s: np.ndarray) -> np.ndarray | None:
        d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(s, axis=0), axis=1))]
        if d[-1] < STEP:
            return None
        t = np.arange(0, d[-1], STEP)
        return np.c_[np.interp(t, d, s[:, 0]), np.interp(t, d, s[:, 1])]

    @staticmethod
    def _order(strokes: list[np.ndarray], start) -> list[np.ndarray]:
        """The strokes in the order a hand would make them: always the nearest start next."""
        starts = np.array([s[0] for s in strokes])
        alive = np.ones(len(strokes), bool)
        out, pos = [], np.array(start, np.float32)
        for _ in range(len(strokes)):
            d = np.linalg.norm(starts - pos, axis=1)
            d[~alive] = 1e9
            i = int(np.argmin(d))
            alive[i] = False
            out.append(strokes[i])
            pos = strokes[i][-1]
        return out

    def phases(self) -> list[tuple[str, float, list[np.ndarray], float, float]]:
        """(name, seconds, strokes, width, strength) of each phase of the drawing, in the order an artist works."""
        raw = {
            "silhouette": (sorted(self._contours(3.0, 25, 70, 70, 0.45), key=lambda s: -len(s))[:22], 1.8, 0.38),
            "hair masses": (self._flow(24, 0.50, 90, 0.30), 1.9, 0.34),
            "light tones": (self._hatch(55, 9, 0.12, 150), 1.4, 0.30),
            "mid tones": (self._hatch(55, 9, 0.30, 120) + self._hatch(15, 11, 0.42, 100), 1.6, 0.34),
            "features": (self._features(), 1.4, 0.78),
            "deep tones": (self._flow(20, 0.62, 44, 0.22) + self._hatch(30, 8, 0.58, 90), 1.7, 0.44),
        }
        out, pos = [], (CW / 2, 0)
        for name, seconds in PHASE_SECONDS:
            strokes, width, strength = raw[name]
            made = [r for r in (self._resample(self._smooth(s)) for s in strokes) if r is not None and len(r) >= 4]
            made = self._order(made, pos) if made else made
            if made:
                pos = made[-1][-1]
            out.append((name, float(seconds), made, width, strength))
        return out

    # -- the pencil's timeline ---------------------------------------------------------------------------------------
    def timeline(self, total_seconds: float) -> Timeline:
        """Every point the pencil visits, with the time it gets there; the whole drawing takes exactly `total_seconds`."""
        ev_t, ev_p, ev_w, ev_a, ev_d = [], [], [], [], []
        t, cur = 0.0, np.array((CW / 2, -40), np.float32)
        phases = self.phases()
        scale = total_seconds / sum(seconds for _, seconds, _, _, _ in phases)
        for name, seconds, strokes, width, strength in phases:
            seconds *= scale
            lifts, prev_end = [], cur
            for s in strokes:
                lifts.append((LIFT_NEAR if np.linalg.norm(s[0] - prev_end) < 30 else LIFT_FAR) * scale)
                prev_end = s[-1]
            speed = sum(len(s) * STEP for s in strokes) / max(seconds - sum(lifts), seconds * 0.5) if strokes else 1.0
            for s, lift in zip(strokes, lifts, strict=True):
                a, b = cur, s[0]
                for i in range(1, 5):                              # the pencil lifts and travels to the next stroke along a small arc
                    u = i / 4
                    hover = math.sin(math.pi * u) * (3 if lift < LIFT_NEAR * scale * 1.5 else 12)
                    ev_t.append(t + lift * u); ev_p.append(a + (b - a) * u - np.array([0, hover], np.float32))
                    ev_w.append(0); ev_a.append(0); ev_d.append(False)
                t += lift
                base = strength * (0.8 + 0.4 * self.rng.rand())
                for k in range(len(s)):
                    u = k / max(len(s) - 1, 1)
                    t += STEP / (speed * (0.55 + 0.9 * math.sin(math.pi * min(max(u, 0.04), 0.96))))
                    pressure = max(0.0, min(1.0, u / 0.14, (1 - u) / 0.2)) ** 0.6
                    tone = float(self.dark[min(max(int(s[k][1]), 0), CH - 1), min(max(int(s[k][0]), 0), CW - 1)])
                    tone_mod = 1.0 if name in ("silhouette", "features") else min(1.0, 0.25 + tone * 1.5)
                    ev_t.append(t); ev_p.append(s[k]); ev_w.append(width * (0.75 + 0.45 * pressure))
                    ev_a.append(base * tone_mod * (0.35 + 0.65 * pressure)); ev_d.append(k > 0)
                cur = s[-1]
        tl = Timeline(np.array(ev_t), np.array(ev_p, np.float32), np.array(ev_w, np.float32), np.array(ev_a, np.float32), np.array(ev_d))
        tl.t = tl.t * (total_seconds / max(tl.seconds, 1e-6))        # the strokes' own pace sums to a little off the target
        return tl

    # -- the paper ---------------------------------------------------------------------------------------------------
    def _segment(self, p, q, width: float, alpha: float) -> None:
        r = int(width) + 3
        x0, y0 = max(int(min(p[0], q[0])) - r, 0), max(int(min(p[1], q[1])) - r, 0)
        x1, y1 = min(int(max(p[0], q[0])) + r + 1, CW), min(int(max(p[1], q[1])) + r + 1, CH)
        if x0 >= x1 or y0 >= y1:
            return
        mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.line(mask, (int(round((p[0] - x0) * 16)), int(round((p[1] - y0) * 16))),
                 (int(round((q[0] - x0) * 16)), int(round((q[1] - y0) * 16))), 255, max(1, int(round(width))), cv2.LINE_AA, 4)
        roi = self.graphite[y0:y1, x0:x1]
        roi[:] = 1 - (1 - roi) * (1 - alpha * (mask.astype(np.float32) / 255.0))

    def image(self) -> np.ndarray:
        """The paper with the graphite drawn so far (BGR)."""
        return np.clip(self.paper * (1 - (self.graphite * self.grain)[..., None] * 0.97), 0, 255).astype(np.uint8)

    def finished(self, tl: Timeline) -> np.ndarray:
        """The finished drawing (a quick look at the result without making the video)."""
        self.graphite[:] = 0
        prev = None
        for i in range(len(tl.t)):
            if tl.draws[i] and prev is not None:
                self._segment(prev, tl.pos[i], tl.width[i], tl.alpha[i])
            prev = tl.pos[i]
        return self.image()

    def render(self, out_dir: Path, seconds: float | None = None, fps: int = PROGRESS_FPS,
               progress: Callable[[float], None] | None = None) -> dict:
        """The progress video of the drawing and the pencil's position for every frame, in `out_dir`. Returns the record to keep."""
        seconds = seconds or ai_config.drawing_settings().base_seconds
        out_dir.mkdir(parents=True, exist_ok=True)
        tl = self.timeline(seconds)
        self.graphite[:] = 0
        video, pen_file = out_dir / "progress.mp4", out_dir / "pen.npy"
        frames = int(round(seconds * fps)) + 1
        pen = np.zeros((frames, 2), np.float32)
        proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{CW}x{CH}",
                                 "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
                                 "-movflags", "+faststart", str(video)], stdin=subprocess.PIPE)
        index, prev = 0, None
        try:
            for f in range(frames):
                now = f / fps
                while index < len(tl.t) and tl.t[index] <= now:
                    if tl.draws[index] and prev is not None:
                        self._segment(prev, tl.pos[index], tl.width[index], tl.alpha[index])
                    prev = tl.pos[index]
                    index += 1
                pen[f] = prev if prev is not None else (CW / 2, -40)
                proc.stdin.write(self.image().tobytes())
                if progress and f % (fps * 10) == 0:
                    progress(f / frames)
        finally:
            proc.stdin.close()
            proc.wait()
        np.save(pen_file, pen)
        return {"video": str(video), "pen": str(pen_file), "seconds": seconds, "fps": fps, "width": CW, "height": CH,
                "frames": frames}


def write_record(out_dir: Path, record: dict) -> Path:
    path = out_dir / "drawing.json"
    path.write_text(json.dumps(record))
    return path
