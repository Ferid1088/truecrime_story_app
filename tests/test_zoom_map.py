"""The zoom-in map: imagery registry, hosts, camera path (pure, no browser)."""

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.core import ai_config as AC
from app.documentary import storage
from app.documentary.visuals import typography as TY
from app.documentary.zoom_map import camera as CAM
from app.documentary.zoom_map import data as DATA
from app.documentary.zoom_map import labels as LAB
from app.documentary.zoom_map import limits as LIM
from app.documentary.zoom_map import registry as REG
from app.documentary.zoom_map import renderer as RENDER
from app.documentary.zoom_map import runtime as RT
from app.documentary.zoom_map import server as SRV
from app.documentary.zoom_map import shot as SHOT

FALL_RIVER = (41.7015, -71.155)
COLOGNE = (50.94, 6.96)
NOWHERE = (-45.0, 170.0)


def raw():
    return json.loads(json.dumps(AC.load_raw_config()["zoom_map"]))


def cfg_with(**changes):
    data = raw()
    data.update(changes)
    return AC.ZoomMapConfig.model_validate(data)


def entry(**changes):
    base = dict(raw()["imagery"][0])
    base.update(changes)
    return base


@pytest.mark.parametrize("field", ["licence", "licence_url", "attribution", "provider"])
def test_an_imagery_entry_without_licence_or_attribution_fails(field):
    with pytest.raises(ValueError, match=field):
        cfg_with(imagery=[entry(**{field: " "})])


