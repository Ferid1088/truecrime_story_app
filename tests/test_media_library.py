"""Media library (Master task §8, §9.5): relevance tiers, research with
entity/tier/rights fields, muted footage clips, clip shots in the render
engine and map zoom chains that follow the place. No network: every HTTP
call goes through an httpx.MockTransport; video is synthesized by FFmpeg."""
import asyncio
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.db.models import Case, Source, VisualAsset, VisualPlan
from app.documentary.visuals import footage as FT
from app.documentary.visuals import maps as MAPS
from app.documentary.visuals import tiers as T

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg required")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _case(db) -> Case:
    title = f"Media {uuid.uuid4().hex[:6]}"
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-"), language="en")
    db.add(case)
    db.commit()
    return case


def _jpeg(seed: int, size=(800, 600)) -> bytes:
    """A distinct picture per seed (different perceptual hashes)."""
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (6, 8, 3), dtype=np.uint8)
    img = Image.fromarray(small).resize(size, Image.BILINEAR)
    import io

    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _video(path: Path, seconds=4.0, size="640x480", audio=True, vf=None) -> Path:
    """Synthetic footage: lavfi testsrc (+ a loud 1 kHz tone)."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
           "-i", f"testsrc=size={size}:rate=30:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=1000:duration={seconds}"]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-shortest"] if audio else []
    cmd += [str(path)]
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    return path


def _streams(path: Path) -> list[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                         check=True).stdout
    return out.split()


class FakeWeb:
    """Routes for httpx.MockTransport; records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for match, respond in self.routes:
            if match(request):
                return respond(request)
        return httpx.Response(404, text="not found")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self), follow_redirects=True)

    def hosts(self) -> set[str]:
        return {r.url.host for r in self.requests}

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def _host(h, path_prefix=""):
    return lambda r: r.url.host == h and r.url.path.startswith(path_prefix)


def _search(word):
    return lambda r: word in (r.url.params.get("gsrsearch") or "")


@pytest.fixture
def media_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.visual_search, "min_request_interval_s", 0.0)
    monkeypatch.setattr(ai_config.search_engine, "searxng_url", "http://searx.test")
    monkeypatch.delenv(ai_config.search_engine.searxng_url_env, raising=False)
    monkeypatch.setattr(MAPS, "cache_dir", lambda: tmp_path / "map_cache")
    (tmp_path / "map_cache").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# tiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("args, tier, label", [
    # a case-source photo of the scene: real case evidence
    (("source_page", "evidence", None, "source"), 1, "exact_case"),
    # the victim's portrait from a case article: the exact person
    (("source_page", "evidence", "person", "exact"), 2, "exact_entity"),
    (("wikimedia", "context", "person", "exact"), 2, "exact_entity"),
    # the church exterior found by an exact query: the exact building
    (("wikimedia", "context", "building", "exact"), 3, "exact_place"),
    (("internet_archive", "context", "place", "exact"), 3, "exact_place"),
    # a forest found by a context query: contextually accurate
    (("wikimedia", "context", "place", "context"), 4, "contextual"),
    (("wikimedia_video", "context", "building", "context"), 4, "contextual"),
    # a generic illustration: last resort
    (("searxng_images", "illustration", "place", "exact"), 5, "generic"),
    # generated material: maps show the exact place, cards quote the case
    (("generated", "context", "place", None), 3, "exact_place"),
    (("generated", "evidence", "document", None), 1, "exact_case"),
])
def test_provisional_tiers(args, tier, label):
    assert T.provisional_tier(*args) == tier
    assert T.tier_label(tier) == label
    assert T.provisional_why(*args)[1]  # every tier is explained


def _found(provider, role, entity_type, kind):
    tier = T.provisional_tier(provider, role, entity_type, kind)
    return VisualAsset(provider=provider, asset_role=role, entity_type=entity_type,
                       relevance_tier=tier)


@pytest.mark.parametrize("found, verdict, tier", [
    # a case-source photo that really shows the scene stays case evidence
    (("source_page", "evidence", None, "source"),
     {"matches_claim": "yes", "role": "evidence", "subject_type": "building"}, 1),
    # the victim's portrait, confirmed: the exact person
    (("source_page", "evidence", "person", "exact"),
     {"matches_claim": "yes", "role": "evidence", "subject_type": "person"}, 2),
    # the church found by an exact query, confirmed: the exact building
    (("wikimedia", "context", "building", "exact"),
     {"matches_claim": "yes", "role": "context", "subject_type": "building"}, 3),
    # ... but it shows another church: contextual at best
    (("wikimedia", "context", "building", "exact"),
     {"matches_claim": "no", "role": "context", "subject_type": "building"}, 4),
    # a forest from a context query stays contextual even when it matches
    (("wikimedia", "context", "place", "context"),
     {"matches_claim": "yes", "role": "context", "subject_type": "landscape"}, 4),
    # the verifier calls it an illustration: generic
    (("wikimedia", "context", "place", "exact"),
     {"matches_claim": "yes", "role": "illustration", "subject_type": "landscape"}, 5),
    # wrong subject and wrong period: generic
    (("source_page", "evidence", None, "source"),
     {"matches_claim": "no", "role": "evidence", "period_ok": "no"}, 5),
    # unsure: the provisional tier is kept for a human to review
    (("source_page", "evidence", "person", "exact"),
     {"matches_claim": "unclear", "role": "evidence", "subject_type": "person"}, 2),
    # real footage of the event found by an exact query: case footage
    (("internet_archive", "context", "event", "exact"),
     {"matches_claim": "yes", "role": "evidence", "subject_type": "event"}, 1),
])
def test_verified_tiers(found, verdict, tier):
    asset = _found(*found)
    assert T.verified_tier(asset, verdict) == tier
    assert T.verified_why(asset, verdict)[1]


