"""The three coordinated covers of a Short: 9:16 (Reel), 4:5 (Facebook and Instagram feed) and 1:1 (profile grid), each composed for its own shape and not cropped from another.

Everything on a cover is drawn in code from real parts: the host's own posed picture in his studio (the outfit picture of the outfit his clip was made in), the channel's logo, the hook in the
language's own script with its key word on a red brush stroke, and a banner naming the person of the real case (with that person's photo, only when the owner has cleared it).
No image model draws a face, a word or a logo, so every script is exact and nobody's likeness is invented. Sizes and places come from config/parameters/covers.json (`triptych`)."""

from __future__ import annotations

import itertools
import json
import random
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from app.core.ai_config import ai_config
from app.covers import avatar as AV
from app.covers.compose import CoverError, display_title
from app.documentary.visuals import typography as TY
from app.thumbnails.compose import RTL

SHORT_SHAPES = ("reel", "feed", "grid")
FILM_SHAPES = ("film",)
FORMATS = (*SHORT_SHAPES, *FILM_SHAPES)


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def settings() -> dict:
    return ai_config.covers_settings().triptych


def key_phrase(title: str, language: str) -> str:
    """The one or two words of a hook that carry its meaning, found by rule: words that are not stopwords, a negation or the verb next to it counts most, later words slightly more, longer words slightly more."""
    cfg = settings()["highlight"]
    words = display_title(title, language).split()
    stop, negations = set(cfg["stopwords"].get(language, [])), set(cfg["negations"].get(language, []))

    def bare(word: str) -> str:
        return word.strip(".,;:!?\"'«»“”„()").lower()

    def worth(i: int) -> float:
        if bare(words[i]) in stop:
            return -1.0
        return (cfg["negation_bonus"] if bare(words[i]) in negations else 1.0) + cfg["length_weight"] * len(bare(words[i])) / 4 + cfg["late_weight"] * i / max(1, len(words) - 1)

    best: tuple[float, int, int] | None = None
    for size in range(1, cfg["max_words"] + 1):
        for start in range(len(words) - size + 1):
            scores = [worth(i) for i in range(start, start + size)]
            if min(scores) < 0:
                continue
            total = sum(scores) / (size ** 0.5)                              # a pair must earn more than its better word alone
            if best is None or total > best[0]:
                best = (total, start, size)
    if best is None:
        return words[-1]
    return " ".join(words[best[1]:best[1] + best[2]])


def _keyed_logo(path: Path, height: int) -> Image.Image:
    """The channel's logo with its black ground made transparent (the file is a picture on black)."""
    with Image.open(path) as source:
        rgb = source.convert("RGB")
    luminance = np.asarray(rgb.convert("L"), dtype=np.float32)
    rgb.putalpha(Image.fromarray((np.clip((luminance - 14) / 70, 0, 1) * 255).astype(np.uint8)))
    logo = rgb.crop(rgb.getbbox())
    return logo.resize((round(logo.width * height / logo.height), height), Image.LANCZOS)


