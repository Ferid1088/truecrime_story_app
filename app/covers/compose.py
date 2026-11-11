"""The cover of a film or of a short, drawn in code: the language's theme (colours and signature form), a real scene picture, the avatar's portrait, the channel's
logo and a localized title of a few words. Nothing here is made by an image model, so the title and the logo are exact in every script.

Sizes and fractions come from config/parameters/covers.json. For a right-to-left language (Persian, Arabic) the places of the logo, title and portrait are mirrored;
the pictures themselves are never flipped."""

from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from app.core.ai_config import CoverThemeConfig, ai_config
from app.documentary.visuals import typography as TY
from app.thumbnails.compose import RTL, contrast

TURKISH_UPPER = str.maketrans({"i": "İ", "ı": "I"})


class CoverError(ValueError):
    pass


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def display_title(text: str, language: str) -> str:
    """The title as the cover shows it: capitals in the Latin scripts (Turkish with its dotted İ), as written in Persian and Arabic."""
    if language in RTL:
        return text.strip()
    return text.strip().translate(TURKISH_UPPER).upper() if language == "tr" else text.strip().upper()


def title_issues(text: str) -> list[str]:
    cfg = ai_config.covers_settings().title
    words = len(text.split())
    if words < cfg["min_words"] or words > cfg["max_words"]:
        return [f"the title has {words} words, a cover title has {cfg['min_words']} to {cfg['max_words']}"]
    return []


def _background(theme: CoverThemeConfig, size: tuple[int, int]) -> Image.Image:
    width, height = size
    base = np.zeros((height, width, 3), dtype=np.float32) + np.array(_rgb(theme.background), dtype=np.float32)
    yy, xx = np.mgrid[0:height, 0:width]
    distance = np.sqrt(((xx - width / 2) / (width / 1.5)) ** 2 + ((yy - height / 2) / (height / 1.5)) ** 2)
    base *= np.clip(1.08 - 0.32 * distance, 0.6, 1.08)[..., None]
    return Image.fromarray(base.clip(0, 255).astype("uint8")).convert("RGBA")


def _fit(picture: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(picture.convert("RGB"), (max(1, size[0]), max(1, size[1])), Image.LANCZOS)


def _paste(canvas: Image.Image, picture: Image.Image, origin: tuple[int, int], mask: Image.Image | None = None) -> None:
    canvas.paste(picture.convert("RGBA"), origin, mask)


def _mask(size: tuple[int, int], draw_shape) -> Image.Image:
    scale = 2                                    # drawn twice as large and reduced: smooth edges
    big = Image.new("L", (size[0] * scale, size[1] * scale), 0)
    draw_shape(ImageDraw.Draw(big), scale)
    return big.resize(size, Image.LANCZOS)


def _bezier(points: list[tuple[float, float]], steps: int = 90) -> list[tuple[float, float]]:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = points
    out = []
    for i in range(steps + 1):
        t = i / steps
        a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t ** 2 * (1 - t), t ** 3
        out.append((a * x0 + b * x1 + c * x2 + d * x3, a * y0 + b * y1 + c * y2 + d * y3))
    return out


def _ribbon(draw: ImageDraw.ImageDraw, path: list[tuple[float, float]], widest: float, fill) -> None:
    """A stroke that thins to nothing along its path (the copper path that vanishes at the clue)."""
    left, right = [], []
    for i, (x, y) in enumerate(path):
        nx, ny = path[min(i + 1, len(path) - 1)]
        px, py = path[max(i - 1, 0)]
        length = math.hypot(nx - px, ny - py) or 1.0
        half = widest * (1 - i / len(path)) / 2
        left.append((x - (ny - py) / length * half, y + (nx - px) / length * half))
        right.append((x + (ny - py) / length * half, y - (nx - px) / length * half))
    draw.polygon(left + right[::-1], fill=fill)


def _glow(canvas: Image.Image, draw_shape, color, blur: float) -> None:
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw_shape(ImageDraw.Draw(layer))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))
    canvas.alpha_composite(layer)


def _octagon(cx: float, cy: float, radius: float) -> list[tuple[float, float]]:
    return [(cx + radius * math.cos(math.radians(22.5 + 45 * i)), cy + radius * math.sin(math.radians(22.5 + 45 * i))) for i in range(8)]