def test_verified_tier_starts_from_the_search_claim_on_a_recheck():
    asset = _found("wikimedia", "context", "building", "exact")
    asset.relevance_tier = 4  # an earlier verdict demoted it
    assert T.verified_tier(asset, {"matches_claim": "yes", "role": "context",
                                   "subject_type": "building"}, provisional=3) == 3


# ---------------------------------------------------------------------------
# research: library fields, context queries never hit source pages
# ---------------------------------------------------------------------------


def _research_web():
    def commons(r):
        q = r.url.params.get("gsrsearch") or ""
        if "Marien" in q:
            name, lic, img = "St Marien Stendal.jpg", "CC BY-SA 4.0", "church.jpg"
        elif "forest" in q:
            name, lic, img = "Pine forest Altmark.jpg", "Public domain", "forest.jpg"
        else:
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"query": {"pages": {"1": {
            "index": 1, "title": f"File:{name}",
            "imageinfo": [{"mime": "image/jpeg", "width": 800, "height": 600,
                           "url": f"https://upload.wikimedia.org/{img}",
                           "thumburl": f"https://upload.wikimedia.org/{img}",
                           "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{img}",
                           "extmetadata": {"LicenseShortName": {"value": lic},
                                           "Artist": {"value": "<a>Jane Doe</a>"},
                                           "ImageDescription": {"value": name}}}]}}}})

    def searx(r):
        return httpx.Response(200, json={"results": [
            {"img_src": "https://pics.example/marien.jpg", "url": "https://pics.example/p",
             "title": "Marienkirche"}]})

    page = ('<html><head><meta property="og:image" content="/img/scene.jpg">'
            '<meta property="og:title" content="Body found near the church"></head></html>')
    images = {"/img/scene.jpg": 1, "/church.jpg": 2, "/forest.jpg": 3, "/marien.jpg": 4,
              "/forest2.jpg": 5}
    return FakeWeb([
        (lambda r: r.url.host == "commons.wikimedia.org" and r.url.path == "/w/api.php", commons),
        (_host("searx.test"), searx),
        (_host("news.example", "/story"), lambda r: httpx.Response(
            200, text=page, headers={"content-type": "text/html"})),
        (lambda r: r.url.path in images, lambda r: httpx.Response(
            200, content=_jpeg(images[r.url.path]), headers={"content-type": "image/jpeg"})),
    ])


def test_research_stores_library_fields_and_context_queries_skip_source_pages(
        db_session, media_env, monkeypatch):
    from app.documentary.visuals.research import VisualResearchAgent

    monkeypatch.setattr(ai_config.footage, "enabled", False)
    case = _case(db_session)
    db_session.add(Source(case_id=case.id, title="Body found", url="https://news.example/story",
                          source_type="news", publisher="News Example", reliability_score=0.9))
    db_session.commit()
    web = _research_web()
    queries = [
        {"query": "St. Marien Stendal", "entity": "st_marien", "entities": ["st_marien"],
         "entity_type": "building", "kind": "exact"},
        {"query": "pine forest Altmark", "entity": "forest_area", "entity_type": "place",
         "kind": "context"},
    ]

    async def go(qs, found_during="research"):
        async with web.client() as client:
            return await VisualResearchAgent(client).run(db_session, case, qs,
                                                         found_during=found_during)

    stats = asyncio.run(go(queries))
    assert stats["added"] == 4, stats
    assets = {a.provider + ":" + (a.found_for or ""): a for a in
              db_session.query(VisualAsset).filter_by(case_id=case.id).all()}
    scene = next(a for k, a in assets.items() if k.startswith("source_page"))
    assert (scene.relevance_tier, scene.case_relevance) == (1, "exact_case")
    assert scene.rights_status == "editorial_review_required"
    assert scene.source_name == "News Example" and scene.found_during == "research"
    church = assets["wikimedia:St. Marien Stendal"]
    assert (church.relevance_tier, church.case_relevance) == (3, "exact_place")
    assert (church.entity_key, church.entity_type) == ("st_marien", "building")
    assert church.license == "CC BY-SA 4.0" and church.rights_status == "creative_commons"
    assert church.source_url == "https://upload.wikimedia.org/church.jpg"
    assert church.source_name == "Wikimedia Commons" and church.created_at is not None
    assert church.credit == "Jane Doe / Wikimedia Commons"
    web_find = assets["searxng_images:St. Marien Stendal"]
    assert web_find.rights_status == "unknown" and web_find.relevance_tier == 3
    forest = assets["wikimedia:pine forest Altmark"]
    assert (forest.relevance_tier, forest.case_relevance) == (4, "contextual")
    assert forest.rights_status == "public_domain" and forest.entity_type == "place"
    # the context query asked only the rights-known library
    searx_queries = [r.url.params.get("q") for r in web.requests if r.url.host == "searx.test"]
    assert searx_queries == ["St. Marien Stendal"]

    # production-time search with a context query: no source page, no web search
    before = len(web.requests)
    web.routes.insert(0, (_search("heath"), lambda r: httpx.Response(200, json={"query": {
        "pages": {"1": {"index": 1, "title": "File:Heath.jpg", "imageinfo": [{
            "mime": "image/jpeg", "width": 800, "height": 600,
            "url": "https://upload.wikimedia.org/forest2.jpg",
            "thumburl": "https://upload.wikimedia.org/forest2.jpg",
            "extmetadata": {"LicenseShortName": {"value": "CC0"}}}]}}}})))
    stats = asyncio.run(go([{"query": "heath Altmark", "entity": "forest_area",
                             "entity_type": "place", "kind": "context"}], "production_search"))
    new = web.requests[before:]
    assert {r.url.host for r in new} == {"commons.wikimedia.org", "upload.wikimedia.org"}
    heath = db_session.query(VisualAsset).filter_by(case_id=case.id, found_for="heath Altmark").one()
    assert heath.found_during == "production_search" and heath.relevance_tier == 4
    assert heath.rights_status == "public_domain"


