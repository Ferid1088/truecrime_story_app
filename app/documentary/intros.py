"""Channel intros: 5–7 s, made from the channel's logo, the same for every
film of a channel over time.

Each channel has one concept (config channels.<lang>.intro_concept),
chosen by the producer from previews:

  flashlight   ClueVera (en) — a flashlight searches the dark and finds the
               logo, then the light comes up; dust in the beam, a red glint,
               a deep hit.
  trail_stamp  Fallspur (de) — a red trail of evidence dots runs across dark
               case paper into the folder's own trail; the logo lands like
               a stamp; a cold light passes over it.
  moonrise     رد خاموش (fa) — the red moon glows in the dark, the winding
               path draws itself down from it, the calligraphy appears
               right to left; a classical (nylon-string) guitar on its three
               bass strings only — a slow, dark E-minor line and a deep chord —
               over a low drone, a low hit.
  sand         أثر خفي (ar) — fine sand covers the logo and blows away right
               to left, the red trace glows last; wind, oud-like notes
               (maqam Hijaz), a low hit.
  (keyhole and arch: the other two previews, kept as alternatives.)

Pictures are drawn with numpy/OpenCV, the sound is synthesized (no samples,
no costs). An intro is rendered once per channel at the film's size into
data/intros/<lang>/ (intro.mp4 + intro.wav + intro.json) and reused; it is
made again only when the logo file, the concept, the size or the intro
code version changes (fingerprint) — so every film of a channel opens with
exactly the same intro.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from app.core.ai_config import ai_config

VERSION = 1
ROOT = Path(__file__).resolve().parents[2]
DESIGN_H = 720.0  # the concepts were designed at 1280×720; sizes scale with H


@dataclass(frozen=True)
class Concept:
    name: str
    seconds: float
    logo_height: float  # in design pixels


CONCEPTS = {
    "keyhole": Concept("keyhole", 6.0, 600),
    "flashlight": Concept("flashlight", 6.0, 600),
    "trail_stamp": Concept("trail_stamp", 6.0, 620),
    "moonrise": Concept("moonrise", 6.5, 620),
    "arch": Concept("arch", 6.0, 620),
    "sand": Concept("sand", 6.0, 620),
}
# the producer's choice (10 Oct): ClueVera B, Fallspur A, Persian moonrise
# on a classical guitar's bass strings, Arabic B
DEFAULT_CONCEPT = {"en": "flashlight", "de": "trail_stamp", "fa": "moonrise", "ar": "sand"}
DEFAULT_LOGO = {"en": "data/logos/Logo_English.png", "de": "data/logos/Logo_German.png",
                "fa": "data/logos/Logo_Persian.png", "ar": "data/logos/Logo_Arabic.png"}

_LOCK = threading.Lock()


def ease(p):
    p = np.clip(p, 0.0, 1.0)
    return p * p * (3 - 2 * p)


def ease_out(p):
    p = np.clip(p, 0.0, 1.0)
    return 1 - (1 - p) ** 3


# ---------------------------------------------------------------------------
# where a channel's intro lives
# ---------------------------------------------------------------------------


def channel_intro(language: str) -> tuple[str, Path] | None:
    """(concept, logo path) of a channel, None without a logo."""
    ch = ai_config.channels.get(language)
    concept = (getattr(ch, "intro_concept", None) if ch else None) or DEFAULT_CONCEPT.get(language)
    logo = (getattr(ch, "logo", None) if ch else None) or DEFAULT_LOGO.get(language)
    if not concept or concept not in CONCEPTS or not logo:
        return None
    path = Path(logo)
    path = path if path.is_absolute() else ROOT / path
    return (concept, path) if path.exists() else None


def intro_seconds(language: str) -> float | None:
    """The intro's length (known without rendering it), None without one."""
    if not ai_config.chapters.intro_enabled:
        return None
    found = channel_intro(language)
    return CONCEPTS[found[0]].seconds if found else None


def intro_dir(language: str) -> Path:
    base = Path(ai_config.chapters.intro_dir)
    return (base if base.is_absolute() else ROOT / base) / language


def fingerprint(concept: str, logo: Path, W: int, H: int, fps: int, sr: int) -> str:
    h = hashlib.sha256()
    h.update(logo.read_bytes())
    h.update(json.dumps([VERSION, concept, W, H, fps, sr]).encode())
    return h.hexdigest()[:16]


