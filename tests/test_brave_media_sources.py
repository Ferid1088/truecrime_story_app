"""Part 4b: Brave image search, the original owners named by visual leads,
and Brave video results kept as links only."""

import asyncio
import json

import httpx

from app.core.ai_config import ai_config
from app.db.models import Case, MediaLink, VideoSource, VisualAsset, VisualLead
from app.documentary.visuals import research as RS
from app.documentary.visuals import rights as R
from app.research_engine.search import SearchResult
from app.utils import slugify


def _case(db, title="Brave Media Case"):
    case = Case(canonical_title=title, slug=slugify(title + str(id(db))), language="en")
    db.add(case)
    db.commit()
    return case


class FakeBrave:
    def __init__(self, images=None, videos=None, web=None):
        self.images, self.videos, self.web = images or [], videos or [], web or []
        self.calls = []

    async def search_media(self, kind, query, language, limit=10):
        self.calls.append((kind, query))
        return list(self.images if kind == "images" else self.videos)

    async def search(self, query, language, categories=None, time_range=None, limit=10):
        self.calls.append(("web", query))
        return list(self.web)


def _image_result(url="https://img.example/a.jpg", page="https://news.example/story", width=900):
    return {"type": "image_result", "title": "Crime scene photo", "url": page,
            "source": "news.example", "properties": {"url": url, "width": width, "height": 600}}


def test_brave_image_provider_returns_candidates_with_the_original_image_url():
    provider = RS.BraveImageProvider(FakeBrave(images=[
        _image_result(),
        _image_result("https://img.example/tiny.jpg", width=100)]))
    out = asyncio.run(provider.search("Lizzie Borden house", 5))
    assert [(c.url, c.provider, c.page_url, c.source_name) for c in out] == [
        ("https://img.example/a.jpg", "brave_images", "https://news.example/story", "news.example")]


def test_brave_image_results_default_to_editorial_review_and_stock_libraries_stay_blocked():
    status, _ = R.classify("brave_images", None, "https://img.example/a.jpg", "https://news.example/story")
    assert status == "editorial_review_required"
    blocked, _ = R.classify("brave_images", None, "https://media.gettyimages.com/x.jpg", None)
    assert blocked == "do_not_use"
    lead, _ = R.classify("lead_source", None, "https://court.example/x.jpg", "https://court.example/p")
    assert lead == "editorial_review_required"


def test_video_search_results_are_stored_as_links_only(db_session):
    case = _case(db_session)
    brave = FakeBrave(videos=[
        {"url": "https://www.youtube.com/watch?v=abc", "title": "Trial footage",
         "description": "Court", "video": {"creator": "Court TV", "duration": "12:30"}}])
    added = asyncio.run(RS.store_video_links(db_session, case, brave,
                                             [{"query": "Borden trial footage"}]))
    again = asyncio.run(RS.store_video_links(db_session, case, brave,
                                             [{"query": "Borden trial footage"}]))
    link = db_session.query(MediaLink).one()
    assert (added, again) == (1, 0)                       # the same link is never stored twice
    assert (link.platform, link.creator, link.duration, link.status) == (
        "www.youtube.com", "Court TV", "12:30", "new")
    assert db_session.query(VisualAsset).filter_by(case_id=case.id).count() == 0  # nothing downloaded, no asset made