def test_uploads_are_case_material(db_session, media_env):
    from app.documentary.visuals.research import add_uploaded_image

    case = _case(db_session)
    a = add_uploaded_image(db_session, case, _jpeg(9), "family.jpg", asset_role="evidence")
    assert (a.relevance_tier, a.case_relevance, a.found_during) == (1, "exact_case", "upload")


# ---------------------------------------------------------------------------
# footage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url, name, status", [
    ("http://creativecommons.org/licenses/publicdomain/", "Public domain", "public_domain"),
    ("https://creativecommons.org/publicdomain/zero/1.0/", "CC0 1.0", "public_domain"),
    ("https://creativecommons.org/publicdomain/mark/1.0/", "Public Domain Mark 1.0", "public_domain"),
    ("http://creativecommons.org/licenses/by/4.0/", "CC BY 4.0", "creative_commons"),
    ("http://creativecommons.org/licenses/by-sa/3.0/", "CC BY-SA 3.0", "creative_commons"),
    ("http://creativecommons.org/licenses/by-nc-nd/3.0/", "CC BY-NC-ND 3.0", "permission_required"),
    (None, None, "unknown"),
])
def test_internet_archive_license_urls(url, name, status):
    from app.documentary.visuals import rights as R

    assert FT.license_from_url(url) == name
    assert R.classify("internet_archive", name, "https://archive.org/details/x")[0] == status


def test_footage_pure_helpers():
    assert FT.parse_length("1166.13") == pytest.approx(1166.13)
    assert FT.parse_length("00:19:26") == 1166 and FT.parse_length("19:26") == 1166
    assert FT.parse_length(None) is None
    # short files whole; long ones from 10 % in, clamped to fit
    assert FT.clip_window(8.0, 24.0) == (0.0, 8.0)
    assert FT.clip_window(100.0, 24.0) == (10.0, 24.0)
    assert FT.clip_window(25.0, 24.0) == (1.0, 24.0)
    files = [{"name": "a.ogv", "size": "1000", "height": "480", "length": "60"},
             {"name": "a_512kb.mp4", "size": "900", "height": "240", "length": "60"},
             {"name": "a.mp4", "size": "5000", "height": "480", "length": "60"},
             {"name": "a_huge.mp4", "size": str(10 ** 10), "height": "1080"},
             {"name": "a.txt", "size": "10"}]
    assert FT.pick_ia_file(files, 360, 10 ** 6, 1800)["name"] == "a.mp4"
    assert FT.pick_ia_file(files, 360, 2000, 1800)["name"] == "a.ogv"
    assert FT.pick_ia_file(files, 360, 10 ** 6, 30) is None  # source too long
    info = {"url": "https://u/orig.webm", "height": 1080, "size": 10 ** 10, "derivatives": [
        {"src": "https://u/orig.webm", "height": 1080},  # the original (no transcode key)
        {"src": "https://u/t/a.240p.vp9.webm", "transcodekey": "240p.vp9.webm", "height": 240},
        {"src": "https://u/t/a.360p.mpeg4.mov", "transcodekey": "360p.mpeg4.mov", "height": 360},
        {"src": "https://u/t/a.720p.vp9.webm", "transcodekey": "720p.vp9.webm", "height": 720},
        {"src": "https://u/t/a.1080p.vp9.webm", "transcodekey": "1080p.vp9.webm", "height": 1080}]}
    assert FT.pick_derivative(info, 360, 10 ** 8)[0] == "https://u/t/a.720p.vp9.webm"
    assert FT.pick_derivative({"url": "https://u/x.webm", "height": 480, "size": 10},
                              360, 10 ** 8)[0] == "https://u/x.webm"
    assert FT.pick_derivative({"url": "https://u/x.webm", "height": 240, "size": 10},
                              360, 10 ** 8) is None
    # footage is searched for flagged queries, else the top entity of each
    qs = [{"query": "a1", "entity": "a"}, {"query": "a2", "entity": "a"},
          {"query": "ctx", "entity": "c", "kind": "context"}, {"query": "b1", "entity": "b"}]
    assert [q["query"] for q in FT.footage_queries(qs, 8)] == ["a1", "b1", "ctx"]
    assert [q["query"] for q in FT.footage_queries(qs, 1)] == ["a1"]
    qs[3]["footage"] = True
    assert [q["query"] for q in FT.footage_queries(qs, 8)] == ["b1"]
    assert FT.footage_queries(qs, 0) == []


def test_footage_download_limits(tmp_path):
    web = FakeWeb([
        (_host("big.example"), lambda r: httpx.Response(
            200, content=b"x" * 5000, headers={"content-type": "video/mp4"})),
        (_host("page.example"), lambda r: httpx.Response(
            200, text="<html></html>", headers={"content-type": "text/html"})),
    ])

    async def go(url):
        async with web.client() as client:
            return await FT.download_video(url, client, tmp_path / "x.bin", 1000)

    with pytest.raises(FT.FootageRejected, match="too large"):
        asyncio.run(go("https://big.example/v.mp4"))
    with pytest.raises(FT.FootageRejected, match="not a video"):
        asyncio.run(go("https://page.example/v.mp4"))


