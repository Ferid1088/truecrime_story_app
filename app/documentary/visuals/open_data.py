"""Open-data sources for places: USGS/USDA NAIP aerial imagery of the United States and the NASA
Image and Video Library. Both are public domain (docs/open_sources_terms.md) and both ask for a
credit, which the film shows. Their pictures pass the same verification as any other picture."""

from __future__ import annotations

import math
from urllib.parse import urlencode

import httpx

from app.core.ai_config import ai_config
from app.documentary.visuals import maps
from app.documentary.visuals.research import Candidate

EARTH_RADIUS_M = 6378137.0
MERCATOR_ORIGIN = math.pi * EARTH_RADIUS_M


def to_mercator(lat: float, lon: float) -> tuple[float, float]:
    return (lon * MERCATOR_ORIGIN / 180.0,
            EARTH_RADIUS_M * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def mercator_box(lat: float, lon: float, meters: float) -> list[float]:
    """A square of `meters` on the ground around a point, in Web Mercator (EPSG:3857) units."""
    x, y = to_mercator(lat, lon)
    half = meters / 2 / math.cos(math.radians(lat))
    return [x - half, y - half, x + half, y + half]


class UsgsNaipProvider:
    """Aerial images of a place from the USGS NAIP image service (exportImage by bounding box):
    one image per configured scale around the place the geocoder finds for the query."""

    name = "usgs_naip"
    entity_types = ("place", "building", "landscape")

    def __init__(self, client: httpx.AsyncClient | None):
        self.client = client
        self.cfg = ai_config.visual_search.open_data_settings().usgs_naip

    def _covers(self, lat: float, lon: float) -> bool:
        x, y = to_mercator(lat, lon)
        x0, y0, x1, y1 = self.cfg.extent
        return x0 <= x <= x1 and y0 <= y <= y1

    async def search_for(self, query: dict, limit: int) -> list[Candidate]:
        place = None
        for text in (query.get("query"), query.get("entity_name")):
            place = place or (await maps.geocode(text, self.client) if text else None)
        if place is None or not self._covers(place["lat"], place["lon"]):
            return []
        out = []
        for meters in self.cfg.view_meters[:limit]:
            params = {"bbox": ",".join(f"{v:.1f}" for v in mercator_box(place["lat"], place["lon"], meters)),
                      "bboxSR": 3857, "imageSR": 3857, "size": f"{self.cfg.size_px},{self.cfg.size_px}",
                      "format": "jpgpng", "f": "image"}
            out.append(Candidate(
                url=f"{self.cfg.export_url}?{urlencode(params, safe=',')}", provider=self.name,
                page_url=self.cfg.page_url, title=f"Aerial view of {query.get('entity_name') or query['query']}"
                f" ({meters} m)", caption=f"NAIP aerial image, about {meters} m across, around "
                f"{place.get('display_name') or query['query']}", license=self.cfg.license,
                credit=self.cfg.credit, source_name="USGS The National Map", found_for=query["query"]))
        return out


class NasaProvider:
    """Pictures from the NASA Image and Video Library found by text; an item that names a
    third party's copyright is skipped (NASA's own guidelines exclude those)."""

    name = "nasa"
    entity_types = ("place", "landscape")

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.cfg = ai_config.visual_search.open_data_settings().nasa

    async def search_for(self, query: dict, limit: int) -> list[Candidate]:
        return await self.search(query["query"], limit)

    async def search(self, query: str, limit: int) -> list[Candidate]:
        r = await self.client.get(self.cfg.search_url, params={
            "q": query, "media_type": "image", "page_size": limit})
        r.raise_for_status()
        out = []
        for item in (r.json().get("collection") or {}).get("items") or []:
            data = (item.get("data") or [{}])[0]
            text = " ".join(str(data.get(k) or "") for k in ("description", "secondary_creator")).lower()
            if not data.get("nasa_id") or "©" in text or "(c)" in text or "copyright" in text:
                continue
            url = await self._file(data["nasa_id"])
            if url:
                out.append(Candidate(
                    url=url, provider=self.name, page_url=self.cfg.page_url.format(nasa_id=data["nasa_id"]),
                    title=(data.get("title") or "")[:300], caption=(data.get("description") or "")[:600] or None,
                    license=self.cfg.license, credit=self.cfg.credit, source_name="NASA",
                    found_for=query, date=(data.get("date_created") or "")[:10] or None))
        return out[:limit]

    async def _file(self, nasa_id: str) -> str | None:
        """The first file of the item whose name carries one of the configured size markers."""
        r = await self.client.get(self.cfg.asset_url.format(nasa_id=nasa_id))
        r.raise_for_status()
        hrefs = [i.get("href", "") for i in (r.json().get("collection") or {}).get("items") or []]
        for marker in self.cfg.size_markers:
            hit = next((h for h in hrefs if marker in h), None)
            if hit:
                return hit
        return None
