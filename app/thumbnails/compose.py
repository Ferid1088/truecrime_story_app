"""ThumbnailComposer — template-guided, deterministic (no image model).

Canvas 1280x720. The host stands large on one side; one real case picture
sits in a framed panel on the other; a small localized status badge sits
at the panel's outer top corner; optional 0-4 word text sits on a scrim at the panel's foot. The
background is the channel's own studio, darkened and softened, so the
channel is recognisable before any text is read. One font family per
script. Everything that is text is drawn as text — exact Persian/Arabic
shaping, exact badge wording."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from app.core.ai_config import ai_config
from app.documentary import storage
from app.documentary.visuals import typography as TY

RTL = ("fa", "ar")


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _lum(rgb) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _text_color_on(bg) -> tuple[int, int, int]:
    dark, light = (16, 17, 20), (250, 247, 240)
    return dark if contrast(bg, dark) >= contrast(bg, light) else light


def _background(language: str, W: int, H: int) -> tuple[Image.Image, str]:
    base = Image.new("RGB", (W, H), _hex(ai_config.thumbnail.background))
    kind = "plain"
    ch = ai_config.channels.get(language)
    studio = storage.ROOT / (ch.studio_dir or f"data/studio/{language}") / "01_overview_wide.png" \
        if ch else None
    if studio is not None and studio.exists():
        room = ImageOps.fit(Image.open(studio).convert("RGB"), (W, H), Image.LANCZOS)
        room = room.filter(ImageFilter.GaussianBlur(14))
        base = Image.blend(base, _dim(room, 0.45), 0.85)
        kind = "studio"
    arr = np.asarray(base, dtype=np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt(((xx - W / 2) / (W / 1.6)) ** 2 + ((yy - H / 2) / (H / 1.4)) ** 2)
    arr *= np.clip(1.15 - 0.55 * d, 0.45, 1.0)[..., None]       # vignette
    return Image.fromarray(arr.clip(0, 255).astype("uint8")), kind


def _dim(img: Image.Image, f: float) -> Image.Image:
    return img.point(lambda v: int(v * f))


def _visible(img: Image.Image) -> Image.Image:
    box = img.split()[-1].getbbox()
    return img.crop(box) if box else img


def _shadow(layer: Image.Image, blur: int = 18, opacity: float = 0.55) -> Image.Image:
    a = layer.split()[-1].point(lambda v: int(v * opacity))
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sh.putalpha(a.filter(ImageFilter.GaussianBlur(blur)))
    return sh


def compose_thumbnail(brief: dict, host_path: Path, primary_path: Path, out_path: Path,
                      secondary_path: Path | None = None) -> dict:
    cfg = ai_config.thumbnail
    W, H = cfg.width, cfg.height
    lang = brief["language"]
    side = brief["host"]["side"]
    m = int(cfg.margin * W)
    accent = _hex(cfg.accent.get(lang, cfg.accent["en"]))
    bg, bg_kind = _background(lang, W, H)
    canvas = bg.convert("RGBA")
    layout: dict = {"canvas": [W, H], "elements": [], "fonts": [], "background": bg_kind}

    # --- case picture panel (opposite side of the host) -------------------
    pw = int(cfg.image_width * W)
    ph = int(H * 0.66)
    px = W - m - pw if side == "left" else m
    py = int(H * 0.19)
    kind = brief["case_visuals"][0].get("kind", "other")
    focus = (0.5, 0.3) if kind in ("victim", "person", "suspect") else (0.5, 0.5)
    pic = ImageOps.fit(Image.open(primary_path).convert("RGB"), (pw, ph), Image.LANCZOS,
                       centering=focus)
    frame = 4
    canvas.paste(Image.new("RGBA", (pw + 2 * frame, ph + 2 * frame), accent + (255,)),
                 (px - frame, py - frame))
    canvas.paste(pic, (px, py))
    layout["panel"] = [px, py, pw, ph]
    layout["elements"].append("case_picture")
    if secondary_path is not None:
        sw, sh_ = int(pw * 0.34), int(ph * 0.34)
        sp = ImageOps.fit(Image.open(secondary_path).convert("RGB"), (sw, sh_), Image.LANCZOS)
        sx = px + pw - sw - 14 if side == "left" else px + 14
        sy = py + ph - sh_ - 14
        canvas.paste(Image.new("RGBA", (sw + 6, sh_ + 6), (250, 247, 240, 255)), (sx - 3, sy - 3))
        canvas.paste(sp, (sx, sy))
        layout["secondary"] = [sx, sy, sw, sh_]
        layout["elements"].append("secondary_picture")

    # --- host --------------------------------------------------------------
    host = _visible(Image.open(host_path).convert("RGBA"))
    hh = int(H * (cfg.host_height_xl if brief["host"]["size"] == "xl" else cfg.host_height))
    hw = int(host.width * hh / host.height)
    host = host.resize((hw, hh), Image.LANCZOS)
    # centred in the half the picture panel leaves free
    hx = ((px - m) // 2 - hw // 2) if side == "left" else ((px + pw + m + W) // 2 - hw // 2)
    hx = max(0, min(hx, W - hw))
    hy = H - hh
    canvas.alpha_composite(_shadow(host), (hx + (10 if side == "left" else -10), hy + 6))
    canvas.alpha_composite(host, (hx, hy))
    layout["host"] = [hx, hy, hw, hh]
    layout["elements"].append("host")

    d = ImageDraw.Draw(canvas)
    rtl = lang in RTL

    # --- status badge (outer top corner of the panel) -----------------------
    label = brief["resolution_label"]
    bh = max(int(cfg.badge_height * H), cfg.badge_min_height_px)
    fnt = TY.font(lang, "sans", "bold", int(bh * 0.52))
    tw = TY.text_width(label, fnt, lang)
    bw = tw + int(bh * 0.9)
    bx = px if not rtl else px + pw - bw
    by = max(py - bh - 14, m // 2)
    d.rounded_rectangle((bx, by, bx + bw, by + bh), radius=bh // 4, fill=accent)
    ink = _text_color_on(accent)
    d.text((bx + bw / 2, by + bh / 2), label, font=fnt, fill=ink, anchor="mm", **TY._kw(lang))
    layout["badge"] = [bx, by, bw, bh]
    layout["badge_ink_contrast"] = round(contrast(accent, ink), 2)
    layout["elements"].append("status_badge")
    layout["fonts"].append(TY.FONTS[(lang if lang in RTL else "latin", "sans", "bold")])

    # --- optional text on a scrim at the panel's foot ----------------------
    text = brief.get("thumbnail_text") or ""
    if text:
        tf = TY.font(lang, "sans", "bold", int(H * 0.07))
        lines = TY.wrap(text, tf, lang, pw - 40)[:2]
        lh = int(H * 0.085)
        sh_h = lh * len(lines) + 28
        scrim = Image.new("RGBA", (pw, sh_h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(scrim)
        for i in range(sh_h):
            sd.line((0, i, pw, i), fill=(10, 10, 12, int(215 * i / sh_h)))
        canvas.alpha_composite(scrim, (px, py + ph - sh_h))
        d = ImageDraw.Draw(canvas)
        for i, line in enumerate(lines):
            y = py + ph - sh_h + 14 + i * lh + lh // 2
            x = px + pw - 20 if rtl else px + 20
            d.text((x, y), line, font=tf, fill=(250, 247, 240), anchor="rm" if rtl else "lm",
                   **TY._kw(lang))
        layout["text"] = [px, py + ph - sh_h, pw, sh_h]
        layout["elements"].append("text")

    rgb = canvas.convert("RGB")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for q in (92, 86, 80, 72):
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= cfg.max_file_bytes:
            break
    out_path.write_bytes(buf.getvalue())
    layout["bytes"] = buf.tell()
    layout["side"] = side
    return layout