def ensure_intro(language: str, W: int, H: int, fps: int) -> dict | None:
    """The channel's intro at this size: {video, audio, seconds, concept} —
    rendered on first use (minutes), afterwards always the same files."""
    found = channel_intro(language)
    if not found or not ai_config.chapters.intro_enabled:
        return None
    concept, logo = found
    sr = ai_config.loudness.sample_rate
    out = intro_dir(language)
    tag = f"{W}x{H}"
    meta_path = out / f"intro_{tag}.json"
    fp = fingerprint(concept, logo, W, H, fps, sr)
    with _LOCK:
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if (meta.get("fingerprint") == fp and (out / meta["video"]).exists()
                    and (out / meta["audio"]).exists()):
                return {**meta, "video": str(out / meta["video"]),
                        "audio": str(out / meta["audio"])}
        out.mkdir(parents=True, exist_ok=True)
        video, audio = out / f"intro_{tag}.mp4", out / f"intro_{tag}.wav"
        IntroMaker(logo, W, H, fps, sr).render(concept, video, audio)
        meta = {"concept": concept, "seconds": CONCEPTS[concept].seconds, "fingerprint": fp,
                "logo": str(logo.relative_to(ROOT)) if logo.is_relative_to(ROOT) else str(logo),
                "video": video.name, "audio": audio.name, "width": W, "height": H, "fps": fps}
        meta_path.write_text(json.dumps(meta, indent=1))
        return {**meta, "video": str(video), "audio": str(audio)}


# ---------------------------------------------------------------------------
# sound (numpy only: FFT filters, Karplus–Strong strings)
# ---------------------------------------------------------------------------