@pytest.mark.parametrize("provider,url", [
    ("Google", "https://gibs.earthdata.nasa.gov/x/{z}/{y}/{x}"),
    ("NASA", "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"),
    ("NASA", "https://ecn.t3.tiles.virtualearth.net/tiles/a{q}.jpeg"),
    ("NASA", "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"),
    ("Mapbox", "https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}.jpg"),
    ("NASA", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
])
def test_forbidden_providers_and_hosts_are_rejected(provider, url):
    with pytest.raises(ValueError, match="forbidden"):
        cfg_with(imagery=[entry(provider=provider, url=url)])


def test_an_entry_that_is_not_allowed_is_rejected():
    with pytest.raises(ValueError, match="allowed"):
        cfg_with(imagery=[entry(allowed=False)])


def test_the_shipped_registry_is_valid_and_every_entry_names_licence_and_attribution():
    for e in AC.ai_config.zoom_map.imagery:
        assert e.allowed and e.licence and e.attribution and e.licence_url
        REG.check_host(e.url)


@pytest.mark.parametrize("url", [
    "https://tile.openstreetmap.org/3/1/2.png", "https://maps.googleapis.com/x",
    "https://www.google.com/maps/@41,-71,3a", "https://a.tiles.mapbox.com/x",
    "https://example.org/tile.png"])
def test_the_host_check_blocks_forbidden_and_unknown_hosts(url):
    with pytest.raises(REG.ForbiddenHost):
        REG.check_host(url)


def test_imagery_follows_the_place_and_ends_at_the_best_level():
    def ids(p):
        return [e.id for e in REG.entries_for(*p)]
    assert ids(FALL_RIVER) == ["gibs_blue_marble", "gibs_landsat_weld", "usgs_imagery"]
    assert "nrw_dop" in ids(COLOGNE) and "usgs_imagery" not in ids(COLOGNE)
    assert ids(NOWHERE) == ["gibs_blue_marble", "gibs_landsat_weld"]
    assert REG.best_zoom(*FALL_RIVER) == 16 and REG.best_zoom(*COLOGNE) == 19
    assert REG.best_zoom(*NOWHERE) == 12     # nothing deeper: the vector map takes over


def test_tile_urls_for_xyz_and_wms_entries():
    cfg = AC.ai_config.zoom_map
    gibs = next(e for e in cfg.imagery if e.id == "gibs_landsat_weld")
    assert REG.upstream_url(gibs, 6, 19, 23).endswith("/2000-12-01/GoogleMapsCompatible_Level12/6/23/19.png")
    usgs = next(e for e in cfg.imagery if e.id == "usgs_imagery")
    assert REG.upstream_url(usgs, 16, 19814, 24401).endswith("/tile/16/24401/19814")
    wms = next(e for e in cfg.imagery if e.id == "nrw_dop")
    x, y = REG.tile_xy(*COLOGNE, 15)
    assert "BBOX=774154.222,6610274.206,775377.215,6611497.199" in REG.upstream_url(wms, 15, x, y)


def test_attribution_names_the_map_data_and_each_imagery_source_once():
    entries = REG.entries_for(*FALL_RIVER)
    text = REG.attribution_for(entries + entries)
    assert text[0] == "© OpenStreetMap contributors"
    assert "Imagery: U.S. Geological Survey" in text and len(text) == len(set(text)) == 4


# ---- camera ----------------------------------------------------------------


def plan(variant="zoom_in", **kw):
    return CAM.build_plan(AC.ai_config.zoom_map, variant, FALL_RIVER, kw.pop("granularity", "city"), **kw)


def test_the_zoom_is_fast_at_first_and_slows_down_to_the_street():
    p = plan()
    fr = CAM.frames(p, 25, 1920, 1080)
    zooms = [f.camera.zoom for f in fr]
    assert zooms[0] == pytest.approx(1.9) and zooms[-1] == pytest.approx(16.0)
    assert all(b >= a for a, b in zip(zooms, zooms[1:], strict=False))
    third = len(zooms) // 3
    assert zooms[third] - zooms[0] > 2 * (zooms[-1] - zooms[-1 - third])   # decelerating
    assert fr[-1].camera.lon == pytest.approx(FALL_RIVER[1]) and fr[-1].camera.lat == pytest.approx(FALL_RIVER[0])
    assert len(fr) == round(p.seconds * 25)


def test_the_same_plan_gives_the_same_camera():
    a = CAM.frames(plan(), 25, 1920, 1080)
    b = CAM.frames(plan(), 25, 1920, 1080)
    assert a == b


def test_a_place_that_returns_starts_from_another_level_and_direction():
    first, back = plan(return_index=0), plan(return_index=1)
    assert first.zoom_start != back.zoom_start and first.bearing_start != back.bearing_start


@pytest.mark.parametrize("variant", ["zoom_in", "pitched_3d"])
@pytest.mark.parametrize("return_index", [0, 1, 2])
def test_a_zoom_shot_stays_on_its_aim_point_in_every_frame(variant, return_index):
    fr = CAM.frames(plan(variant, return_index=return_index), 25, 1920, 1080)
    assert all(f.camera.lon == pytest.approx(FALL_RIVER[1]) and f.camera.lat == pytest.approx(FALL_RIVER[0])
               for f in fr)
    assert len({f.camera.bearing for f in fr}) == 1        # the map never turns


@pytest.mark.parametrize("variant", ["zoom_in", "pitched_3d"])
def test_a_zoom_shot_comes_to_rest_and_holds_on_the_aim_point(variant):
    fr = CAM.frames(plan(variant), 25, 1920, 1080)
    tail = fr[int(len(fr) * 0.85):]
    assert max(f.camera.zoom for f in tail) - min(f.camera.zoom for f in tail) < 0.05
    assert max(f.camera.pitch for f in tail) - min(f.camera.pitch for f in tail) < 1.0


def test_the_pitched_variant_tilts_while_it_zooms_and_ends_in_the_street():
    fr = CAM.frames(plan("pitched_3d", granularity="city"), 25, 1920, 1080)
    assert fr[0].camera.pitch == 0.0
    assert fr[-1].camera.pitch == pytest.approx(62.0, abs=0.5) and fr[-1].camera.zoom >= 16.2


def test_the_route_draws_a_line_between_two_places_and_the_camera_follows_it():
    other = (41.8236, -71.4128)    # Providence
    p = plan("route", other=other)
    fr = CAM.frames(p, 25, 1920, 1080)
    assert fr[0].line_progress == 0.0 and fr[-1].line_progress == pytest.approx(1.0)
    lines = [f.line_progress for f in fr]
    assert all(b >= a for a, b in zip(lines, lines[1:], strict=False))
    mid = fr[len(fr) // 2].camera
    assert min(other[1], FALL_RIVER[1]) - 0.2 <= mid.lon <= max(other[1], FALL_RIVER[1]) + 0.2
    assert fr[-1].camera.lon == pytest.approx(FALL_RIVER[1], abs=0.01)
    assert fr[-1].pulse is not None


def test_fit_zoom_shows_both_places():
    z = CAM.fit_zoom((41.7, -71.15), (41.82, -71.41), 1920, 1080)
    assert 9.0 < z < 12.5


# ---- labels, data, limits, cache, hosts ---------------------------------------

FEATURE = {"name": "Köln", "name:en": "Cologne", "name:de": "Köln", "name:fa": "کلن", "name:ar": "كولونيا"}


@pytest.mark.parametrize("language,shown", [("en", "Cologne"), ("de", "Köln"), ("fa", "کلن"), ("ar", "كولونيا")])
def test_a_label_is_one_name_in_the_language_of_the_film(language, shown):
    assert LAB.pick(FEATURE, language) == shown
    expr = LAB.text_field(language)
    assert expr[0] == "coalesce" and expr[1] == ["get", f"name:{language}"]


def test_the_label_chain_falls_back_without_mixing_scripts():
    only_local = {"name": "Fall River"}
    assert LAB.pick(only_local, "fa") == "Fall River"          # no Persian name: the local one, alone
    assert LAB.pick({"name:en": "Rome", "name": "Roma"}, "fa") == "Rome"
    for language in ("en", "de", "fa", "ar"):
        assert isinstance(LAB.pick(FEATURE, language), str) and "(" not in LAB.pick(FEATURE, language)


def test_persian_and_arabic_overlays_sit_right_to_left():
    assert LAB.is_rtl("fa") and LAB.is_rtl("ar") and not LAB.is_rtl("en") and not LAB.is_rtl("de")
    def centre(language, text):
        box = TY.overlay("place", text, language, 1920, 1080).getchannel("A").getbbox()
        return (box[0] + box[2]) / 2
    assert centre("en", "Fall River") < 960 < centre("fa", "فال ریور")


def test_the_extract_box_surrounds_the_places_and_is_reused_or_widened(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    monkeypatch.setattr(DATA, "root", lambda: tmp_path)
    box = DATA.bbox_around([FALL_RIVER], 30.0)
    assert box[0] < FALL_RIVER[1] < box[2] and box[1] < FALL_RIVER[0] < box[3]
    assert (box[3] - box[1]) == pytest.approx(2 * 30.0 / 111.0, abs=0.01)
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        Path(cmd[4]).write_bytes(b"pm")
        return SimpleNamespace(returncode=0, stderr="")

    first = DATA.ensure_extract(8, [FALL_RIVER], runner=runner)
    again = DATA.ensure_extract(8, [FALL_RIVER], runner=runner)            # covered: no second cut
    assert len(calls) == 1 and again["bbox"] == first["bbox"]
    DATA.ensure_extract(8, [(41.9, -71.6)], runner=runner)                    # outside: cut again, wider
    assert len(calls) == 2
    wide = DATA.ensure_extract(8, [FALL_RIVER], runner=runner)
    assert wide["bbox"][0] < first["bbox"][0] and len(calls) == 2
    cmd = calls[0]
    assert cmd[2] == "extract" and cmd[3].startswith("https://build.protomaps.com/") and "--maxzoom=15" in cmd
    assert all("tile.openstreetmap.org" not in part for part in cmd)
    assert (tmp_path / "data/maps/8/region.json").exists()


def test_an_extract_failure_is_reported_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(DATA, "root", lambda: tmp_path)
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    with pytest.raises(DATA.MapDataError, match="extract failed"):
        DATA.ensure_extract(9, [FALL_RIVER], runner=lambda *a, **k: SimpleNamespace(returncode=1, stderr="boom"))


def limits(**lim):
    cfg = cfg_with(limits={**raw()["limits"], **lim})
    return LIM.MapLimits(cfg)


TOLD = {"fall river", "providence"}


def test_a_map_only_shows_a_place_the_story_has_told():
    m = limits()
    assert m.reason_against("fall river", "A1", 100.0, TOLD) is None
    assert m.reason_against("rocky point", "A1", 100.0, TOLD) == "map_place_not_told"


def test_limits_per_film_per_act_and_gap_and_returns():
    m = limits(max_per_film=3, max_per_act=2, min_gap_seconds=90.0, max_returns_per_place=1)
    told = TOLD | {"a", "b", "c"}
    assert m.register("a", "A1", 0.0) == 0
    assert m.reason_against("b", "A1", 30.0, told) == "map_too_close"
    assert m.reason_against("b", "A1", 100.0, told) is None
    m.register("b", "A1", 100.0)
    assert m.reason_against("c", "A1", 300.0, told) == "map_act_limit"
    assert m.reason_against("a", "A2", 300.0, told) is None               # one return is allowed
    assert m.register("a", "A2", 300.0) == 1                              # ... the second show is a return
    assert m.reason_against("a", "A3", 500.0, told) == "map_film_limit"
    m2 = limits(max_per_film=9, max_per_act=9, min_gap_seconds=0.0, max_returns_per_place=1)
    m2.register("a", "A1", 0.0)
    m2.register("a", "A2", 1.0)
    assert m2.reason_against("a", "A3", 2.0, told | {"a"}) == "place_already_mapped"


class Recorder(SRV.Fetcher):
    """Stands in for the network: records every URL, answers imagery with a small picture."""

    def __init__(self):
        self.urls: list[str] = []

    async def get(self, url, timeout=60.0):
        self.urls.append(url)
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), (90, 120, 80)).save(buf, "JPEG")
        return 200, buf.getvalue()


def cache(tmp_path, monkeypatch, fetcher):
    monkeypatch.setattr(DATA, "root", lambda: tmp_path)
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    return SRV.ImageryCache(8, fetcher)


def test_imagery_tiles_are_cached_with_source_date_and_licence(tmp_path, monkeypatch):
    rec = Recorder()
    c = cache(tmp_path, monkeypatch, rec)
    usgs = next(e for e in AC.ai_config.zoom_map.imagery if e.id == "usgs_imagery")
    first = asyncio.run(c.get(usgs, 16, 19814, 24401))
    second = asyncio.run(c.get(usgs, 16, 19814, 24401))
    c.save()
    assert first == second and len(rec.urls) == 1 and (c.fetched, c.from_cache) == (1, 1)
    record = json.loads((tmp_path / "data/maps/8/imagery/manifest.json").read_text())["usgs_imagery/16/19814/24401.jpg"]
    assert record["source"].startswith("https://basemap.nationalmap.gov/") and record["fetched"]
    assert record["licence"] == usgs.licence and record["attribution"] == usgs.attribution


def test_a_tile_the_provider_does_not_have_is_remembered(tmp_path, monkeypatch):
    class Gone(Recorder):
        async def get(self, url, timeout=60.0):
            self.urls.append(url)
            return 404, b""
    rec = Gone()
    c = cache(tmp_path, monkeypatch, rec)
    usgs = next(e for e in AC.ai_config.zoom_map.imagery if e.id == "usgs_imagery")
    assert asyncio.run(c.get(usgs, 17, 1, 1)) is None and asyncio.run(c.get(usgs, 17, 1, 1)) is None
    assert len(rec.urls) == 1


def test_the_tile_server_calls_allowed_hosts_only_and_refuses_the_rest(tmp_path, monkeypatch):
    rec = Recorder()
    c = cache(tmp_path, monkeypatch, rec)
    pm = tmp_path / "region.pmtiles"
    pm.write_bytes(b"x" * 10)
    server = SRV.TileServer(8, pm, c, rec)
    for path in ("/imagery/gibs_blue_marble/3/2/2", "/imagery/usgs_imagery/16/24401/19814",
                 "/imagery/nrw_dop/15/10987/17087", "/glyphs/Noto%20Sans%20Regular/0-255.pbf",
                 "/sprites/dark.json"):
        asyncio.run(server._serve(path, None))
    from urllib.parse import urlparse
    hosts = {urlparse(u).hostname for u in rec.urls}
    assert hosts <= set(AC.ai_config.zoom_map.hosts["allowed"])
    assert not any(bad in h for h in hosts for bad in AC.ai_config.zoom_map.hosts["forbidden"])

    class Route:
        def __init__(self, url):
            self.request = SimpleNamespace(url=url, headers={})
            self.aborted = False
        async def abort(self):
            self.aborted = True
        async def fulfill(self, **kw):
            self.answer = kw
    for url in ("https://tile.openstreetmap.org/3/1/2.png", "https://mt1.google.com/vt?x=1",
                "https://api.mapbox.com/styles/v1/x"):
        r = Route(url)
        asyncio.run(server.handle(r))
        assert r.aborted
    assert len(server.denied) == 3


def test_a_provider_that_is_not_allowed_never_reaches_the_network(tmp_path, monkeypatch):
    rec = Recorder()
    c = cache(tmp_path, monkeypatch, rec)
    bad = AC.ZoomImagery.model_construct(**{**raw()["imagery"][0], "url": "https://example.org/{z}/{y}/{x}.jpg", "params": {}})
    with pytest.raises(REG.ForbiddenHost):
        asyncio.run(c.get(bad, 1, 0, 0))
    assert rec.urls == []


def test_the_render_time_budget_is_checked():
    assert SHOT.within_budget(100.0, 8.0) and not SHOT.within_budget(1000.0, 8.0)


def test_the_variant_follows_the_beat():
    assert SHOT.variant_for({"after": None, "return": 0}) == "zoom_in"
    assert SHOT.variant_for({"after": {"place": "A"}, "return": 0}) == "route"
    assert SHOT.variant_for({"after": {"place": "A"}, "return": 1}) == "pitched_3d"


# ---- the shot in the film ------------------------------------------------------


def fake_asset(language="en", variant="zoom_in", place="Fall River", returns=0, code="VIS_000900"):
    spec = {"key": SHOT.spec_key(language, place, returns, variant, 41.7, -71.15), "kind": "zoom", "variant": variant,
            "seconds": 8.0, "attribution": ["© OpenStreetMap contributors", "Imagery: U.S. Geological Survey"],
            "marker": {"lat": 41.7, "lon": -71.15}, "place": SHOT.usage_place(place, returns)}
    return SimpleNamespace(asset_code=code, asset_type="map", provider="generated", local_path="x.mp4",
                           spec_json=json.dumps(spec))


def test_the_plan_shot_finds_its_zoom_video_in_the_films_language():
    info = {"place": "Fall River", "return": 0, "after": None, "lat": 41.7, "lon": -71.15}
    assets = {"VIS_000900": fake_asset("en"), "VIS_000901": fake_asset("de", code="VIS_000901")}
    got = SHOT.zoom_shot({"map_zoom": info}, assets, "de")["fields"]
    assert got["asset_id"] == "VIS_000901" and got["zoom_video"] and got["clip_end"] == 8.0
    assert SHOT.zoom_shot({"map_zoom": info}, assets, "fa") is None          # not made for fa: the old map is used
    assert SHOT.zoom_shot({}, assets, "en") is None


def test_the_shot_carries_its_attribution_to_the_screen_and_the_credits():
    from app.documentary.production.script import credit_overlays

    fields = SHOT.zoom_shot({"map_zoom": {"place": "Fall River", "return": 0, "after": None, "lat": 41.7, "lon": -71.15}},
                            {"v": fake_asset()}, "en")["fields"]
    shot = {**fields, "start": 30.0, "end": 40.0}
    out = credit_overlays([shot], 100.0)
    assert out and "© OpenStreetMap contributors" in out[0]["text"] and "Imagery: U.S. Geological Survey" in out[0]["text"]


def test_a_returning_place_counts_as_a_new_appearance_for_the_repetition_rule():
    assert SHOT.usage_place("Fall River", 0) == "Fall River"
    assert SHOT.usage_place("Fall River", 1) != SHOT.usage_place("Fall River", 0)


def test_the_old_map_is_found_when_no_zoom_video_exists():
    old = SimpleNamespace(asset_code="VIS_000001", asset_type="map",
                          spec_json=json.dumps({"key": "map|fall river|9", "zoom": 9}))
    close = SimpleNamespace(asset_code="VIS_000002", asset_type="map",
                            spec_json=json.dumps({"key": "map|fall river|14", "zoom": 14}))
    assert [a.asset_code for a in SHOT.fallback_chain({"x": close, "y": old}, "Fall River")] == ["VIS_000001", "VIS_000002"]
    assert SHOT.fallback_chain({"x": close}, "Providence") == []


def test_the_page_config_ends_the_imagery_at_the_best_level_of_the_place():
    cfg = AC.ai_config.zoom_map
    plan = CAM.build_plan(cfg, "zoom_in", NOWHERE, "city")
    conf = RENDER.page_config(cfg, plan, "en", REG.entries_for(*NOWHERE, cfg))
    assert conf["imageryEnd"] == 11          # tile zoom 12 = map zoom 11: beyond it the vector street map takes over
    assert [e["id"] for e in conf["imagery"]] == ["gibs_blue_marble", "gibs_landsat_weld"]
    assert all(e["mapMax"] == e["zoomMax"] - 1 for e in conf["imagery"])
    us = RENDER.page_config(cfg, CAM.build_plan(cfg, "zoom_in", FALL_RIVER, "city"), "en",
                            REG.entries_for(*FALL_RIVER, cfg))
    assert us["imageryEnd"] == 15 and us["imagery"][-1]["bounds"] == [-125.0, 24.0, -66.0, 49.5]
    assert conf["textField"][:2] == ["coalesce", ["get", "name:en"]]


# ---- the real render (headless Chromium, the case's extract) --------------------

EXTRACT = storage.ROOT / "data/maps/8/region.pmtiles"
needs_browser = pytest.mark.skipif(bool(RT.missing()) or not EXTRACT.exists(),
                                   reason="map runtime or the case-8 extract is not installed")


def tiny_plan(variant="zoom_in", **kw):
    cfg = AC.ai_config.zoom_map
    p = CAM.build_plan(cfg, variant, FALL_RIVER, "city", **kw)
    return CAM.ZoomPlan(**{**p.__dict__, "seconds": 0.16})


def render_tiny(tmp_path, name, plan=None, language="en"):
    rec = Recorder()
    res = asyncio.run(RENDER.render(plan or tiny_plan(), 9000, EXTRACT, tmp_path / f"{name}.mp4", language,
                                    320, 180, 25, fetcher=rec))
    return res, rec


@pytest.fixture
def scratch_case():
    """Case 9000 of the map folder: tiles fetched by the test are removed afterwards."""
    import shutil

    yield 9000
    shutil.rmtree(storage.ROOT / "data/maps/9000", ignore_errors=True)


@needs_browser
def test_the_same_input_gives_identical_frames(tmp_path, scratch_case):
    a, _ = render_tiny(tmp_path, "a")
    b, _ = render_tiny(tmp_path, "b")
    assert a.frame_hashes == b.frame_hashes and len(a.frame_hashes) == 4
    assert a.path.exists() and a.file_bytes > 0 and a.attribution[0] == "© OpenStreetMap contributors"


@needs_browser
def test_a_render_calls_allowed_hosts_only(tmp_path, scratch_case):
    res, rec = render_tiny(tmp_path, "c", language="de")
    from urllib.parse import urlparse
    hosts = {urlparse(u).hostname for u in rec.urls}
    assert hosts <= set(AC.ai_config.zoom_map.hosts["allowed"]) and res.denied == []
    assert res.render_seconds < 120 and {i["id"] for i in res.imagery} <= {e.id for e in AC.ai_config.zoom_map.imagery}


def test_dark_pixels_outside_a_composites_swath_become_transparent():
    img = Image.new("RGB", (2, 1))
    img.putpixel((0, 0), (10, 12, 8))        # open water outside the swath
    img.putpixel((1, 0), (60, 90, 40))       # land
    buf = io.BytesIO()
    img.save(buf, "PNG")
    out = Image.open(io.BytesIO(SRV.clear_dark(buf.getvalue(), 24))).convert("RGBA")
    assert out.getpixel((0, 0))[3] == 0 and out.getpixel((1, 0)) == (60, 90, 40, 255)


def test_the_zoom_runs_at_half_the_earlier_speed():
    """Owner 2026-10-20: the zoom is twice as slow as the first version (8 s and 9 s shots)."""
    variants = AC.ai_config.zoom_map.variants
    assert (variants["zoom_in"].seconds, variants["route"].seconds, variants["pitched_3d"].seconds) == (16.0, 18.0, 18.0)
    assert AC.ai_config.zoom_map.limits["place_label_after_seconds"] == 7.0


def test_a_place_name_that_is_not_a_place_of_its_own_falls_back_to_the_street_and_then_the_region(monkeypatch):
    import asyncio

    from app.documentary.visuals import maps

    assert maps.place_fallbacks("Cunningham Court Residence, Ohio, United States") == [
        "Cunningham Court Residence, Ohio, United States", "Cunningham Court, Ohio, United States", "Ohio, United States"]
    assert maps.place_fallbacks("Tipp City") == ["Tipp City"]
    asked = []

    async def lookup(name, client=None, near=None):
        asked.append(name)
        return [{"lat": 39.9, "lon": -84.2, "display_name": name, "granularity": "street"}] if name.startswith("Cunningham Court,") else []

    monkeypatch.setattr(maps, "_lookup", lookup)
    found = asyncio.run(maps.geocode("Cunningham Court Residence, Ohio, United States"))
    assert found["lat"] == 39.9 and asked == ["Cunningham Court Residence, Ohio, United States",
                                              "Cunningham Court, Ohio, United States"]
