import asyncio

from app.lifecycle.selection import legal_risk_flags, prepare_candidates
from app.research_engine.orchestrator import _discovery_queries
from app.lifecycle.identity import IdentityIndex


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
            "rationale": "The suspect was convicted and sentenced.",
            "resolution_status": "SOLVED",
            "resolution_evidence": "Convicted and sentenced.",
        },
    ]
    result = asyncio.run(prepare_candidates(
        raw, [], IdentityIndex([]), count=1, include_unsolved=True,
    ))
    suggested = [row for row in result["candidates"] if row["selection"]["state"] == "suggested"]
    filtered = [row for row in result["candidates"] if row["selection"]["state"] == "not_shown"]
    assert suggested[0]["title"] == "Resolved case"
    assert filtered[0]["legal_risk"] is True
