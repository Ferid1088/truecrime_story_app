"""Which open imagery covers a place at which zoom, and which hosts the zoom map may call."""

from __future__ import annotations

import math
from urllib.parse import urlparse

from app.core.ai_config import ZoomImagery, ZoomMapConfig, ai_config


class ForbiddenHost(RuntimeError):
    """A request to a provider the zoom map must never use (Google, Bing, Esri, Mapbox, the public
    OpenStreetMap tile servers, Street View ...) or to a host nobody allowed."""


def check_host(url: str, cfg: ZoomMapConfig | None = None) -> str:
    cfg = cfg or ai_config.zoom()
    host = (urlparse(url).hostname or "").lower()
    if any(f in host for f in cfg.hosts["forbidden"]):
        raise ForbiddenHost(f"{host} is a forbidden provider")
    if host not in {h.lower() for h in cfg.hosts["allowed"]}:
        raise ForbiddenHost(f"{host} is not an allowed host of the zoom map")
    return host


def covers(entry: ZoomImagery, lat: float, lon: float) -> bool:
    if entry.coverage == "global":
        return True
    return any(w <= lon <= e and s <= lat <= n for w, s, e, n in entry.coverage)


def entries_for(lat: float, lon: float, cfg: ZoomMapConfig | None = None) -> list[ZoomImagery]:
    """Imagery covering the place, coarse first: later entries are drawn over earlier ones."""
    cfg = cfg or ai_config.zoom()
    found = [e for e in cfg.imagery if covers(e, lat, lon)]
    return sorted(found, key=lambda e: (-e.resolution_m, e.zoom_min))


def best_zoom(lat: float, lon: float, cfg: ZoomMapConfig | None = None) -> int | None:
    """The deepest zoom with open imagery at the place (None: no imagery at all)."""
    found = entries_for(lat, lon, cfg)
    return max((e.zoom_max for e in found), default=None)


def tile_bbox_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    world = 20037508.342789244
    size = 2 * world / (2 ** z)
    return (-world + x * size, world - (y + 1) * size, -world + (x + 1) * size, world - y * size)


def upstream_url(entry: ZoomImagery, z: int, x: int, y: int) -> str:
    """The provider URL of one tile (xyz entries use the tile scheme, WMS entries a bounding box)."""
    url = entry.url
    for key, value in entry.params.items():
        url = url.replace("{" + key + "}", value)
    if entry.kind == "wms":
        west, south, east, north = tile_bbox_3857(z, x, y)
        return url.replace("{bbox-epsg-3857}", f"{west:.3f},{south:.3f},{east:.3f},{north:.3f}")
    return url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))


def tile_xy(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def attribution_for(entries: list[ZoomImagery], cfg: ZoomMapConfig | None = None) -> list[str]:
    """What the shot shows on screen: the map data and every imagery source it used."""
    cfg = cfg or ai_config.zoom()
    out = [str(cfg.data["vector_attribution"])]
    for e in entries:
        if e.attribution not in out:
            out.append(e.attribution)
    return out
