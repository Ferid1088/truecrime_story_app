"""The reel's end card (S5): channel name + logo, ~2s, no URL printed. Composed once per
language and reused by every reel through app/shortform/asset_store.py — this module never
writes a file directly; it is only ever called as the shared asset store's ``create`` callback,
so a creation is automatically a cache-miss event and every later call is a reuse."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.core.ai_config import ai_config
from app.documentary.visuals import typography as T

W, H = 1080, 1920
LOGO_MAX_WIDTH_FRACTION = 0.55


def render_end_card(language: str, *, background: tuple[int, int, int] = (10, 10, 12)) -> Image.Image:
    """Channel name (localized label) centred above its logo image, on a plain background.
    No URL anywhere on the card (the publish manifest carries the link/checklist, never the
    pixels)."""
    cfg = ai_config.shorts_host_led
    name = cfg.channel_name.get(language)
    logo_rel = cfg.channel_logo.get(language)
    if not name or not logo_rel:
        raise ValueError(f"no channel_name/channel_logo configured for language {language!r}")
    root = Path(__file__).resolve().parents[2]
    logo_path = root / logo_rel
    if not logo_path.exists():
        raise FileNotFoundError(f"channel logo not found: {logo_path}")

    img = Image.new("RGB", (W, H), background)
    logo = Image.open(logo_path).convert("RGBA")
    target_w = int(W * LOGO_MAX_WIDTH_FRACTION)
    target_h = int(target_w * logo.height / logo.width)
    logo = logo.resize((target_w, target_h))
    logo_y = (H - target_h) // 2
    img.paste(logo, ((W - target_w) // 2, logo_y), logo)

    f = T.font(language, "sans", "bold", 64)
    width = int(W * 0.8)
    lines = T.wrap(name, f, language, width)
    line_height = int(64 * 1.3)
    text_y = logo_y - line_height * len(lines) - 48
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    T._draw_lines(draw, lines, f, language, (W - width) // 2, text_y, width,
                  "center", (245, 242, 235, 255), 1.3)
    return img


def create_end_card_asset(path: Path, language: str) -> None:
    """``SharedAssetStore`` create-callback: render the card once and save it at ``path``."""
    render_end_card(language).save(path)