def _brush(size: tuple[int, int], colour: tuple[int, int, int], seed: str, tilt: bool = True) -> Image.Image:
    """A broad red brush stroke: a thick band with ragged ends and an uneven rim, bristle streaks along it, turned a little like a hand-painted mark. The result is larger than `size` by the turn."""
    cfg = settings()["brush"]
    rng = random.Random(seed)
    w, h = size
    wobble = cfg["rim_wobble"]
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    pad, steps = h * 0.9, 36
    top = [(pad + (w - 2 * pad) * i / steps, h * 0.06 + rng.uniform(-h * wobble, h * wobble)) for i in range(steps + 1)]
    bottom = [(pad + (w - 2 * pad) * i / steps, h * 0.94 + rng.uniform(-h * wobble, h * wobble)) for i in range(steps + 1)]
    draw.polygon(top + bottom[::-1], fill=255)
    for _ in range(60):                                                     # ragged, rounded ends: no flat edge where the brush was lifted
        cx = rng.choice((rng.uniform(pad * 0.1, pad * 1.4), w - rng.uniform(pad * 0.1, pad * 1.4)))
        cy, r = rng.uniform(h * 0.1, h * 0.9), rng.uniform(h * 0.03, h * 0.16)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    for side in (0, 1):
        for _ in range(8):
            r = rng.uniform(h * 0.08, h * 0.2)
            cx = rng.uniform(pad * 0.15, pad * 0.9) if side == 0 else w - rng.uniform(pad * 0.15, pad * 0.9)
            cy = rng.uniform(h * 0.2, h * 0.8)
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    for _ in range(cfg["streaks"]):                                         # dry-brush gaps at the ends, as a loaded brush leaves them
        y = rng.uniform(h * 0.08, h * 0.92)
        length = rng.uniform(w * 0.05, w * 0.22)
        x = rng.choice((rng.uniform(0, w * 0.1), w - length - rng.uniform(0, w * 0.1)))
        draw.line((x, y, x + length, y + rng.uniform(-2, 2)), fill=0, width=max(1, round(h * rng.uniform(0.012, 0.03))))
    colour_layer = np.empty((h, w, 3), dtype=np.float32)
    shade = np.clip(1 + (np.random.default_rng(rng.randrange(2 ** 32)).normal(0, 0.05, (h, 1)) * 2), 0.85, 1.15)       # faint bristle lines along the stroke
    for c in range(3):
        colour_layer[:, :, c] = np.clip(colour[c] * shade, 0, 255)
    stroke = Image.fromarray(colour_layer.astype(np.uint8), "RGB").convert("RGBA")
    stroke.putalpha(mask.filter(ImageFilter.GaussianBlur(h * 0.012)))
    return stroke.rotate(cfg["tilt_degrees"], expand=True, resample=Image.BICUBIC) if tilt else stroke


def _text(canvas: Image.Image, xy: tuple[float, float], text: str, font, language: str, fill, anchor: str, blur: int, outline: int = 0, between=None) -> None:
    """Text with a soft dark shadow behind it and, when `outline` is given, a dark rim around the letters, so it reads on any picture. `between` is called after the shadow and before the letters
    (the brush stroke goes there, so the shadow does not dim it)."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    TY.draw_text(ImageDraw.Draw(layer), (xy[0] + blur * 0.3, xy[1] + blur * 0.45), text, font, language, fill=(0, 0, 0, 220), anchor=anchor, **({"stroke_width": outline, "stroke_fill": (0, 0, 0, 220)} if outline else {}))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))
    if between is not None:
        between()
    rim = {"stroke_width": outline, "stroke_fill": _rgb(settings()["title_outline"]["colour"]) + (255,)} if outline else {}
    TY.draw_text(ImageDraw.Draw(canvas), xy, text, font, language, fill=fill, anchor=anchor, **rim)


def _fit(words: list[str], language: str, box_w: int, max_lines: int, size: int, min_size: int, max_h: int):
    """The largest bold size at which the words fit `box_w` in at most `max_lines` lines and `max_h` pixels, broken where the lines come out most even."""
    while size >= min_size:
        font = TY.font(language, "sans", "bold", size)
        best = None
        for lines in range(1, max_lines + 1):
            for cuts in itertools.combinations(range(1, len(words)), lines - 1):
                edges = (0, *cuts, len(words))
                parts = [" ".join(words[a:b]) for a, b in zip(edges, edges[1:], strict=False)]
                widest = max(TY.text_width(p, font, language) for p in parts)
                if widest <= box_w and len(parts) * round(size * 1.28) <= max_h and (best is None or (len(parts), widest) < (len(best[0]), best[1])):
                    best = (parts, widest)
            if best:
                return font, best[0], size
        size -= max(2, size // 40)
    raise CoverError("the title does not fit its place even at the smallest size")


def _key_run(parts: list[str], key: list[str]) -> tuple[int, int]:
    """(start, length) of the longest run of the key phrase's words that stands together in this line, (0, 0) when none does."""
    best = (0, 0)
    for a in range(len(key)):
        for b in range(a + 1, len(key) + 1):
            for i in range(len(parts) - (b - a) + 1):
                if parts[i:i + b - a] == key[a:b] and b - a > best[1]:
                    best = (i, b - a)
    return best


