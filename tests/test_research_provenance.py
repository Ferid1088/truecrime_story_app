"""Research provenance tests — engine level.

Covers multilingual provenance, language detection, relational
query->result->source links, job telemetry, stop conditions, URL
safety, backend fallback, and assembler integration — all against the
first-party research engine, never an LLM vendor's search.
"""

import asyncio
import itertools
import json

import pytest

from app.db.models import (
    Case, ResearchJob, ResearchQuery, ResearchResult, ResearchSourceLink,
    Source,
)
from app.research_engine.fetcher import FetchOutcome
from app.research_engine.language import detect_source_language
from app.research_engine.orchestrator import (
    EngineConfig, Progress, ResearchOrchestrator,
)
from app.research_engine.planner import PlannedQuery
from app.research_engine.safety import UnsafeURLError, validate_url
from app.research_engine.search import SearchBackendRegistry, SearchResult
from app.services.research_jobs import (
    _apply_job_telemetry, _ingest_research, _persist_provenance, create_job,
)
from app.utils import detect_language


# --- fakes -----------------------------------------------------------------


class FakeBackend:
    name = "fake"

    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    def is_available(self):
        return True

    async def search(self, query, language, categories=None,
                     time_range=None, limit=10):
        self.calls.append((query, language))
        if self.error:
            raise self.error
        return [
            SearchResult(
                backend="fake", query=query, language=language,
                rank=i + 1, url=u["url"],
                canonical_url=u.get("canonical_url"),
                title=u.get("title") or u["url"],
                snippet=u.get("snippet"),
                detected_language=u.get("detected_language"))
            for i, u in enumerate(self.results)
        ]


class FakeFetcher:
    def __init__(self, outcome=None, handler=None):
        self.outcome = outcome
        self.handler = handler
        self.calls = []

    async def fetch(self, url):
        self.calls.append(url)
        if self.handler:
            return self.handler(url)
        return self.outcome


class FakePlanner:
    """Scripted per-call plans: list of lists of PlannedQuery."""

    def __init__(self, plans):
        self.plans = plans
        self.calls = 0

    async def plan(self, case_context, target_language, **kw):
        idx = min(self.calls, len(self.plans) - 1)
        self.calls += 1
        return self.plans[idx]


def _pq(query, language, purpose="local_news", priority=1):
    return PlannedQuery(query=query, language=language, purpose=purpose,
                        priority=priority)


_ARTICLE = """<html><head><title>Case report</title></head><body>
<article><h1>The Nannup Four</h1>
<p>The Nannup Four were a group of four people who disappeared in
Western Australia. Police investigated the case for years and the
families were informed of every development as the search continued
across the southwest region of the state.</p>
<p>Detectives searched rural properties and interviewed witnesses who
reported sightings near the town. The investigation remained open and
officers continued to appeal for information from the public about the
missing persons.</p></article></body></html>"""

_DE_ARTICLE = """<html><head><title>Vermisstenfall</title></head><body>
<article><p>Die Polizei in Westaustralien hat die Suche nach den vier
Vermissten eingestellt. Die Familie wurde über die Entscheidung des
Gerichts informiert, nachdem die Ermittlungen nicht weiter geführt
werden konnten. Die Beamten durchsuchten ländliche Grundstücke und
befragten Zeugen in der Umgebung der Kleinstadt.</p></article>
</body></html>"""


def _ok_outcome(html=_ARTICLE, final_url=None, url="https://a.example/x"):
    return FetchOutcome(
        original_url=url, canonical_url=None,
        status="ok", status_code=200, content_type="text/html",
        body=html.encode("utf-8"), final_url=final_url)


def _orchestrator(backend, outcome=None, plans=None, **cfg):
    """Orchestrator with fake infra; planner injected directly so the
    LLM layer is never touched."""
    orch = ResearchOrchestrator(
        registry=SearchBackendRegistry([backend]),
        fetcher=FakeFetcher(outcome=outcome),
        generation_provider=None,
        config=EngineConfig(
            min_full_text_chars=100, min_accept_score=0.0,
            enable_llm_rerank=False, enable_gap_analysis=False,
            **cfg),
    )
    if plans is not None:
        orch.planner = FakePlanner(plans)
    return orch


