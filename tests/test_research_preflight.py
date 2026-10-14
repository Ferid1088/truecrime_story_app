from types import SimpleNamespace

from app.documentary.api import _research_preflight


def test_placeholder_case_shows_warning_and_exact_queries():
    out = _research_preflight(SimpleNamespace(
        canonical_title="DRYRUN english",
        summary=None,
        aliases_json="[]",
        people_json="[]",
        location=None,
        incident_date=None,
    ))
    assert out["needs_details"] is True
    assert out["warning"] == "Research will search for: 'DRYRUN english'. Add details for better results."
    assert out["queries"] == ["DRYRUN english"]


def test_described_case_has_no_warning_and_includes_identity_queries():
    out = _research_preflight(SimpleNamespace(
        canonical_title="Harbor disappearance",
        summary="A disappearance investigated by Rotterdam police.",
        aliases_json='["Harbor case"]',
        people_json='["Alex Doe"]',
        location="Rotterdam",
        incident_date="2020-04-01",
    ))
    assert out["needs_details"] is False
    assert out["warning"] is None
    assert out["queries"] == ["Harbor disappearance", "Harbor case", "Alex Doe", "Rotterdam"]
