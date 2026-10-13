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
    ledger = BudgetService(db_session)
    row = ledger.record(case_id=case.id, stage="research", model="fake-search",
                        units=10, cost_usd=0.80, tokens_in=4, tokens_out=6)
    assert row.cost_eur == pytest.approx(0.80 * 0.92)
    assert ledger.snapshot(case.id).spent_eur == pytest.approx(row.cost_eur)
    with pytest.raises(BudgetStopped, match="would exceed"):
        ledger.check(case.id, "tts", 0.5)
    db_session.refresh(case)
    assert case.status == "budget stopped"
    assert case.budget_stop_reason
    snapshot = ledger.set_cap(case.id, 5.0)
    db_session.refresh(case)
    assert case.status == "story_ready"
    assert snapshot.remaining_eur > 4.0


def test_budget_records_euro_call_cost_without_double_counting(db_session):
    case = _case(db_session)
    ledger = BudgetService(db_session)
    ledger.record(case_id=case.id, stage="master_story", model="fake-writer",
                  units=100, cost_usd=1.0, cost_eur=2.0)
    assert ledger.snapshot(case.id).spent_eur == pytest.approx(2.0)
