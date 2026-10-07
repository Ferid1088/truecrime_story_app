"""Geo visuals (Part 29): orientation maps from legally usable map data —
never screen recordings. The tile source and geocoder are config
(default OpenStreetMap, credited on screen). Tiles and geocodes are
cached; requests carry a descriptive User-Agent and are spaced out.

A map sequence is a few zoom levels around one place (wide -> close),
darkened for a documentary look, with a marker at the place. Labels in
the film's language are overlays added at render time.

The zoom chain follows the place itself (Master task §9.5): country ->
region -> city -> the relevant area, as far as the place's granularity
(Nominatim addresstype/class/type) and its extent (bounding box) require.
A later place near an earlier map of the same film starts at city level —
the viewer already knows the country.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import re
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


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


# ---------------------------------------------------------------------------
# place granularity + zoom chain (pure)
# ---------------------------------------------------------------------------

# Nominatim addresstype (or place type) -> granularity of the map story.
_GRANULARITY = {
    "country": "country",
    "state": "state", "region": "state", "province": "state",
    "county": "county", "state_district": "county", "district": "county",
    "municipality": "county",
    "city": "city",
    "town": "town",
    "village": "village", "hamlet": "village", "isolated_dwelling": "village",
    "farm": "village", "locality": "village",
    "suburb": "suburb", "city_district": "suburb", "borough": "suburb",
    "quarter": "suburb", "neighbourhood": "suburb", "city_block": "suburb",
    "road": "road",
    "building": "building", "house_number": "building", "house_name": "building",
    "amenity": "building", "shop": "building", "tourism": "building",
    "office": "building", "craft": "building", "man_made": "building",
    "railway": "building", "aeroway": "building", "historic": "building",
    "emergency": "building", "military": "building", "club": "building",
}
# Natural/land-use features are shown whole: their extent decides the zoom.
_AREA_CLASSES = {"natural", "landuse", "leisure", "waterway", "water"}
_AREA_TYPES = {
    "wood", "forest", "park", "nature_reserve", "protected_area", "national_park",
    "heath", "moor", "wetland", "scrub", "grassland", "meadow", "water", "lake",
    "reservoir", "river", "beach", "bay", "valley", "peak", "ridge", "cliff",
    "island", "islet", "garden", "cemetery", "farmland", "quarry",
}

# Final zoom per granularity: (lowest, highest, without a bbox). Between
# lowest and highest the bounding box decides (a large town needs less
# zoom than a small one). Countries are fixed: their map orients in the
# continent, the next step does the rest.
ZOOM_BY_GRANULARITY: dict[str, tuple[int, int, int]] = {
    "country": (5, 5, 5),
    "state": (6, 7, 7),
    "county": (8, 10, 9),
    "city": (11, 12, 11),
    "town": (12, 13, 12),
    "village": (13, 14, 13),
    "suburb": (13, 14, 14),
    "road": (13, 16, 16),
    "building": (16, 17, 17),
    "area": (9, 16, 13),
    "unknown": (10, 16, 11),
}
# Wide -> close context steps: country, region, city, street approach.
CONTEXT_STEPS = (5, 7, 11, 14)
CITY_ZOOM = 11
# A context step must be at least this much wider than the next step.
MIN_STEP_GAP = 2
MAX_STEPS = 4
# Steps dropped first when a chain is longer than MAX_STEPS.
DROP_ORDER = (14, 7)
# A place within this distance of the film's previous map starts at city level.
NEARBY_KM = 60.0
# Share of the frame the bounding box may fill when fitting it.
FIT_FILL = 0.6
MAP_W, MAP_H = 2400, 1350


def granularity(result: dict) -> str:
    """Granularity of one Nominatim result: country | state | county |
    city | town | village | suburb | road | building | area | unknown."""
    cls = str(result.get("class") or result.get("osm_class") or "").lower()
    typ = str(result.get("type") or result.get("osm_type") or "").lower()
    at = str(result.get("addresstype") or "").lower()
    if cls in _AREA_CLASSES or typ in _AREA_TYPES:
        return "area"
    if cls == "boundary" and typ not in ("administrative", ""):
        return "area"  # historic/protected boundaries (a forest district)
    if at in _GRANULARITY:
        return _GRANULARITY[at]
    if cls == "highway":
        return "road"
    if cls == "place" and typ in _GRANULARITY:
        return _GRANULARITY[typ]
    if cls in _GRANULARITY:
        return _GRANULARITY[cls]
    return "unknown"


def _bbox(result: dict) -> list[float] | None:
    """[south, north, west, east] from Nominatim's boundingbox."""
    bb = result.get("boundingbox") or result.get("bbox")
    try:
        s, n, w, e = (float(x) for x in bb)
    except (TypeError, ValueError):
        return None
    return [s, n, w, e] if n >= s and e >= w else None


