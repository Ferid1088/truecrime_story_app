import asyncio

import httpx

from app.agents.visual_research import VisualResearchAgent
from app.core.ai_config import ai_config
from app.documentary.visuals.planner import expansion_queries, research_queries
from app.documentary.visuals.research import WikimediaProvider, category_matches


def test_category_matches_needs_every_name_word():
    assert category_matches("Lizzie Borden", "Category:Lizzie Borden")
    assert not category_matches("Lizzie Borden", "Category:Borden, Ontario")


def test_expansion_queries_follow_entity_type():
    out = expansion_queries({"type": "person", "name": "Lizzie Borden"})
    assert out and all("Lizzie Borden" in q for q in out)
    assert len(out) <= ai_config.visual_search.max_expansions_per_entity


def test_research_queries_include_expansions_after_exact():
    req = {"entities": [{"key": "lb", "type": "person", "name": "Lizzie Borden",
                         "search_queries": ["Lizzie Borden"], "context_queries": ["Fall River 1892"]}],
           "beats": [{"beat_id": "b1", "requirements": [{"entity": "lb", "priority": "high"}]}]}
    qs = [q["query"] for q in research_queries(req)]
    assert qs[0] == "Lizzie Borden"
    assert "Lizzie Borden historical photograph" in qs
    assert qs.index("Lizzie Borden historical photograph") < qs.index("Fall River 1892")


def _category_files(handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await WikimediaProvider(client).category_files("Lizzie Borden", 10)

    return asyncio.run(go())


def test_category_files_harvests_category_and_subcategories(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        calls.append(q)
        if q.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"title": "Category:Lizzie Borden"}]}})
        if q.get("list") == "categorymembers":
            return httpx.Response(200, json={"query": {"categorymembers": [{"title": "Category:Lizzie Borden trial"}]}})
        title = q["gcmtitle"]
        return httpx.Response(200, json={"query": {"pages": {"1": {
            "title": f"File:{title[-5:]}.jpg", "index": 1, "imageinfo": [{
                "mime": "image/jpeg", "width": 1200, "url": "https://x/a.jpg",
                "thumburl": f"https://x/{title[-5:]}.jpg", "descriptionurl": "https://c/a",
                "extmetadata": {}}]}}}})

    monkeypatch.setattr(ai_config.visual_search, "min_request_interval_s", 0.0)
    out = _category_files(handler)
    assert len(out) == 2
    assert {c.title for c in out} == {"orden.jpg", "trial.jpg"}


def test_category_lookup_skips_other_entities_category(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"query": {"search": [{"title": "Category:Borden, Ontario"}]}})

    monkeypatch.setattr(ai_config.visual_search, "min_request_interval_s", 0.0)
    assert _category_files(handler) == []


def test_agent_curated_candidates_one_lookup_per_entity(monkeypatch):
    seen = []

    class Fake:
        async def category_files(self, name, limit):
            seen.append(name)
            return []

        async def article_files(self, name, limit, language):
            return []

    agent = VisualResearchAgent(client=object())
    qs = [{"query": "A", "entity": "a", "kind": "exact"},
          {"query": "A more", "entity": "a", "kind": "exact"},
          {"query": "B ctx", "entity": "b", "kind": "context"}]
    asyncio.run(agent._curated_candidates(Fake(), qs))
    assert seen == ["A"]


def test_expansion_queries_carry_the_names_a_candidate_must_hold():
    from app.documentary.visuals.planner import expansion_item
    from app.documentary.visuals.research import Candidate, names_entity

    q = expansion_item({"key": "lb", "type": "person", "name": "Lizzie Borden", "aliases": ["Lizzie"]},
                       "Lizzie Borden archive")
    assert q["must_name"] == ["Lizzie Borden", "Lizzie"]
    assert names_entity(Candidate(url="u", provider="wikimedia", title="Lizzie Borden 1889.jpg"), q["must_name"])
    assert not names_entity(Candidate(url="u", provider="wikimedia", title="French liberty.jpg"), q["must_name"])


def test_only_the_most_needed_entities_are_expanded(monkeypatch):
    from app.documentary.visuals.planner import research_queries

    monkeypatch.setattr(ai_config.visual_search, "max_expansion_entities", 1)
    ents = [{"key": k, "type": "person", "name": n, "search_queries": [n], "context_queries": []}
            for k, n in (("a", "Anna Aaa"), ("b", "Bert Bbb"))]
    req = {"entities": ents, "beats": [{"beat_id": "b1", "requirements": [
        {"entity": "a", "priority": "high"}, {"entity": "b", "priority": "low"}]}]}
    qs = [q["query"] for q in research_queries(req)]
    assert "Anna Aaa archive" in qs and "Bert Bbb archive" not in qs


def test_rejected_assets_do_not_use_up_the_asset_budget():
    from app.db.models import VisualAsset

    rows = [VisualAsset(verification_status=v) for v in ("rejected", "rejected", "verified", "unverified")]
    assert VisualResearchAgent(client=object()).asset_budget(rows) == ai_config.visual_search.max_assets - 2


def test_footage_that_calls_itself_fiction_or_a_stage_show_is_skipped():
    from app.agents.footage import staged
    from app.documentary.visuals.footage import FootageCandidate

    markers = ai_config.footage.fiction_markers
    netflix = FootageCandidate(provider="internet_archive", url=None, title="MONSTER: THE LIZZIE BORDEN STORY - Netflix (2026)")
    promo = FootageCandidate(provider="internet_archive", url=None, title="Lizzie Borden of Fall River (Promo)")
    newsreel = FootageCandidate(provider="internet_archive", url=None, title="Fall River 1930 street scenes")
    assert staged(netflix, markers) and staged(promo, markers) and not staged(newsreel, markers)


def test_footage_must_name_what_it_was_searched_for():
    from app.agents.footage import about_query
    from app.documentary.visuals.footage import FootageCandidate

    generic = ai_config.footage.generic_query_words
    war = FootageCandidate(provider="internet_archive", url=None, found_for="Fall River Massachusetts history",
                           title="Pacific war film compilation", caption="bombers, harbor, infantry")
    local = FootageCandidate(provider="internet_archive", url=None, found_for="Fall River Massachusetts history",
                             title="Fall River mills 1920s")
    assert not about_query(war, generic) and about_query(local, generic)


def test_same_subject_needs_one_name_inside_the_other():
    from app.documentary.visuals.research import same_subject

    assert same_subject("Lizzie Borden", "Lizzie Borden") and same_subject("Borden House", "Lizzie Borden House")
    assert not same_subject("Lizzie Borden", "Borden, Ontario")


def test_article_files_take_the_pictures_of_the_matching_article_only():
    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        if q.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"title": "Lizzie Borden"}]}})
        assert q["generator"] == "images" and q["titles"] == "Lizzie Borden"
        return httpx.Response(200, json={"query": {"pages": {"1": {
            "title": "File:Lizzie Borden 1890.jpg", "index": 1, "imageinfo": [{
                "mime": "image/jpeg", "width": 900, "url": "https://x/a.jpg", "descriptionurl": "https://c/a",
                "extmetadata": {}}]}, "2": {"title": "File:Seal.svg", "index": 2, "imageinfo": [{
                    "mime": "image/svg+xml", "width": 900, "url": "https://x/s.svg"}]}}}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await WikimediaProvider(client).article_files("Lizzie Borden", 10, "en")

    out = asyncio.run(go())
    assert [c.title for c in out] == ["Lizzie Borden 1890.jpg"]
    assert out[0].source_name == "Wikipedia (en): Lizzie Borden"