class Sound:
    def __init__(self, seconds: float, sr: int, seed: int = 7):
        self.sr = sr
        self.n = int(seconds * sr)
        self.dur = seconds
        self.rng = np.random.default_rng(seed)
        self.mix = np.zeros(self.n)

    def t(self, seconds: float | None = None) -> np.ndarray:
        return np.arange(int((seconds or self.dur) * self.sr)) / self.sr

    def band(self, x: np.ndarray, lo: float | None = None, hi: float | None = None) -> np.ndarray:
        X = np.fft.rfft(x)
        f = np.fft.rfftfreq(len(x), 1 / self.sr)
        m = np.ones_like(f)
        if hi is not None:
            m *= 1 / (1 + (f / hi) ** 4)
        if lo is not None:
            m *= 1 / (1 + (lo / np.maximum(f, 1e-3)) ** 4)
        return np.fft.irfft(X * m, len(x))

    def add(self, x: np.ndarray, at: float = 0.0, gain: float = 1.0) -> None:
        i = int(at * self.sr)
        m = max(0, min(len(x), self.n - i))
        self.mix[i:i + m] += x[:m] * gain

    def drone(self, freqs, swell=(0.2, 1.0), gain=0.35):
        t = self.t()
        x = sum(np.sin(2 * math.pi * f * t + i) * (0.6 / (i + 1)) for i, f in enumerate(freqs))
        x = x + self.band(self.rng.normal(0, 0.25, len(t)), hi=180)
        e = np.interp(t, [0, self.dur * 0.6, self.dur * 0.85, self.dur],
                      [swell[0], swell[1], swell[1], 0])
        self.add(x * e, 0, gain)

    def whoosh(self, at, length=1.2, lo=300, hi=3000, gain=0.35):
        n = int(length * self.sr)
        x = self.band(self.rng.normal(0, 1, n), lo, hi)
        self.add(x * np.sin(np.linspace(0, math.pi, n)) ** 2, at, gain)

    def boom(self, at, f0=110, f1=38, length=2.2, gain=0.9):
        t = self.t(length)
        f = f1 + (f0 - f1) * np.exp(-t * 6)
        x = np.sin(2 * math.pi * np.cumsum(f) / self.sr) * np.exp(-t * 2.2)
        x += self.band(self.rng.normal(0, 1, len(t)), hi=900) * np.exp(-t * 30) * 0.5
        self.add(x, at, gain)

    def click(self, at, gain=0.25, lo=2000):
        n = int(0.03 * self.sr)
        x = self.band(self.rng.normal(0, 1, n), lo, min(12000, lo * 4))
        self.add(x * np.exp(-np.linspace(0, 12, n)), at, gain)

    def pluck(self, at, freq, length=2.5, gain=0.35, bright=0.996, body=0.5):
        """Karplus–Strong plucked string (oud / setar colour)."""
        N = int(self.sr / freq)
        buf = self.rng.uniform(-1, 1, N)
        n = int(length * self.sr)
        y = np.empty(n)
        for k in range(n):
            j = k % N
            y[k] = buf[j]
            buf[j] = bright * 0.5 * (buf[j] + buf[(k + 1) % N])
        y = self.band(y, hi=2500 + 2500 * body) * np.exp(-np.linspace(0, 3.2, n))
        self.add(y, at, gain)

    def guitar(self, at, freq, length=3.5, gain=0.3) -> np.ndarray:
        """A clean electric guitar note: two slightly detuned Karplus–Strong
        strings (a light chorus), bright, returned for a reverb bus."""
        n = int(length * self.sr)
        out = np.zeros(self.n)
        for cents, delay, g in ((0.0, 0.0, 1.0), (6.0, 0.008, 0.6)):
            f = freq * 2 ** (cents / 1200)
            N = max(2, int(self.sr / f))
            buf = self.rng.uniform(-1, 1, N)
            buf = buf - buf.mean()
            y = np.empty(n)
            for k in range(n):
                j = k % N
                y[k] = buf[j]
                buf[j] = 0.9985 * 0.5 * (buf[j] + buf[(k + 1) % N])
            y = self.band(y, lo=80, hi=5200) * np.exp(-np.linspace(0, 2.4, n))
            i = int((at + delay) * self.sr)
            m = max(0, min(n, self.n - i))
            out[i:i + m] += y[:m] * g * gain
        return out

    def classical(self, at, freq, length=4.0, gain=0.35) -> np.ndarray:
        """A deep nylon-string (classical) guitar note: a warm, soft
        Karplus–Strong string with the guitar body's low resonance and no
        bright edge — returned for a reverb bus."""
        n = int(length * self.sr)
        N = max(2, int(self.sr / freq))
        # a softer pluck (the flesh of the thumb): smoothed excitation
        buf = np.convolve(self.rng.uniform(-1, 1, N + 6), np.ones(6) / 6, "valid")[:N]
        buf = buf - buf.mean()
        y = np.empty(n)
        for k in range(n):
            j = k % N
            y[k] = buf[j]
            buf[j] = 0.9975 * 0.5 * (buf[j] + buf[(k + 1) % N])
        y = self.band(self.band(y, lo=50, hi=1300), hi=1300)  # bass strings: deep, no edge
        # body: the low resonance of the guitar's box
        t = np.arange(n) / self.sr
        body = self.band(y, lo=90, hi=260) * 0.6
        y = (y + body) * np.exp(-t * 1.1)
        out = np.zeros(self.n)
        i = int(at * self.sr)
        m = max(0, min(n, self.n - i))
        out[i:i + m] += y[:m] * gain
        return out

    def reverb(self, x: np.ndarray, seconds=2.6, wet=0.45) -> np.ndarray:
        """A long, dark plate-like reverb (FFT convolution with a decaying
        noise impulse)."""
        n = int(seconds * self.sr)
        ir = self.rng.normal(0, 1, n) * np.exp(-np.linspace(0, 6.5, n))
        ir = self.band(ir, lo=150, hi=4500)
        ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
        L = len(x) + n
        y = np.fft.irfft(np.fft.rfft(x, L) * np.fft.rfft(ir, L), L)[: len(x)]
        return x * (1 - wet) + y * wet * 1.8

    def tremolo(self, x: np.ndarray, rate=4.2, depth=0.25) -> np.ndarray:
        t = np.arange(len(x)) / self.sr
        return x * (1 - depth + depth * np.sin(2 * math.pi * rate * t))

    def wind(self, gain=0.12):
        t = self.t()
        x = self.band(self.rng.normal(0, 1, len(t)), 200, 900)
        mod = 0.6 + 0.4 * np.sin(2 * math.pi * 0.23 * t) * np.sin(2 * math.pi * 0.11 * t + 1)
        self.add(x * mod * np.interp(t, [0, 0.8, self.dur - 0.8, self.dur], [0, 1, 1, 0]), 0, gain)

    def frame_drum(self, at, gain=0.6):
        """A deep frame-drum hit (daf colour): low thump + skin noise."""
        self.boom(at, f0=95, f1=60, length=1.2, gain=gain)
        n = int(0.25 * self.sr)
        skin = self.band(self.rng.normal(0, 1, n), 300, 1800) * np.exp(-np.linspace(0, 9, n))
        self.add(skin, at, 0.25 * gain)

    def stereo(self) -> np.ndarray:
        x = self.mix / (np.max(np.abs(self.mix)) + 1e-9) * 0.89
        a, b = int(0.05 * self.sr), int(0.4 * self.sr)
        x[:a] *= np.linspace(0, 1, a)
        x[-b:] *= np.linspace(1, 0, b)
        st = np.stack([x, x], 1)
        d = int(0.012 * self.sr)
        st[d:, 1] = 0.85 * st[d:, 1] + 0.15 * x[:-d]
        return st