def fit_zoom(bbox: list[float], W: int = MAP_W, H: int = MAP_H,
             fill: float = FIT_FILL, tile: int | None = None) -> int:
    """Highest zoom at which the bounding box fits `fill` of the frame."""
    tile = tile or ai_config.maps.tile_size
    s, n, w, e = bbox
    x0, y0 = _tile_xy(n, w, 0)
    x1, y1 = _tile_xy(s, e, 0)
    span_x = max(x1 - x0, 1e-9) * tile      # world pixels at zoom 0
    span_y = max(y1 - y0, 1e-9) * tile
    z = min(math.log2(W * fill / span_x), math.log2(H * fill / span_y))
    return max(0, min(19, math.floor(z)))


def as_point(p) -> tuple[float, float] | None:
    """(lat, lon) of a place info dict or a (lat, lon) pair; None otherwise."""
    if p is None:
        return None
    if isinstance(p, dict):
        try:
            return float(p["lat"]), float(p["lon"])
        except (KeyError, TypeError, ValueError):
            return None
    try:
        lat, lon = p
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def is_nearby(a, b, km: float = NEARBY_KM) -> bool:
    """Both places known and within `km` of each other."""
    pa, pb = as_point(a), as_point(b)
    return pa is not None and pb is not None and _km(pa, pb) <= km


def target_zoom(place_info: dict) -> int:
    """The closest zoom a place needs to be understood."""
    g = place_info.get("granularity") or "unknown"
    lo, hi, default = ZOOM_BY_GRANULARITY.get(g, ZOOM_BY_GRANULARITY["unknown"])
    bb = _bbox(place_info)
    if bb is None or lo == hi:
        return default
    return max(lo, min(hi, fit_zoom(bb)))


def zoom_chain(place_info: dict, previous=None) -> list[int]:
    """Zoom levels (wide -> close) of one place's map sequence.

    country -> region -> city -> street approach, as far as the place
    needs (a country is one map; a church is country, region, city and
    street level). `previous`: an earlier mapped place of the same film
    ({"lat", "lon"} or (lat, lon)); within NEARBY_KM the chain starts at
    city level instead of repeating the country. At most MAX_STEPS."""
    target = target_zoom(place_info)
    steps = [s for s in CONTEXT_STEPS if s <= target - MIN_STEP_GAP]
    if is_nearby(place_info, previous):
        steps = [s for s in steps if s >= CITY_ZOOM]
    chain = steps + [target]
    for drop in DROP_ORDER:
        if len(chain) <= MAX_STEPS:
            break
        if drop in chain[:-1]:
            chain.remove(drop)
    return chain[-MAX_STEPS:]


# ---------------------------------------------------------------------------
# geocoding
# ---------------------------------------------------------------------------


def _place_info(x: dict) -> dict:
    return {"lat": float(x["lat"]), "lon": float(x["lon"]),
            "display_name": x.get("display_name"),
            "granularity": granularity(x), "bbox": _bbox(x),
            "addresstype": x.get("addresstype"), "osm_class": x.get("class"),
            "osm_type": x.get("type"), "place_rank": x.get("place_rank")}


async def geocode(place: str, client: httpx.AsyncClient | None = None,
                  near: tuple[float, float] | None = None) -> dict | None:
    """{"lat", "lon", "display_name", "granularity", "bbox" ([s, n, w,
    e]), "addresstype", "osm_class", "osm_type", "place_rank"} or None.
    Several places share a name (there is more than one "Wilhelmshof"):
    with `near` (a place of the same case) the closest match wins.
    Results cached per query."""
    key = hashlib.sha1(place.lower().encode()).hexdigest()[:16]
    f = cache_dir() / f"geo6_{key}.json"
    if f.exists():
        res = json.loads(f.read_text())
    else:
        own = client is None
        client = client or httpx.AsyncClient(
            timeout=25, headers={"User-Agent": ai_config.visual_search.user_agent})
        try:
            r = await _polite_get(client, ai_config.maps.geocoder_url,
                                  params={"q": place, "format": "json", "limit": 5})
            res = r.json()
        except (httpx.HTTPError, ValueError):
            return None
        finally:
            if own:
                await client.aclose()
        res = [_place_info(x) for x in res or [] if "lat" in x and "lon" in x]
        f.write_text(json.dumps(res))
    # The result must really be that place: every word of the name (before
    # the first comma) appears in the result ("Bundesstraße 188" is not
    # "An der Bundesstraße").
    import unicodedata

    def fold(t: str) -> str:
        t = unicodedata.normalize("NFKD", t or "").lower()
        return "".join(ch for ch in t if not unicodedata.combining(ch)).replace("ß", "ss")

    words = [w for w in re.findall(r"\w+", fold(place.split(",")[0])) if len(w) > 1 or w.isdigit()]
    res = [x for x in res if all(w in fold(x.get("display_name") or "") for w in words)]
    if not res:
        return None
    if near is not None:
        return min(res, key=lambda x: _km(near, (x["lat"], x["lon"])))
    return res[0]


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