def _footage_web(wiki_bytes: bytes, ia_bytes: bytes) -> FakeWeb:
    def commons(r):
        def page(i, title, lic, src, height=480, page_url=None):
            return {"index": i, "title": f"File:{title}", "videoinfo": [{
                "mime": "video/webm", "width": 640, "height": height, "duration": 4.0,
                "size": len(wiki_bytes),
                "url": f"https://upload.wikimedia.org/{title}?utm_source=x",
                "descriptionurl": page_url or f"https://commons.wikimedia.org/wiki/File:{title}",
                "extmetadata": {"LicenseShortName": {"value": lic},
                                "Artist": {"value": "Max Muster"}},
                "derivatives": [{"src": src, "transcodekey": f"{height}p.vp9.webm",
                                 "width": 640, "height": height}]}]}
        return httpx.Response(200, json={"query": {"pages": {
            "1": page(1, "Stendal Marienkirche.webm", "CC BY-SA 4.0",
                      "https://upload.wikimedia.org/transcoded/marien.480p.vp9.webm"),
            # a stock library behind the file: never requested
            "2": page(2, "Stock.webm", "CC BY 4.0", "https://media.gettyimages.com/clip.webm",
                      page_url="https://www.gettyimages.com/detail/123"),
            # non-commercial: needs permission, never downloaded
            "3": page(3, "NC.webm", "CC BY-NC 2.0",
                      "https://upload.wikimedia.org/transcoded/nc.480p.vp9.webm"),
            # too small
            "4": page(4, "Tiny.webm", "CC0", "https://upload.wikimedia.org/transcoded/tiny.webm",
                      height=240),
        }}})

    def ia_search(r):
        assert r.url.params["q"] == "(St. Marien Stendal) AND mediatype:movies AND licenseurl:*" \
            or r.url.params["q"].startswith("(Stendal) AND mediatype:movies")
        assert r.url.params.get_list("fl[]") == ["identifier", "title", "licenseurl", "date",
                                                 "creator"]
        return httpx.Response(200, json={"response": {"docs": [
            {"identifier": "stendal_1990", "title": "Stendal 1990", "creator": "Archive Films",
             "licenseurl": "http://creativecommons.org/licenses/by/4.0/", "date": "1990-05-01T00:00:00Z"},
            {"identifier": "youtube-abc123", "title": "A mirror without a license"},
        ]}})

    def ia_meta(r):
        assert r.url.path == "/metadata/stendal_1990"
        return httpx.Response(200, json={"metadata": {"description": "Streets of Stendal"},
                                         "files": [
            {"name": "stendal_1990_huge.mp4", "size": str(10 ** 10), "height": "1080"},
            {"name": "stendal_1990.mp4", "size": str(len(ia_bytes)), "length": "4.00",
             "height": "480", "width": "640"}]})

    return FakeWeb([
        (lambda r: r.url.host == "commons.wikimedia.org" and _search("filetype:video")(r), commons),
        (_host("archive.org", "/advancedsearch.php"), ia_search),
        (_host("archive.org", "/metadata/"), ia_meta),
        (_host("archive.org", "/download/stendal_1990/stendal_1990.mp4"), lambda r: httpx.Response(
            200, content=ia_bytes, headers={"content-type": "video/mp4"})),
        (_host("upload.wikimedia.org", "/transcoded/marien"), lambda r: httpx.Response(
            200, content=wiki_bytes, headers={"content-type": "video/webm"})),
    ])


