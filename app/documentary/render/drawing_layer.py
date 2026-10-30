"""The pencil drawing in the film: a window in the corner at the start of the reading direction (top-left for English, top-right for
Persian and Arabic), flush with the frame's edge, 60 % visible over the pictures; over a gap it grows to the whole screen at 100 %
(the sides filled with a blurred copy, the portrait never stretched), and shrinks again after it. Over a card it is hidden and its
progress waits. The portraits are the progress videos made once for each photo (`drawing/portrait.py`); this layer only picks the
frame the planned progress asks for and puts the tiny pencil where the file says the pencil is."""

from __future__ import annotations

import bisect
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.core.ai_config import ai_config
from app.documentary import storage

ROOT = storage.ROOT
FORWARD_READ_LIMIT = 60                     # frames to read on instead of seeking


def _ease(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def _pencil(scale: float) -> tuple[np.ndarray, tuple[int, int]]:
    """The pencil: black and white, tiny, as RGBA, and where its point is in the image."""
    cfg = ai_config.drawing_settings().pencil
    s = 16
    length, thick = max(cfg.length * scale, 3.0), max(cfg.thickness * scale, 1.0)
    L, T, m = int(length * s), int(thick * s), 3 * s
    img = Image.new("RGBA", (L + 2 * m, T + 2 * m), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y0, cone = m, int(0.22 * L)
    d.polygon([(m, y0 + T / 2), (m + cone, y0), (m + cone, y0 + T)], fill=(238, 238, 238, 255))
    d.polygon([(m, y0 + T / 2), (m + cone * 0.3, y0 + T / 2 - T * 0.18), (m + cone * 0.3, y0 + T / 2 + T * 0.18)], fill=(15, 15, 15, 255))
    d.rectangle([m + cone, y0, m + L - int(0.2 * L), y0 + T], fill=(18, 18, 18, 255))
    d.rectangle([m + cone, y0 + T * 0.44, m + L - int(0.2 * L), y0 + T * 0.56], fill=(235, 235, 235, 255))
    d.rectangle([m + L - int(0.2 * L), y0, m + L - int(0.1 * L), y0 + T], fill=(190, 190, 190, 255))
    d.rounded_rectangle([m + L - int(0.1 * L), y0, m + L, y0 + T], int(T * 0.3), fill=(245, 245, 245, 255))
    img = img.resize((max(img.width // s, 1), max(img.height // s, 1)), Image.LANCZOS)
    tip = (m // s, (y0 + T // 2) // s)
    pad = max(img.size) + 4
    big = Image.new("RGBA", (pad * 2, pad * 2), (0, 0, 0, 0))
    big.paste(img, (pad - tip[0], pad - tip[1]))
    rot = big.rotate(cfg.angle_degrees, resample=Image.BICUBIC, center=(pad, pad))
    box = rot.getbbox()
    return np.array(rot.crop(box)), (pad - box[0], pad - box[1])


class DrawingLayer:
    """Composites the drawing window onto a film frame."""

    def __init__(self, script: dict, width: int, height: int):
        data = script.get("drawing") or {}
        self.cfg = ai_config.drawing_settings()
        self.W, self.H = width, height
        self.segments = sorted(data.get("segments") or [], key=lambda s: s["start"])
        self.starts = [s["start"] for s in self.segments]
        self.rtl = bool(data.get("rtl"))
        self.portraits = data.get("portraits") or {}
        self.win_w = round(width * self.cfg.window.width / self.cfg.window.reference_width)
        self.win_h = round(height * self.cfg.window.height / self.cfg.window.reference_height)
        self.sprite, self.tip = _pencil(width / self.cfg.window.reference_width)
        self._caps: dict[str, cv2.VideoCapture] = {}
        self._last: dict[str, tuple[int, np.ndarray]] = {}
        self._pen: dict[str, np.ndarray] = {}

    @property
    def window_fraction(self) -> float:
        """The share of the frame's width the window takes (the "illustration" label moves beside it)."""
        return self.win_w / self.W

    def segment_at(self, t: float) -> dict | None:
        i = bisect.bisect_right(self.starts, t) - 1
        return self.segments[i] if i >= 0 and t < self.segments[i]["end"] else None

    def _frame(self, code: str, index: int) -> np.ndarray:
        meta = self.portraits[code]
        index = max(0, min(index, int(meta["frames"]) - 1))
        last = self._last.get(code)
        if last is not None and last[0] == index:
            return last[1]
        cap = self._caps.get(code)
        if cap is None:
            cap = self._caps[code] = cv2.VideoCapture(str(ROOT / meta["video"]))
            self._pen[code] = np.load(ROOT / meta["pen"])
        at = last[0] if last is not None else -10 ** 9
        if not 0 < index - at <= FORWARD_READ_LIMIT:
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            at = index - 1
        frame = None
        for _ in range(index - at):
            ok, frame = cap.read()
            if not ok:
                frame = last[1] if last is not None else np.full((int(meta["height"]), int(meta["width"]), 3), 230, np.uint8)
                break
        self._last[code] = (index, frame)
        return frame

    def _grow(self, seg: dict, t: float) -> float:
        """How far the window has grown to the whole screen: only in a gap, over `grow_seconds` at each end."""
        if seg["mode"] != "full":
            return 0.0
        g = self.cfg.grow_seconds
        return min(_ease((t - seg["start"]) / g), _ease((seg["end"] - t) / g))

    def _visibility(self, seg: dict, t: float) -> float:
        f = self.cfg.fade_seconds
        v = 1.0
        if seg.get("fade_in"):
            v = min(v, _ease((t - seg["start"]) / f))
        if seg.get("fade_out"):
            v = min(v, _ease((seg["end"] - t) / f))
        return v

    def apply(self, frame: np.ndarray, t: float) -> np.ndarray:
        seg = self.segment_at(t)
        if seg is None:
            return frame
        code, meta = seg["portrait"], self.portraits[seg["portrait"]]
        index = int((seg["from"] + (t - seg["start"]) * seg["speed"]) * float(meta["fps"]))
        paper = self._frame(code, index)
        k = self._grow(seg, t)
        small_x = self.W - self.win_w if self.rtl else 0
        x = round(small_x * (1 - k))
        y = 0
        w = round(self.win_w + (self.W - self.win_w) * k)
        h = round(self.win_h + (self.H - self.win_h) * k)
        alpha = (self.cfg.window_opacity + (self.cfg.full_opacity - self.cfg.window_opacity) * k) * self._visibility(seg, t)
        ph = h
        pw = round(ph * paper.shape[1] / paper.shape[0])
        if pw > w:                                            # the window is narrower than the portrait: the portrait fits it
            pw, ph = w, round(w * paper.shape[0] / paper.shape[1])
        tile = np.empty((h, w, 3), np.uint8)
        if (pw, ph) == (w, h):
            tile[:] = cv2.resize(paper, (w, h), interpolation=cv2.INTER_AREA)
        else:
            cover = cv2.resize(paper, (w, round(w * paper.shape[0] / paper.shape[1])), interpolation=cv2.INTER_AREA)
            top = max((cover.shape[0] - h) // 2, 0)
            back = cv2.GaussianBlur(cover[top:top + h], (0, 0), 25)
            tile[:] = cv2.resize(back, (w, h)) if back.shape[:2] != (h, w) else back
            ox = (w - pw) // 2
            tile[:, ox:ox + pw] = cv2.resize(paper, (pw, ph), interpolation=cv2.INTER_AREA)[:h]
        out = frame.copy()
        region = out[y:y + h, x:x + w].astype(np.float32)
        out[y:y + h, x:x + w] = (region * (1 - alpha) + tile.astype(np.float32) * alpha).astype(np.uint8)
        pen = self._pen[code][max(0, min(index, len(self._pen[code]) - 1))]
        if pen[1] >= 0:
            scale = ph / paper.shape[0]
            self._paste_pencil(out, x + (w - pw) // 2 + pen[0] * scale, y + pen[1] * scale, alpha, (x, y, w, h))
        return out

    def _paste_pencil(self, frame: np.ndarray, px: float, py: float, alpha: float, box: tuple[int, int, int, int]) -> None:
        """The pencil's point at (px, py), only inside the window."""
        sh, sw = self.sprite.shape[:2]
        x0, y0 = int(px) - self.tip[0], int(py) - self.tip[1]
        bx, by, bw, bh = box
        cx0, cy0, cx1, cy1 = max(x0, bx), max(y0, by), min(x0 + sw, bx + bw), min(y0 + sh, by + bh)
        if cx0 >= cx1 or cy0 >= cy1:
            return
        sub = self.sprite[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
        a = (sub[..., 3:4] / 255.0) * alpha
        frame[cy0:cy1, cx0:cx1] = (frame[cy0:cy1, cx0:cx1] * (1 - a) + sub[..., :3][..., ::-1] * a).astype(np.uint8)

    def close(self) -> None:
        for cap in self._caps.values():
            cap.release()
        self._caps.clear()
        self._last.clear()