def _stage_recorder():
    prog = Progress()
    prog.stages = []
    prog.stage = prog.stages.append
    return prog


# --- per-language query runs ------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "de", "fa", "ar"])
def test_language_query_run(lang):
    orch = _orchestrator(
        FakeBackend([{"url": f"https://{lang}.example/a"}]),
        outcome=_ok_outcome(),
        plans=[[_pq("q1", lang)]],
        max_rounds_per_language=1)
    result = asyncio.run(orch.research_case({"title": "Case X"}, [lang]))
    assert result["queries"], f"{lang}: no queries logged"
    assert all(q["language"] == lang for q in result["queries"])
    assert result["language_stats"][lang]["sources_found"] >= 1
    assert result["language_stats"][lang]["sources_accepted"] >= 1


# --- query language differs from source language -----------------------------


def test_query_language_differs_from_source_language():
    """A de query surfacing an English page keeps both facts:
    query.language=de, source language detected from content — never
    relabeled."""
    snippet = ("This is an English summary of the case with the people "
               "and the facts and what was reported by the police.")
    orch = _orchestrator(
        FakeBackend([{"url": "https://en.example/a",
                      "title": "English article",
                      "snippet": snippet,
                      "detected_language": "en"}]),
        outcome=_ok_outcome(),
        plans=[[_pq("q1", "de")]],
        max_rounds_per_language=1)
    result = asyncio.run(orch.research_case({"title": "Case X"}, ["de"]))
    assert result["queries"][0]["language"] == "de"
    res = result["results"][0]
    assert res["result_language"] == "en"
    src = result["sources"][0]
    assert src["language"] == "en"
    assert src["declared_language"] == "de"  # query language preserved


def test_language_detected_from_fetched_text():
    """Fetched text wins over the query language and page metadata."""
    german = (
        "Die Polizei in Westaustralien hat die Suche nach den vier "
        "Vermissten eingestellt. Die Familie wurde über die Entscheidung "
        "des Gerichts informiert, nachdem die Ermittlungen nicht weiter "
        "geführt werden konnten. "
    ) * 4
    res = detect_source_language(
        german, title="English title", snippet="English snippet",
        html_lang="en", query_language="en")
    assert res.language == "de"
    assert res.method == "fetched_text"
    assert res.declared == "en"


def test_short_content_falls_back_to_query_language():
    res = detect_source_language(
        None, title="کوتاه", snippet="کوتاه", query_language="fa")
    assert res.language is None
    assert res.method == "unknown"
    # The orchestrator falls back to the query language for the record.
    assert (res.language or "fa") == "fa"


def test_html_metadata_language_used_when_no_text():
    res = detect_source_language(
        None, title="Hi", snippet=None, html_lang="de-DE",
        query_language="en")
    assert res.language == "de"
    assert res.method == "page_metadata"


def test_detect_language_unit():
    assert detect_language(
        "The police said the family was informed and the case is on hold. "
        "They had been missing for weeks and the search was called off."
    )[0] == "en"
    assert detect_language(
        "Die Polizei hat die Suche eingestellt und die Familie wurde über "
        "die Entscheidung des Gerichts informiert. "
    )[0] == "de"
    assert detect_language(
        "پلیس استرالیا جستجو برای چهار نفر گمشده را متوقف کرد. خانواده "
        "آنها در مورد تصمیم دادگاه مطلع شدند و پرونده این پرونده است."
    )[0] == "fa"
    assert detect_language(
        "قالت الشرطة إن البحث عن الأربعة المفقودين قد توقف وتم إبلاغ "
        "العائلة بقرار المحكمة بعد أن لم تتمكن التحقيقات من الاستمرار."
    )[0] == "ar"


