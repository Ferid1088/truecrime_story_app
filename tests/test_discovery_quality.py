import asyncio

from app.lifecycle.identity import IdentityIndex
from app.lifecycle.selection import legal_risk_flags, prepare_candidates
from app.research_engine.orchestrator import _discovery_queries
from tests.test_case_lifecycle import FakeGen, FakeRegistry


def test_historical_theme_uses_pre_2000_queries():
    queries = _discovery_queries("historical cases (before 2000)", "en")
    assert queries
    assert all("before 2000" in query.lower() for query in queries)
    assert queries != _discovery_queries("true crime", "en")


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
        raw, [], IdentityIndex([]), count=1, include_unsolved=True,
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
        raw, [], IdentityIndex([]), count=1, include_unsolved=True,
    ))
    row = result["candidates"][0]
    assert row["selection"]["status"] == "STATUS_UNDER_REVIEW"
    assert row["legal_risk"] is True
    assert row["legal_risk_reasons"] == ["suspect not convicted"]
