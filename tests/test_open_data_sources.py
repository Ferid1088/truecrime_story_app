import asyncio
import json
import math
from pathlib import Path

import httpx

from app.core.ai_config import ai_config
from app.documentary.visuals import maps
from app.documentary.visuals import open_data as OD
from app.documentary.visuals import rights as R
from app.documentary.visuals.research import RIGHTS_KNOWN_PROVIDERS


def run(coro):
    return asyncio.run(coro)


def _geocode(lat, lon):
    async def fake(place, client=None, near=None):
        return {"lat": lat, "lon": lon, "display_name": place, "granularity": "city"}

    return fake


def test_a_square_around_a_point_is_as_wide_on_the_ground_as_asked():
    x0, y0, x1, y1 = OD.mercator_box(46.7325, -117.0002, 400)
    assert math.isclose(x1 - x0, y1 - y0, rel_tol=1e-6)
    assert math.isclose((x1 - x0) * math.cos(math.radians(46.7325)), 400, rel_tol=1e-3)
    assert math.isclose((x0 + x1) / 2, -117.0002 * 20037508.342789244 / 180, rel_tol=1e-9)


def test_an_aerial_view_is_offered_at_each_configured_scale_with_the_usgs_credit(monkeypatch):
    monkeypatch.setattr(maps, "geocode", _geocode(46.7325, -117.0002))
    cfg = ai_config.visual_search.open_data_settings().usgs_naip
    out = run(OD.UsgsNaipProvider(None).search_for({"query": "Moscow Idaho", "entity_name": "Moscow"}, 10))
    assert len(out) == len(cfg.view_meters)
    assert len({c.url for c in out}) == len(out)
    c = out[0]
    assert c.provider == "usgs_naip" and c.url.startswith(cfg.export_url)
    assert f"size={cfg.size_px},{cfg.size_px}" in c.url and "f=image" in c.url
    assert c.license == cfg.license and c.credit == cfg.credit
    assert R.classify(c.provider, c.license, c.url)[0] == "public_domain"


def test_a_place_outside_the_united_states_gets_no_aerial_view(monkeypatch):
    monkeypatch.setattr(maps, "geocode", _geocode(52.52, 13.405))
    assert run(OD.UsgsNaipProvider(None).search_for({"query": "Berlin"}, 10)) == []


def test_a_place_the_geocoder_does_not_know_gets_none(monkeypatch):
    async def none(place, client=None, near=None):
        return None

    monkeypatch.setattr(maps, "geocode", none)
    assert run(OD.UsgsNaipProvider(None).search_for({"query": "Nowhere"}, 10)) == []


def test_aerial_views_are_only_for_places_buildings_and_landscapes():
    assert set(OD.UsgsNaipProvider.entity_types) == {"place", "building", "landscape"}
    assert set(OD.NasaProvider.entity_types) == {"place", "landscape"}


def _nasa_client(items, files, asked):
    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"collection": {"items": items}})
        nasa_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"collection": {"items": [{"href": h} for h in files[nasa_id]]}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _item(nasa_id, title="Palouse from orbit", description="Farmland near Moscow, Idaho.", **data):
    return {"data": [{"nasa_id": nasa_id, "title": title, "description": description,
                      "date_created": "2019-07-01T00:00:00Z", "center": "JSC", **data}]}


def test_nasa_images_are_found_by_text_and_the_large_file_is_taken():
    async def go():
        asked = []
        async with _nasa_client([_item("PIA1")], {"PIA1": [
                "https://images-assets.nasa.gov/image/PIA1/PIA1~orig.tif",
                "https://images-assets.nasa.gov/image/PIA1/PIA1~large.jpg",
                "https://images-assets.nasa.gov/image/PIA1/PIA1~thumb.jpg"]}, asked) as client:
            return await OD.NasaProvider(client).search("Palouse Idaho", 5), asked

    out, asked = run(go())
    [c] = out
    assert c.url.endswith("PIA1~large.jpg") and c.provider == "nasa" and c.credit == ai_config.visual_search.open_data_settings().nasa.credit
    assert c.page_url == "https://images.nasa.gov/details/PIA1" and c.date.startswith("2019")
    assert R.classify(c.provider, c.license, c.url)[0] == "public_domain"
    assert any("media_type=image" in u for u in asked)


def test_a_nasa_item_marked_as_third_party_copyright_is_skipped():
    items = [_item("A1", description="Photo (c) Jane Doe, used with permission."),
             _item("A2", secondary_creator="© Example Agency"), _item("A3")]
    files = {k: [f"https://images-assets.nasa.gov/image/{k}/{k}~large.jpg"] for k in ("A1", "A2", "A3")}

    async def go():
        async with _nasa_client(items, files, []) as client:
            return await OD.NasaProvider(client).search("Idaho", 5)

    assert [c.page_url.rsplit("/", 1)[-1] for c in run(go())] == ["A3"]


def test_the_open_data_providers_are_configured_and_known_for_their_rights():
    providers = json.loads(Path("config/parameters/visuals.json").read_text())["visual_search"]["providers"]
    assert {"usgs_naip", "nasa"} <= set(providers) and "mapillary" not in " ".join(providers).lower()
    assert {"usgs_naip", "nasa"} <= set(RIGHTS_KNOWN_PROVIDERS)


def test_a_public_domain_picture_of_a_provider_that_asks_for_a_credit_is_credited():
    class A:
        rights_status, provider, credit = "public_domain", "usgs_naip", "USGS, USDA, The National Map: Orthoimagery"

    assert R.credit_due(A)
    A.provider = "wikimedia"
    assert not R.credit_due(A)
    A.rights_status, A.provider = "creative_commons", "wikimedia"
    assert R.credit_due(A)


def test_mapillary_is_not_used_and_the_terms_say_why():
    text = Path("docs/open_sources_terms.md").read_text()
    assert "Mapillary" in text and "not implemented" in text and "Section 12" in text