# --- helpers for DB-level tests ----------------------------------------------


_case_counter = itertools.count()


def _mk_case(db):
    n = next(_case_counter)
    c = Case(canonical_title=f"Prov Case {n}", slug=f"prov-case-{n}")
    db.add(c)
    db.commit()
    return c


def _result_payload():
    return {
        "queries": [
            {"language": "en", "query": "nannup four", "purpose": "news",
             "priority": "high", "round": 0},
            {"language": "de", "query": "nannup vier", "purpose": "archive",
             "priority": "medium", "round": 0},
        ],
        "results": [
            {"query_idx": 0, "url": "https://a.example/x", "title": "A",
             "canonical_url": "a.example/x", "accepted": True,
             "rejection_reason": None, "fetch_status": "fetched",
             "final_url": "https://www.a.example/x?r=1",
             "result_language": "en", "relevance": 0.9},
            {"query_idx": 1, "url": "https://a.example/x?utm=1",
             "title": "A dup", "canonical_url": "a.example/x",
             "accepted": False, "rejection_reason": "duplicate",
             "fetch_status": "not_attempted", "result_language": "de"},
            {"query_idx": 0, "url": "https://bad", "title": "bad",
             "accepted": False, "rejection_reason": "invalid_url",
             "fetch_status": "not_attempted"},
        ],
        "sources": [
            {"url": "https://a.example/x", "title": "A", "language": "en",
             "declared_language": "en", "detected_language": "en",
             "language_confidence": 0.9,
             "language_detection_method": "title_snippet",
             "summary": "s", "_result_idx": 0},
        ],
        "language_stats": {"en": {"queries": 1}, "de": {"queries": 1}},
        "_telemetry": {
            "calls": 5, "searches": 2, "fetches": 1,
            "input_tokens": 100, "output_tokens": 200,
            "cost_usd": 0.05, "search_cost_usd": 0.02,
            "fetch_cost_usd": 0.01, "aux_cost_usd": 0.02,
            "models_used": ["gpt-5.6-luna"],
        },
    }


# --- relational provenance ----------------------------------------------------


def test_provenance_persistence_and_multi_query_discovery(db_session):
    db = db_session
    case = _mk_case(db)
    job = create_job(db, "research", {"case_title": case.canonical_title},
                     case_id=case.id)
    payload = _result_payload()
    out = _ingest_research(db, job, payload)
    _apply_job_telemetry(job, payload)
    db.commit()

    assert out["sources_added"] == 1
    qs = db.query(ResearchQuery).filter_by(research_job_id=job.id).all()
    assert len(qs) == 2
    assert {q.language for q in qs} == {"en", "de"}

    rs = db.query(ResearchResult).join(ResearchQuery).filter(
        ResearchQuery.research_job_id == job.id).all()
    assert len(rs) == 3
    accepted = [r for r in rs if r.accepted]
    assert len(accepted) == 1
    reasons = {r.rejection_reason for r in rs if not r.accepted}
    assert reasons == {"duplicate", "invalid_url"}

    links = (
        db.query(ResearchSourceLink)
        .join(ResearchResult)
        .join(ResearchQuery)
        .filter(ResearchQuery.research_job_id == job.id)
        .all()
    )
    src = db.query(Source).filter_by(case_id=case.id).one()
    # one canonical link + one duplicate link -> same source, two queries
    assert {l.link_type for l in links} == {"canonical", "duplicate"}
    assert {l.source_id for l in links} == {src.id}
    q_langs = {
        db.get(ResearchResult, l.research_result_id).query.language
        for l in links
    }
    assert q_langs == {"en", "de"}

    # telemetry promoted to columns
    assert job.search_calls == 2 and job.fetch_calls == 1
    assert job.total_tokens == 300
    assert abs(job.total_cost_usd - 0.05) < 1e-9
    assert job.sources_discovered == 3
    assert job.sources_accepted == 1
    assert job.sources_rejected == 2
    assert json.loads(job.languages_completed) == ["de", "en"]
    assert job.model == "gpt-5.6-luna"
    assert job.profile == "DEEP_CASE_RESEARCH"


