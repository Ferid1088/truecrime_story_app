"""The cover of a film or of a short, drawn in code over the scene picture the image model made: a calm scrim in the theme's colour where the words go, the channel's
logo, the localized title of a few words and the avatar's portrait. The title and the logo are exact in every script because no image model draws them.

Sizes and fractions come from config/parameters/covers.json. For a right-to-left language (Persian, Arabic) the places of the logo, title and portrait are mirrored;
the scene picture itself is never flipped (the model is told to keep the right side empty for these languages)."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from app.core.ai_config import ai_config
from app.documentary.visuals import typography as TY
from app.thumbnails.compose import RTL

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


def _mask(size: tuple[int, int], draw_shape) -> Image.Image:
    scale = 2                                    # drawn twice as large and reduced: smooth edges
    big = Image.new("L", (size[0] * scale, size[1] * scale), 0)
    draw_shape(ImageDraw.Draw(big), scale)
    return big.resize(size, Image.LANCZOS)


def _scrim(canvas: Image.Image, color: tuple[int, int, int], kind: str, rtl: bool, peak: float) -> None:
    """A soft veil in the theme's colour over the side (long) or the top (short) where the words stand: full strength (`peak`) up to the configured plateau, then fading out."""
    cfg = ai_config.covers_settings().scrim
    width, height = canvas.size
    extent, plateau = cfg["extent"][kind], cfg["plateau"]
    if kind == "long":
        ramp = np.clip((1 - np.linspace(0, 1, width) / extent) / (1 - plateau), 0, 1)
        alpha = (ramp[::-1] if rtl else ramp)[None, :].repeat(height, 0)
    else:
        ramp = np.clip((1 - np.linspace(0, 1, height) / extent) / (1 - plateau), 0, 1)
        alpha = ramp[:, None].repeat(width, 1)
    layer = np.zeros((height, width, 4), dtype=np.uint8)
    layer[..., :3] = color
    layer[..., 3] = (alpha * peak * 255).astype(np.uint8)
    canvas.alpha_composite(Image.fromarray(layer, "RGBA"))