def _signature(theme: CoverThemeConfig, canvas: Image.Image, scene: Image.Image, box: tuple[int, int, int, int], pointer: tuple[float, float]) -> None:
    """The scene in the language's signature form, with the one recurring mark of the channel (the pointer line, the copper path, the broken frame ...)."""
    x, y, w, h = box
    accent, rim, ink = _rgb(theme.accent), _rgb(theme.rim), _rgb(theme.ink)
    d = min(w, h)
    cx, cy = x + w / 2, y + h / 2
    stroke = max(3, round(d * 0.012))
    draw = ImageDraw.Draw(canvas)
    kind = theme.signature
    if kind in ("spotlight_circle", "magnifier"):
        size = d if kind == "spotlight_circle" else round(d * 0.8)
        ox, oy = round(cx - size / 2), round(cy - size / 2 - (0 if kind == "spotlight_circle" else d * 0.04))
        if kind == "spotlight_circle":
            _glow(canvas, lambda dr: dr.ellipse((ox - stroke, oy - stroke, ox + size + stroke, oy + size + stroke), outline=rim + (255,), width=stroke), rim, d * 0.02)
        _paste(canvas, _fit(scene, (size, size)), (ox, oy), _mask((size, size), lambda dr, s: dr.ellipse((0, 0, size * s, size * s), fill=255)))
        if kind == "spotlight_circle":
            draw.line((pointer[0], pointer[1], cx + d * 0.04, cy + d * 0.3), fill=accent, width=max(2, stroke // 2))
            draw.ellipse((cx + d * 0.04 - stroke * 1.6, cy + d * 0.3 - stroke * 1.6, cx + d * 0.04 + stroke * 1.6, cy + d * 0.3 + stroke * 1.6), fill=accent)
        else:
            ring = stroke * 3
            draw.ellipse((ox - ring // 2, oy - ring // 2, ox + size + ring // 2, oy + size + ring // 2), outline=rim, width=ring)
            hx, hy = cx + size * 0.5 * 0.7071, cy - d * 0.04 + size * 0.5 * 0.7071
            draw.line((hx, hy, hx + d * 0.17, hy + d * 0.17), fill=rim, width=ring * 2)
    elif kind == "octagon_frame":
        radius = d / 2 * 0.98
        points = _octagon(cx, cy, radius)
        mask = _mask(canvas.size, lambda dr, s: dr.polygon([(px * s, py * s) for px, py in points], fill=255))
        full = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        full.paste(_fit(scene, (round(radius * 2), round(radius * 2))), (round(cx - radius), round(cy - radius)))
        canvas.paste(full, (0, 0), mask)
        for i in range(8):
            if i != 5:                                                   # one segment is broken where the light is
                draw.line([points[i], points[(i + 1) % 8]], fill=accent, width=stroke * 2)
    elif kind == "file_tab":
        pad = round(d * 0.03)
        draw.rectangle((x - pad, y - pad, x + w + pad, y + h + pad), fill=_rgb("#f2ead8"), outline=ink, width=stroke)
        draw.rectangle((x - pad, y - pad - round(d * 0.07), x - pad + round(w * 0.22), y - pad), fill=accent, outline=ink, width=stroke)
        _paste(canvas, _fit(scene, (w, h)), (x, y))
        elbow = (pointer[0], cy + d * 0.25)
        draw.line([pointer, elbow, (cx, elbow[1])], fill=accent, width=stroke * 2)
        draw.ellipse((cx - stroke * 2, elbow[1] - stroke * 2, cx + stroke * 2, elbow[1] + stroke * 2), fill=accent)
    elif kind == "doorway_arch":
        radius = w // 2
        mask = _mask((w, h), lambda dr, s: (dr.pieslice((0, 0, w * s, w * s), 180, 360, fill=255), dr.rectangle((0, radius * s, w * s, h * s), fill=255)))
        _paste(canvas, _fit(scene, (w, h)), (x, y), mask)
        edge = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(stroke * 4 + 1)))
        canvas.paste(Image.new("RGBA", (w, h), rim + (255,)), (x, y), edge)
    elif kind == "clipped_photo_compass":
        border = round(d * 0.02)
        draw.rectangle((x - border, y - border, x + w + border, y + h + border), fill=(250, 248, 242))
        draw.rectangle((x - border * 3, y - border * 3, x + w + border * 3, y + h + border * 3), outline=ink, width=max(1, stroke // 3))
        _paste(canvas, _fit(scene, (w, h)), (x, y))
        arc = round(d * 0.16)
        draw.arc((x + w - arc, y - arc // 2, x + w + arc, y + arc * 3 // 2), 90, 270, fill=accent, width=stroke)
    elif kind == "offset_planes":
        _paste(canvas, _fit(scene, (w, h)), (x, y))
        draw.rectangle((x - round(w * 0.04), y + round(h * 0.06), x + round(w * 0.43), y + h), fill=rim)
        draw.rectangle((x + round(w * 0.57), y - round(h * 0.04), x + w + round(w * 0.04), y + round(h * 0.94)), fill=accent)
    elif kind == "woven_break":
        weave = Image.new("RGBA", (w + 2 * stroke * 4, h + 2 * stroke * 4), (0, 0, 0, 0))
        wd = ImageDraw.Draw(weave)
        step = max(8, round(d * 0.045))
        for i in range(-weave.height, weave.width + weave.height, step):
            wd.line((i, 0, i + weave.height, weave.height), fill=rim + (150,), width=max(2, step // 3))
            wd.line((i + weave.height, 0, i, weave.height), fill=_rgb(theme.background) + (150,), width=max(2, step // 3))
        canvas.alpha_composite(weave, (x - stroke * 4, y - stroke * 4))
        _paste(canvas, _fit(scene, (round(w * 0.7), round(h * 0.82))), (round(x + w * 0.15), round(y + h * 0.09)))
        path = _bezier([(x, y + h * 0.9), (x + w * 0.3, y + h * 0.5), (x + w * 0.6, y + h * 1.0), (x + w, y + h * 0.1)])
        draw.line(path, fill=accent, width=stroke * 2)
    else:                                                                # copper_path
        radius = round(d * 0.04)
        mask = _mask((w, h), lambda dr, s: dr.rounded_rectangle((0, 0, w * s, h * s), radius=radius * s, fill=255))
        _paste(canvas, _fit(scene, (w, h)), (x, y), mask)
        ribbon = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        _ribbon(ImageDraw.Draw(ribbon), _bezier([(x + w * 1.02, y - h * 0.05), (x - w * 0.12, y + h * 0.3), (x + w * 1.1, y + h * 0.65), (x + w * 0.5, y + h * 0.9)]),
                d * 0.07, accent + (255,))
        canvas.alpha_composite(ribbon)


def _title_lines(text: str, language: str, width: int, size: int, max_lines: int):
    """The font size (shrunk until the title fits) and the lines of the title."""
    while size >= 12:
        font = TY.font(language, "sans", "bold", size)
        lines = TY.wrap(text, font, language, width)
        if len(lines) <= max_lines and all(TY.text_width(line, font, language) <= width for line in lines):
            return font, lines, size
        size = round(size * 0.92)
    raise CoverError(f"the title {text!r} does not fit its place")


def _place(layout: dict, rtl: bool) -> tuple[float, float]:
    """The left edge (a fraction) of a box: mirrored for a right-to-left language."""
    return 1 - layout["x"] - layout["width"] if rtl else layout["x"]


def compose(kind: str, language: str, scene: Path, portrait: Image.Image, logo: Path, title: str, out: Path, *, scale: float = 1.0) -> dict:
    """The cover (kind "long" or "short") written to `out` as a JPEG within the platform's size limit; returns what is on it (boxes in pixels) and its checks.
    `scale` makes it smaller (tests); the real size is the one in the configuration."""
    cfg = ai_config.covers_settings()
    fmt, layout, theme = cfg.formats[kind], cfg.layout[kind], cfg.themes.get(language)
    if theme is None:
        raise CoverError(f"no cover theme for {language}")
    if problems := title_issues(title):
        raise CoverError(problems[0])
    width, height = round(fmt.width * scale), round(fmt.height * scale)
    rtl = language in RTL
    canvas = _background(theme, (width, height))
    draw = ImageDraw.Draw(canvas)
    elements: dict[str, list[int]] = {}

    title_cfg = layout["title"]
    box_w = round(title_cfg["width"] * width)
    font, lines, size = _title_lines(display_title(title, language), language, box_w, round(title_cfg["size"] * height), title_cfg["max_lines"])
    line_h = round(size * 1.18)
    title_x = round(_place(title_cfg, rtl) * width)
    title_y = round(title_cfg["y"] * height)
    centred = kind == "short"
    ink, accent = _rgb(theme.ink), _rgb(theme.accent)
    for i, line in enumerate(lines):
        color = accent if i == len(lines) - 1 and len(lines) > 1 and kind == "long" else ink
        tx = title_x + (box_w / 2 if centred else box_w if rtl else 0)
        TY.draw_text(draw, (tx, title_y + i * line_h), line, font, language, fill=color, anchor="ma" if centred else "ra" if rtl else "la")
    elements["title"] = [title_x, title_y, box_w, line_h * len(lines)]

    scene_cfg = layout["scene"]
    box = (round(_place(scene_cfg, rtl) * width), round(scene_cfg["y"] * height), round(scene_cfg["width"] * width), round(scene_cfg["height"] * height))
    pointer = (title_x + box_w / 2, title_y + line_h * len(lines)) if centred else (title_x + box_w if not rtl else title_x, title_y + line_h * len(lines) - line_h / 2)
    with Image.open(scene) as picture:
        _signature(theme, canvas, picture.copy(), box, pointer)
    elements["scene"] = list(box)

    av = layout["avatar"]
    av_box = (round(_place(av, rtl) * width), round(av["y"] * height), round(av["width"] * width), round(av["height"] * height))
    face = ImageOps.fit(portrait.convert("RGB"), (av_box[2], av_box[3]), Image.LANCZOS, centering=(0.5, 0.3))
    frame = max(3, round(min(av_box[2], av_box[3]) * 0.02))
    mask = _mask((av_box[2], av_box[3]), lambda dr, s: dr.rounded_rectangle((0, 0, av_box[2] * s, av_box[3] * s), radius=round(min(av_box[2], av_box[3]) * 0.08) * s, fill=255))
    canvas.paste(Image.new("RGBA", (av_box[2] + 2 * frame, av_box[3] + 2 * frame), accent + (255,)), (av_box[0] - frame, av_box[1] - frame),
                 _mask((av_box[2] + 2 * frame, av_box[3] + 2 * frame), lambda dr, s: dr.rounded_rectangle(
                     (0, 0, (av_box[2] + 2 * frame) * s, (av_box[3] + 2 * frame) * s), radius=round(min(av_box[2], av_box[3]) * 0.1) * s, fill=255)))
    _paste(canvas, face, (av_box[0], av_box[1]), mask)
    elements["avatar"] = list(av_box)

    logo_h = round(layout["logo_height"] * height)
    with Image.open(logo) as mark:
        mark = ImageOps.contain(mark.convert("RGB"), (logo_h, logo_h), Image.LANCZOS)
    margin = round(layout["margin"] * width)
    logo_x = (width - mark.width) // 2 if centred else width - margin - mark.width if rtl else margin
    logo_y = round(layout["logo_top"] * height) if "logo_top" in layout else margin
    _paste(canvas, mark, (logo_x, logo_y), _mask(mark.size, lambda dr, s: dr.rounded_rectangle((0, 0, mark.width * s, mark.height * s), radius=round(mark.width * 0.12) * s, fill=255)))
    elements["logo"] = [logo_x, logo_y, mark.width, mark.height]

    out.parent.mkdir(parents=True, exist_ok=True)
    rgb = canvas.convert("RGB")
    for quality in (fmt.quality, 86, 80, 72, 64):
        buffer = io.BytesIO()
        rgb.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
        if buffer.tell() <= fmt.max_bytes:
            break
    out.write_bytes(buffer.getvalue())
    result = {"kind": kind, "language": language, "size": [width, height], "scale": scale, "signature": theme.signature, "elements": elements, "bytes": buffer.tell(),
              "title_font_px": size, "title_lines": lines, "title_contrast": round(contrast(_rgb(theme.background), ink), 2)}
    result["problems"] = layout_problems(result)
    return result


def layout_problems(made: dict) -> list[str]:
    """What breaks the covers' rules: a title or logo in the platforms' reserved margins, a title too faint for its background, a portrait that reaches the bottom margin."""
    cfg = ai_config.covers_settings()
    width, height = made["size"]
    problems: list[str] = []
    if made["title_contrast"] < cfg.min_title_contrast:
        problems.append(f"the title's contrast is {made['title_contrast']}, at least {cfg.min_title_contrast} is needed")
    if made["kind"] == "short":
        zone = cfg.safe_zones["short"]
        for name in ("title", "logo", "avatar"):
            x, y, w, h = made["elements"][name]
            if y < zone["top_fraction"] * height or y + h > (1 - zone["bottom_fraction"]) * height:
                problems.append(f"the {name} reaches the top or bottom margin of a short")
            if x < zone["side_fraction"] * width * 0.5 or x + w > width - zone["side_fraction"] * width * 0.5:
                problems.append(f"the {name} reaches the side margin of a short")
    else:
        corner = cfg.safe_zones["long"]["corner_free_fraction"]
        for name, (x, y, w, h) in made["elements"].items():
            if x + w > width * (1 - corner[0]) and y + h > height * (1 - corner[1]) and name != "scene":
                problems.append(f"the {name} sits where the video's length badge covers a long cover")
    return problems
