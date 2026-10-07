"""Tests for the research-provider layer and research jobs.

Active research runs on the TrueCrime Search Engine (SearXNG +
fetcher); provider HTTP is mocked — no live requests."""
import json
from contextlib import ExitStack

import pytest
from unittest.mock import AsyncMock, patch

from app.core.ai_config import ai_config
from app.providers.base import ProviderError, ProviderJob
from app.services import research_jobs
from app.db.models import Case, ResearchJob, Source, DiscoveryCandidate


class FakeProvider:
    name = "truecrime"

    def __init__(self, configured=True, poll_job=None, health=True):
        self._configured = configured
        self._poll_job = poll_job
        self._health = health
        self.started = []

    def is_configured(self):
        return self._configured

    async def check_health(self):
        return self._health

    async def start_discovery(self, **kwargs):
        return "or-discovery"

    async def start_case_research(
        self, case_title, language, objective=None, research_languages=None,
        context=None,
    ):
        return "or-research"

    async def poll(self, external_id):
        if self._poll_job:
            return self._poll_job
        return ProviderJob(external_id=external_id, status="running")


def _provider_patch(provider):
    """Patch the research provider AND keep the downstream extraction
    pipeline hermetic — provider tests must never hit live APIs."""
    from app.agents.research import ResearchAgent

    async def _noop_run(self, db, case, contradiction_hints=None):
        return {"facts": 0, "contradictions": 0}

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.services.research_jobs.get_research_provider",
            return_value=provider,
        )
    )
    stack.enter_context(patch.object(ResearchAgent, "run", _noop_run))
    return stack


# ---------------------------------------------------------------------------
# Research provider status endpoint
# ---------------------------------------------------------------------------


def test_research_status_unconfigured(client):
    with patch("app.main.get_research_provider", return_value=FakeProvider(configured=False)):
        r = client.get("/api/integrations/research/status")
    assert r.status_code == 200
    body = r.json()
    assert body == {"provider": "truecrime", "configured": False, "reachable": False}
    assert "apk" not in json.dumps(body)


def test_research_status_configured_reachable(client):
    with patch("app.main.get_research_provider", return_value=FakeProvider(health=True)):
        r = client.get("/api/integrations/research/status")
        assert r.json() == {"provider": "truecrime", "configured": True, "reachable": True}


def test_research_status_configured_unreachable(client):
    with patch("app.main.get_research_provider", return_value=FakeProvider(health=False)):
        r = client.get("/api/integrations/research/status")
        assert r.json() == {"provider": "truecrime", "configured": True, "reachable": False}


# ---------------------------------------------------------------------------
# Discovery jobs
# ---------------------------------------------------------------------------


