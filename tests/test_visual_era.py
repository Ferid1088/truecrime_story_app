"""P3/P4: modern vs historical cases get different providers and query phrasing. The incident
year decides (config visual_search.era_boundary_year), never the case's open/closed status."""

import uuid

from app.core.ai_config import ai_config
from app.db.models import Case
from app.documentary.visuals import era as ERA


def _case(incident_date):
    return Case(canonical_title="Era", slug=uuid.uuid4().hex, language="en",
                incident_date=incident_date)


def test_a_recent_year_is_modern():
    assert ERA.era_of(_case("2022-11-13")) == "modern"


def test_an_old_year_is_historical():
    assert ERA.era_of(_case("1892-08-04")) == "historical"


def test_the_boundary_year_itself_is_modern(monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "era_boundary_year", 1980)
    assert ERA.era_of(_case("1980-01-01")) == "modern"
    assert ERA.era_of(_case("1979-12-31")) == "historical"


def test_an_unknown_year_defaults_to_historical():
    # the full provider/template set every case used before era awareness existed — a case
    # whose date could not be read never silently loses a provider it would otherwise have had
    assert ERA.era_of(_case(None)) == "historical"
    assert ERA.era_of(_case("sometime in the past")) == "historical"


def test_incident_year_reads_the_first_plausible_year_in_the_text():
    assert ERA.incident_year(_case("August 4, 1892")) == 1892
    assert ERA.incident_year(_case("November 13, 2022")) == 2022


def test_providers_for_era_falls_back_to_the_flat_list_when_era_is_not_configured(monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "providers", ["source_pages", "wikimedia"])
    monkeypatch.setattr(ai_config.visual_search, "providers_by_era", {})
    assert ERA.providers_for_era("modern") == ["source_pages", "wikimedia"]
    assert ERA.providers_for_era("historical") == ["source_pages", "wikimedia"]


def test_modern_cases_can_turn_off_wikimedia_free_text_search(monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "providers", ["source_pages", "wikimedia", "brave_images"])
    monkeypatch.setattr(ai_config.visual_search, "providers_by_era", {
        "modern": ["source_pages", "brave_images"],
        "historical": ["source_pages", "wikimedia", "brave_images"],
    })
    assert "wikimedia" not in ERA.providers_for_era("modern")
    assert "wikimedia" in ERA.providers_for_era("historical")


def test_templates_for_era_falls_back_to_the_flat_templates(monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "expansion_templates",
                        {"place": ["{name} historical photograph"]})
    monkeypatch.setattr(ai_config.visual_search, "expansion_templates_by_era", {})
    assert ERA.templates_for_era("modern", "place") == ["{name} historical photograph"]


def test_modern_query_templates_replace_the_historical_phrasing(monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "expansion_templates_by_era", {
        "modern": {"place": ["{name} press conference", "{name} campus"]},
        "historical": {"place": ["{name} historical photograph", "{name} old postcard"]},
    })
    assert ERA.templates_for_era("modern", "place") == ["{name} press conference", "{name} campus"]
    assert ERA.templates_for_era("historical", "place") == [
        "{name} historical photograph", "{name} old postcard"]


# ---- P3: the research agent itself skips Wikimedia free-text search for a modern case ----------

def test_a_modern_case_skips_wikimedia_free_text_search_but_keeps_categories(monkeypatch, db_session):
    import asyncio
    import uuid

    import httpx

    from app.agents import visual_research as VR
    from app.core.ai_config import ai_config as cfg
    from app.db.models import Case

    monkeypatch.setattr(cfg.documentary, "storage_dir", "/tmp/era-test-cases")
    monkeypatch.setattr(cfg.footage, "enabled", False)
    monkeypatch.setattr(cfg.visual_search, "providers", ["source_pages", "wikimedia"])
    monkeypatch.setattr(cfg.visual_search, "providers_by_era",
                        {"modern": ["source_pages"], "historical": ["source_pages", "wikimedia"]})
    monkeypatch.setattr(cfg.visual_search, "wikimedia_categories", True)

    free_text_calls = []
    category_calls = []

    class SpyWikimedia:
        def __init__(self, client):
            pass

        async def search(self, query, limit):
            free_text_calls.append(query)
            return []

        async def category_files(self, name, limit):
            category_calls.append(name)
            return []

        async def article_files(self, name, limit, language):
            return []

    monkeypatch.setattr(VR, "WikimediaProvider", SpyWikimedia)
    case = Case(canonical_title="Modern", slug=uuid.uuid4().hex, language="en",
               incident_date="2022-11-13")
    db_session.add(case)
    db_session.commit()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=b""))) as client:
            agent = VR.VisualResearchAgent(client)
            return await agent.run(db_session, case, [{"query": "campus", "entity": "e",
                                                        "entity_type": "place", "kind": "exact"}])

    asyncio.run(go())
    assert free_text_calls == []           # P3: off for a modern case
    assert category_calls == ["campus"]    # categories stay on regardless of era
