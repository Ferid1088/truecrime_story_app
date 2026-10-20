from uuid import uuid4

import pytest

from app.db.models import Case
from app.services.budget import BudgetService, BudgetStopped


def _case(db):
    case = Case(canonical_title="Budget proof", slug=f"budget-proof-{uuid4().hex[:8]}", language="en",
                total_cost_cap_eur=1.0)
    db.add(case)
    db.commit()
    return case


def test_budget_stops_at_cap_and_can_resume_after_raising_it(db_session):
    case = _case(db_session)
    case.total_cost_cap_eur = 20.0
    db_session.commit()
    ledger = BudgetService(db_session)
    row = ledger.record(case_id=case.id, stage="research", model="fake-search",
                        units=10, cost_eur=20.0, tokens_in=4, tokens_out=6)
    assert row.cost_eur == pytest.approx(20.0)
    assert ledger.snapshot(case.id).spent_eur == pytest.approx(20.0)
    with pytest.raises(BudgetStopped, match=r"case budget €20.00 reached: spent €20.00"):
        ledger.check(case.id, "tts")
    db_session.refresh(case)
    assert case.status == "budget stopped"
    assert case.budget_stop_reason
    snapshot = ledger.set_cap(case.id, 25.0)
    ledger.check(case.id, "tts")  # no longer at the (now higher) cap: proceeds
    db_session.refresh(case)
    assert case.status == "story_ready"
    assert snapshot.remaining_eur == pytest.approx(5.0)
    assert db_session.query(type(row)).filter_by(case_id=case.id).count() == 1


def test_budget_check_allows_19_99_and_stops_only_at_20_00(db_session):
    # The only cost limit: actual recorded spend against the EUR cap.
    # No estimate is ever involved in this comparison.
    case = _case(db_session)
    case.total_cost_cap_eur = 20.0
    db_session.commit()
    ledger = BudgetService(db_session)
    ledger.record(case_id=case.id, stage="research", model="fake", units=1, cost_eur=19.99)
    ledger.check(case.id, "master_story")  # 19.99 < 20.00: the call may proceed
    ledger.record(case_id=case.id, stage="research", model="fake", units=1, cost_eur=0.01)
    with pytest.raises(BudgetStopped, match=r"case budget €20.00 reached: spent €20.00"):
        ledger.check(case.id, "master_story")


def test_budget_records_euro_call_cost_without_double_counting(db_session):
    case = _case(db_session)
    ledger = BudgetService(db_session)
    ledger.record(case_id=case.id, stage="master_story", model="fake-writer",
                  units=100, cost_usd=1.0, cost_eur=2.0)
    assert ledger.snapshot(case.id).spent_eur == pytest.approx(2.0)


def test_budget_record_keeps_the_measured_call_duration(db_session):
    # A writer call can legitimately take minutes (see
    # tests/test_pilot_master_regressions.py); the ledger must keep that
    # instead of silently recording every call as instantaneous, or nothing
    # in the job/case can ever show real provider latency.
    case = _case(db_session)
    ledger = BudgetService(db_session)
    row = ledger.record(case_id=case.id, stage="master_approval", model="fake-writer",
                        units=100, cost_eur=0.03, duration_ms=334126)
    assert row.duration_ms == 334126

    no_duration = ledger.record(case_id=case.id, stage="master_approval", model="fake-writer",
                                units=100, cost_eur=0.03)
    assert no_duration.duration_ms == 0


def test_actual_spend_crossing_the_cap_stops_the_next_call(db_session):
    """A provider response larger than any estimate still governs the next gate."""
    case = _case(db_session)
    case.total_cost_cap_eur = 1.0
    db_session.commit()
    ledger = BudgetService(db_session)
    estimated = ledger.record(case_id=case.id, stage="research", model="fake",
                              units=100, cost_eur=0.10, estimated=True)
    actual = ledger.record(case_id=case.id, stage="master_story", model="fake",
                           units=1000, cost_eur=1.25, estimated=False)
    assert actual.cost_eur > estimated.cost_eur
    with pytest.raises(BudgetStopped, match="case budget"):
        ledger.check(case.id, "spoken")


def test_missing_provider_usage_is_explicitly_estimated(db_session):
    case = _case(db_session)
    row = BudgetService(db_session).record(
        case_id=case.id, stage="research", model="unknown", units=None,
        cost_eur=0.05, estimated=True,
    )
    assert row.estimated is True


def test_recorded_provider_calls_are_excluded_from_case_budget(db_session):
    case = _case(db_session)
    case.total_cost_cap_eur = 20.0
    db_session.commit()
    ledger = BudgetService(db_session)
    ledger.record(case_id=case.id, stage="discovery", model="recorded-e2e",
                  units=100, cost_eur=19.99, is_test_data=True)
    # Test-data spend never counts: the case is nowhere near its cap.
    assert ledger.snapshot(case.id).spent_eur == pytest.approx(0.0)
    ledger.check(case.id, "research")  # must not raise
    ledger.record(case_id=case.id, stage="research", model="live", units=1, cost_eur=20.0)
    with pytest.raises(BudgetStopped, match="case budget"):
        ledger.check(case.id, "story")


def test_a_new_case_with_no_explicit_cap_tracks_the_configured_default(db_session, monkeypatch):
    """A case created with no explicit total_cost_cap_eur must track whatever
    config/parameters/policies.json's budget.total_cap_eur currently is —
    never a value baked in at creation time, or raising the configured
    default later would silently not apply to any case created earlier."""
    from app.core.ai_config import ai_config

    case = Case(canonical_title="No explicit cap", slug=f"no-cap-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    assert case.total_cost_cap_eur is None

    monkeypatch.setattr(ai_config.budget, "total_cap_eur", 123.0)
    assert BudgetService(db_session).snapshot(case.id).cap_eur == pytest.approx(123.0)

    monkeypatch.setattr(ai_config.budget, "total_cap_eur", 456.0)
    assert BudgetService(db_session).snapshot(case.id).cap_eur == pytest.approx(456.0)