@needs_ffmpeg
def test_footage_is_stored_muted_trimmed_with_keyframe_and_rights(db_session, media_env,
                                                                   monkeypatch, tmp_path):
    from app.documentary import storage
    from app.documentary.visuals.research import VisualResearchAgent

    monkeypatch.setattr(ai_config.visual_search, "providers", [])  # footage only
    monkeypatch.setattr(ai_config.footage, "clip_seconds", 2.5)
    monkeypatch.setattr(ai_config.footage, "max_candidates_per_query", 5)
    wiki = _video(tmp_path / "src" / "wiki.mp4").read_bytes()
    ia = _video(tmp_path / "src" / "ia.mp4", vf="hflip").read_bytes()
    web = _footage_web(wiki, ia)
    case = _case(db_session)

    async def go():
        async with web.client() as client:
            return await VisualResearchAgent(client).run(db_session, case, [
                {"query": "St. Marien Stendal", "entity": "st_marien", "entity_type": "building",
                 "kind": "exact", "footage": True}])

    stats = asyncio.run(go())
    fs = stats["footage"]
    assert fs["added"] == 2 and stats["added"] == 2, fs
    assert fs["rights_skipped"] == 3  # stock library, NC license, unlicensed mirror
    # blocked domains, refused rights and oversized files are never requested
    assert not any("gettyimages" in u for u in web.urls())
    assert not any("nc.480p" in u or "youtube-abc123" in u or "huge" in u for u in web.urls())
    clips = {a.provider: a for a in db_session.query(VisualAsset).filter_by(case_id=case.id)}
    assert set(clips) == {"wikimedia_video", "internet_archive"}
    for a in clips.values():
        path = storage.resolve(a.local_path)
        assert a.asset_type == "video" and path.suffix == ".mp4" and path.exists()
        assert _streams(path) == ["video"]  # muted: no audio stream at all
        probe = FT.probe_video(path)
        assert probe["duration"] <= 2.5 + 0.05 and a.duration_seconds == pytest.approx(2.5, abs=0.05)
        assert (a.clip_start, a.clip_end) == (0.0, a.duration_seconds)
        assert a.width % 2 == 0 and a.height % 2 == 0 and a.height <= 1080
        rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=r_frame_rate", "-of", "csv=p=0", str(path)],
                              capture_output=True, text=True, check=True).stdout.strip()
        assert rate == "25/1"
        thumb = storage.resolve(a.thumbnail_path)
        assert thumb.exists() and Image.open(thumb).format == "JPEG"
        assert a.has_original_audio is False and a.phash
        assert (a.relevance_tier, a.case_relevance) == (3, "exact_place")
        assert (a.entity_key, a.entity_type, a.found_for) == ("st_marien", "building",
                                                              "St. Marien Stendal")
        assert a.found_during == "research" and a.rights_status == "creative_commons"
        spec = json.loads(a.spec_json)
        assert spec["source_had_audio"] is True and spec["source_window"] == [0.4, 2.9]
    w, i = clips["wikimedia_video"], clips["internet_archive"]
    assert w.license == "CC BY-SA 4.0" and w.credit == "Max Muster / Wikimedia Commons"
    assert w.source_url == "https://upload.wikimedia.org/Stendal Marienkirche.webm"
    assert i.license == "CC BY 4.0" and i.credit == "Archive Films / Internet Archive"
    assert i.page_url == "https://archive.org/details/stendal_1990"
    assert i.caption == "Streets of Stendal" and i.date_start == "1990-05-01"
    # no download leftovers
    assert not list(storage.visuals_dir(case.id, "video").glob(".*"))
    # a second run finds the same files: nothing new is downloaded
    n = len(web.requests)
    stats = asyncio.run(go())
    assert stats["footage"]["added"] == 0 and stats["footage"]["duplicates"] == 2
    assert not any("/download/" in u or "/transcoded/" in u for u in web.urls()[n:])


@needs_ffmpeg
def test_footage_respects_the_clip_cap_and_dedupes_by_keyframe(db_session, media_env,
                                                               monkeypatch, tmp_path):
    from app.documentary.visuals.research import VisualResearchAgent

    monkeypatch.setattr(ai_config.visual_search, "providers", [])
    monkeypatch.setattr(ai_config.footage, "clip_seconds", 2.0)
    same = _video(tmp_path / "src" / "same.mp4").read_bytes()
    web = _footage_web(same, same)  # both archives carry the same shot
    case = _case(db_session)

    async def go():
        async with web.client() as client:
            return await VisualResearchAgent(client).run(db_session, case, [
                {"query": "Stendal", "entity": "stendal", "entity_type": "place"}])

    fs = asyncio.run(go())["footage"]
    assert fs["added"] == 1 and fs["duplicates"] == 1, fs
    monkeypatch.setattr(ai_config.footage, "max_clips_per_case", 1)
    case2 = _case(db_session)

    async def go2():
        async with web.client() as client:
            return await VisualResearchAgent(client).run(db_session, case2, [
                {"query": "Stendal", "entity": "stendal", "entity_type": "place"}])

    fs = asyncio.run(go2())["footage"]
    assert fs["added"] == 1 and fs["candidates"] == 1  # stops at the cap


@needs_ffmpeg
def test_video_assets_are_verified_through_their_keyframe(db_session, media_env, monkeypatch,
                                                          tmp_path):
    from app.documentary.visuals.verification import VisualVerificationAgent
    from app.providers.generation.base import GenerationResult

    seen = {}

    class Gen:
        async def generate_structured(self, role, system, user, images=None):
            seen["payload"], seen["images"] = json.loads(user), images
            return ({"depicts": "Crowd at the church", "subject_type": "event",
                     "matches_claim": "yes", "role": "evidence", "period_ok": "yes",
                     "entities": [], "reveals": [], "quality": 0.7, "watermark": False,
                     "graphic_or_sensitive": False, "confidence": 0.9},
                    GenerationResult(text="{}", model="m/vision", provider="fake"))

    monkeypatch.setattr("app.documentary.visuals.verification.get_generation_provider", Gen)
    case = _case(db_session)
    clip = _video(tmp_path / "clip.mp4", audio=False)
    tier = T.provisional_tier("internet_archive", "context", "event", "exact")
    a = VisualAsset(case_id=case.id, asset_code="VIS_CLIP01", asset_type="video",
                    provider="internet_archive", asset_role="context", entity_type="event",
                    local_path=str(clip), thumbnail_path=None, duration_seconds=4.0,
                    clip_start=0.0, clip_end=4.0, relevance_tier=tier,
                    case_relevance=T.tier_label(tier), rights_status="public_domain")
    db_session.add(a)
    db_session.commit()
    asyncio.run(VisualVerificationAgent().verify(db_session, case, a, [], []))
    assert seen["images"][0].startswith("data:image/jpeg;base64,")
    # judged by start, middle and end frames side by side (one picture)
    assert seen["payload"]["media"].startswith("video: start, middle and end frames")
    from PIL import Image
    import base64, io

    sheet = Image.open(io.BytesIO(base64.b64decode(seen["images"][0].split(",", 1)[1])))
    assert sheet.width > 2.5 * sheet.height
    assert Path(a.thumbnail_path).suffix == ".jpg"
    assert a.verification_status == "verified"
    assert (a.relevance_tier, a.case_relevance) == (1, "exact_case")
    v = json.loads(a.verification_json)
    assert v["provisional_tier"] == 2 and v["tier"] == 1 and v["tier_reason"]


