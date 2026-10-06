"""Data-first render engine (Parts 38–39). It contains NO editorial
logic: it executes a production script — shots with motion and
transitions, text overlays, subtitles — over the finished documentary
audio, and encodes an MP4 with FFmpeg.

Frames are produced with OpenCV (sub-pixel affine camera moves on a
pre-graded canvas), overlays are composited from Pillow layers, raw
frames are piped into libx264. Stills that do not fit 16:9 sit on a
blurred, darkened copy of themselves instead of being cropped hard.
"""

from __future__ import annotations

import math
import subprocess
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from app.core.ai_config import ai_config
from app.documentary import storage
from app.documentary.visuals.typography import overlay as text_layer

MARGIN = 1.16          # canvas headroom for camera moves
BLACK = (6, 6, 7)


class RenderError(RuntimeError):
    pass


def _ease(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return p * p * (3 - 2 * p)


@lru_cache(maxsize=24)
def _canvas(path: str, W: int, H: int, kind: str) -> np.ndarray:
    """Graded RGB canvas of size (W*MARGIN, H*MARGIN) for one asset."""
    src = storage.resolve(path)
    if src is None or not Path(src).exists():
        raise RenderError(f"missing visual file {path}")
    img = Image.open(src).convert("RGB")
    cw, ch = int(W * MARGIN), int(H * MARGIN)
    ar, frame_ar = img.width / img.height, cw / ch
    if kind in ("image",) and abs(ar - frame_ar) / frame_ar > 0.22:
        # portrait/odd aspect: contained over a blurred version of itself
        bg = img.copy()
        s = max(cw / bg.width, ch / bg.height)
        bg = bg.resize((int(bg.width * s) + 1, int(bg.height * s) + 1), Image.LANCZOS)
        bg = bg.crop(((bg.width - cw) // 2, (bg.height - ch) // 2,
                      (bg.width - cw) // 2 + cw, (bg.height - ch) // 2 + ch))
        bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(28))).enhance(0.45)
        s = min(cw / img.width, ch / img.height) * 0.96
        fg = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
        bg.paste(fg, ((cw - fg.width) // 2, (ch - fg.height) // 2))
        img = bg
    else:
        s = max(cw / img.width, ch / img.height)
        img = img.resize((max(cw, int(img.width * s) + 1), max(ch, int(img.height * s) + 1)),
                         Image.LANCZOS)
        img = img.crop(((img.width - cw) // 2, (img.height - ch) // 2,
                        (img.width - cw) // 2 + cw, (img.height - ch) // 2 + ch))
    if kind == "image":
        # one consistent documentary grade: a little less colour, a
        # little more contrast, soft vignette
        img = ImageEnhance.Color(img).enhance(0.82)
        img = ImageEnhance.Contrast(img).enhance(1.06)
    arr = np.asarray(img).astype(np.float32)
    if kind == "image":
        yy, xx = np.mgrid[0:ch, 0:cw]
        d = np.sqrt(((xx - cw / 2) / (cw / 2)) ** 2 + ((yy - ch / 2) / (ch / 2)) ** 2)
        arr *= (1.0 - 0.28 * np.clip(d - 0.55, 0, 1) ** 1.4)[..., None]
    return np.clip(arr, 0, 255).astype(np.uint8)


def _src_point(shot: dict, key: str, cw: int, ch: int) -> tuple[float, float] | None:
    """Map a point in asset pixels (highlight/marker/face) to canvas px."""
    val = shot.get(key)
    w, h = shot.get("width"), shot.get("height")
    if not val or not w or not h:
        return None
    if len(val) == 4:
        x, y = (val[0] + val[2]) / 2, (val[1] + val[3]) / 2
    else:
        x, y = val[0], val[1]
    s = max(cw / w, ch / h)
    return (x * s - (w * s - cw) / 2, y * s - (h * s - ch) / 2)


def _view(shot: dict, p: float, cw: int, ch: int, W: int, H: int) -> tuple[float, float, float]:
    """(zoom, center_x, center_y) of the camera at progress p (0..1).
    zoom 1 = the whole canvas; the window is (cw/zoom, ch/zoom)."""
    m = ai_config.motion
    motion = shot.get("motion") or "NONE"
    length = max(shot["end"] - shot["start"], 0.1)
    amp = min(1.0, length / 10.0) * float(shot.get("speed") or 1.0)
    e = _ease(p)
    cx, cy = cw / 2, ch / 2
    base = MARGIN * 0.97  # start slightly inside the canvas
    if motion == "SLOW_PUSH":
        return base * (1 + (m.push_scale - 1) * amp * e), cx, cy
    if motion == "SLOW_PULL":
        return base * (1 + (m.push_scale - 1) * amp * (1 - e)), cx, cy
    if motion in ("PAN_LEFT", "PAN_RIGHT"):
        z = base * 1.03
        dx = cw * m.pan_fraction * amp * (e - 0.5)
        return z, cx + (dx if motion == "PAN_RIGHT" else -dx), cy
    if motion in ("CROP_FOCUS", "SUBTLE_2_5D"):
        focus = _src_point(shot, "focus", cw, ch) or (cx, cy * 0.9)
        z = base * (1 + 0.14 * amp * e) if motion == "CROP_FOCUS" else base * (1 + 0.05 * amp * e)
        return z, cx + (focus[0] - cx) * 0.6 * e, cy + (focus[1] - cy) * 0.6 * e
    if motion == "DOCUMENT_HIGHLIGHT":
        focus = _src_point(shot, "highlight", cw, ch) or (cx, cy)
        z = base * (1 + 0.38 * e)
        return z, cx + (focus[0] - cx) * e, cy + (focus[1] - cy) * e
    if motion == "DOCUMENT_SCROLL":
        return base * 1.15, cx, cy - ch * 0.12 * (e - 0.5)
    return base, cx, cy


def _warp(canvas: np.ndarray, zoom: float, vx: float, vy: float, W: int, H: int) -> np.ndarray:
    ch, cw = canvas.shape[:2]
    ww, wh = cw / zoom, ch / zoom
    vx = min(max(vx, ww / 2), cw - ww / 2)
    vy = min(max(vy, wh / 2), ch - wh / 2)
    kx, ky = W / ww, H / wh
    M = np.float32([[kx, 0, -kx * (vx - ww / 2)], [0, ky, -ky * (vy - wh / 2)]])
    return cv2.warpAffine(canvas, M, (W, H), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REFLECT)


class FrameMaker:
    def __init__(self, script: dict, W: int, H: int):
        self.script = script
        self.W, self.H = W, H
        self.black = np.full((H, W, 3), BLACK, np.uint8)
        self._parallax: dict = {}

    def shot_frame(self, shot: dict, t: float) -> np.ndarray:
        kind = shot.get("kind")
        if kind == "black" or not shot.get("path"):
            return self.black
        W, H = self.W, self.H
        length = max(shot["end"] - shot["start"], 0.1)
        p = (t - shot["start"]) / length
        if kind == "map":
            paths = shot.get("map_paths") or [shot["path"]]
            seg = min(int(max(p, 0) * len(paths)), len(paths) - 1)
            sp = max(p, 0) * len(paths) - seg
            frame = self._map_frame(paths[seg], sp, shot)
            blend = 0.25  # crossfade into the next zoom level
            if seg + 1 < len(paths) and sp > 1 - blend:
                a = (sp - (1 - blend)) / blend
                nxt = self._map_frame(paths[seg + 1], 0.0, shot)
                frame = cv2.addWeighted(frame, 1 - a, nxt, a, 0)
            return frame
        canvas = _canvas(shot["path"], W, H, "document" if kind == "document" else "image")
        ch, cw = canvas.shape[:2]
        z, vx, vy = _view(shot, p, cw, ch, W, H)
        frame = _warp(canvas, z, vx, vy, W, H)
        if shot.get("motion") == "SUBTLE_2_5D":
            frame = self._parallax_frame(shot, canvas, p, z, vx, vy, frame)
        if shot.get("motion") == "DOCUMENT_HIGHLIGHT":
            frame = self._dim_outside(frame, shot, p, z, vx, vy, cw, ch)
        return frame

    def _map_frame(self, path: str, p: float, shot: dict) -> np.ndarray:
        canvas = _canvas(path, self.W, self.H, "map")
        ch, cw = canvas.shape[:2]
        z = MARGIN * 0.97 * (1 + 0.12 * _ease(p))
        return _warp(canvas, z, cw / 2, ch / 2, self.W, self.H)

    def _parallax_frame(self, shot, canvas, p, z, vx, vy, frame):
        """Foreground (soft central ellipse) drifts slightly more than the
        background: a subtle sense of depth, no warping."""
        key = shot["path"]
        if key not in self._parallax:
            mask = np.zeros((self.H, self.W), np.float32)
            cv2.ellipse(mask, (self.W // 2, int(self.H * 0.55)),
                        (int(self.W * 0.28), int(self.H * 0.36)), 0, 0, 360, 1.0, -1)
            self._parallax[key] = cv2.GaussianBlur(mask, (0, 0), self.W * 0.05)[..., None]
        shift = ai_config.motion.parallax_shift_fraction * self.W * (_ease(p) - 0.5)
        fg = _warp(canvas, z * 1.012, vx - shift / max(z, 1e-3), vy, self.W, self.H)
        m = self._parallax[key]
        return (fg * m + frame * (1 - m)).astype(np.uint8)

    def _dim_outside(self, frame, shot, p, z, vx, vy, cw, ch):
        hl = shot.get("highlight")
        w, h = shot.get("width"), shot.get("height")
        if not hl or not w or not h:
            return frame
        s = max(cw / w, ch / h)
        ox, oy = (w * s - cw) / 2, (h * s - ch) / 2
        ww, wh = cw / z, ch / z
        vx = min(max(vx, ww / 2), cw - ww / 2)
        vy = min(max(vy, wh / 2), ch - wh / 2)

        def to_frame(x, y):
            cx_, cy_ = x * s - ox, y * s - oy
            return ((cx_ - (vx - ww / 2)) * self.W / ww, (cy_ - (vy - wh / 2)) * self.H / wh)

        x0, y0 = to_frame(hl[0], hl[1])
        x1, y1 = to_frame(hl[2], hl[3])
        mask = np.full((self.H, self.W), 1.0, np.float32)
        cv2.rectangle(mask, (int(x0) - 20, int(y0) - 20), (int(x1) + 20, int(y1) + 20), 0.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), 25)
        dim = 0.45 * _ease(min(1.0, p * 2))
        return (frame * (1 - dim * mask[..., None])).astype(np.uint8)


class Overlays:
    """Pre-rendered RGBA text layers composited only where they have ink."""

    def __init__(self, script: dict, W: int, H: int, burn_subtitles: bool):
        self.items = []
        lang = script["language"]
        for ov in script.get("overlays") or []:
            layer = text_layer(ov["kind"], ov["text"], lang, W, H)
            self.items.append((ov["start"], ov["end"], *self._prep(layer)))
        if burn_subtitles:
            for sub in script.get("subtitles") or []:
                layer = text_layer("subtitle", sub["text"], lang, W, H)
                self.items.append((sub["start"], sub["end"], *self._prep(layer)))
        self.items.sort(key=lambda x: x[0])

    @staticmethod
    def _prep(layer: Image.Image):
        arr = np.asarray(layer).astype(np.float32)
        alpha = arr[..., 3] / 255.0
        ys, xs = np.nonzero(alpha > 0.01)
        if not len(xs):
            return (0, 0, 0, 0), None, None
        box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
        x0, y0, x1, y1 = box
        return box, arr[y0:y1, x0:x1, :3], alpha[y0:y1, x0:x1, None]

    def apply(self, frame: np.ndarray, t: float) -> np.ndarray:
        out = None
        for start, end, box, rgb, a in self.items:
            if start > t:
                break
            if t >= end or rgb is None:
                continue
            fade = min(1.0, (t - start) / 0.45, (end - t) / 0.45)
            if out is None:
                out = frame.copy()
            x0, y0, x1, y1 = box
            region = out[y0:y1, x0:x1].astype(np.float32)
            aa = a * fade
            out[y0:y1, x0:x1] = (region * (1 - aa) + rgb * aa).astype(np.uint8)
        return frame if out is None else out


def _transition_seconds(shot: dict) -> float:
    lo, hi = ai_config.motion.crossfade_seconds
    if shot.get("transition_in") == "FADE_BLACK":
        return hi * 1.4
    if shot.get("transition_in") == "CROSSFADE":
        length = shot["end"] - shot["start"]
        return max(lo, min(hi, length * 0.12))
    return 0.0


def write_srt(subs: list[dict], path: Path) -> Path:
    def ts(x):
        h, rem = divmod(max(x, 0.0), 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}"

    lines = []
    for i, s in enumerate(subs, 1):
        lines += [str(i), f"{ts(s['start'])} --> {ts(s['end'])}", s["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class VideoRenderer:
    def __init__(self, width: int | None = None, height: int | None = None):
        cfg = ai_config.render
        self.W = width or cfg.width
        self.H = height or cfg.height
        self.fps = cfg.fps

    def render(self, script: dict, out_path: Path, max_seconds: float | None = None,
               progress=None) -> dict:
        W, H, fps = self.W, self.H, self.fps
        duration = script["duration"]
        if max_seconds:
            duration = min(duration, max_seconds)
        shots = [s for s in script["shots"] if s["start"] < duration]
        if not shots:
            raise RenderError("production script has no shots")
        audio = storage.resolve((script.get("audio") or {}).get("path"))
        if audio is None or not audio.exists():
            raise RenderError("documentary audio is missing; render the voice first")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        srt = write_srt([s for s in script.get("subtitles") or [] if s["start"] < duration],
                        out_path.with_suffix(".srt"))
        lang = script.get("language", "und")
        iso3 = {"en": "eng", "de": "ger", "fa": "per", "ar": "ara"}.get(lang, "und")
        cfg = ai_config.render
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
            "-i", str(audio), "-i", str(srt),
            "-map", "0:v", "-map", "1:a", "-map", "2:s",
            "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-c:s", "mov_text", f"-metadata:s:s:0", f"language={iso3}",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out_path),
        ]
        maker = FrameMaker(script, W, H)
        overlays = Overlays(script, W, H, cfg.burn_subtitles)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        total = int(math.ceil(duration * fps))
        idx = 0
        try:
            for f in range(total):
                t = f / fps
                while idx + 1 < len(shots) and shots[idx + 1]["start"] <= t:
                    idx += 1
                shot = shots[idx]
                frame = maker.shot_frame(shot, t)
                td = _transition_seconds(shot)
                since = t - shot["start"]
                if td and since < td and idx > 0:
                    a = _ease(since / td)
                    if shot.get("transition_in") == "FADE_BLACK":
                        prev = maker.shot_frame(shots[idx - 1], t) if a < 0.5 else maker.black
                        k = abs(1 - 2 * a)
                        base = prev if a < 0.5 else frame
                        frame = (base.astype(np.float32) * k).astype(np.uint8)
                    else:
                        prev = maker.shot_frame(shots[idx - 1], t)
                        frame = cv2.addWeighted(prev, 1 - a, frame, a, 0)
                elif idx == 0 and since < 1.2:
                    frame = (frame.astype(np.float32) * _ease(since / 1.2)).astype(np.uint8)
                if duration - t < 1.5:  # fade out at the very end
                    frame = (frame.astype(np.float32) * _ease((duration - t) / 1.5)).astype(np.uint8)
                frame = overlays.apply(frame, t)
                proc.stdin.write(np.ascontiguousarray(frame).tobytes())
                if progress and f % (fps * 5) == 0:
                    progress(f / total, f"frame {f}/{total}")
            proc.stdin.close()
            err = proc.stderr.read().decode(errors="replace")
            if proc.wait() != 0:
                raise RenderError(f"ffmpeg failed: {err[-400:]}")
        except BrokenPipeError as e:
            err = proc.stderr.read().decode(errors="replace")
            raise RenderError(f"ffmpeg stopped: {err[-400:]}") from e
        finally:
            _canvas.cache_clear()
        return {"path": storage.rel(out_path), "srt": storage.rel(srt),
                "duration": round(duration, 3), "width": W, "height": H, "fps": fps,
                "frames": total, "shots": len(shots)}
