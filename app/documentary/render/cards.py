"""Full-frame cards the renderer draws from the production script — no
editorial logic here: what a card says and where the timeline stands was
decided (and approved) before render.

  chapter  — "Chapter 2" (small, spaced) over a red rule, the chapter's
             title in serif; only the number when its title was left out
  title    — the film's title after the cold open
  board    — the detective board: notes pinned on a cork board under the
             board's name in the film's language

Text cards sit on a near-black ground with a soft vignette and never zoom or
move: text on screen is always read at its drawn size. (The other text pages,
timeline, conflict and recap, are app/documentary/graphics.)
"""

from __future__ import annotations

import math
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.core.ai_config import ai_config
from app.documentary.visuals.typography import (
    RTL,
    _draw_lines,
    draw_text,
    font,
    text_width,
    wrap,
)

INK = (245, 242, 235)
MUTED = (138, 134, 128)
DIM = (62, 60, 58)
RED = (206, 44, 40)
CARD_KINDS = ("chapter", "title", "detective_board", "graphic")


def _ease(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return p * p * (3 - 2 * p)


@lru_cache(maxsize=4)
def ground(W: int, H: int) -> np.ndarray:
    """Near-black with a faint warm centre and darker corners."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    v = np.clip(1.0 - 0.55 * d, 0.0, 1.0)[..., None]
    lo, hi = np.array([5, 5, 6], np.float32), np.array([24, 22, 21], np.float32)
    return (lo + (hi - lo) * v).astype(np.uint8)


def _canvas(W: int, H: int) -> Image.Image:
    return Image.fromarray(ground(W, H).copy(), "RGB")


def chapter_image(label: str, title: str | None, language: str, W: int, H: int) -> np.ndarray:
    img = _canvas(W, H)
    d = ImageDraw.Draw(img)
    s = H / 1080
    small = font(language, "sans", "bold", int(30 * s))
    big = font(language, "serif", "bold", int(78 * s))
    lab = label if language in RTL else " ".join(label.upper())  # spaced capitals
    lines = wrap(title, big, language, int(W * 0.72))[:3] if title else []
    lh = int(big.size * 1.22)
    block = int(small.size * 1.4) + int(34 * s) + lh * len(lines)
    y = (H - block) // 2
    lw = text_width(lab, small, language)
    draw_text(d, ((W - lw) / 2, y), lab, small, language, fill=MUTED)
    y += int(small.size * 1.4) + int(14 * s)
    rule = int(64 * s)
    d.rectangle(((W - rule) // 2, y, (W + rule) // 2, y + max(3, int(4 * s))), fill=RED)
    y += int(20 * s)
    if lines:
        _draw_lines(d, lines, big, language, int(W * 0.14), y, int(W * 0.72), "center", INK, 1.22)
    return np.asarray(img)


def title_image(title: str, language: str, W: int, H: int) -> np.ndarray:
    img = _canvas(W, H)
    d = ImageDraw.Draw(img)
    s = H / 1080
    big = font(language, "serif", "bold", int(96 * s))
    lines = wrap(title, big, language, int(W * 0.78))[:3]
    lh = int(big.size * 1.2)
    y = (H - lh * len(lines)) // 2 - int(20 * s)
    _draw_lines(d, lines, big, language, int(W * 0.11), y, int(W * 0.78), "center", INK, 1.2)
    rule = int(90 * s)
    ry = y + lh * len(lines) + int(26 * s)
    d.rectangle(((W - rule) // 2, ry, (W + rule) // 2, ry + max(3, int(4 * s))), fill=RED)
    return np.asarray(img)


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@lru_cache(maxsize=4)
def cork(W: int, H: int) -> np.ndarray:
    """A cork board in a wooden frame: mottled tan with dark flecks, the
    frame lit from the top left, a soft shadow inside the frame."""
    rng = np.random.default_rng(7)
    base = np.array([176, 130, 82], np.float32)
    coarse = cv2.resize(rng.normal(0, 1, (H // 24 + 2, W // 24 + 2)).astype(np.float32),
                        (W, H), interpolation=cv2.INTER_CUBIC)
    fine = rng.normal(0, 1, (H, W)).astype(np.float32)
    flecks = (rng.random((H, W)) > 0.985).astype(np.float32) * -38
    light = (coarse * 9 + fine * 7 + flecks)[..., None]
    img = np.clip(base + light, 0, 255)
    frame = max(10, int(min(W, H) * 0.035))
    yy, xx = np.mgrid[0:H, 0:W]
    edge = np.minimum.reduce([xx, yy, W - 1 - xx, H - 1 - yy]).astype(np.float32)
    shade = np.clip(edge - frame, 0, frame * 2.5) / (frame * 2.5)
    img = img * (0.62 + 0.38 * shade[..., None])
    wood = np.array([96, 58, 30], np.float32) + (xx * 0 + rng.normal(0, 4, (H, W)))[..., None]
    grain = np.sin(yy / 3.0 + coarse * 2) * 6
    wood = wood + grain[..., None]
    out = np.where((edge < frame)[..., None], wood, img)
    bevel = (edge < frame * 0.25)[..., None]
    out = np.where(bevel, out * 0.7, out)
    return np.clip(out, 0, 255).astype(np.uint8)


class DetectiveBoardCard:
    """A real detective board: a cork board in a wooden frame with paper
    notes pinned at slight angles. Notes already pinned stay still; each
    new note flies in from outside the frame, lands with a small bounce
    and is pinned, then red string is pulled tight from its pin to the
    pins of the notes it is really related to (the links the plan
    carries — never decorative). Notes keep the same place in every
    language; a note's title is RTL-shaped for fa/ar by typography."""

    def __init__(self, shot: dict, language: str, W: int, H: int):
        self.W, self.H = W, H
        info = shot.get("card") or {}
        cfg = ai_config.detective_board
        self.cards = {c["id"]: c for c in info.get("cards") or []}
        layout = {box["id"]: box for box in info.get("layout") or []}
        self.order = [c["id"] for c in info.get("cards") or [] if c["id"] in layout]
        self.new_ids = [i for i in info.get("new_card_ids") or [] if i in layout]
        self.layout = layout
        self.language = language
        self.s = H / 1080
        self.cfg = cfg
        self.thread_rgb = _hex_to_rgb(cfg.thread_color)
        self.pin_rgb = [_hex_to_rgb(c) for c in cfg.pin_colors]
        self.plaque = self._plaque()
        self.sprites = {i: self._note(self.cards[i], layout[i]) for i in self.order}
        self.pins = {i: self._pin_point(layout[i]) for i in self.order}
        self.base = self._compose(since=None)

    # ---- the title plate above the notes (in the film's language)
    def _plaque(self) -> tuple[Image.Image, tuple[int, int]]:
        """A dark wooden plate with the board's name, centred in the free strip above the first row of notes."""
        from app.documentary.graphics import theme as T

        text = T.label(self.language, "detective_board")
        size = int(self.H * 0.052)
        f = T.font(self.language, "title", size)
        text_w = text_width(text, f, self.language)
        w, h = text_w + int(self.W * 0.05), int(self.H * 0.078)
        plate = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(plate)
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=int(h * 0.18), fill=(52, 31, 17, 255),
                            outline=(130, 86, 48, 255), width=max(2, int(self.s * 3)))
        draw_text(d, ((w - text_w) / 2, (h - f.getbbox("Hg")[3]) / 2 - self.s * 2), text, f, self.language,
                  fill=(244, 232, 205, 255))
        return plate, ((self.W - w) // 2, int(self.H * 0.012))

    # ---- notes
    def _note(self, card: dict, box: dict) -> tuple[Image.Image, tuple[int, int]]:
        """The note as an RGBA sprite (paper, tape, title, soft shadow),
        rotated by its tilt; returns (sprite, offset of the sprite's
        top-left from the note's own top-left)."""
        s, language = self.s, self.language
        w, h = box["w"], box["h"]
        pad = int(18 * s)
        paper = _hex_to_rgb(self.cfg.paper_colors.get(str(card.get("kind")), "#efe6d2"))
        kind = _hex_to_rgb(self.cfg.kind_colors.get(str(card.get("kind")), "#8a8680"))
        layer = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rectangle((pad, pad, pad + w, pad + h), fill=paper + (255,))
        tab = max(4, int(h * 0.07))
        d.rectangle((pad, pad, pad + w, pad + tab), fill=kind + (255,))
        d.line((pad, pad + h - 1, pad + w, pad + h - 1), fill=(0, 0, 0, 40), width=2)
        top = tab + int(h * 0.16)  # room for the pin above the title
        margin = int(w * 0.08)
        size = int(h * 0.2)
        while True:
            f = font(language, "serif", "bold", size)
            lines = wrap(card.get("title") or "", f, language, w - 2 * margin)
            lh = int(f.size * 1.18)
            if (len(lines) <= 3 and lh * len(lines) <= h - top - int(h * 0.06)) or size <= 12:
                break
            size -= 2
        ty = pad + top + (h - top - lh * len(lines)) // 2
        _draw_lines(d, lines, f, language, pad + margin, ty, w - 2 * margin, "center",
                    (34, 30, 27, 255), 1.18)
        if sum(map(ord, card["id"])) % 2:
            tape = (225, 214, 170, 190)
            tw, th = int(w * 0.22), int(18 * s)
            d.polygon([(pad - 6 * s, pad + h * 0.2), (pad + tw, pad + h * 0.2 - th),
                       (pad + tw + 4 * s, pad + h * 0.2 - th + 10 * s),
                       (pad - 2 * s, pad + h * 0.2 + 12 * s)], fill=tape)
        if card.get("kind") == "contradiction":
            d.rectangle((pad + w * 0.3, pad - int(5 * s), pad + w * 0.7, pad + int(9 * s)),
                        fill=(225, 214, 170, 200))
        angle = float(box.get("angle") or 0.0)
        rotated = layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        shadow_a = rotated.split()[3].filter(ImageFilter.GaussianBlur(max(3, int(6 * s))))
        shadow = Image.new("RGBA", rotated.size, (0, 0, 0, 0))
        shadow.putalpha(shadow_a.point(lambda v: int(v * 0.55)))
        sprite = Image.new("RGBA", rotated.size, (0, 0, 0, 0))
        so = int(7 * s)
        sprite.alpha_composite(shadow, (so, so))
        sprite.alpha_composite(rotated)
        offset = ((rotated.width - w) // 2, (rotated.height - h) // 2)
        return sprite, offset

    def _paste(self, img: Image.Image, i: str, dx: int, dy: int, scale: float) -> None:
        sprite, (ox, oy) = self.sprites[i]
        box = self.layout[i]
        if scale != 1.0:
            sprite = sprite.resize((max(1, int(sprite.width * scale)),
                                    max(1, int(sprite.height * scale))), Image.Resampling.BICUBIC)
            ox, oy = int(ox * scale), int(oy * scale)
            dx -= int(box["w"] * (scale - 1) / 2)
            dy -= int(box["h"] * (scale - 1) / 2)
        img.paste(sprite, (box["x"] - ox + dx, box["y"] - oy + dy), sprite)

    # ---- pins and string
    def _pin_point(self, box: dict) -> tuple[int, int]:
        """Where the pin sits: a little below the top edge, centred, then
        turned with the note's tilt about its centre."""
        cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
        rx, ry = 0.0, -box["h"] * 0.5 + box["h"] * 0.1
        a = math.radians(-float(box.get("angle") or 0.0))
        return (int(cx + rx * math.cos(a) - ry * math.sin(a)),
                int(cy + rx * math.sin(a) + ry * math.cos(a)))

    def _link_targets(self, i: str) -> list[str]:
        return [link["to"] for link in self.cards[i].get("links") or []
                if link.get("to") in self.layout]

    def _thread(self, arr: np.ndarray, a: tuple[int, int], b: tuple[int, int],
                progress: float) -> None:
        """String from pin `a` to pin `b`, drawn up to `progress` — a
        shallow sag so it hangs, a darker twin below it as its shadow."""
        if progress <= 0:
            return
        ax, ay = a
        bx, by = b
        sag = math.hypot(bx - ax, by - ay) * self.cfg.thread_sag_fraction
        pts = []
        for k in range(41):
            t = k / 40 * min(progress, 1.0)
            x = ax + (bx - ax) * t
            y = ay + (by - ay) * t + 4 * sag * t * (1 - t)
            pts.append((int(x), int(y)))
        poly = np.array(pts, np.int32).reshape(-1, 1, 2)
        width = max(2, int(3 * self.s))
        shadow = poly + np.array([[[int(3 * self.s), int(4 * self.s)]]], np.int32)
        cv2.polylines(arr, [shadow], False, (60, 38, 22), width, cv2.LINE_AA)
        cv2.polylines(arr, [poly], False, self.thread_rgb, width, cv2.LINE_AA)

    def _draw_pin(self, arr: np.ndarray, i: str, press: float) -> None:
        x, y = self.pins[i]
        s = self.s
        color = self.pin_rgb[sum(map(ord, i)) % len(self.pin_rgb)]
        r = int((11 + 5 * (1 - press)) * s)
        cv2.circle(arr, (x + int(4 * s), y + int(5 * s)), r, (70, 45, 28), -1, cv2.LINE_AA)
        cv2.circle(arr, (x, y), r, color, -1, cv2.LINE_AA)
        cv2.circle(arr, (x - int(r * 0.3), y - int(r * 0.3)), max(2, r // 3),
                   tuple(min(255, c + 110) for c in color), -1, cv2.LINE_AA)

    # ---- the board at one moment
    def _compose(self, since: float | None) -> np.ndarray:
        """The board `since` seconds after the appearance starts (None:
        every note settled). Strings run under the paper so no title is
        ever crossed; the length next to each pin stays on top, which
        reads as the string tied to that pin."""
        cfg = self.cfg
        arr = cork(self.W, self.H).copy()
        timing = {i: (n * cfg.stagger_seconds) for n, i in enumerate(self.new_ids)}
        progress: dict[str, float] = {}
        for i in self.order:
            if since is None or i not in timing:
                progress[i] = 1.0
            else:
                progress[i] = _ease((since - timing[i] - cfg.paste_seconds)
                                    / max(cfg.thread_seconds, 0.05))
        strings = [(i, to, progress[i]) for i in self.order for to in self._link_targets(i)
                   if progress[i] > 0 and (since is None or progress.get(to, 1.0) > 0)]
        for i, to, prog in strings:
            self._thread(arr, self.pins[i], self.pins[to], prog)
        img = Image.fromarray(arr, "RGB")
        img.paste(self.plaque[0], self.plaque[1], self.plaque[0])
        landed: list[tuple[str, float]] = []
        for i in self.order:
            if since is None or i not in timing:
                self._paste(img, i, 0, 0, 1.0)
                continue
            local = (since - timing[i]) / max(cfg.paste_seconds, 0.05)
            if local <= 0:
                continue
            fly = _ease(min(local, 1.0))
            box = self.layout[i]
            sx = -box["w"] * 1.5 if box["x"] + box["w"] / 2 < self.W / 2 \
                else self.W + box["w"] * 0.5
            bounce = math.sin(min(local, 1.0) * math.pi) * 0.06
            self._paste(img, i, int((sx - box["x"]) * (1 - fly)),
                        int((-box["h"] * 1.5 - box["y"]) * (1 - fly)),
                        1.0 + 0.35 * (1 - fly) + bounce)
            if local >= 1.0:
                landed.append((i, local - 1.0))
        arr = np.asarray(img).copy()
        stubs = np.zeros(arr.shape[:2], np.uint8)
        for i, to, _ in strings:
            for pin in (self.pins[i], self.pins[to]):
                cv2.circle(stubs, pin, int(self.layout[i]["w"] * 0.16), 255, -1)
        over = arr.copy()
        for i, to, prog in strings:
            self._thread(over, self.pins[i], self.pins[to], prog)
        # the string over the paper only near a pin
        near = stubs.astype(bool) & (over != arr).any(axis=2)
        arr[near] = over[near]
        flying = {i for i in timing if since is not None
                  and (since - timing[i]) / max(cfg.paste_seconds, 0.05) < 1.0}
        for i in self.order:
            if i not in flying:
                after = next((a for j, a in landed if j == i), None)
                self._draw_pin(arr, i, 1.0 if after is None else min(1.0, after * 6))
        return arr

    def frame(self, since: float, p: float) -> np.ndarray:
        if not self.new_ids:
            return self.base.copy()
        return self._compose(since)
