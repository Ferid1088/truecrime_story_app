import asyncio

import pytest

from app.lifecycle.identity import IdentityIndex
from app.lifecycle.selection import legal_risk_flags, prepare_candidates
from app.research_engine.orchestrator import (
    EngineConfig,
    ResearchOrchestrator,
    _discovery_queries,
)
from app.research_engine.search import SearchBackendRegistry, SearchError, SearchResult
from tests.test_case_lifecycle import FakeFetcher, FakeGen, FakeRegistry


def test_historical_theme_uses_pre_2000_queries():
    queries = _discovery_queries("historical cases (before 2000)", "en")
    assert queries
    assert all("before 2000" in query.lower() for query in queries)
    assert queries != _discovery_queries("true crime", "en")


def test_discovery_queries_carry_the_requested_country_and_year_range():
    plain = _discovery_queries("true crime", "en")
    with_country = _discovery_queries("true crime", "en", country_name="Germany")
    with_both = _discovery_queries(
        "true crime", "en", country_name="Germany", year_from=2000, year_to=2010)
    assert plain != with_country
    assert all("Germany" in q for q in with_country)
    assert all("Germany" in q and "2000" in q and "2010" in q for q in with_both)


class _MeteredBraveLikeBackend:
    """A search backend that reports a per-request cost via
    last_search_diagnostics, exactly like BraveSearchBackend does."""

    name = "fake_brave"
    PER_REQUEST_COST_USD = 0.005

    def __init__(self):
        self.last_search_diagnostics: dict | None = None

    def is_available(self) -> bool:
        return True

    async def search(self, query, language, categories=None, time_range=None, limit=10):
        self.last_search_diagnostics = {
            "query": query, "estimated_cost_usd": self.PER_REQUEST_COST_USD,
        }
        return [SearchResult(backend=self.name, query=query, language=language, rank=1,
                             url="https://news.example/case", title="A case")]


def test_discover_cases_counts_real_brave_search_spend():
    """Every discovery query is a real, billed Brave Search request
    (config/parameters/pricing.json's brave_web.usd_per_request) — the
    telemetry a ResearchJob's search_cost_usd/total_cost_usd are built
    from (app/services/research_jobs._apply_job_telemetry) must count it,
    or discovery spend silently under-reports to zero."""
    backend = _MeteredBraveLikeBackend()
    registry = SearchBackendRegistry([backend])
    orch = ResearchOrchestrator(registry=registry, fetcher=FakeFetcher(),
                                generation_provider=FakeGen(), config=EngineConfig())
    result = asyncio.run(orch.discover_cases(2, ["en"], None, [], known_identities=[]))
    tel = result["_telemetry"]
    assert tel["searches"] > 0
    expected = round(tel["searches"] * _MeteredBraveLikeBackend.PER_REQUEST_COST_USD, 8)
    assert tel["search_cost_usd"] == expected
    assert tel["cost_usd"] >= expected


class _PerLanguageFailingBackend:
    """Raises SearchError for one language (e.g. a Brave 422 on a bad
    search_lang) and returns real results for the rest — the shape of the
    2026-10-23 incident where "other" 422'd but en/de/fa/ar worked fine."""

    name = "fake_brave"

    def __init__(self, failing_languages: set[str]):
        self.failing_languages = failing_languages
        self.calls: list[str] = []
        self.last_search_diagnostics: dict | None = None

    def is_available(self) -> bool:
        return True

    async def search(self, query, language, categories=None, time_range=None, limit=10):
        self.calls.append(language)
        if language in self.failing_languages:
            raise SearchError("brave", "search_unavailable",
                              "search unavailable: Brave returned HTTP 422 (results=0)",
                              {"http_status": 422, "attempts": [{"http_status": 422}]})
        self.last_search_diagnostics = {"query": query, "http_status": 200,
                                        "estimated_cost_usd": 0.005}
        return [SearchResult(backend=self.name, query=query, language=language, rank=1,
                             url=f"https://news.example/{language}", title=f"A case ({language})")]