# ---------------------------------------------------------------------------
# render: clip shots
# ---------------------------------------------------------------------------


def _ramp(path: Path, seconds=3) -> Path:
    """Each frame's brightness encodes its index (Y = 16 + 2.5 * n)."""
    ramp = (f"color=c=black:s=160x90:r=25:d={seconds},format=yuv420p,"
            "geq=lum='16+N*2.5':cb=128:cr=128")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", ramp,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "1", str(path)], check=True)
    return path


@needs_ffmpeg
def test_clip_frames_follow_the_shot_time_and_hold_at_the_end(tmp_path):
    from app.documentary.render.engine import FrameMaker

    ramp = _ramp(tmp_path / "ramp.mp4")
    shot = {"index": 0, "start": 10.0, "end": 14.0, "kind": "video", "path": str(ramp),
            "clip_start": 0.4, "clip_end": 1.6}
    maker = FrameMaker({"shots": [shot]}, 320, 180)

    def frame_no(t):
        f = maker.shot_frame(shot, t)
        assert f.shape == (180, 320, 3)
        return round(float(f[90, 160].mean()) / (2.5 * 255 / 219))

    # the frame at clip_start + (t - shot start), at 25 fps
    for t in (10.0, 10.2, 10.52, 11.0):
        assert abs(frame_no(t) - round((0.4 + t - 10.0) * 25)) <= 1, t
    # past the clip window the last frame of the window holds
    assert frame_no(12.0) == frame_no(13.9) and abs(frame_no(13.9) - 39) <= 1
    reader = next(iter(maker._clips.values()))
    assert reader.pos <= 40  # sequential reads, no decoding past the window
    # missing or corrupt files: black, no crash
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    for p in (tmp_path / "missing.mp4", bad):
        s = dict(shot, path=str(p), start=20.0)
        assert np.array_equal(maker.shot_frame(s, 20.5), maker.black)
    # an mp4 under an image shot plays as a clip instead of crashing the still loader
    s = {"start": 30.0, "end": 31.0, "kind": "image", "path": str(ramp)}
    assert abs(round(float(maker.shot_frame(s, 30.4)[90, 160].mean()) / (2.5 * 255 / 219)) - 10) <= 1
    maker.release_clips(100.0)
    assert not maker._clips


@needs_ffmpeg
def test_render_plays_clips_muted_under_the_script_audio(tmp_path):
    from app.documentary.render.engine import VideoRenderer

    clip = _video(tmp_path / "clip.mp4", seconds=3, size="320x240", audio=True)  # loud tone
    img = tmp_path / "still.jpg"
    img.write_bytes(_jpeg(3))
    maps = []
    for k in range(4):
        p = tmp_path / f"map{k}.jpg"
        p.write_bytes(_jpeg(20 + k, (600, 338)))
        maps.append(str(p))
    wav = tmp_path / "doc.wav"  # the documentary audio: silence
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=mono", "-t", "6", str(wav)], check=True)
    script = {"language": "en", "duration": 6.0, "audio": {"path": str(wav)},
              "shots": [
                  {"index": 0, "start": 0.0, "end": 2.5, "kind": "video", "path": str(clip),
                   "clip_start": 0.5, "clip_end": 2.0, "motion": "NONE",
                   "transition_in": "FADE_BLACK"},
                  {"index": 1, "start": 2.5, "end": 3.5, "kind": "image", "path": str(img),
                   "width": 800, "height": 600, "motion": "SLOW_PUSH",
                   "transition_in": "CROSSFADE"},
                  {"index": 2, "start": 3.5, "end": 6.0, "kind": "map", "path": maps[-1],
                   "map_paths": maps, "motion": "MAP_ZOOM", "transition_in": "CROSSFADE"}],
              "overlays": [], "subtitles": [{"start": 0.2, "end": 1.5, "text": "A clip."}]}
    out = tmp_path / "out.mp4"
    info = VideoRenderer(320, 180).render(script, out)
    assert info["frames"] == 6 * ai_config.render.fps
    assert _streams(out) == ["video", "audio", "subtitle"]
    vol = subprocess.run(["ffmpeg", "-v", "info", "-i", str(out), "-map", "0:a", "-af",
                          "volumedetect", "-f", "null", "-"], capture_output=True, text=True,
                         check=True).stderr
    max_db = float(vol.split("max_volume:")[1].split("dB")[0])
    assert max_db < -60  # only the (silent) script audio; the clip's tone never plays
    # the clip really moves in the film
    frames = []
    for t in (0.9, 1.6):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(out),
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, check=True).stdout
        frames.append(np.frombuffer(raw, np.uint8).astype(int))
    assert np.abs(frames[0] - frames[1]).mean() > 2


# ---------------------------------------------------------------------------
# maps: contextual zoom
# ---------------------------------------------------------------------------


STENDAL = {"lat": 52.6, "lon": 11.86}


@pytest.mark.parametrize("result, gran", [
    ({"class": "boundary", "type": "administrative", "addresstype": "country"}, "country"),
    ({"class": "boundary", "type": "administrative", "addresstype": "state"}, "state"),
    ({"class": "boundary", "type": "administrative", "addresstype": "town"}, "town"),
    ({"class": "place", "type": "city", "addresstype": "city"}, "city"),
    ({"class": "place", "type": "suburb", "addresstype": "suburb"}, "suburb"),
    ({"class": "amenity", "type": "place_of_worship", "addresstype": "amenity"}, "building"),
    ({"class": "building", "type": "church", "addresstype": "building"}, "building"),
    ({"class": "highway", "type": "residential", "addresstype": "road"}, "road"),
    ({"class": "natural", "type": "wood", "addresstype": "natural"}, "area"),
    ({"class": "landuse", "type": "forest", "addresstype": "landuse"}, "area"),
    ({"class": "boundary", "type": "historic", "addresstype": "historic"}, "area"),
    ({"class": "leisure", "type": "park", "addresstype": "leisure"}, "area"),
    ({}, "unknown"),
])
def test_place_granularity(result, gran):
    assert MAPS.granularity(result) == gran


