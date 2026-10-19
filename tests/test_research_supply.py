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


def test_agent_category_candidates_one_lookup_per_entity(monkeypatch):
    seen = []

    class Fake:
        async def category_files(self, name, limit):
            seen.append(name)
            return []

    agent = VisualResearchAgent(client=object())
    qs = [{"query": "A", "entity": "a", "kind": "exact"},
          {"query": "A more", "entity": "a", "kind": "exact"},
          {"query": "B ctx", "entity": "b", "kind": "context"}]
    asyncio.run(agent._category_candidates(Fake(), qs))
    assert seen == ["A"]