# ---------------------------------------------------------------------------
# pictures
# ---------------------------------------------------------------------------


class IntroMaker:
    def __init__(self, logo: Path, W: int, H: int, fps: int, sr: int, seed: int = 7):
        self.logo, self.W, self.H, self.fps, self.sr = logo, W, H, fps, sr
        self.k = H / DESIGN_H
        self.rng = np.random.default_rng(seed)
        self.yy, self.xx = np.mgrid[0:H, 0:W].astype(np.float32)
        self.vig = np.clip(1.0 - 0.75 * (((self.xx - W / 2) / (W / 2)) ** 2
                                         + ((self.yy - H / 2) / (H / 2)) ** 2), 0.15, 1)

    # -- helpers ------------------------------------------------------------
    def load_logo(self, height: float):
        im = np.asarray(Image.open(self.logo).convert("RGBA")).astype(np.float32)
        rgb, a = im[..., :3], im[..., 3] / 255.0
        alpha = np.clip((rgb.max(-1) - 6) / 40.0, 0, 1) * a  # drawn on black
        s = height * self.k / im.shape[0]
        size = (max(1, int(im.shape[1] * s)), max(1, int(im.shape[0] * s)))
        return (cv2.resize(rgb, size, interpolation=cv2.INTER_AREA),
                cv2.resize(alpha, size, interpolation=cv2.INTER_AREA))

    def place(self, rgb, alpha, scale=1.0, dx=0.0, dy=0.0):
        h, w = alpha.shape
        W, H = self.W, self.H
        M = np.float32([[scale, 0, W / 2 - scale * w / 2 + dx * self.k],
                        [0, scale, H / 2 - scale * h / 2 + dy * self.k]])
        return (cv2.warpAffine(rgb, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0)),
                cv2.warpAffine(alpha, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=0))

    def ground(self, warm=(16, 13, 12)):
        base = np.empty((self.H, self.W, 3), np.float32)
        base[:] = warm
        base *= self.vig[..., None]
        g = self.rng.normal(0, 3.2, (max(1, self.H // 2), max(1, self.W // 2))).astype(np.float32)
        return base + cv2.resize(g, (self.W, self.H), interpolation=cv2.INTER_LINEAR)[..., None]

    def noise(self, scale, seed, octaves=3):
        rng = np.random.default_rng(seed)
        out = np.zeros((self.H, self.W), np.float32)
        amp, tot = 1.0, 0.0
        for o in range(octaves):
            s = max(2, int(scale * (2 ** o)))
            n = rng.random((s * self.H // self.W + 2, s + 2)).astype(np.float32)
            out += amp * cv2.resize(n, (self.W, self.H), interpolation=cv2.INTER_CUBIC)
            tot += amp
            amp *= 0.5
        out /= tot
        return (out - out.min()) / (out.max() - out.min() + 1e-9)

    def keyhole_mask(self, cx, cy, size, soft=6.0):
        m = np.zeros((self.H, self.W), np.float32)
        cv2.circle(m, (int(cx), int(cy - size * 0.18)), max(1, int(size * 0.32)), 1.0, -1,
                   cv2.LINE_AA)
        pts = np.array([[cx - size * 0.16, cy - size * 0.1], [cx + size * 0.16, cy - size * 0.1],
                        [cx + size * 0.26, cy + size * 0.5], [cx - size * 0.26, cy + size * 0.5]],
                       np.int32)
        cv2.fillPoly(m, [pts], 1.0, cv2.LINE_AA)
        return cv2.GaussianBlur(m, (0, 0), soft * self.k) if soft else m

    def arch_mask(self, cx, cy, w, h, soft=6.0):
        m = np.zeros((self.H, self.W), np.float32)
        pts = []
        for i in range(41):
            a = i / 40
            x = cx - w / 2 + w * a
            y = (cy - h / 2 + (h * 0.42) * (1 - math.sin(math.pi * a)) ** 0.7
                 if 0 < a < 1 else cy - h / 2 + h * 0.42)
            pts.append([x, y])
        pts += [[cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]]
        cv2.fillPoly(m, [np.array(pts, np.int32)], 1.0, cv2.LINE_AA)
        return cv2.GaussianBlur(m, (0, 0), soft * self.k) if soft else m

    def sheen(self, alpha, p, angle=0.35, width=0.08, strength=0.9):
        d = (self.xx / self.W + angle * self.yy / self.H) - (-0.3 + 1.6 * p)
        return (np.exp(-(d / width) ** 2) * alpha * strength)[..., None]

    def glow(self, rgb, a, sigma=18, k=0.6):
        return cv2.GaussianBlur(rgb * a[..., None], (0, 0), sigma * self.k) * k

    @staticmethod
    def comp(base, rgb, a):
        a3 = a[..., None]
        return base * (1 - a3) + rgb * a3

    def particles(self, n, seed):
        rng = np.random.default_rng(seed)
        return {"x": rng.random(n) * self.W, "y": rng.random(n) * self.H,
                "vx": rng.normal(0, 6, n) * self.k, "vy": rng.normal(-8, 5, n) * self.k,
                "r": (rng.random(n) * 1.6 + 0.6) * self.k, "b": rng.random(n) * 0.8 + 0.2,
                "ph": rng.random(n) * 6.28}

    def draw_particles(self, frame, P, t, light=None, color=(255, 230, 190)):
        x = (P["x"] + P["vx"] * t) % self.W
        y = (P["y"] + P["vy"] * t) % self.H
        layer = np.zeros((self.H, self.W), np.float32)
        for xi, yi, r, b, ph in zip(x, y, P["r"], P["b"], P["ph"], strict=True):
            lit = b * (0.6 + 0.4 * math.sin(t * 2 + ph))
            if light is not None:
                lit *= light[int(yi) % self.H, int(xi) % self.W]
            if lit > 0.02:
                cv2.circle(layer, (int(xi), int(yi)), max(1, int(math.ceil(r))), float(lit), -1,
                           cv2.LINE_AA)
        layer = cv2.GaussianBlur(layer, (0, 0), 1.2 * self.k)
        return frame + layer[..., None] * np.array(color, np.float32) * 0.9

    @staticmethod
    def fade(frame, t, dur, fin=0.25, fout=0.5):
        return frame * max(0.0, min(1.0, t / fin) * min(1.0, (dur - t) / fout))

    # -- concepts -----------------------------------------------------------
    def keyhole(self):
        dur = CONCEPTS["keyhole"].seconds
        rgb, a = self.load_logo(CONCEPTS["keyhole"].logo_height)
        P = self.particles(140, 3)
        k = self.k
        KX, KY, KS = -51.0 * k, -51.0 * k, 120.0 * k  # the logo's own keyhole
        W, H = self.W, self.H

        def fr(t):
            base = self.ground()
            q = ease((t - 1.3) / 2.4)
            s = 3.2 - 2.2 * q
            dx, dy = -KX * s * (1 - q), -KY * s * (1 - q)
            hx, hy = W / 2 + dx + KX * s, H / 2 + dy + KY * s
            grow = 0.15 + 0.75 * ease((t - 0.25) / 0.8)
            size = KS * s * grow * (1 + 30 * ease((t - 1.9) / 1.7))
            light = self.keyhole_mask(hx, hy + size * 0.05, size,
                                      soft=4 + 25 * float(ease((t - 1.9) / 1.7)))
            r, al = self.place(rgb, a, s, dx / k, dy / k)
            out = self.comp(base, r, al * np.clip(light, 0, 1))
            out += self.glow(r, al * light, 22, 0.25 + 0.25 * float(ease((t - 3.4) / 0.6)))
            beam = cv2.GaussianBlur(light, (0, 0), 30 * k) * (1 - float(ease((t - 1.9) / 1.2))) * 45
            out += beam[..., None] * np.array([1.0, 0.85, 0.6])
            sp = (t - 3.9) / 1.1
            if 0 <= sp <= 1:
                out += self.sheen(al, sp, 0.4, 0.06, 0.5) * np.array([255, 220, 160], np.float32)
            out = self.draw_particles(out, P, t, light=np.clip(light + 0.12, 0, 1))
            return self.fade(out, t, dur, 0.15, 0.6)

        snd = Sound(dur, self.sr)
        snd.drone((55, 82.4), (0.25, 1.0))
        snd.click(0.3, 0.2, 1500)
        snd.whoosh(1.4, 2.0, 200, 1800, 0.3)
        snd.boom(3.6, gain=0.85)
        snd.whoosh(3.85, 1.0, 3000, 9000, 0.07)
        return dur, fr, snd

    def trail_stamp(self):
        dur = CONCEPTS["trail_stamp"].seconds
        rgb, a = self.load_logo(CONCEPTS["trail_stamp"].logo_height)
        k = self.k
        paper = self.noise(6, 11) * 18 + 8
        # design 1280×720 points, ending where the logo's own red trail starts
        pts = (np.array([[40, 690], [190, 610], [330, 640], [455, 520], [581, 366]], np.float32)
               - [640, 360]) * k + [self.W / 2, self.H / 2]
        seg = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]

        def fr(t):
            base = self.ground((14, 12, 11)) + paper[..., None] * 0.6
            upto = float(ease((t - 0.2) / 1.5)) * seg[-1]
            layer = np.zeros((self.H, self.W), np.float32)
            d = 0.0
            while d <= upto:
                i = min(np.searchsorted(seg, d, side="right") - 1, len(pts) - 2)
                f = (d - seg[i]) / (seg[i + 1] - seg[i])
                x, y = pts[i] + (pts[i + 1] - pts[i]) * f
                cv2.circle(layer, (int(x), int(y)), max(1, int(4 * k)), 1.0, -1, cv2.LINE_AA)
                d += 20 * k
            for i in range(len(pts) - 1):
                if seg[i] <= upto:
                    cv2.circle(layer, (int(pts[i][0]), int(pts[i][1])), max(2, int(8 * k)), 1.0,
                               -1, cv2.LINE_AA)
            vis = 1 - 0.75 * float(ease((t - 2.2) / 0.8))
            out = base + cv2.GaussianBlur(layer, (0, 0), 0.8 * k)[..., None] \
                * np.array([205, 32, 30]) * vis
            sp = (t - 1.75) / 0.3
            if sp > 0:
                scale = 1.18 - 0.18 * float(ease_out(sp))
                shake = math.sin(t * 90) * 5 * max(0, 1 - (t - 2.05) / 0.25) if t > 2.05 else 0
                r, al = self.place(rgb, a, scale, shake, shake * 0.5)
                out = self.comp(out, r, al * min(1.0, sp * 1.6))
                if t > 2.05:
                    out += self.glow(r, al, 16, 0.3 * max(0, 1 - (t - 2.1) / 1.0))
                ss = (t - 3.3) / 1.2
                if 0 <= ss <= 1:
                    out += self.sheen(al, ss, 0.35, 0.06, 0.45) * np.array([235, 235, 225],
                                                                           np.float32)
            return self.fade(out, t, dur, 0.1, 0.6)

        snd = Sound(dur, self.sr)
        snd.drone((58.3, 87.3), (0.25, 0.85))
        for i in range(20):
            snd.click(0.25 + 0.075 * i, 0.11, 2500)
        snd.boom(2.05, f0=140, f1=45, length=1.8, gain=1.0)
        snd.click(2.05, 0.6, 600)
        snd.whoosh(3.25, 1.2, 2500, 8000, 0.06)
        return dur, fr, snd

    def moonrise(self):
        dur = CONCEPTS["moonrise"].seconds
        rgb, a = self.load_logo(CONCEPTS["moonrise"].logo_height)
        k = self.k
        W, H = self.W, self.H
        noise = self.noise(7, 41)
        xx, yy = self.xx, self.yy

        def fr(t):
            base = self.ground((11, 9, 9))
            r, al = self.place(rgb, a, 1.0 + 0.03 * float(ease(t / dur)))
            zone = np.exp(-(((xx - (W / 2 + 175 * k)) ** 2 + (yy - (H / 2 - 225 * k)) ** 2)
                            / (2 * (70 * k) ** 2)))
            m1 = zone * float(ease((t - 0.3) / 1.0))
            m2 = np.clip(((0.2 + 1.1 * float(ease((t - 1.0) / 1.8))) - yy / H) / 0.08
                         + noise * 0.4 - 0.2, 0, 1)
            m3 = np.clip((xx / W + 0.08 * noise - (1.1 - 1.3 * float(ease((t - 2.2) / 1.6))))
                         / 0.07 + 0.5, 0, 1)
            right = np.clip((xx - (W / 2 - 120 * k)) / (160 * k), 0, 1)
            m = np.maximum(m1, np.maximum(m2 * right, m3))
            m = cv2.GaussianBlur(np.clip(m, 0, 1), (0, 0), 14 * k)
            out = self.comp(base, r, al * m)
            out += self.glow(r, al * m1, 30, 0.6)
            return self.fade(out, t, dur, 0.15, 0.6)

        snd = Sound(dur, self.sr)
        # a deep classical (nylon-string) guitar, low register, E minor: the
        # low E when the moon appears, a slow dark arpeggio while the path
        # draws itself, a deep chord under the calligraphy, a low hit
        snd.drone((41.2, 61.7), (0.2, 0.8), 0.24)
        # only the guitar's three bass strings (E2, A2, D3): a slow, dark
        # E-minor line with the thumb, then a deep chord — the sound stays low
        notes = [(0.3, 82.41, 0.46), (0.95, 98.00, 0.38), (1.45, 110.00, 0.38),
                 (1.95, 123.47, 0.36), (2.45, 110.00, 0.34), (2.95, 98.00, 0.34),
                 (3.75, 82.41, 0.48), (3.80, 123.47, 0.36), (3.85, 164.81, 0.3)]
        bus = sum(snd.classical(at, f, 3.8, g) for at, f, g in notes)
        snd.add(snd.reverb(bus, 2.4, 0.3), 0, 1.0)
        snd.boom(3.75, f0=75, f1=32, length=2.4, gain=0.55)
        return dur, fr, snd

    def arch(self):
        dur = CONCEPTS["arch"].seconds
        rgb, a = self.load_logo(CONCEPTS["arch"].logo_height)
        k = self.k
        W, H = self.W, self.H
        pattern = np.zeros((H, W), np.float32)
        step = int(120 * k)
        for cy in range(-step, H + step, max(step, 1)):
            for cx in range(-step, W + step, max(step, 1)):
                for rot in (0, 45):
                    box = cv2.boxPoints(((cx, cy), (70 * k, 70 * k), rot)).astype(np.int32)
                    cv2.polylines(pattern, [box], True, 1.0, 1, cv2.LINE_AA)
        pattern = cv2.GaussianBlur(pattern, (0, 0), 0.7 * k)

        def fr(t):
            base = self.ground((12, 10, 9))
            pv = float(ease(t / 1.2)) * (1 - float(ease((t - 2.2) / 1.2)))
            base += pattern[..., None] * np.array([170, 130, 70]) * 0.35 * pv * self.vig[..., None]
            op = float(ease((t - 0.8) / 2.0))
            light = self.arch_mask(W / 2, H / 2 + 10 * k, (30 + op * 1500) * k,
                                   (50 + op * 2000) * k, soft=10 + op * 25)
            r, al = self.place(rgb, a, 1.25 - 0.25 * float(ease_out((t - 0.8) / 2.4)))
            out = self.comp(base, r, al * np.clip(light, 0, 1))
            out += cv2.GaussianBlur(light, (0, 0), 30 * k)[..., None] * (1 - op) ** 2 \
                * np.array([45, 32, 16])
            sp = (t - 3.4) / 1.1
            if 0 <= sp <= 1:
                out += self.sheen(al, sp, 0.3, 0.06, 0.5) * np.array([255, 210, 140], np.float32)
            return self.fade(out, t, dur, 0.1, 0.6)

        snd = Sound(dur, self.sr)
        d = 146.83  # maqam Hijaz on D: D Eb F# G
        snd.drone((73.4, 110.0), (0.25, 0.9))
        for at, f in [(0.6, d), (1.0, d * 2 ** (1 / 12)), (1.4, d * 2 ** (4 / 12)),
                      (1.85, d * 2 ** (5 / 12)), (2.75, d * 2 ** (4 / 12)), (2.8, d / 2)]:
            snd.pluck(at, f, 2.2, 0.36, 0.995, 0.4)
        snd.boom(2.8, f0=100, f1=40, gain=0.75)
        snd.whoosh(0.8, 2.0, 300, 2500, 0.15)
        return dur, fr, snd

    def flashlight(self):
        dur = CONCEPTS["flashlight"].seconds
        rgb, a = self.load_logo(CONCEPTS["flashlight"].logo_height)
        P = self.particles(90, 5)
        k = self.k
        W, H = self.W, self.H
        path = [(-0.35, 0.25), (0.25, -0.2), (-0.05, 0.05), (0.0, 0.0)]

        def beam(t):
            p = float(np.clip((t - 0.4) / 2.6, 0, 1)) * (len(path) - 1)
            i = min(int(p), len(path) - 2)
            f = float(ease(p - i))
            return (W / 2 + (path[i][0] + (path[i + 1][0] - path[i][0]) * f) * W,
                    H / 2 + (path[i][1] + (path[i + 1][1] - path[i][1]) * f) * H)

        def fr(t):
            base = self.ground() * 0.6
            cx, cy = beam(t)
            rad = (150 + 900 * float(ease((t - 3.0) / 1.2))) * k
            light = np.exp(-(((self.xx - cx) ** 2 + (self.yy - cy) ** 2) / (2 * rad ** 2)))
            light = np.clip(light * 1.4, 0, 1) * (t > 0.4)
            r, al = self.place(rgb, a, 1.0 + 0.04 * float(ease(t / dur)))
            out = self.comp(base, r, al * np.clip(light + 0.03, 0, 1))
            out += light[..., None] * 18
            sp = (t - 4.0) / 1.0
            if 0 <= sp <= 1:
                out += self.sheen(al, sp, -0.3, 0.05, 0.5) * np.array([255, 215, 150], np.float32)
            out = self.draw_particles(out, P, t, light=light)
            return self.fade(out, t, dur, 0.1, 0.6)

        snd = Sound(dur, self.sr)
        snd.drone((49, 73.4), (0.3, 1.0))
        snd.click(0.4, 0.5, 900)
        snd.whoosh(0.6, 2.4, 150, 900, 0.18)
        snd.boom(3.05, gain=0.8)
        return dur, fr, snd

    def sand(self):
        dur = CONCEPTS["sand"].seconds
        rgb, a = self.load_logo(CONCEPTS["sand"].logo_height)
        k = self.k
        noise = self.noise(14, 51, 4)
        grains0 = self.noise(30, 53, 2)
        red = ((rgb[..., 0] > rgb[..., 1] * 1.8) & (rgb[..., 0] > 90)).astype(np.float32)

        def fr(t):
            base = self.ground((14, 11, 9))
            p = float(ease((t - 0.4) / 2.8)) * 1.2
            field = 0.7 * (1 - self.xx / self.W) + 0.3 * noise
            m = np.clip((p - field) / 0.05, 0, 1)
            r, al = self.place(rgb, a, 1.0 + 0.04 * float(ease(t / dur)))
            cover = (1 - m) * cv2.GaussianBlur(al, (0, 0), 6 * k)
            grains = np.roll(grains0, int(-t * 60 * k), axis=1)
            out = self.comp(base, r, al * m)
            out += (cover * grains)[..., None] * np.array([150, 118, 80]) * 0.55 \
                * (1 - float(ease((t - 3.2) / 0.5)))
            rr, ra = self.place(rgb * red[..., None], a * red)
            g = float(ease((t - 3.4) / 0.6)) * (1 - 0.5 * float(ease((t - 4.6) / 1.0)))
            out += self.glow(rr, ra * m, 14, 0.9 * g)
            return self.fade(out, t, dur, 0.1, 0.6)

        snd = Sound(dur, self.sr)
        d = 146.83  # maqam Hijaz on D
        snd.wind(0.16)
        snd.drone((73.4, 110.0), (0.1, 0.7))
        for at, f, g in [(1.0, d * 2 ** (5 / 12), 0.3), (1.6, d * 2 ** (4 / 12), 0.3),
                         (2.3, d * 2 ** (1 / 12), 0.3), (3.4, d, 0.34)]:
            snd.pluck(at, f, 2.2, g, 0.995, 0.4)
        snd.boom(3.4, f0=90, f1=40, gain=0.6)
        return dur, fr, snd

    # -- output -------------------------------------------------------------
    def render(self, concept: str, video: Path, audio: Path) -> None:
        dur, fr, snd = getattr(self, concept)()
        st = snd.stereo()
        tmp_wav = audio.with_name(audio.stem + ".part.wav")
        with wave.open(str(tmp_wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(self.sr)
            w.writeframes((np.clip(st, -1, 1) * 32767).astype(np.int16).tobytes())
        part = video.with_name(video.stem + ".part.mp4")
        cfg = ai_config.render
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{self.W}x{self.H}", "-r", str(self.fps), "-i", "-", "-i", str(tmp_wav),
               "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(min(cfg.crf, 18)),
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest",
               "-movflags", "+faststart", str(part)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for f in range(int(round(dur * self.fps))):
                frame = np.clip(fr(f / self.fps), 0, 255).astype(np.uint8)
                proc.stdin.write(np.ascontiguousarray(frame).tobytes())
            proc.stdin.close()
            err = proc.stderr.read().decode(errors="replace")
            if proc.wait() != 0:
                raise RuntimeError(f"intro encode failed: {err[-300:]}")
        except BaseException:
            if proc.poll() is None:
                proc.kill()
            part.unlink(missing_ok=True)
            tmp_wav.unlink(missing_ok=True)
            raise
        part.replace(video)
        tmp_wav.replace(audio)