def test_discovery_returns_job_and_completes(client, db_session):
    result = {
        "candidates": [
            {
                "title": "The Lighthouse Keeper Vanishing",
                "rationale": "Unsolved maritime disappearance.",
                "aliases": ["Flannan Isles case"],
                "key_people": ["Thomas Marshall"],
                "location": "Scotland",
                "source_richness": "high",
            },
            {
                "title": "Duplicate Case",
                "aliases": ["The Lighthouse Keeper Vanishing"],
            },
        ]
    }
    provider = FakeProvider(
        poll_job=ProviderJob(
            external_id="s1", status="completed", result=result
        )
    )
    with patch("app.main.get_research_provider", return_value=provider), _provider_patch(provider):
        r = client.post(
            "/api/topics/discover",
            json={"count": 2, "languages": ["en"], "theme": "cold case"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "running"
        job_id = body["job_id"]

        r = client.get(f"/api/research-jobs/{job_id}")
        job = r.json()
        assert job["status"] == "completed"
        assert job["result"]["skipped_duplicates"] == 1
        candidates = job["result"]["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["title"] == "The Lighthouse Keeper Vanishing"
        assert candidates[0]["candidate_id"]

    # the duplicate is stored too — with the reason it was rejected (audit)
    stored = db_session.query(DiscoveryCandidate).order_by(DiscoveryCandidate.id).all()
    states = {c.title: c.state for c in stored}
    assert states == {"The Lighthouse Keeper Vanishing": "suggested", "Duplicate Case": "duplicate"}
    dup = next(c for c in stored if c.state == "duplicate")
    assert "Lighthouse Keeper Vanishing" in dup.duplicate_reason


def test_discovery_fails_cleanly_when_unconfigured(client):
    """With the research provider unconfigured, discovery falls back to
    the generation provider; if that is also unavailable the error
    surfaces as 500."""
    from app.providers.generation.base import GenerationError

    provider = FakeProvider(configured=False)

    class UnconfiguredGen:
        def is_configured(self):
            return False

        async def generate_structured(self, *a, **kw):
            raise GenerationError("missing_key", "TrueCrime_OPENROUTER_API_KEY is not configured.")

    with patch("app.main.get_research_provider", return_value=provider), _provider_patch(provider), \
         patch("app.agents.topic_discovery.get_generation_provider", return_value=UnconfiguredGen()):
        r = client.post("/api/topics/discover", json={"count": 3, "languages": ["en"]})
    assert r.status_code == 500
    assert "OPENROUTER" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Research jobs
# ---------------------------------------------------------------------------


def _make_case(client) -> int:
    r = client.post(
        "/api/cases",
        json={"canonical_title": "The Zodiac Letters", "language": "en", "force": True},
    )
    return r.json()["id"]


def test_research_job_ingests_sources(client, db_session):
    case_id = _make_case(client)
    result = {
        "case": {"summary": "Serial killer active in California."},
        "sources": [
            {
                "title": "SF Chronicle Archive",
                "url": "https://example.com/zodiac",
                "publisher": "SF Chronicle",
                "source_type": "news",
                "language": "en",
                "published_at": "1969-10-13",
                "summary": "Archival reporting on the letters.",
                "reliability": 0.9,
            },
            {
                "title": "Duplicate URL",
                "url": "https://example.com/zodiac",
            },
        ],
        "possible_facts": [{"claim": "Unverified claim", "confidence": 0.4}],
        "possible_contradictions": [],
        "research_gaps": ["No court transcript found"],
        "_telemetry": {
            "calls": 5, "searches": 2, "fetches": 1,
            "input_tokens": 1200, "output_tokens": 300, "cost_usd": 0.0123,
        },
    }
    provider = FakeProvider(
        poll_job=ProviderJob(external_id="s2", status="completed", result=result)
    )
    with patch("app.main.get_research_provider", return_value=provider), _provider_patch(provider):
        r = client.post(f"/api/cases/{case_id}/research")
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        r = client.get(f"/api/research-jobs/{job_id}")
        job = r.json()
        assert job["status"] == "completed"
        assert "sources_added=1" in job["result_summary"]
        # Provider telemetry surfaces in the job summary
        assert "calls=5" in job["result_summary"]
        assert "cost=$0.0123" in job["result_summary"]

    sources = db_session.query(Source).filter(Source.case_id == case_id).all()
    assert len(sources) == 1
    s = sources[0]
    assert s.research_provider == "truecrime"
    assert s.external_reference == "or-research"
    assert s.published_at == "1969-10-13"
    assert s.summary
    assert s.status == "active"
    # possible_facts must NOT enter the fact layer
    from app.db.models import Fact

    assert db_session.query(Fact).filter(Fact.case_id == case_id).count() == 0


def test_research_job_failed_provider(client, db_session):
    case_id = _make_case(client)
    provider = FakeProvider(
        poll_job=ProviderJob(external_id="s3", status="failed", error="research job failed upstream.")
    )
    with patch("app.main.get_research_provider", return_value=provider), _provider_patch(provider):
        r = client.post(f"/api/cases/{case_id}/research")
        job_id = r.json()["job_id"]
        r = client.get(f"/api/research-jobs/{job_id}")
        job = r.json()
    assert job["status"] == "failed"
    assert "failed" in job["error"]
    case = db_session.get(Case, case_id)
    assert case.status != "researching"


def test_research_job_poll_error_marks_failed(client):
    case_id = _make_case(client)

    class BrokenProvider(FakeProvider):
        async def poll(self, external_id):
            raise ProviderError("rate_limited", "search backend rate limited (429).")

    provider = BrokenProvider()
    with patch("app.main.get_research_provider", return_value=provider), _provider_patch(provider):
        r = client.post(f"/api/cases/{case_id}/research")
        job_id = r.json()["job_id"]
        r = client.get(f"/api/research-jobs/{job_id}")
        job = r.json()
    assert job["status"] == "failed"
    assert "rate limit" in job["error"]


def test_list_research_jobs_for_case(client):
    case_id = _make_case(client)
    provider = FakeProvider()
    with patch("app.main.get_research_provider", return_value=provider), _provider_patch(provider):
        client.post(f"/api/cases/{case_id}/research")
        client.post(f"/api/cases/{case_id}/research")
        rows = client.get(f"/api/research-jobs?case_id={case_id}").json()
    assert len(rows) == 2
    assert all(row["status"] == "running" for row in rows)


def test_historical_devin_jobs_still_render(client, db_session):
    """Old provider="devin"/"openrouter" rows are data, not code —
    they must remain readable alongside new truecrime jobs."""
    from app.db.models import Case, ResearchJob

    case = Case(canonical_title="Historical Case", slug="historical-case",
                language="en", status="researched")
    db_session.add(case)
    db_session.commit()
    db_session.add(ResearchJob(
        case_id=case.id, provider="devin", job_type="research",
        status="completed", result_summary="sources_added=5",
    ))
    db_session.add(ResearchJob(
        case_id=case.id, provider="openrouter", job_type="research",
        status="running",
    ))
    db_session.add(ResearchJob(
        case_id=case.id, provider="truecrime", job_type="research",
        status="queued",
    ))
    db_session.commit()

    rows = client.get(f"/api/research-jobs?case_id={case.id}").json()
    providers = {r["provider"] for r in rows}
    assert providers == {"devin", "openrouter", "truecrime"}
    devin_row = next(r for r in rows if r["provider"] == "devin")
    assert devin_row["status"] == "completed"
    assert devin_row["result_summary"] == "sources_added=5"


# ---------------------------------------------------------------------------
# TrueCrime Search Engine unit tests
# ---------------------------------------------------------------------------

from app.research_engine.provider import (
    TrueCrimeSearchProvider,
    _JOBS,
)
from app.research_engine.urlnorm import (
    canonicalize_url,
    youtube_video_id,
)
from app.research_engine.safety import UnsafeURLError, validate_url
from app.research_engine.search import SearchResult
from app.research_engine.ranking import rank_results, select_for_fetch
from app.research_engine.extractor import extract
from app.research_engine.language import detect_source_language


def test_poll_unknown_job_fails_cleanly():
    """A restarted process loses in-flight jobs — polling an unknown
    external id must fail fast, never hang."""
    import asyncio

    provider = TrueCrimeSearchProvider()
    job = asyncio.run(provider.poll("tce-does-not-exist"))
    assert job.status == "failed"
    assert job.error


def test_provider_name_is_truecrime():
    provider = TrueCrimeSearchProvider()
    assert provider.name == "truecrime"


def test_provider_status_has_no_openrouter_fields():
    import asyncio
    provider = TrueCrimeSearchProvider()
    provider.searxng = _FakeSearx(healthy=True)
    status = asyncio.run(provider.provider_status())
    assert status["provider"] == "truecrime"
    assert status["engine"] == "truecrime_search_engine"
    assert status["search_backend"]["healthy"] is True
    assert "authorized" not in status


class _FakeSearx:
    def __init__(self, healthy=True):
        self._h = healthy

    async def health(self):
        return {"backend": "searxng", "healthy": self._h}


# --- URL canonicalization (Part 8) ------------------------------------------

def test_url_canonicalization_tracking_params():
    u = ("https://www.abc.net.au/news/2026/story?utm_source=tw&"
         "utm_campaign=x&id=42#frag")
    assert canonicalize_url(u) == "abc.net.au/news/2026/story?id=42"


def test_url_canonicalization_variants_equal():
    a = canonicalize_url("http://www.example.com/a/b?utm_source=x")
    b = canonicalize_url("https://m.example.com/a/b")
    c = canonicalize_url("https://example.com/a/b/#comments")
    assert a == b == c


def test_url_canonicalization_youtube_variants():
    vid = "abcdefghijk"
    for u in [
        f"https://www.youtube.com/watch?v={vid}&t=30s",
        f"https://youtu.be/{vid}?si=abc",
        f"https://www.youtube.com/embed/{vid}",
        f"https://www.youtube.com/shorts/{vid}",
    ]:
        assert canonicalize_url(u) == f"youtube.com/watch?v={vid}"
        assert youtube_video_id(u) == vid


def test_url_canonicalization_rejects_garbage():
    assert canonicalize_url("") is None
    assert canonicalize_url("javascript:alert(1)") is None
    assert canonicalize_url("file:///etc/passwd") is None
    assert canonicalize_url("not a url") is None


# --- URL safety / SSRF (Part 9) ----------------------------------------------

def test_safety_rejects_localhost():
    with pytest.raises(UnsafeURLError):
        validate_url("http://localhost:8080/admin")
    with pytest.raises(UnsafeURLError):
        validate_url("http://foo.localhost/")


def test_safety_rejects_private_ips():
    for u in ["http://127.0.0.1/x", "http://10.0.0.5/x",
              "http://192.168.1.1/x", "http://169.254.169.254/latest/meta-data",
              "http://0.0.0.0/x"]:
        with pytest.raises(UnsafeURLError):
            validate_url(u)


def test_safety_rejects_non_http_protocols():
    for u in ["file:///etc/passwd", "javascript:alert(1)",
              "data:text/html,x", "ftp://files.example/x"]:
        with pytest.raises(UnsafeURLError):
            validate_url(u)


def test_safety_accepts_public_https():
    # DNS resolution may fail offline — only malformed/unsafe must raise.
    assert validate_url("https://www.abc.net.au/news/x") == \
        "https://www.abc.net.au/news/x"


# --- Ranking (Part 23) --------------------------------------------------------

def _mk_result(url, title, rank, snippet=""):
    return SearchResult(
        backend="searxng", query="nannup four", language="en",
        rank=rank, url=url, title=title, snippet=snippet,
        canonical_url=canonicalize_url(url),
        domain=url.split("/")[2],
    )


def test_ranking_prefers_relevant_quality():
    results = [
        _mk_result("https://reddit.com/r/x/comments/1", "Nannup Four", 3),
        _mk_result("https://www.abc.net.au/news/nannup-four",
                   "The curious case of the Nannup Four", 1,
                   "The four vanished from Nannup in 2007"),
        _mk_result("https://random-blog.example/post",
                   "My lunch recipes", 2),
    ]
    ranked = rank_results(results, "nannup four disappearance")
    assert ranked[0].result.domain == "www.abc.net.au"
    # irrelevant result rejected or scored far below
    assert ranked[-1].score < ranked[0].score


def test_ranking_rejects_seen_canonical():
    results = [
        _mk_result("https://a.example/story?utm_source=x", "Story", 1),
    ]
    canon = canonicalize_url(results[0].url).split("?")[0]
    ranked = rank_results(results, "story", seen_canonical={canon})
    assert ranked[0].rejected is True
    assert ranked[0].rejection_reason == "duplicate"


def test_select_for_fetch_respects_budget_and_floor():
    results = [
        _mk_result(f"https://news{i}.example.com/a", f"Article {i}", i + 1)
        for i in range(8)
    ]
    ranked = rank_results(results, "article")
    picked = select_for_fetch(ranked, max_fetches=3)
    assert len(picked) == 3
    assert all(not r.rejected for r in picked)


# --- Language detection (Part 17) ---------------------------------------------

def test_language_detection_from_fetched_text():
    de = detect_source_language(
        "Die vier Personen verschwanden im Jahr 2007 aus dem kleinen Ort "
        "Nannup in Westaustralien. Die Polizei suchte jahrelang nach "
        "Hinweisen, doch der Fall blieb ungelöst und mysteriös.")
    assert de.language == "de"
    assert de.method == "fetched_text"

    fa = detect_source_language(
        "چهار نفر در سال ۲۰۰۷ از شهر کوچک نانوپ در استرالیای غربی "
        "ناپدید شدند. پلیس سال‌ها به دنبال سرنخ بود اما پرونده حل نشد.")
    assert fa.language == "fa"

    ar = detect_source_language(
        "اختفى أربعة أشخاص في عام 2007 من بلدة نانوب الصغيرة في غرب "
        "أستراليا. بحثت الشرطة عن أدلة لسنوات لكن القضية بقيت غامضة.")
    assert ar.language == "ar"


def test_language_query_language_not_trusted():
    """A German query returning an English page must detect en."""
    res = detect_source_language(
        "The four people disappeared from the small town of Nannup in "
        "Western Australia in 2007. Police searched for years but the "
        "case remains one of Australia's most baffling mysteries.",
        query_language="de")
    assert res.language == "en"
    assert res.declared == "de"


# --- Extraction (Parts 13-15) --------------------------------------------------

def test_extractor_full_text_classification():
    html = (b"<html><head><title>T</title></head><body><article><p>"
            + b"Real article content. " * 400
            + b"</p></article></body></html>")
    doc = extract(html, "text/html", "https://a.example/x")
    assert doc.text and len(doc.text) > 1000
    assert doc.content_status == "full_text"


def test_extractor_metadata_only_when_no_text():
    doc = extract(b"", "text/html", "https://a.example/x")
    assert doc.text is None
    assert doc.content_status == "unavailable"


def test_extractor_pdf():
    import io
    from pypdf import PdfWriter
    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(buf)
    doc = extract(buf.getvalue(), "application/pdf", "https://a.example/r.pdf")
    assert doc.extractor == "pypdf"
    assert doc.page_count == 1


# --- Orchestrator contract ------------------------------------------------------

def test_orchestrator_result_contract():
    """The engine's result dict must satisfy the research_jobs
    ingestion contract: queries/results/sources + telemetry."""
    import asyncio
    from app.research_engine.orchestrator import (
        EngineConfig, ResearchOrchestrator)
    from app.research_engine.search import SearchBackendRegistry

    class FakeBackend:
        name = "fake"

        def is_available(self):
            return True

        async def search(self, query, language, categories=None,
                         time_range=None, limit=10):
            return [
                SearchResult(
                    backend="fake", query=query, language=language,
                    rank=1, url="https://news.example/article",
                    canonical_url="news.example/article",
                    title="Nannup Four case", snippet="The four vanished",
                    domain="news.example"),
                SearchResult(
                    backend="fake", query=query, language=language,
                    rank=2, url="https://junk.example/ad",
                    canonical_url="junk.example/ad",
                    title="unrelated", snippet="", domain="junk.example"),
            ]

    class FakeFetcher:
        async def fetch(self, url):
            from app.research_engine.fetcher import FetchOutcome
            return FetchOutcome(
                original_url=url,
                canonical_url=canonicalize_url(url),
                final_url=url, status="ok", status_code=200,
                content_type="text/html",
                body=b"<html><body><article><p>" +
                     b"Detailed reporting on the Nannup Four case. " * 200 +
                     b"</p></article></body></html>")

    orch = ResearchOrchestrator(
        registry=SearchBackendRegistry([FakeBackend()]),
        fetcher=FakeFetcher(),
        generation_provider=None,
        config=EngineConfig(max_rounds_per_language=1,
                            max_queries_per_language=1,
                            enable_gap_analysis=False,
                            enable_llm_rerank=False),
    )
    # planner absent -> no queries; force seed via fallback planner path
    orch.planner = None
    result = asyncio.run(orch.research_case(
        {"title": "Nannup Four"}, ["en"]))
    for key in ("queries", "results", "sources", "case",
                "research_gaps", "language_stats", "_telemetry"):
        assert key in result, key


def test_orchestrator_search_to_source_pipeline():
    """One accepted result -> one source with honest full_text status."""
    import asyncio
    from app.research_engine.orchestrator import (
        EngineConfig, ResearchOrchestrator)
    from app.research_engine.search import SearchBackendRegistry
    from app.research_engine.planner import PlannedQuery

    class FakeBackend:
        name = "fake"

        def is_available(self):
            return True

        async def search(self, query, language, categories=None,
                         time_range=None, limit=10):
            return [SearchResult(
                backend="fake", query=query, language=language,
                rank=1, url="https://abc.net.au/news/nannup",
                canonical_url="abc.net.au/news/nannup",
                title="The Nannup Four investigation",
                snippet="Police searched for the four people",
                domain="abc.net.au")]

    class FakeFetcher:
        async def fetch(self, url):
            from app.research_engine.fetcher import FetchOutcome
            return FetchOutcome(
                original_url=url,
                canonical_url=canonicalize_url(url),
                final_url=url, status="ok", status_code=200,
                content_type="text/html",
                body=b"<html><body><article><p>" +
                     b"Detailed reporting on the Nannup Four case. " * 200 +
                     b"</p></article></body></html>")

    class FakePlanner:
        async def plan(self, ctx, lang, **kw):
            return [PlannedQuery(query="nannup four", language=lang)]

    orch = ResearchOrchestrator(
        registry=SearchBackendRegistry([FakeBackend()]),
        fetcher=FakeFetcher(),
        generation_provider=None,
        config=EngineConfig(max_rounds_per_language=1,
                            enable_gap_analysis=False,
                            enable_llm_rerank=False),
    )
    orch.planner = FakePlanner()
    result = asyncio.run(orch.research_case(
        {"title": "Nannup Four"}, ["en"]))
    assert len(result["sources"]) == 1
    src = result["sources"][0]
    assert src["content_status"] == "full_text"
    assert src["full_text_available"] is True
    assert src["retrieval_method"] == "http_fetch"
    assert src["detected_language"] == "en"
    assert result["results"][0]["accepted"] is True
    assert result["_telemetry"]["searches"] >= 1


def test_historical_openrouter_provider_rows_still_render(client, db_session):
    """provider='openrouter' job rows remain readable data."""
    from app.db.models import Case, ResearchJob

    case = db_session.query(Case).first() or Case(
        canonical_title="Hist", slug="hist", language="en")
    db_session.add(case)
    db_session.commit()
    db_session.add(ResearchJob(
        case_id=case.id, provider="openrouter",
        job_type="research", status="completed",
        result_summary="sources_added=3"))
    db_session.commit()
    rows = client.get(f"/api/research-jobs?case_id={case.id}").json()
    assert any(r["provider"] == "openrouter" for r in rows)
