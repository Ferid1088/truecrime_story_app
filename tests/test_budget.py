import pytest
from uuid import uuid4

from app.db.models import Case
from app.services.budget import BudgetService, BudgetStopped


def _case(db):
    case = Case(canonical_title="Budget proof", slug=f"budget-proof-{uuid4().hex[:8]}", language="en",
                total_cost_cap_eur=1.0)
    db.add(case)
    db.commit()
    return case


def test_budget_stops_before_paid_stage_and_can_resume(db_session):
    case = _case(db_session)
    case.total_cost_cap_eur = 20.0
    db_session.commit()
    ledger = BudgetService(db_session)
    row = ledger.record(case_id=case.id, stage="research", model="fake-search",
                        units=10, cost_eur=19.50, tokens_in=4, tokens_out=6)
    assert row.cost_eur == pytest.approx(19.50)
    assert ledger.snapshot(case.id).spent_eur == pytest.approx(19.50)
    with pytest.raises(BudgetStopped, match="would exceed"):
        ledger.check(case.id, "tts", 2.0)
    db_session.refresh(case)
    assert case.status == "budget stopped"
    assert case.budget_stop_reason
    snapshot = ledger.set_cap(case.id, 25.0)
    ledger.check(case.id, "tts", 2.0)
    db_session.refresh(case)
    assert case.status == "story_ready"
    assert snapshot.remaining_eur == pytest.approx(5.5)
    assert db_session.query(type(row)).filter_by(case_id=case.id).count() == 1


def test_budget_records_euro_call_cost_without_double_counting(db_session):
    case = _case(db_session)
    ledger = BudgetService(db_session)
    ledger.record(case_id=case.id, stage="master_story", model="fake-writer",
                  units=100, cost_usd=1.0, cost_eur=2.0)
    assert ledger.snapshot(case.id).spent_eur == pytest.approx(2.0)


def test_actual_provider_usage_stops_the_next_stage_at_cap(db_session):
    """A provider response larger than the estimate must govern the next gate."""
    case = _case(db_session)
    case.total_cost_cap_eur = 1.0
    db_session.commit()
    ledger = BudgetService(db_session)
    estimated = ledger.record(case_id=case.id, stage="research", model="fake",
                              units=100, cost_eur=0.10, estimated=True)
    actual = ledger.record(case_id=case.id, stage="master_story", model="fake",
                           units=1000, cost_eur=1.25, estimated=False)
    assert actual.cost_eur > estimated.cost_eur
    with pytest.raises(BudgetStopped, match="remaining"):
        ledger.check(case.id, "spoken", 0.01)


def test_missing_provider_usage_is_explicitly_estimated(db_session):
    case = _case(db_session)
    row = BudgetService(db_session).record(
        case_id=case.id, stage="research", model="unknown", units=None,
        cost_eur=0.05, estimated=True,
    )
    assert row.estimated is True


def test_recorded_provider_calls_are_excluded_from_case_budget(db_session):
    case = _case(db_session)
    ledger = BudgetService(db_session)
    ledger.record(case_id=case.id, stage="discovery", model="recorded-e2e",
                  units=100, cost_eur=19.99, is_test_data=True)
    assert ledger.snapshot(case.id).spent_eur == pytest.approx(0.0)
    ledger.record(case_id=case.id, stage="research", model="live",
                  units=1, cost_eur=19.50)
    with pytest.raises(BudgetStopped, match="remaining"):
        ledger.check(case.id, "story", 1.0)