def _luminance(rgb: np.ndarray) -> np.ndarray:
    linear = np.where(rgb <= 0.03928, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return linear @ np.array([0.2126, 0.7152, 0.0722])


def _worst_contrast(canvas: Image.Image, box: tuple[int, int, int, int], ink: tuple[int, int, int]) -> float:
    """The contrast of the ink against the picture under `box`: the figure the worst few percent of its pixels still reach (a dark corner of the picture counts)."""
    x, y, w, h = box
    pixels = np.asarray(canvas.crop((x, y, x + w, y + h)).convert("RGB"), dtype=np.float64) / 255.0
    ink_l = float(_luminance(np.array(ink, dtype=np.float64) / 255.0))
    back = _luminance(pixels).ravel()
    ratio = (np.maximum(back, ink_l) + 0.05) / (np.minimum(back, ink_l) + 0.05)
    return float(np.percentile(ratio, ai_config.covers_settings().scrim["percentile"]))


def _veil(canvas: Image.Image, theme_color: tuple[int, int, int], kind: str, rtl: bool, box: tuple[int, int, int, int], ink: tuple[int, int, int]) -> tuple[Image.Image, float, float]:
    """The picture with the veil, made stronger step by step until the title (ink) reads on it; returns it, the veil's strength and the title's contrast."""
    cfg = ai_config.covers_settings()
    peak = cfg.scrim["start_peak"]
    while True:
        veiled = canvas.copy()
        _scrim(veiled, theme_color, kind, rtl, peak)
        contrast_now = _worst_contrast(veiled, box, ink)
        if contrast_now >= cfg.min_title_contrast or peak >= cfg.scrim["max_peak"] - 1e-9:
            return veiled, peak, contrast_now
        peak = min(peak + cfg.scrim["step"], cfg.scrim["max_peak"])


def _title_lines(text: str, language: str, width: int, size: int, max_lines: int):
    """The font size (shrunk until the title fits) and the lines of the title."""
    while size >= 12:
        font = TY.font(language, "sans", "bold", size)
        lines = TY.wrap(text, font, language, width)
        if len(lines) <= max_lines and all(TY.text_width(line, font, language) <= width for line in lines):
            return font, lines, size
        size = round(size * 0.92)
    raise CoverError(f"the title {text!r} does not fit its place")


def _place(layout: dict, rtl: bool) -> float:
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
    with Image.open(scene) as picture:
        canvas = ImageOps.fit(picture.convert("RGB"), (width, height), Image.LANCZOS).convert("RGBA")
    title_cfg = layout["title"]
    box_w = round(title_cfg["width"] * width)
    font, lines, size = _title_lines(display_title(title, language), language, box_w, round(title_cfg["size"] * height), title_cfg["max_lines"])
    line_h = round(size * 1.18)
    title_x = round(_place(title_cfg, rtl) * width)
    title_y = round(title_cfg["y"] * height)
    title_box = (title_x, title_y, box_w, line_h * len(lines))
    ink, accent = _rgb(theme.ink), _rgb(theme.accent)
    canvas, veil, title_contrast = _veil(canvas, _rgb(theme.background), kind, rtl, title_box, ink)
    accent_ok = _worst_contrast(canvas, title_box, accent) >= cfg.min_accent_contrast
    draw = ImageDraw.Draw(canvas)
    elements: dict[str, list[int]] = {}
    centred = kind == "short"
    for i, line in enumerate(lines):
        color = accent if i == len(lines) - 1 and len(lines) > 1 and kind == "long" and accent_ok else ink
        tx = title_x + (box_w / 2 if centred else box_w if rtl else 0)
        TY.draw_text(draw, (tx, title_y + i * line_h), line, font, language, fill=color, anchor="ma" if centred else "ra" if rtl else "la")
    elements["title"] = list(title_box)

    av = layout["avatar"]
    av_box = (round(_place(av, rtl) * width), round(av["y"] * height), round(av["width"] * width), round(av["height"] * height))
    face = ImageOps.fit(portrait.convert("RGB"), (av_box[2], av_box[3]), Image.LANCZOS, centering=(0.5, 0.3))
    frame = max(3, round(min(av_box[2], av_box[3]) * 0.02))
    mask = _mask((av_box[2], av_box[3]), lambda dr, s: dr.rounded_rectangle((0, 0, av_box[2] * s, av_box[3] * s), radius=round(min(av_box[2], av_box[3]) * 0.08) * s, fill=255))
    canvas.paste(Image.new("RGBA", (av_box[2] + 2 * frame, av_box[3] + 2 * frame), accent + (255,)), (av_box[0] - frame, av_box[1] - frame),
                 _mask((av_box[2] + 2 * frame, av_box[3] + 2 * frame), lambda dr, s: dr.rounded_rectangle(
                     (0, 0, (av_box[2] + 2 * frame) * s, (av_box[3] + 2 * frame) * s), radius=round(min(av_box[2], av_box[3]) * 0.1) * s, fill=255)))
    canvas.paste(face.convert("RGBA"), (av_box[0], av_box[1]), mask)
    elements["avatar"] = list(av_box)

    logo_h = round(layout["logo_height"] * height)
    with Image.open(logo) as mark:
        mark = ImageOps.contain(mark.convert("RGB"), (logo_h, logo_h), Image.LANCZOS)
    margin = round(layout["margin"] * width)
    side = round(layout.get("logo_x", layout["margin"]) * width)
    logo_x = (width - mark.width) // 2 if centred else width - side - mark.width if rtl else side
    logo_y = round(layout["logo_top"] * height) if "logo_top" in layout else margin
    canvas.paste(mark.convert("RGBA"), (logo_x, logo_y), _mask(mark.size, lambda dr, s: dr.rounded_rectangle((0, 0, mark.width * s, mark.height * s), radius=round(mark.width * 0.12) * s, fill=255)))
    elements["logo"] = [logo_x, logo_y, mark.width, mark.height]

    out.parent.mkdir(parents=True, exist_ok=True)
    rgb = canvas.convert("RGB")
    for quality in (fmt.quality, 86, 80, 72, 64):
        buffer = io.BytesIO()
        rgb.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
        if buffer.tell() <= fmt.max_bytes:
            break
    out.write_bytes(buffer.getvalue())
    result = {"kind": kind, "language": language, "size": [width, height], "scale": scale, "elements": elements, "bytes": buffer.tell(), "title_font_px": size, "title_lines": lines,
              "title_contrast": round(title_contrast, 2), "veil_strength": round(veil, 2)}
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
            bottom = zone["avatar_bottom_fraction"] if name == "avatar" else zone["bottom_fraction"]       # the portrait sits in the bottom corner (owner decision)
            if y < zone["top_fraction"] * height or y + h > (1 - bottom) * height:
                problems.append(f"the {name} reaches the top or bottom margin of a short")
            if x < zone["side_fraction"] * width * 0.5 or x + w > width - zone["side_fraction"] * width * 0.5:
                problems.append(f"the {name} reaches the side margin of a short")
    else:
        corner = cfg.safe_zones["long"]["corner_free_fraction"]
        for name, (x, y, w, h) in made["elements"].items():
            if x + w > width * (1 - corner[0]) and y + h > height * (1 - corner[1]):
                problems.append(f"the {name} sits where the video's length badge covers a long cover")
    return problems
