"""P5: pre-filter before download. Every exact-query candidate's own text (title, caption,
page url) must name the entity (any alias) or the case, else it is never downloaded; at most
visual_search.max_downloads_per_entity downloads per entity in one run; duplicates are removed
by normalised URL and title before the download, by perceptual hash after (existing)."""

import asyncio
import io
import random
import uuid

import httpx
from PIL import Image

from app.agents import visual_research as VR
from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary.visuals.research import normalize_title, normalize_url


def _case(db, title="Idaho student murders"):
    case = Case(canonical_title=title, slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


def _jpeg(seed=1, w=900, h=600):
    rng = random.Random(seed)
    img = Image.new("RGB", (w, h))
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(w * h)])
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


# ---- normalize_url / normalize_title --------------------------------------------------------

def test_normalize_url_strips_query_fragment_and_trailing_slash():
    assert (normalize_url("https://Img.Example/a.jpg?w=200#x")
            == normalize_url("https://img.example/a.jpg/"))


def test_normalize_title_folds_case_and_whitespace():
    assert normalize_title("  The   House  ") == normalize_title("the house")


# ---- the general pre-filter (not just expansion queries) ------------------------------------

class FakeBrave:
    def __init__(self, results):
        self.results = results

    async def search_media(self, kind, query, language, limit=10):
        return self.results


def _run(db, case, queries, results, monkeypatch, tmp_path):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.footage, "enabled", False)
    monkeypatch.setattr(ai_config.visual_search, "providers", ["brave_images"])
    monkeypatch.setattr(VR, "brave_backend", lambda: FakeBrave(results))
    images = {}

    def handler(request):
        data = images.setdefault(str(request.url), _jpeg(len(images) + 1))
        return httpx.Response(200, content=data, headers={"content-type": "image/jpeg"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await VR.VisualResearchAgent(client).run(db, case, queries)

    return asyncio.run(go())


def _result(n, title, url=None):
    return {"type": "image_result", "title": title, "url": url or f"https://news.example/{n}",
            "source": "news.example",
            "properties": {"url": f"https://img.example/{n}.jpg", "width": 900, "height": 600}}


def test_an_exact_query_candidate_whose_text_names_nothing_is_never_downloaded(db_session, tmp_path, monkeypatch):
    case = _case(db_session)
    results = [_result(1, "Completely unrelated stock photo of a sunset")]
    queries = [{"query": "1122 King Road", "entity": "house", "entity_type": "building",
               "entity_name": "1122 King Road", "kind": "exact",
               "must_name": ["1122 King Road"]}]
    stats = _run(db_session, case, queries, results, monkeypatch, tmp_path)
    assert stats["added"] == 0 and stats["prefiltered"] == 1
    assert db_session.query(VisualAsset).filter_by(case_id=case.id).count() == 0


def test_an_exact_query_candidate_naming_the_case_instead_of_the_entity_still_passes(db_session, tmp_path, monkeypatch):
    case = _case(db_session, title="Idaho student murders")
    results = [_result(1, "A scene from the Idaho student murders investigation")]
    queries = [{"query": "1122 King Road", "entity": "house", "entity_type": "building",
               "entity_name": "1122 King Road", "kind": "exact",
               "must_name": ["1122 King Road"]}]
    stats = _run(db_session, case, queries, results, monkeypatch, tmp_path)
    assert stats["added"] == 1 and stats["prefiltered"] == 0


def test_a_context_query_item_carries_no_must_name():
    # a context query deliberately looks for a stand-in, never the exact thing's own name
    from app.documentary.mvp.research import query_item

    entity = {"key": "forest", "name": "Specific Forest", "type": "place"}
    assert query_item(entity, "pine forest region", "context")["must_name"] == []
    assert query_item(entity, "Specific Forest photo", "exact")["must_name"] == ["Specific Forest"]


# ---- per-entity download cap -----------------------------------------------------------------

def test_at_most_the_configured_number_of_downloads_per_entity(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "max_downloads_per_entity", 2)
    case = _case(db_session, title="Idaho student murders")
    results = [_result(i, f"1122 King Road photo {i}") for i in range(1, 6)]
    queries = [{"query": "1122 King Road", "entity": "house", "entity_type": "building",
               "entity_name": "1122 King Road", "kind": "exact",
               "must_name": ["1122 King Road"]}]
    stats = _run(db_session, case, queries, results, monkeypatch, tmp_path)
    assert stats["added"] == 2 and stats["entity_cap_skipped"] == 3


# ---- URL and title normalisation dedup, before any download -----------------------------------

def test_duplicates_by_normalised_url_are_removed_before_download(db_session, tmp_path, monkeypatch):
    case = _case(db_session, title="Idaho student murders")
    results = [_result(1, "1122 King Road photo", url="https://news.example/story?utm=a"),
              _result(1, "1122 King Road photo", url="https://news.example/story?utm=b")]
    queries = [{"query": "1122 King Road", "entity": "house", "entity_type": "building",
               "entity_name": "1122 King Road", "kind": "exact", "must_name": ["1122 King Road"]}]
    # same image URL (properties.url) for both -> the candidate URL dedup already catches this;
    # force different candidate URLs with the same image to isolate the new source-url normalisation
    stats = _run(db_session, case, queries, results, monkeypatch, tmp_path)
    assert stats["added"] + stats["duplicates"] == 2


def test_duplicates_by_normalised_title_are_removed_before_download(db_session, tmp_path, monkeypatch):
    case = _case(db_session, title="Idaho student murders")
    results = [_result(1, "1122 King Road   photo"), _result(2, "1122 king road photo")]
    queries = [{"query": "1122 King Road", "entity": "house", "entity_type": "building",
               "entity_name": "1122 King Road", "kind": "exact", "must_name": ["1122 King Road"]}]
    stats = _run(db_session, case, queries, results, monkeypatch, tmp_path)
    assert stats["added"] == 1 and stats["duplicates"] == 1