def test_source_language_detection_columns_persisted(db_session):
    db = db_session
    case = _mk_case(db)
    job = create_job(db, "research", {}, case_id=case.id)
    _ingest_research(db, job, _result_payload())
    src = db.query(Source).filter_by(case_id=case.id).one()
    assert src.declared_language == "en"
    assert src.detected_language == "en"
    assert src.language_detection_method == "title_snippet"


def test_queries_endpoint_and_source_discovered_by(db_session, client):
    db = db_session
    case = _mk_case(db)
    job = create_job(db, "research", {}, case_id=case.id)
    _ingest_research(db, job, _result_payload())
    db.commit()

    res = client.get(f"/api/research-jobs/{job.id}/queries")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    en_q = next(q for q in data if q["language"] == "en")
    assert en_q["query"] == "nannup four"
    assert any(r["rejection_reason"] == "invalid_url"
               for r in en_q["results"])

    src = db.query(Source).filter_by(case_id=case.id).one()
    res = client.get(f"/api/cases/{case.id}/sources/{src.id}")
    assert res.status_code == 200
    d = res.json()
    assert d["detected_language"] == "en"
    assert {q["language"] for q in d["discovered_by"]} == {"en", "de"}


# --- redirect preservation -----------------------------------------------------


def test_redirect_final_url_preserved():
    orch = _orchestrator(
        FakeBackend([{"url": "https://short.example/r"}]),
        outcome=_ok_outcome(final_url="https://final.example/article"),
        plans=[[_pq("q1", "en")]],
        max_rounds_per_language=1)
    result = asyncio.run(orch.research_case({"title": "C"}, ["en"]))
    entry = result["results"][0]
    assert entry["url"] == "https://short.example/r"
    assert entry["final_url"] == "https://final.example/article"
    assert result["sources"][0]["url"] == "https://final.example/article"


# --- URL safety --------------------------------------------------------------


def test_unsafe_urls_rejected():
    for bad in ("javascript:alert(1)", "data:text/html,x",
                "ftp://host/file", "file:///etc/passwd",
                "http://localhost/x", "https://x.localhost/a",
                "http://127.0.0.1/a", "http://10.0.0.5/a",
                "http://169.254.169.254/latest/meta-data",
                "http://[::1]/a"):
        with pytest.raises(UnsafeURLError):
            validate_url(bad)
    # Valid public URLs pass (DNS resolution not required here).
    assert validate_url("https://example.com/a?b=1",
                        resolve_dns=False)


def test_fetch_failure_produces_honest_rejection():
    """An unfetchable URL is never fabricated — it becomes a rejected
    result (or a metadata_only source with real failure provenance)."""
    orch = _orchestrator(
        FakeBackend([{"url": "https://dead.example/x"}]),
        outcome=FetchOutcome(
            original_url="https://dead.example/x", canonical_url=None,
            status="failed", status_code=None,
            error="request_failed:ConnectError"),
        plans=[[_pq("q1", "en")]],
        max_rounds_per_language=1)
    result = asyncio.run(orch.research_case({"title": "C"}, ["en"]))
    entry = result["results"][0]
    assert entry["rejection_reason"].startswith("fetch_")
    src = result["sources"][0]
    assert src["content_status"] == "metadata_only"
    assert not src.get("full_text")


# --- assembler integration ----------------------------------------------------


def test_assembler_used_by_production():
    class FakeAssembler:
        async def summarize(self, case_context, sources):
            self.called_with = len(sources)
            return {"summary": "s"}

    orch = _orchestrator(
        FakeBackend([{"url": "https://a.example/1"}]),
        outcome=_ok_outcome(),
        plans=[[_pq("q1", "en")]],
        max_rounds_per_language=1)
    orch.assembler = FakeAssembler()
    result = asyncio.run(orch.research_case({"title": "C"}, ["en"]))
    assert result["case"]["summary"] == "s"
    assert orch.assembler.called_with == 1