def host_mask(plate: Image.Image, face: tuple[float, float, float, float]) -> Image.Image:
    """Where the host is in the studio frame (white), found by OpenCV's GrabCut from a box around his head and body; used only to keep the host out of the side blur."""
    import cv2

    cfg = settings()["host_mask"]
    k = cfg["work_width"] / plate.width
    small = cv2.cvtColor(np.asarray(plate.resize((cfg["work_width"], round(plate.height * k)), Image.LANCZOS)), cv2.COLOR_RGB2BGR)
    fx, fy, fw, fh = (v * k for v in face)
    cx = fx + fw / 2
    x0, x1 = max(1, round(cx - fw * cfg["half_width"])), min(small.shape[1] - 1, round(cx + fw * cfg["half_width"]))
    y0 = max(1, round(fy - fh * 0.4))
    mask = np.full(small.shape[:2], cv2.GC_BGD, np.uint8)
    mask[y0:, x0:x1] = cv2.GC_PR_FGD                                        # probably the host: a box round the head and the body below it
    c0, c1, chin = round(cx - fw * cfg["core_half_width"]), round(cx + fw * cfg["core_half_width"]), round(fy + fh * 1.15)
    mask[chin:, c0:c1] = cv2.GC_FGD                                          # surely the host: the column of the chest, which teaches GrabCut the colours of his clothes
    mask[round(fy + fh * 0.2):round(fy + fh * 0.9), round(cx - fw * 0.35):round(cx + fw * 0.35)] = cv2.GC_FGD
    cv2.grabCut(small, mask, None, np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64), cfg["iterations"], cv2.GC_INIT_WITH_MASK)
    host = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    host = cv2.dilate(cv2.morphologyEx(host, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)), np.ones((cfg["grow"], cfg["grow"]), np.uint8))
    return Image.fromarray(cv2.GaussianBlur(host, (0, 0), cfg["feather"])).resize(plate.size, Image.LANCZOS)


def _studio(plate: Image.Image, face: tuple[float, float, float, float], size: tuple[int, int], target: dict, veil: dict, host: Image.Image) -> Image.Image:
    """The studio frame scaled so the host's face has this format's width and place; what the frame does not reach is filled from the frame itself, blurred and darkened. The side strips fade
    into the same blurred, darkened picture, so a studio sign cut by the edge of the cover is not left as a few readable letters."""
    w, h = size
    fx, fy, fw, fh = face
    scale = target["face_width"] * w / fw
    scaled = plate.resize((round(plate.width * scale), round(plate.height * scale)), Image.LANCZOS)
    left, top = round(target["face_x"] * w - (fx + fw / 2) * scale), round(target["face_y"] * h - (fy + fh / 2) * scale)
    canvas = ImageEnhance.Brightness(ImageOps.fit(plate, size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(w * 0.05))).enhance(0.5).convert("RGBA")
    scaled = _finish(scaled, f"{fx:.0f}{fy:.0f}").convert("RGBA")
    scaled.putalpha(_feather(scaled.size, round(w * 0.06)))                     # where the picture ends inside the cover it fades into the blurred fill, with no hard edge
    canvas.alpha_composite(scaled.crop((max(0, -left), max(0, -top), min(scaled.width, w - left), min(scaled.height, h - top))), (max(0, left), max(0, top)))
    x = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    placed = Image.new("L", size, 0)
    placed.paste(host.resize(scaled.size, Image.LANCZOS).crop((max(0, -left), max(0, -top), min(scaled.width, w - left), min(scaled.height, h - top))), (max(0, left), max(0, top)))
    ramp = np.clip((np.abs(x) - veil["blur_start"]) / (veil["blur_full"] - veil["blur_start"]), 0, 1)
    strength = ramp * ramp * (3 - 2 * ramp) * (1 - np.asarray(placed, dtype=np.float32) / 255)          # a smooth step from sharp to fully blurred; the host himself is never blurred
    smeared = ImageEnhance.Brightness(canvas.convert("RGB").filter(ImageFilter.GaussianBlur(w * veil["edge_blur"]))).enhance(0.55)
    return Image.composite(smeared, canvas.convert("RGB"), Image.fromarray((strength * 255).astype(np.uint8))).convert("RGBA")


