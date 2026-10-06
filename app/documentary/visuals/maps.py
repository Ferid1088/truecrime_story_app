"""Geo visuals (Part 29): orientation maps from legally usable map data —
never screen recordings. The tile source and geocoder are config
(default OpenStreetMap, credited on screen). Tiles and geocodes are
cached; requests carry a descriptive User-Agent and are spaced out.

A map sequence is a few zoom levels around one place (wide -> close),
darkened for a documentary look, with a marker at the place. Labels in
the film's language are overlays added at render time.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageEnhance

from app.core.ai_config import ai_config
from app.documentary.storage import cases_root

_last_request = 0.0


def cache_dir() -> Path:
    d = cases_root().parent / "map_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _polite_get(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    global _last_request
    wait = 1.0 - (time.monotonic() - _last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = time.monotonic()
    r = await client.get(url, **kw)
    r.raise_for_status()
    return r


async def geocode(place: str, client: httpx.AsyncClient | None = None) -> dict | None:
    """{"lat", "lon", "display_name"} or None. Cached per query."""
    key = hashlib.sha1(place.lower().encode()).hexdigest()[:16]
    f = cache_dir() / f"geo_{key}.json"
    if f.exists():
        data = json.loads(f.read_text())
        return data or None
    own = client is None
    client = client or httpx.AsyncClient(
        timeout=25, headers={"User-Agent": ai_config.visual_search.user_agent})
    try:
        r = await _polite_get(client, ai_config.maps.geocoder_url,
                              params={"q": place, "format": "json", "limit": 1})
        res = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if own:
            await client.aclose()
    data = {}
    if res:
        data = {"lat": float(res[0]["lat"]), "lon": float(res[0]["lon"]),
                "display_name": res[0].get("display_name")}
    f.write_text(json.dumps(data))
    return data or None


def _tile_xy(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(min(lat, 85.0511), -85.0511))
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


async def _tile(client: httpx.AsyncClient, z: int, x: int, y: int) -> Image.Image:
    n = 2 ** z
    x %= n
    if not 0 <= y < n:
        return Image.new("RGB", (ai_config.maps.tile_size,) * 2, (20, 22, 26))
    f = cache_dir() / "tiles" / str(z) / str(x) / f"{y}.png"
    if not f.exists():
        url = ai_config.maps.tile_url.format(z=z, x=x, y=y)
        r = await client.get(url)
        r.raise_for_status()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(r.content)
    return Image.open(io.BytesIO(f.read_bytes())).convert("RGB")


async def render_map(lat: float, lon: float, zoom: int, out: Path,
                     W: int = 2400, H: int = 1350,
                     client: httpx.AsyncClient | None = None) -> dict:
    """Stitch tiles centred on (lat, lon); darken; mark the place.
    Returns {"path", "marker": [x, y]} (marker in image pixels)."""
    ts = ai_config.maps.tile_size
    own = client is None
    client = client or httpx.AsyncClient(
        timeout=25, headers={"User-Agent": ai_config.visual_search.user_agent})
    try:
        cx, cy = _tile_xy(lat, lon, zoom)
        px_c, py_c = cx * ts, cy * ts
        left, top = px_c - W / 2, py_c - H / 2
        canvas = Image.new("RGB", (W, H))
        for tx in range(int(left // ts), int((left + W) // ts) + 1):
            for ty in range(int(top // ts), int((top + H) // ts) + 1):
                tile = await _tile(client, zoom, tx, ty)
                canvas.paste(tile, (int(tx * ts - left), int(ty * ts - top)))
    finally:
        if own:
            await client.aclose()
    cfg = ai_config.maps
    canvas = ImageEnhance.Color(canvas).enhance(0.35)
    canvas = ImageEnhance.Brightness(canvas).enhance(1.0 - cfg.darken)
    canvas = ImageEnhance.Contrast(canvas).enhance(1.15)
    d = ImageDraw.Draw(canvas, "RGBA")
    mx, my = W // 2, H // 2
    for r, a in ((34, 50), (22, 90)):
        d.ellipse((mx - r, my - r, mx + r, my + r), fill=(214, 64, 52, a))
    d.ellipse((mx - 9, my - 9, mx + 9, my + 9), fill=(214, 64, 52, 255),
              outline=(255, 255, 255, 230), width=3)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, "JPEG", quality=90)
    return {"path": str(out), "marker": [mx, my]}