def test_discover_cases_continues_other_languages_after_one_search_error():
    """One language's search failing (invalid param, transient error) must
    not discard the candidates already found in the other languages, and
    must not be silently swallowed either."""
    backend = _PerLanguageFailingBackend(failing_languages={"other"})
    registry = SearchBackendRegistry([backend])
    orch = ResearchOrchestrator(registry=registry, fetcher=FakeFetcher(),
                                generation_provider=FakeGen(), config=EngineConfig())
    result = asyncio.run(
        orch.discover_cases(1, ["en", "other"], None, [], known_identities=[]))
    assert "en" in backend.calls and "other" in backend.calls
    assert result["_telemetry"]["searches"] > 0
    failed = [d for d in result["search_diagnostics"] if d.get("error_kind")]
    assert failed and failed[0]["http_status"] == 422
    assert failed[0]["language"] == "other"


def test_discover_cases_fails_loudly_when_every_search_fails():
    """If every language's search fails, the job must fail (not quietly
    report zero candidates, which would look like "no cases out there")."""
    backend = _PerLanguageFailingBackend(failing_languages={"en", "de"})
    registry = SearchBackendRegistry([backend])
    orch = ResearchOrchestrator(registry=registry, fetcher=FakeFetcher(),
                                generation_provider=FakeGen(), config=EngineConfig())
    with pytest.raises(SearchError, match="search unavailable"):
        asyncio.run(orch.discover_cases(1, ["en", "de"], None, [], known_identities=[]))


def test_prepare_candidates_rejects_cases_outside_the_requested_country():
    raw = [
        {"title": "In-country case", "rationale": "x", "location": "Ulm, Germany",
         "resolution_status": "SOLVED", "incident_date": "2010"},
        {"title": "Out-of-country case", "rationale": "x", "location": "Lagos, Nigeria",
         "resolution_status": "SOLVED", "incident_date": "2010"},
    ]
    result = asyncio.run(prepare_candidates(
        raw, [], IdentityIndex([]), count=5, resolution_filter="both", country_code="DE"))
    by_title = {c["title"]: c["selection"] for c in result["candidates"]}
    assert by_title["In-country case"]["state"] in ("suggested", "eligible")
    assert by_title["Out-of-country case"]["state"] == "filtered"
    assert "country" in by_title["Out-of-country case"]["reason"].lower()


def test_prepare_candidates_rejects_unknown_location_only_when_a_country_was_requested():
    raw = [{"title": "No location given", "rationale": "x", "incident_date": "2010",
            "resolution_status": "SOLVED"}]
    with_country = asyncio.run(prepare_candidates(
        raw, [], IdentityIndex([]), count=5, resolution_filter="both", country_code="DE"))
    assert with_country["candidates"][0]["selection"]["state"] == "filtered"
    assert "location unverified" in with_country["candidates"][0]["selection"]["reason"].lower()

    without_country = asyncio.run(prepare_candidates(
        raw, [], IdentityIndex([]), count=5, resolution_filter="both", country_code=None))
    assert without_country["candidates"][0]["selection"]["state"] in ("suggested", "eligible")


def test_prepare_candidates_rejects_cases_outside_the_requested_year_range():
    raw = [
        {"title": "In range", "rationale": "x", "incident_date": "2010",
         "resolution_status": "SOLVED"},
        {"title": "Too old", "rationale": "x", "incident_date": "1985",
         "resolution_status": "SOLVED"},
    ]
    result = asyncio.run(prepare_candidates(
        raw, [], IdentityIndex([]), count=5, resolution_filter="both",
        incident_year_from=2000, incident_year_to=2020))
    by_title = {c["title"]: c["selection"] for c in result["candidates"]}
    assert by_title["In range"]["state"] in ("suggested", "eligible")
    assert by_title["Too old"]["state"] == "filtered"
    assert "year" in by_title["Too old"]["reason"].lower()


def test_resolution_filter_solved_only_rejects_unsolved_by_default():
    raw = [{"title": "A solved case", "rationale": "x", "resolution_status": "SOLVED"},
           {"title": "An unsolved case", "rationale": "x", "resolution_status": "UNSOLVED"}]
    result = asyncio.run(prepare_candidates(
        raw, [], IdentityIndex([]), count=5))  # resolution_filter defaults to solved_only
    by_title = {c["title"]: c["selection"] for c in result["candidates"]}
    assert by_title["A solved case"]["state"] in ("suggested", "eligible")
    assert by_title["An unsolved case"]["state"] == "filtered"
    assert "unsolved" in by_title["An unsolved case"]["reason"].lower()