def _feather(size: tuple[int, int], reach: int) -> Image.Image:
    """An opacity mask the size of the picture: fully opaque inside, fading to nothing over `reach` pixels at each of its four edges."""
    w, h = size
    x = np.minimum(np.arange(w), np.arange(w)[::-1])[None, :] / reach
    y = np.minimum(np.arange(h), np.arange(h)[::-1])[:, None] / reach
    return Image.fromarray((np.clip(np.minimum(x, y), 0, 1) * 255).astype(np.uint8))


def _finish(image: Image.Image, seed: str) -> Image.Image:
    """The enlarged studio frame made crisper: a gentle unsharp mask (no halos at this strength) and a fine film grain that hides the blockiness of the upscale. Nothing is added to the face."""
    cfg = settings()["finish"]
    sharp = image.filter(ImageFilter.UnsharpMask(radius=cfg["unsharp_radius"], percent=cfg["unsharp_percent"], threshold=cfg["unsharp_threshold"]))
    noise = np.random.default_rng(zlib.crc32(seed.encode())).normal(0, cfg["grain"], sharp.size[::-1] + (1,)).astype(np.float32)
    return Image.fromarray(np.clip(np.asarray(sharp, dtype=np.float32) + noise, 0, 255).astype(np.uint8))


def _gradient(size: tuple[int, int], top_peak: float, bottom_peak: float, top_reach: float, bottom_reach: float, edge: float = 0.0, edge_reach: float = 0.1) -> Image.Image:
    """Dark veils at the top and the bottom (where the words go) and a soft vignette all round."""
    w, h = size
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    top = np.clip(1 - y / top_reach, 0, 1) ** 1.4 * top_peak
    bottom = np.clip((y - (1 - bottom_reach)) / bottom_reach, 0, 1) ** 1.4 * bottom_peak
    x = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    vignette = np.clip((x ** 2 + (2 * y - 1) ** 2 * 0.6 - 0.55), 0, 1) * 0.45
    sides = np.clip(1 - (1 - np.abs(x)) / (2 * edge_reach), 0, 1) ** 1.2 * edge           # the side strips are darkened so a studio sign cut by the edge is not read as stray letters
    alpha = np.clip(top + bottom + vignette + sides, 0, 0.92) * 255
    layer = Image.new("RGBA", (w, h), (4, 2, 2, 0))
    layer.putalpha(Image.fromarray(alpha.astype(np.uint8)))
    return layer