@pytest.mark.parametrize("info, chain", [
    # a country is one orientation map
    ({**STENDAL, "granularity": "country"}, [5]),
    # a state: country, then the region
    ({**STENDAL, "granularity": "state", "bbox": [50.9, 53.04, 10.56, 13.19]}, [5, 7]),
    # a city: country, region, city
    ({**STENDAL, "granularity": "city"}, [5, 7, 11]),
    # a town whose extent needs a little more zoom
    ({**STENDAL, "granularity": "town", "bbox": [52.4848, 52.7008, 11.5687, 11.9792]}, [5, 7, 12]),
    ({**STENDAL, "granularity": "village"}, [5, 7, 11, 13]),
    # a church: down to street level, never stopping at the country
    ({**STENDAL, "granularity": "building", "bbox": [52.6046, 52.6052, 11.858, 11.8592]},
     [5, 7, 11, 17]),
    ({**STENDAL, "granularity": "road", "bbox": [52.600, 52.602, 11.85, 11.86]}, [5, 7, 11, 16]),
    # a forest: the whole forest fits the frame
    ({"lat": 53.53, "lon": 10.37, "granularity": "area",
      "bbox": [53.4875, 53.5735, 10.2833, 10.4697]}, [5, 7, 12]),
    ({"lat": 50.0, "lon": 8.0, "granularity": "area", "bbox": [49.5, 50.5, 7.3, 8.8]}, [5, 7, 9]),
    # unknown granularity (an old cached geocode): the former default chain
    ({**STENDAL}, [5, 7, 11]),
])
def test_zoom_chain_follows_the_place(info, chain):
    assert MAPS.zoom_chain(info) == chain
    assert len(chain) <= MAPS.MAX_STEPS and chain == sorted(chain)


def test_nearby_second_place_starts_at_city_level():
    church = {"lat": 52.6049, "lon": 11.8586, "granularity": "building"}
    village = {"lat": 52.70, "lon": 11.70, "granularity": "village"}
    town = {**STENDAL, "granularity": "town", "bbox": [52.4848, 52.7008, 11.5687, 11.9792]}
    assert MAPS.zoom_chain(church, previous=STENDAL) == [11, 14, 17]
    assert MAPS.zoom_chain(village, previous=(52.6, 11.86)) == [11, 13]
    assert MAPS.zoom_chain(town, previous={"lat": 52.55, "lon": 11.9}) == [12]
    # far away (Hamburg -> Munich): the full chain again
    assert MAPS.zoom_chain(church, previous={"lat": 48.14, "lon": 11.58}) == [5, 7, 11, 17]


def test_geocode_returns_granularity_and_bbox(media_env, monkeypatch):
    async def fake_get(client, url, params=None):
        class R:
            @staticmethod
            def json():
                return [{"lat": "52.6049", "lon": "11.8586", "class": "amenity",
                         "type": "place_of_worship", "addresstype": "amenity", "place_rank": 30,
                         "boundingbox": ["52.6046", "52.6052", "11.8580", "11.8592"],
                         "display_name": "St. Marien, Stendal, Sachsen-Anhalt, Deutschland"}]
        return R()

    monkeypatch.setattr(MAPS, "_polite_get", fake_get)
    geo = asyncio.run(MAPS.geocode("St. Marien, Stendal"))
    assert geo["granularity"] == "building" and geo["bbox"] == [52.6046, 52.6052, 11.858, 11.8592]
    assert (geo["addresstype"], geo["osm_class"], geo["osm_type"]) == (
        "amenity", "amenity", "place_of_worship")
    assert MAPS.zoom_chain(geo) == [5, 7, 11, 17]