def test_lead_source_fetches_the_owners_own_page_not_the_youtube_video(db_session):
    case = _case(db_session)
    video = VideoSource(case_id=case.id, video_id="vid00000001", url="https://y/v", title="t")
    db_session.add(video)
    db_session.flush()
    lead = VisualLead(case_id=case.id, video_source_id=video.id, lead_type="court_footage",
                      description_en="Arraignment footage", original_owner="Fall River Superior Court")
    db_session.add(lead)
    db_session.commit()

    def result(url, title):
        return SearchResult(backend="brave", query="q", language="en", rank=1, url=url,
                            canonical_url=url, title=title, snippet=None, domain="d",
                            detected_language="en")

    brave = FakeBrave(web=[
        result("https://www.youtube.com/watch?v=zzz", "Fall River Superior Court video"),
        result("https://unrelated.example/page", "Something else"),
        result("https://fallriver-court.example/archive", "Fall River Superior Court archive")])
    page = '<html><head><meta property="og:image" content="/img/hearing.jpg"></head></html>'

    def handler(request):
        assert request.url.host == "fallriver-court.example"
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await RS.LeadSourceProvider(client, brave).images_for(lead, case)

    out = asyncio.run(go())
    assert [(c.url, c.provider, c.found_for, c.source_name) for c in out] == [
        ("https://fallriver-court.example/img/hearing.jpg", "lead_source",
         f"lead:{lead.id}", "Fall River Superior Court")]
    assert out[0].caption == "Arraignment footage"


def test_a_lead_without_a_named_owner_is_not_searched(db_session):
    case = _case(db_session)
    lead = VisualLead(case_id=case.id, video_source_id=1, lead_type="photo", description_en="x")
    brave = FakeBrave()
    async def go():
        async with httpx.AsyncClient() as client:
            return await RS.LeadSourceProvider(client, brave).images_for(lead, case)
    assert asyncio.run(go()) == []
    assert brave.calls == []


def test_the_brave_media_providers_are_enabled_in_config_and_are_not_context_providers():
    with open("config/parameters/visuals.json") as f:
        cfg = json.load(f)["visual_search"]
    assert {"brave_images", "lead_sources", "brave_video_links"} <= set(cfg["providers"])
    assert "brave_images" not in RS.RIGHTS_KNOWN_PROVIDERS   # context queries skip it


def _jpeg(width=900, height=600):
    import io
    import random

    from PIL import Image

    rng = random.Random(7)
    img = Image.new("RGB", (width, height))
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                 for _ in range(width * height)])
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def test_agent_stores_brave_images_as_editorial_review_assets_and_video_hits_as_links(
        db_session, tmp_path, monkeypatch):
    from app.agents import visual_research as VR

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.footage, "enabled", False)
    monkeypatch.setattr(ai_config.visual_search, "providers", ["brave_images", "brave_video_links"])
    case = _case(db_session, "Agent Brave Case")
    brave = FakeBrave(
        images=[_image_result()],
        videos=[{"url": "https://vimeo.com/1", "title": "Archive reel", "video": {}}])
    monkeypatch.setattr(VR, "brave_backend", lambda: brave)
    data = _jpeg()

    def handler(request):
        return httpx.Response(200, content=data, headers={"content-type": "image/jpeg"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await VR.VisualResearchAgent(client).run(
                db_session, case,
                [{"query": "Borden house", "entity": "borden_house", "entity_type": "building",
                  "kind": "exact"}])

    stats = asyncio.run(go())
    asset = db_session.query(VisualAsset).filter_by(case_id=case.id).one()
    assert (asset.provider, asset.rights_status) == ("brave_images", "editorial_review_required")
    assert asset.page_url == "https://news.example/story" and asset.source_url == "https://img.example/a.jpg"
    assert asset.editorial_approved_at is None and not R.usable(asset, "preview")
    assert stats["by_provider"] == {"brave_images": 1} and stats["video_links"] == 1
    assert db_session.query(MediaLink).filter_by(case_id=case.id).one().url == "https://vimeo.com/1"


def test_media_links_endpoints_list_and_dismiss(client, db_session):
    case = _case(db_session, "Links API Case")
    db_session.add(MediaLink(case_id=case.id, kind="video", url="https://v.example/1", title="T"))
    db_session.commit()
    listing = client.get(f"/api/cases/{case.id}/media-links").json()
    assert [(m["url"], m["status"]) for m in listing] == [("https://v.example/1", "new")]
    out = client.patch(f"/api/media-links/{listing[0]['id']}", json={"status": "dismissed"}).json()
    assert out["status"] == "dismissed"