def _photo(path: Path, width: int, seed: str) -> Image.Image:
    """The person's photo as a black-and-white print with a pale border, slightly turned, with a shadow."""
    with Image.open(path) as source:
        grey = ImageOps.grayscale(source.convert("RGB"))
    inner = ImageOps.fit(grey, (width, round(width * 1.18)), Image.LANCZOS, centering=(0.5, 0.35))
    inner = ImageOps.autocontrast(inner, cutoff=1).convert("RGB")
    border = max(6, width // 28)
    print_ = Image.new("RGB", (inner.width + 2 * border, inner.height + 2 * border), (226, 218, 200))
    print_.paste(inner, (border, border))
    rng = random.Random(seed)
    sheet = print_.convert("RGBA").rotate(rng.uniform(-6.5, -3.5), expand=True, resample=Image.BICUBIC)
    shadow = Image.new("RGBA", (sheet.width + 40, sheet.height + 40), (0, 0, 0, 0))
    shadow.putalpha(Image.new("L", shadow.size, 0))
    blob = Image.new("L", shadow.size, 0)
    blob.paste(sheet.getchannel("A").point(lambda v: 150 if v else 0), (26, 30))
    shadow.putalpha(blob.filter(ImageFilter.GaussianBlur(10)))
    shadow.alpha_composite(sheet, (16, 14))
    return shadow


def _mirrored(lay: dict) -> dict:
    """The layout turned over left to right, for a right-to-left language: the host on the left, the words on the right."""
    out = json.loads(json.dumps(lay))
    for part in ("host",):
        out[part]["face_x"] = 1 - lay[part]["face_x"]
    for part in ("title", "logo", "banner"):
        out[part]["x"] = 1 - lay[part]["x"]
    out["photo"]["x"] = 1 - lay["photo"]["x"] - lay["photo"]["width"]
    return out


def compose_set(language: str, title: str, plate: Path, logo: Path, *, highlight: str | None = None, subject: str | None = None, photo: Path | None = None, seed: str = "cover",
                shapes: tuple[str, ...] = SHORT_SHAPES) -> dict[str, dict]:
    """The three covers of one Short: {format: {"image": RGB image, "elements": {name: [x, y, w, h]}, "problems": [...]}}. `highlight` is the word(s) of the title on the brush stroke
    (the last word when not given); `subject` is the person the banner names; `photo` is that person's photo, given only when the owner has cleared it."""
    cfg = settings()
    if not (3 <= len(title.split()) <= ai_config.covers_settings().title["max_words"]):
        raise CoverError("a title has 3 to 8 words")
    rtl = language in RTL
    colours = {k: _rgb(v) for k, v in cfg["colours"].items()}
    with Image.open(plate) as frame:
        frame = frame.convert("RGB")
    face = AV.host_face(plate)
    host = host_mask(frame, face)
    out: dict[str, dict] = {}
    for name in shapes:
        lay = _mirrored(cfg["layout"][name]) if rtl and name in FILM_SHAPES else cfg["layout"][name]
        w, h = cfg["formats"][name]
        canvas = _studio(frame, face, (w, h), lay["host"], lay["veil"], host)
        canvas.alpha_composite(_gradient((w, h), lay["veil"]["top"], lay["veil"]["bottom"], lay["veil"]["top_reach"], lay["veil"]["bottom_reach"], lay["veil"]["edge"], lay["veil"]["edge_reach"]))
        elements: dict[str, list[int]] = {}
        # the logo, top right
        mark = _keyed_logo(logo, round(lay["logo"]["height"] * h))
        lx, ly = round(lay["logo"]["x"] * w) - mark.width // 2, round(lay["logo"]["y"] * h)
        canvas.alpha_composite(mark, (lx, ly))
        elements["logo"] = [lx, ly, mark.width, mark.height]
        # the hook: centred, one key word on a brush stroke
        t = lay["title"]
        words = display_title(title, language).split()
        for limit in dict.fromkeys((t["max_lines"], t["max_lines_fallback"])):          # fewest lines first; one more only when the words cannot fit otherwise
            try:
                font, lines, size = _fit(words, language, round(t["width"] * w), limit, round(t["size"] * w), round(t["min_size"] * w), round(t["max_height"] * h))
                break
            except CoverError:
                if limit == t["max_lines_fallback"]:
                    raise
        line_h, y = round(size * 1.28), round(t["y"] * h)
        key = display_title(highlight or key_phrase(title, language), language).split()
        outline = max(2, round(size * cfg["title_outline"]["share"]))
        scrim = cfg["title_scrim"]                                          # a soft dark pool behind the words, so a bright wall behind them never costs contrast
        pad = round(size * scrim["pad"])
        pool = Image.new("L", (w, h), 0)
        ImageDraw.Draw(pool).rounded_rectangle((round(t["x"] * w - t["width"] * w / 2) - pad, round(t["y"] * h) - pad, round(t["x"] * w + t["width"] * w / 2) + pad, round(t["y"] * h) + len(lines) * line_h + pad), radius=pad, fill=round(255 * scrim["alpha"]))
        shade = Image.new("RGBA", (w, h), (6, 3, 3, 0))
        shade.putalpha(pool.filter(ImageFilter.GaussianBlur(pad * scrim["blur"])))
        canvas.alpha_composite(shade)
        for line in lines:
            parts, line_w, cx = line.split(), TY.text_width(line, font, language), round(t["x"] * w)
            at, run = _key_run(parts, key)
            paint = None
            if run:                                                          # the brush stroke goes behind the key word(s) only, on each line that holds a part of them
                span = TY.text_width(" ".join(parts[at:at + run]), font, language)
                reach = TY.text_width(" ".join(parts[:at + run]), font, language)          # from the line's start to the end of the key phrase (measured without a trailing space, which reads differently right to left)
                left = cx - line_w / 2 + reach - span if not rtl else cx + line_w / 2 - reach
                stroke = _brush((round(span + size * 0.9), round(line_h * cfg["brush"]["height_share"])), colours["red"], f"{seed}-{name}")
                origin = (round(left - size * 0.45 - (stroke.width - span - size * 0.9) / 2), round(y + size * 0.04 - (stroke.height - line_h * cfg["brush"]["height_share"]) / 2))

                def paint(stroke=stroke, origin=origin, canvas=canvas) -> None:
                    canvas.alpha_composite(stroke, origin)
            _text(canvas, (cx, y), line, font, language, colours["ink"], "ma", max(4, size // 14), outline, between=paint)
            y += line_h
        elements["title"] = [round(t["x"] * w - t["width"] * w / 2), round(t["y"] * h), round(t["width"] * w), y - round(t["y"] * h)]
        # the person's photo, lower left (only a cleared one)
        if photo is not None:
            sheet = _photo(photo, round(lay["photo"]["width"] * w), f"{seed}-photo")
            px, py = round(lay["photo"]["x"] * w), round(lay["photo"]["y"] * h)
            canvas.alpha_composite(sheet, (px, py))
            elements["photo"] = [px, py, sheet.width, sheet.height]
        # the banner: "real case: name"
        if subject:
            b = lay["banner"]
            label = cfg["banner"]["labels"][language]
            bsize = round(b["size"] * w)
            while True:
                bf = TY.font(language, "sans", "bold", bsize)
                nf, nl = (TY.font("en", "sans", "bold", bsize), "en") if subject.isascii() else (bf, language)       # a Latin name keeps its own sans letters inside Persian and Arabic words
                lw, nw, gap = TY.text_width(label, bf, language), TY.text_width(subject, nf, nl), round(bsize * 0.35)
                total = lw + gap + nw
                if total + bsize * 1.6 <= w * 0.94 or bsize <= 12:
                    break
                bsize -= 2                                              # a long label or name gets a smaller type, never a banner wider than the picture
            band = _brush((round(total + bsize * 1.6), round(b["height"] * h)), colours["red"], f"{seed}-banner-{name}", tilt=False)
            bx, by = min(max(0, round(b["x"] * w - band.width / 2)), w - band.width), round(b["y"] * h)      # a wide banner is moved inside the picture, not cut by it
            canvas.alpha_composite(band, (bx, by))
            mid, ty = bx + band.width // 2, by + band.height // 2
            first, second = label, subject
            if rtl:
                _text(canvas, (mid + total / 2, ty), first, bf, language, colours["ink"], "rm", 4)
                _text(canvas, (mid + total / 2 - lw - gap, ty), second, nf, nl, colours["gold"], "rm", 4)
            else:
                _text(canvas, (mid - total / 2, ty), first, bf, language, colours["ink"], "lm", 4)
                _text(canvas, (mid - total / 2 + lw + gap, ty), second, nf, nl, colours["gold"], "lm", 4)
            elements["banner"] = [bx, by, band.width, band.height]
        image = canvas.convert("RGB")
        out[name] = {"image": image, "elements": elements, "problems": problems_of(image, elements, (w, h), lay), "metrics": metrics_of(image, elements, lay)}
        if out[name]["metrics"]["host_sharpness"] < cfg["min_sharpness"]:
            out[name]["problems"].append(f"the host is too soft (sharpness {out[name]['metrics']['host_sharpness']}, at least {cfg['min_sharpness']})")
    return out


def title_contrast(box: Image.Image) -> float:
    """How far the words stand out from what is behind them: the brightest 3% of the title's area (the letters, however thin the script) against its darkest 10%."""
    luminance = np.asarray(box.convert("L"), dtype=np.float32) / 255
    return float(np.percentile(luminance, 97) - np.percentile(luminance, 10))


def metrics_of(image: Image.Image, elements: dict[str, list[int]], lay: dict) -> dict:
    """What was measured on the rendered picture: the sharpness of the host (variance of the Laplacian), the contrast of the title against what is behind it, the title's height in pixels."""
    import cv2

    w, h = image.size
    host = lay["host"]
    hw = host["face_width"] * w
    x0, x1 = max(0, round(host["face_x"] * w - hw * 1.2)), min(w, round(host["face_x"] * w + hw * 1.2))
    y0, y1 = max(0, round(host["face_y"] * h - hw * 0.9)), min(h, round(host["face_y"] * h + hw * 0.9))
    grey = cv2.cvtColor(np.asarray(image.crop((x0, y0, x1, y1))), cv2.COLOR_RGB2GRAY)
    title = elements["title"]
    return {"host_sharpness": round(float(cv2.Laplacian(grey, cv2.CV_64F).var()), 1), "title_contrast": round(title_contrast(image.crop((title[0], title[1], title[0] + title[2], title[1] + title[3]))), 2), "title_px": title[3]}


def problems_of(image: Image.Image, elements: dict[str, list[int]], size: tuple[int, int], lay: dict) -> list[str]:
    """What is wrong with a rendered cover: an element outside the picture, the host's face lost or cut, words too small for a phone, words that do not stand out from the picture behind them."""
    w, h = size
    found: list[str] = []
    for name, (x, y, bw, bh) in elements.items():
        if x < 0 or y < 0 or x + bw > w or y + bh > h:
            found.append(f"the {name} is partly outside the picture")
    band = lay.get("safe_band")
    if band:                                                                    # a cover that a feed crop cuts must keep everything that matters in the middle
        for name, (_, y, _, bh) in elements.items():
            if y < band["top"] * h or y + bh > band["bottom"] * h:
                found.append(f"the {name} is outside the middle part that a feed crop keeps")
    title = elements["title"]
    if title[3] and title[3] / h < 0.07:
        found.append("the title is too small to read on a phone")
    if title_contrast(image.crop((title[0], title[1], title[0] + title[2], title[1] + title[3]))) < 0.35:
        found.append("the title does not stand out from the picture behind it")
    try:
        import cv2

        frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        seen = cv2.FaceDetectorYN.create(str(AV._model()), "", (w, h), 0.5, 0.3, 5000).detect(frame)[1]
        faces = [] if seen is None else [f for f in seen if f[2] * f[3] > 0.0015 * w * h and f[14] >= settings()["min_face_confidence"]]      # the studio's pictures and carvings hold uncertain "faces" that are not the host
        host = lay["host"]                                           # the host's face is where the layout put it (the studio wall holds small pictures of faces that decorate it)
        hx, hy, hw = host["face_x"] * w, host["face_y"] * h, host["face_width"] * w
        hosts = [f for f in faces if abs(f[0] + f[2] / 2 - hx) < hw and abs(f[1] + f[3] / 2 - hy) < hw]
        if band and any(f[1] < band["top"] * h or f[1] + f[3] > band["bottom"] * h for f in hosts):
            found.append("the host's face is outside the middle part that a feed crop keeps")
        if not hosts:
            found.append("no face is visible on it")
        elif any(f[0] < 0 or f[1] < 0 or f[0] + f[2] > w or f[1] + f[3] > h for f in hosts):
            found.append("a face is cut by the edge")
        elif any(f[0] < w * settings()["face_margin"] or f[0] + f[2] > w * (1 - settings()["face_margin"]) for f in hosts):
            found.append("the host's face is too close to the edge")
        if hx - hw / 2 < title[0] + title[2] and hx + hw / 2 > title[0] and hy - hw * 0.65 < title[1] + title[3] and hy + hw * 0.65 > title[1]:
            found.append("the title covers the host's face")
    except (AV.AvatarError, ImportError) as exc:
        found.append(f"the faces could not be checked: {exc}")
    return found