def _fake_maps(monkeypatch, places: dict[str, dict]):
    calls = []

    async def fake_geocode(place, client=None, near=None):
        return dict(places[place], display_name=place) if place in places else None

    async def fake_render(lat, lon, zoom, out, W=2400, H=1350, client=None):
        calls.append(zoom)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(_jpeg(zoom, (W // 8, H // 8)))
        return {"path": str(out), "marker": [W // 16, H // 16]}

    monkeypatch.setattr("app.documentary.visuals.generated.MAPS.geocode", fake_geocode)
    monkeypatch.setattr("app.documentary.visuals.generated.MAPS.render_map", fake_render)
    return calls


PLACES = {
    "Stendal, Germany": {**STENDAL, "granularity": "town",
                         "bbox": [52.4848, 52.7008, 11.5687, 11.9792]},
    "St. Marien, Stendal": {"lat": 52.6049, "lon": 11.8586, "granularity": "building",
                            "bbox": [52.6046, 52.6052, 11.858, 11.8592]},
}


def test_ensure_map_creates_one_asset_per_zoom(db_session, media_env, monkeypatch):
    from app.documentary.visuals.generated import ensure_map

    renders = _fake_maps(monkeypatch, PLACES)
    case = _case(db_session)
    codes = asyncio.run(ensure_map(db_session, case, "St. Marien, Stendal"))
    assert len(codes) == 4 and renders == [5, 7, 11, 17]
    assets = [db_session.query(VisualAsset).filter_by(asset_code=c).one() for c in codes]
    for a, z in zip(assets, [5, 7, 11, 17]):
        spec = json.loads(a.spec_json)
        assert spec["zoom"] == z and spec["zooms"] == [5, 7, 11, 17]
        assert spec["granularity"] == "building" and spec["bbox"] == PLACES["St. Marien, Stendal"]["bbox"]
        assert (a.relevance_tier, a.case_relevance, a.entity_type) == (3, "exact_place", "place")
        assert a.asset_type == "map" and a.found_during == "generated"
    # cached: the same assets, nothing rendered again
    assert asyncio.run(ensure_map(db_session, case, "St. Marien, Stendal")) == codes
    assert len(renders) == 4
    # nearby previous place: starts at city level, reusing the zooms it shares
    near = asyncio.run(ensure_map(db_session, case, "St. Marien, Stendal", previous=STENDAL))
    assert len(near) == 3 and near[0] == codes[2] and near[2] == codes[3]
    assert near[1] not in codes
    assert renders == [5, 7, 11, 17, 14]
    assert asyncio.run(ensure_map(db_session, case, "Nowhere")) == []


def test_materialize_passes_the_previous_map_place(db_session, media_env, monkeypatch):
    from app.documentary.visuals.generated import materialize

    _fake_maps(monkeypatch, PLACES)
    case = _case(db_session)
    plan = {"beats": [
        {"beat_id": "B01", "shots": [{"command": "NEW_IMAGE", "asset_id": "VIS_1"}]},
        {"beat_id": "B02", "shots": [{"command": "SHOW_MAP"}]},
        {"beat_id": "B03", "shots": [{"command": "SHOW_MAP"}]},
    ]}
    reqs = {"beats": [{"beat_id": "B02", "map_place": "Stendal, Germany"},
                      {"beat_id": "B03", "map_place": "St. Marien, Stendal"}]}
    row = VisualPlan(case_id=case.id, blueprint_id=0, plan_json=json.dumps(plan),
                     requirements_json=json.dumps(reqs))
    stats = asyncio.run(materialize(db_session, case, row))
    assert stats["maps"] == 2
    shots = {b["beat_id"]: b["shots"][0] for b in json.loads(row.plan_json)["beats"]}
    first, second = shots["B02"]["map_info"], shots["B03"]["map_info"]
    assert first["zooms"] == [5, 7, 12] and first["after_place"] is None
    assert second["zooms"] == [11, 14, 17] and second["starts_at_city"] is True
    assert second["after_place"] == "Stendal, Germany" and second["granularity"] == "building"
    assert len(shots["B03"]["map_assets"]) == 3


def test_image_search_results_on_news_pages_are_editorial_material():
    from app.documentary.visuals import rights as R

    for page in ("https://www.wdtn.com/news/ashley-flynn/x", "https://dayton247now.com/news/local/x",
                 "https://www.foxnews.com/us/x"):
        assert R.classify("searxng_images", None, "https://cdn.example/a.jpg", page)[0] == \
            "editorial_review_required"
    for page in ("https://www.pinterest.com/pin/1/", "https://shop.example/product/flynn/"):
        assert R.classify("searxng_images", None, "https://cdn.example/a.jpg", page)[0] == "unknown"
    # never allowed in a publish render without a human decision
    assert not R.allowed("editorial_review_required", "publish")


def test_long_commons_queries_fall_back_to_their_core_words():
    from app.documentary.visuals.research import core_query

    assert core_query("police drone and K9 search residential property night") == \
        "police drone K9"
    assert core_query("Tipp City Ohio") is None
    assert core_query("the refrigerator in the garage") is None  # 2 content words


def test_case_region_is_town_and_state():
    from types import SimpleNamespace

    from app.documentary.visuals.gaps import case_region

    assert case_region(SimpleNamespace(location="Tipp City, Ohio, United States"), {}) == \
        "Tipp City Ohio"
    req = {"beats": [{"beat_id": "B01", "map_place": "Stendal, Saxony-Anhalt, Germany"}]}
    assert case_region(SimpleNamespace(location=None), req) == "Stendal Saxony-Anhalt"
    assert case_region(SimpleNamespace(location=None), {}) is None
    tipp = {"beats": [{"beat_id": "B01", "map_place": "Tipp City, Ohio, United States"}]}
    assert case_region(SimpleNamespace(location="Ohio, United States"), tipp) == \
        "Tipp City Ohio"


def test_a_stand_in_is_verified_as_a_labelled_illustration_never_a_face():
    from types import SimpleNamespace

    from app.documentary.visuals.tiers import verified_why
    from app.documentary.visuals.verification import decide

    dog = {"matches_claim": "stand_in", "subject_type": "object", "role": "illustration",
           "confidence": 0.9}
    assert decide(dog) == ("verified", 0.9, "stand_in")
    face = {**dog, "subject_type": "person"}
    assert decide(face)[0] == "rejected" and decide(face)[2] == "stand_in_person"
    assert decide({**dog, "confidence": 0.1})[0] == "rejected"
    asset = SimpleNamespace(provider="wikimedia", asset_role="context", entity_type="object",
                            relevance_tier=2)
    assert verified_why(asset, dog, 2)[0] == 5
    assert verified_why(asset, {**dog, "role": "context"}, 2)[0] == 4
    # an exact claim that does not match stays rejected
    assert decide({"matches_claim": "no", "confidence": 0.9})[0] == "rejected"
