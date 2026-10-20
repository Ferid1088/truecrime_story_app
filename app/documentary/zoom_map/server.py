"""What the headless browser may load: the local page and libraries, the case's map extract, cached
imagery tiles, cached glyphs and sprites. Everything else is refused, and every request to the
outside goes through the host check and is recorded."""

from __future__ import annotations

import asyncio
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

from app.core.ai_config import ZoomImagery, ZoomMapConfig, ai_config
from app.documentary.zoom_map import data as DATA
from app.documentary.zoom_map import registry as REG

PAGE = Path(__file__).with_name("page.html")
_RANGE = re.compile(r"bytes=(\d+)-(\d*)")
_TYPES = {".js": "text/javascript", ".css": "text/css", ".json": "application/json",
          ".png": "image/png", ".pbf": "application/x-protobuf", ".jpg": "image/jpeg"}


class Fetcher:
    """The only place the zoom map goes online while rendering (tests replace it)."""

    async def get(self, url: str, timeout: float = 60.0) -> tuple[int, bytes]:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "truecrime-story-app zoom-map (offline renderer)"})
            return r.status_code, r.content


def clear_dark(png: bytes, below: int) -> bytes:
    """The tile with the pixels no brighter than `below` (outside the composite's swath, open water)
    made transparent."""
    rgba = np.array(Image.open(io.BytesIO(png)).convert("RGBA"))
    rgba[rgba[..., :3].max(axis=2) <= below] = 0
    out = io.BytesIO()
    Image.fromarray(rgba).save(out, "PNG")
    return out.getvalue()


class ImageryCache:
    """Imagery tiles of the area on disk (data/maps/<case>/imagery/<entry>/<z>/<x>/<y>) with the
    source, date and licence of every file in manifest.json. A tile the provider does not have is
    remembered too, so the render never asks twice."""

    def __init__(self, case_id: int, fetcher: Fetcher, cfg: ZoomMapConfig | None = None):
        self.cfg = cfg or ai_config.zoom()
        self.dir = DATA.case_dir(case_id) / "imagery"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fetcher = fetcher
        self.manifest_path = self.dir / "manifest.json"
        self.manifest: dict = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else {}
        self.used: dict[str, ZoomImagery] = {}
        self.fetched = 0
        self.from_cache = 0

    def _path(self, entry: ZoomImagery, z: int, x: int, y: int) -> Path:
        return self.dir / entry.id / str(z) / str(x) / f"{y}.jpg"

    async def get(self, entry: ZoomImagery, z: int, x: int, y: int) -> bytes | None:
        path = self._path(entry, z, x, y)
        rel = str(path.relative_to(self.dir))
        if path.exists():
            self.from_cache += 1
            self.used[entry.id] = entry
            return path.read_bytes()
        if (self.manifest.get(rel) or {}).get("missing"):
            return None
        url = REG.upstream_url(entry, z, x, y)
        REG.check_host(url, self.cfg)
        status, body = await self.fetcher.get(url)
        record = {"entry": entry.id, "source": url, "fetched": datetime.now(UTC).date().isoformat(),
                  "licence": entry.licence, "licence_url": entry.licence_url, "attribution": entry.attribution}
        if status != 200 or body[:2] not in (b"\xff\xd8", b"\x89P"):
            self.manifest[rel] = {**record, "missing": True, "status": status}
            return None
        if entry.nodata_below:
            body = clear_dark(body, entry.nodata_below)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        self.manifest[rel] = record
        self.fetched += 1
        self.used[entry.id] = entry
        return body

    def save(self) -> None:
        self.manifest_path.write_text(json.dumps(self.manifest, indent=1, sort_keys=True))