# --- stage updates -------------------------------------------------------------


def test_stage_updates_during_research():
    orch = _orchestrator(
        FakeBackend([{"url": "https://a.example/1"}]),
        outcome=_ok_outcome(),
        plans=[[_pq("q1", "en")]],
        max_rounds_per_language=1)
    stages = []
    prog = Progress(stage_cb=stages.append)
    asyncio.run(orch.research_case({"title": "C"}, ["en"], progress=prog))
    assert "planning:en" in stages
    assert "searching:en" in stages
    assert "fetching:en" in stages
    assert stages[-1] == "assembling"


def test_poll_exposes_stage_and_telemetry_meta():
    from app.research_engine.provider import TrueCrimeSearchProvider, _JOBS

    provider = TrueCrimeSearchProvider()
    ext = "tce-teststage"
    _JOBS[ext] = {
        "status": "running", "result": None, "error": None,
        "stage": "searching:de",
        "meta": {"telemetry": {"searches": 3, "cost_usd": 0.1}},
    }
    pjob = asyncio.run(provider.poll(ext))
    assert pjob.meta["stage"] == "searching:de"
    assert pjob.meta["telemetry"]["searches"] == 3


def test_poll_unknown_job_fails_clearly():
    """A job id we never saw must fail fast, never hang."""
    from app.research_engine.provider import TrueCrimeSearchProvider

    provider = TrueCrimeSearchProvider()
    pjob = asyncio.run(provider.poll("tce-doesnotexist"))
    assert pjob.status == "failed"
    assert "unknown" in (pjob.error or "").lower()


# --- stop conditions -----------------------------------------------------------


def test_max_queries_stop():
    orch = _orchestrator(
        FakeBackend([{"url": "https://a.example/1"}]),
        outcome=_ok_outcome(),
        plans=[[_pq("q1", "en")], [_pq("q2", "en")], [_pq("q3", "en")]],
        max_queries_per_language=2, max_rounds_per_language=4)
    result = asyncio.run(orch.research_case({"title": "C"}, ["en"]))
    stats = result["language_stats"]["en"]
    assert stats["queries"] == 2
    assert stats["stop_reason"] == "max_queries"


def test_max_rounds_stop():
    orch = _orchestrator(
        FakeBackend([{"url": "https://a.example/1"}]),
        outcome=_ok_outcome(),
        plans=[[_pq("q1", "en")]],
        max_rounds_per_language=1)
    result = asyncio.run(orch.research_case({"title": "C"}, ["en"]))
    stats = result["language_stats"]["en"]
    assert stats["stop_reason"] == "max_rounds"


def test_low_novelty_stop():
    """Repeated identical URLs across rounds produce no new evidence —
    the loop stops instead of burning fetches."""
    same = [{"url": "https://a.example/1"}]
    orch = _orchestrator(
        FakeBackend(same),
        outcome=_ok_outcome(),
        plans=[[_pq("q1", "en")], [_pq("q2", "en")], [_pq("q3", "en")],
               [_pq("q4", "en")], [_pq("q5", "en")]],
        max_rounds_per_language=5,
        consecutive_low_novelty_stop=2)
    result = asyncio.run(orch.research_case({"title": "C"}, ["en"]))
    stats = result["language_stats"]["en"]
    assert stats["stop_reason"] == "low_novelty"
    assert stats["sources_accepted"] == 1


def test_max_fetches_stop():
    """Fetch budget is enforced even when more results are ranked."""
    orch = _orchestrator(
        FakeBackend([{"url": "https://a.example/1"},
                     {"url": "https://b.example/2"},
                     {"url": "https://c.example/3"}]),
        outcome=_ok_outcome(),
        plans=[[_pq("q1", "en")]],
        max_fetches_per_language=1,
        max_rounds_per_language=1)
    result = asyncio.run(orch.research_case({"title": "C"}, ["en"]))
    stats = result["language_stats"]["en"]
    assert stats["fetch_calls"] == 1
    assert len(orch.fetcher.calls) == 1