def test_resolution_filter_unsolved_only_rejects_solved_cases():
    raw = [{"title": "A solved case", "rationale": "x", "resolution_status": "SOLVED"},
           {"title": "An unsolved case", "rationale": "x", "resolution_status": "UNSOLVED"}]
    result = asyncio.run(prepare_candidates(
        raw, [], IdentityIndex([]), count=5, resolution_filter="unsolved_only"))
    by_title = {c["title"]: c["selection"] for c in result["candidates"]}
    assert by_title["An unsolved case"]["state"] in ("suggested", "eligible")
    assert by_title["A solved case"]["state"] == "filtered"
    assert "requests unsolved cases only" in by_title["A solved case"]["reason"]
    assert result["stats"]["filtered_solved"] == 1


def test_resolution_filter_both_keeps_solved_and_unsolved():
    raw = [{"title": "A solved case", "rationale": "x", "resolution_status": "SOLVED"},
           {"title": "An unsolved case", "rationale": "x", "resolution_status": "UNSOLVED"}]
    result = asyncio.run(prepare_candidates(
        raw, [], IdentityIndex([]), count=5, resolution_filter="both"))
    by_title = {c["title"]: c["selection"] for c in result["candidates"]}
    assert by_title["A solved case"]["state"] in ("suggested", "eligible")
    assert by_title["An unsolved case"]["state"] in ("suggested", "eligible")


def test_legal_risk_flags_cover_trial_minors_and_unconvicted_suspect():
    reasons = legal_risk_flags({
        "title": "Active trial involving a minor",
        "rationale": "The accused suspect has been charged.",
        "resolution_status": "UNKNOWN",
    })
    assert reasons == ["ongoing trial", "minors involved", "suspect not convicted"]


def test_legal_risk_candidates_sort_last():
    raw = [
        {
            "title": "Active trial case",
            "rationale": "An ongoing trial concerns a minor.",
            "resolution_status": "UNKNOWN",
        },
        {
            "title": "Resolved case",
            "key_people": ["Ada Resolved"],
            "rationale": "The suspect was convicted and sentenced.",
            "resolution_status": "SOLVED",
            "resolution_evidence": "Convicted and sentenced.",
        },
    ]
    registry = FakeRegistry({"Resolved case": [
        {"url": "https://news-a.example/resolved", "title": "Resolved case",
         "snippet": "Ada Resolved convicted"},
        {"url": "https://news-b.example/resolved", "title": "Resolved case verdict",
         "snippet": "Ada Resolved sentenced"}]})
    gen = FakeGen({"Resolved case": {"status": "SOLVED", "confidence": 0.95,
                                     "supporting_urls": "ALL",
                                     "latest_development": "convicted and sentenced"}})
    result = asyncio.run(prepare_candidates(
        raw, [], IdentityIndex([]), count=1, resolution_filter="both",
        registry=registry, gen=gen,
    ))
    suggested = [row for row in result["candidates"] if row["selection"]["state"] == "suggested"]
    filtered = [row for row in result["candidates"] if row["selection"]["state"] == "not_shown"]
    assert suggested[0]["title"] == "Resolved case"
    assert suggested[0]["legal_risk"] is False
    assert filtered[0]["legal_risk"] is True


def test_unverified_solved_claim_carries_the_same_legal_risk_as_its_stored_status():
    """The stored status of an unverified 'solved' claim is STATUS_UNDER_REVIEW,
    so its legal-risk flag must say so — the case later created from it is
    judged on that stored status."""
    raw = [{
        "title": "Leopold and Loeb murder of Bobby Franks",
        "rationale": "Kidnapping and murder; capture and conviction.",
        "resolution_status": "SOLVED",
        "resolution_evidence": "They were caught and convicted.",
    }]
    result = asyncio.run(prepare_candidates(
        raw, [], IdentityIndex([]), count=1, resolution_filter="both",
    ))
    row = result["candidates"][0]
    assert row["selection"]["status"] == "STATUS_UNDER_REVIEW"
    assert row["legal_risk"] is True
    assert row["legal_risk_reasons"] == ["suspect not convicted"]
