"""S9: the cost estimate includes the picture research — search calls, vision checks, second-pass
checks — and it grows with the length of the film."""

import json
import uuid

from app.core.ai_config import ai_config
from app.db.models import Case, DiscoveryCandidate
from app.services import cost_model as CM


def _case(db):
    case = Case(canonical_title="Estimate", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


def _lines(db, **kw):
    case = _case(db)
    lines, _ = CM.estimate_lines(db, case, ["en"], **kw)
    return {line.stage: line for line in lines}


def test_the_estimate_has_a_line_for_searches_vision_checks_and_second_pass_checks(db_session):
    lines = _lines(db_session, target_minutes=60)
    for stage in ("visual_search", "visual_check", "visual_second_pass"):
        assert stage in lines and lines[stage].expected > 0, stage
    assert lines["visual_search"].units["requests"] > 0
    assert lines["visual_check"].units["checks"] > lines["visual_second_pass"].units["checks"] > 0


def test_the_numbers_follow_the_film_length_and_the_configured_assumptions(db_session):
    import math

    a = json.loads(CM.PRICING_PATH.read_text())["assumptions"]["visual_research"]
    lines = _lines(db_session, target_minutes=60)
    need = 60 * a["requirements_per_minute"] * a["candidates_checked_per_requirement"]
    assert lines["visual_check"].units["checks"] == math.ceil(need)
    assert lines["visual_second_pass"].units["checks"] == math.ceil(lines["visual_check"].units["checks"] * a["second_pass_share"])
    rounds = ai_config.mvp().search
    assert lines["visual_search"].units["requests"] == (
        a["initial_queries"] + (rounds.initial_rounds + rounds.extra_rounds_max - 1) * a["queries_per_round"])


def test_a_longer_film_costs_more_picture_research(db_session):
    short = _lines(db_session, target_minutes=10)
    long = _lines(db_session, target_minutes=60)
    assert long["visual_check"].expected > short["visual_check"].expected
    assert long["visual_search"].expected >= short["visual_search"].expected


def test_the_search_line_says_the_image_price_is_assumed_and_the_checks_use_the_verifier_models(db_session):
    lines = _lines(db_session, target_minutes=60)
    assert "assum" in (lines["visual_search"].price_warning or "").lower()
    assert lines["visual_check"].model == "claude-sonnet-5-5"
    assert lines["visual_second_pass"].model == "gpt-6.1-sol"


def test_the_autopilot_estimate_includes_the_larger_research_and_breaks_it_out(db_session):
    from app.autopilot import service as AS

    cand = DiscoveryCandidate(title="Some case", query="q", rationale="x", fingerprint=uuid.uuid4().hex)
    db_session.add(cand)
    db_session.commit()
    est = AS.estimate_for_candidate(db_session, cand, ["en"])
    assert est["visual_research_eur"] > 0
    assert est["visual_research_eur"] < est["estimated_eur"]
    lines = _lines(db_session, target_minutes=60)
    assert abs(est["visual_research_eur"] - sum(lines[k].expected for k in (
        "visual_search", "visual_check", "visual_second_pass"))) < 0.02