def test_planner_empty_stop():
    orch = _orchestrator(
        FakeBackend([{"url": "https://a.example/1"}]),
        outcome=_ok_outcome(),
        plans=[[]],
        max_rounds_per_language=3)
    result = asyncio.run(orch.research_case({"title": "C"}, ["en"]))
    assert result["language_stats"]["en"]["stop_reason"] == "planner_empty"


# --- backend fallback -----------------------------------------------------------


def test_registry_falls_back_to_next_backend():
    class FailBackend(FakeBackend):
        name = "fail"

        def is_available(self):
            return True

        async def search(self, *a, **kw):
            raise RuntimeError("backend down")

    good = FakeBackend([{"url": "https://a.example/1"}])
    reg = SearchBackendRegistry([FailBackend(), good])
    out = asyncio.run(reg.search("q", "en"))
    assert out and out[0].url == "https://a.example/1"


def test_backend_failure_is_not_fatal():
    """A failing search backend degrades to zero results, never crashes
    the research run."""
    class FailBackend(FakeBackend):
        name = "fail"

        async def search(self, *a, **kw):
            raise RuntimeError("backend down")

    orch = _orchestrator(
        FailBackend(),
        outcome=_ok_outcome(),
        plans=[[_pq("q1", "en")]],
        max_rounds_per_language=1)
    result = asyncio.run(orch.research_case({"title": "C"}, ["en"]))
    stats = result["language_stats"]["en"]
    assert stats["search_calls"] == 1
    assert stats["sources_found"] == 0
    assert result["queries"]


# --- corpus dedupe against existing sources --------------------------------------


def test_existing_corpus_urls_rejected_as_duplicates():
    """URLs already in the case corpus are rejected before fetching."""
    backend = FakeBackend([{"url": "https://a.example/x?b=1"}])
    orch = _orchestrator(
        backend, outcome=_ok_outcome(),
        plans=[[_pq("q1", "en")]],
        max_rounds_per_language=1)
    corpus = {"canonical_urls": ["a.example/x"]}
    result = asyncio.run(orch.research_case(
        {"title": "C"}, ["en"], corpus=corpus))
    assert result["results"][0]["rejection_reason"] == "duplicate"
    assert result["sources"] == []
    assert orch.fetcher.calls == []


# --- provider correctness -------------------------------------------------------


def test_new_jobs_default_to_truecrime(db_session):
    case = _mk_case(db_session)
    job = create_job(db_session, "research",
                     {"case_title": case.canonical_title}, case_id=case.id)
    assert job.provider == "truecrime"
    assert job.current_stage == "queued"
    assert job.profile == "DEEP_CASE_RESEARCH"


def test_historical_devin_job_renders(db_session, client):
    case = _mk_case(db_session)
    job = ResearchJob(
        case_id=case.id, provider="devin", job_type="research",
        status="completed", result_summary="historical",
    )
    db_session.add(job)
    db_session.commit()
    res = client.get(f"/api/research-jobs/{job.id}")
    assert res.status_code == 200
    assert res.json()["provider"] == "devin"
    assert res.json()["status"] == "completed"


def test_historical_openrouter_job_renders(db_session, client):
    """Old OpenRouter research jobs stay readable after migration."""
    case = _mk_case(db_session)
    job = ResearchJob(
        case_id=case.id, provider="openrouter", job_type="research",
        status="completed", result_summary="historical openrouter run",
    )
    db_session.add(job)
    db_session.commit()
    res = client.get(f"/api/research-jobs/{job.id}")
    assert res.status_code == 200
    assert res.json()["provider"] == "openrouter"
    assert res.json()["status"] == "completed"