class TileServer:
    """Answers the browser's requests to `http://maps.local/...`; `log` lists what was served."""

    def __init__(self, case_id: int, extract: Path, cache: ImageryCache, fetcher: Fetcher,
                 cfg: ZoomMapConfig | None = None):
        self.cfg = cfg or ai_config.zoom()
        self.extract = extract
        self.cache = cache
        self.fetcher = fetcher
        self.runtime = DATA.root() / str(self.cfg.data["runtime_dir"])
        self.libs = {
            "/lib/maplibre-gl.js": "node_modules/maplibre-gl/dist/maplibre-gl.js",
            "/lib/maplibre-gl.css": "node_modules/maplibre-gl/dist/maplibre-gl.css",
            "/lib/pmtiles.js": "node_modules/pmtiles/dist/pmtiles.js",
            "/lib/basemaps.js": "node_modules/@protomaps/basemaps/dist/basemaps.js",
            "/lib/rtl-text.js": "node_modules/@mapbox/mapbox-gl-rtl-text/dist/mapbox-gl-rtl-text.js"}
        self.log: list[tuple[str, int]] = []
        self.denied: list[str] = []
        self._gate = asyncio.Semaphore(int(self.cfg.data["max_parallel_fetches"]))
        self.entries = {e.id: e for e in self.cfg.imagery}

    async def handle(self, route) -> None:
        url = route.request.url
        if not url.startswith("http://maps.local/"):
            self.denied.append(url)
            self.log.append((url, 403))
            await route.abort()
            return
        path = url[len("http://maps.local"):].split("?", 1)[0]
        try:
            status, body, ctype, headers = await self._serve(path, route.request.headers.get("range"))
        except REG.ForbiddenHost as e:
            self.denied.append(str(e))
            status, body, ctype, headers = 403, b"", "text/plain", {}
        self.log.append((url, status))
        await route.fulfill(status=status, body=body, content_type=ctype, headers=headers)

    async def _serve(self, path: str, rng: str | None) -> tuple[int, bytes, str, dict]:
        if path == "/page":
            return 200, PAGE.read_bytes(), "text/html", {}
        if path in self.libs:
            f = self.runtime / self.libs[path]
            return 200, f.read_bytes(), _TYPES[f.suffix], {}
        if path == "/region.pmtiles":
            return self._range(rng)
        if path.startswith(("/glyphs/", "/sprites/")):
            return await self._asset(path)
        if path.startswith("/imagery/"):
            return await self._tile(path)
        return 404, b"", "text/plain", {}

    def _range(self, rng: str | None) -> tuple[int, bytes, str, dict]:
        size = self.extract.stat().st_size
        m = _RANGE.match(rng or "")
        if not m:
            return 200, self.extract.read_bytes(), "application/octet-stream", {}
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else size - 1
        with open(self.extract, "rb") as f:
            f.seek(a)
            body = f.read(b - a + 1)
        return 206, body, "application/octet-stream", {
            "content-range": f"bytes {a}-{b}/{size}", "accept-ranges": "bytes"}

    async def _asset(self, path: str) -> tuple[int, bytes, str, dict]:
        rel = path.replace("/glyphs/", "fonts/", 1).replace("/sprites/", "sprites/v4/", 1)
        local = self.runtime / "assets" / rel
        if not local.exists():
            url = str(self.cfg.data["basemap_assets_url"]) + rel
            REG.check_host(url, self.cfg)
            status, body = await self.fetcher.get(url)
            if status != 200:
                return 404, b"", "text/plain", {}
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(body)
        return 200, local.read_bytes(), _TYPES[local.suffix], {}

    async def _tile(self, path: str) -> tuple[int, bytes, str, dict]:
        try:
            _, _, entry_id, z, x, y = path.split("/")
            entry = self.entries[entry_id]
            async with self._gate:
                body = await self.cache.get(entry, int(z), int(x), int(y))
        except (ValueError, KeyError):
            return 404, b"", "text/plain", {}
        if body is None:
            return 404, b"", "text/plain", {}
        return 200, body, "image/png" if body[:2] == b"\x89P" else "image/jpeg", {}
