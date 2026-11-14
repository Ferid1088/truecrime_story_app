import json, math, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from app.documentary.visuals import typography as TY

CFG = json.loads(Path("config/parameters/channel_banner.json").read_text())["banner"]
LANG = sys.argv[1]
W, H = CFG["size"]
SAFE_W, SAFE_H = CFG["safe"]
rng = np.random.default_rng(15)
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)

# base: near-black with a cool top, vignette
base = np.zeros((H, W, 3), np.float32) + np.array([9, 9, 12])
# fog: multi-octave noise
def octave(scale, amp):
    small = rng.random((H // scale + 2, W // scale + 2)).astype(np.float32)
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(scale / 3))
    return np.asarray(img, np.float32) / 255 * amp
fog = octave(400, 1.0) + octave(160, 0.6) + octave(60, 0.25)
fog = (fog - fog.min()) / (fog.max() - fog.min())
# blood moon glow (right, high) + faint counter glow (left)
def glow(cx, cy, r, col, k):
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    g = np.exp(-(d / r) ** 2) * k
    return g[..., None] * np.array(col, np.float32)
img = base + fog[..., None] * np.array([26, 24, 28])
img += glow(2150, 330, 520, (190, 20, 24), 0.55)
img += glow(380, 1100, 700, (120, 14, 18), 0.28)
img += glow(1280, 720, 900, (60, 52, 46), 0.35)
vig = 1 - 0.55 * (((xx - W / 2) / (W / 2)) ** 2 * 0.6 + ((yy - H / 2) / (H / 2)) ** 2 * 0.9)
img *= np.clip(vig, 0.25, 1)[..., None]
canvas = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).convert("RGBA")

# moon disc
moon = Image.new("RGBA", (W, H), (0, 0, 0, 0)); md = ImageDraw.Draw(moon)
md.ellipse((2150 - 150, 330 - 150, 2150 + 150, 330 + 150), fill=(150, 14, 18, 255))
moon = moon.filter(ImageFilter.GaussianBlur(3))
mn = np.asarray(moon.split()[3], np.float32) / 255
tex = octave(30, 1.0)
arr = np.asarray(canvas, np.float32)
arr[..., :3] = arr[..., :3] * (1 - mn[..., None]) + (np.array([160, 18, 22]) * (0.75 + 0.35 * tex))[..., None, :].reshape(H, W, 3) * mn[..., None] if False else arr[..., :3]
moonrgb = np.stack([150 * (0.8 + 0.3 * tex), 14 * (0.8 + 0.3 * tex), 18 * (0.8 + 0.3 * tex)], -1)
arr[..., :3] = arr[..., :3] * (1 - mn[..., None]) + moonrgb * mn[..., None]
canvas = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")

# winding path of footprints across the whole width, left to right, ending below the moon
steps = 38
pts = []
for i in range(steps):
    t = i / (steps - 1)
    x = -40 + t * (W + 80)
    y = 1180 - 540 * t ** 1.4 + 70 * math.sin(t * 9)
    pts.append((x, y))
layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)); ld = ImageDraw.Draw(layer)
for i, (x, y) in enumerate(pts[:-1]):
    nx, ny = pts[i + 1]; ang = math.atan2(ny - y, nx - x)
    side = 1 if i % 2 else -1
    px, py = x - side * math.sin(ang) * 22, y + side * math.cos(ang) * 22
    s = 7 + 5 * (1 - i / steps)
    a = int(60 + 150 * (i / steps) ** 0.8)
    ld.ellipse((px - s * 0.6, py - s, px + s * 0.6, py + s), fill=(205, 60, 50, a))
layer = layer.filter(ImageFilter.GaussianBlur(1.2))
canvas = Image.alpha_composite(canvas, layer)

# darken the safe area slightly for calm text/logo ground
sx0, sy0 = (W - SAFE_W) // 2, (H - SAFE_H) // 2
shade = Image.new("L", (W, H), 0); sd = ImageDraw.Draw(shade)
sd.rounded_rectangle((sx0 - 60, sy0 - 40, sx0 + SAFE_W + 60, sy0 + SAFE_H + 40), radius=60, fill=150)
shade = shade.filter(ImageFilter.GaussianBlur(70))
canvas = Image.composite(Image.new("RGBA", (W, H), (6, 6, 8, 255)), canvas, shade)

# logo centred, fits the safe height with a little air
logo = Image.open(f"data/logos/Logo_{CFG['languages'][LANG]['logo']}.png").convert("RGBA")
rgb = np.asarray(logo.convert("RGB"), np.float32)
key = np.clip((rgb.max(-1) - 14) / 70, 0, 1) ** 0.8
logo = Image.fromarray(np.dstack([rgb, key * 255]).astype(np.uint8), "RGBA")
logo = logo.crop(logo.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox())
lh = CFG["logo_height"]
logo = logo.resize((round(logo.width * lh / logo.height), lh), Image.LANCZOS)
lx, ly = (W - logo.width) // 2, (H - lh) // 2
canvas.alpha_composite(logo, (lx, ly))

# side texts inside the safe area: Latin mark left, the line in the language right
def rgb(h): return tuple(int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
draw = ImageDraw.Draw(canvas)
ivory, copper = rgb(CFG["ivory"]), rgb(CFG["copper"])
left_edge, right_edge = sx0 + 10, sx0 + SAFE_W - 10
f1 = TY.font("en", "sans", "bold", 74)
mark = CFG["mark"]
mw = TY.text_width(mark, f1, "en")
cx_left = (left_edge + lx - 30) // 2
TY.draw_text(draw, (cx_left - mw // 2, H // 2 - 46), mark, f1, "en", fill=ivory, anchor="la")
draw.rectangle((cx_left - 70, H // 2 + 50, cx_left + 70, H // 2 + 55), fill=copper)
rtl = LANG in ("fa", "ar")
x0, x1 = lx + logo.width + 30, right_edge
avail = x1 - x0
first, second = CFG["languages"][LANG]["lines"]

def balanced(text, f):
    words = text.split()
    cuts = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
    return min(cuts, key=lambda c: abs(TY.text_width(c[0], f, LANG) - TY.text_width(c[1], f, LANG))) if cuts else (text,)

size = CFG["text_size"]
while True:
    f2 = TY.font(LANG, "sans", "bold", size)
    parts = [(first, ivory)]
    one = [(second, copper)]
    two = [(t, copper) for t in balanced(second, f2)]
    for body in (one, two):
        lines = parts + body
        if max(TY.text_width(t, f2, LANG) for t, _ in lines) <= avail:
            break
    else:
        size -= 2
        if size < CFG["min_text_size"]:
            raise SystemExit(f"{LANG}: tagline does not fit")
        continue
    break
gap = round(size * 1.6)
top = H // 2 - gap * (len(lines) - 1) // 2 - size // 2 - 8
cx = x0 + avail // 2
for k, (t, col) in enumerate(lines):
    w = TY.text_width(t, f2, LANG)
    TY.draw_text(draw, (cx + w // 2, top + k * gap), t, f2, LANG, fill=col, anchor="ra") if rtl else TY.draw_text(draw, (cx - w // 2, top + k * gap), t, f2, LANG, fill=col, anchor="la")
canvas.convert("RGB").save(sys.argv[2], quality=93, subsampling=0)
print(sys.argv[2], size, [t for t, _ in lines])
