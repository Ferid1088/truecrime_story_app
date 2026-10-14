"""Case-wide EUR budget checks and provider-cost ledger helpers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, ProviderCall
from app.utils import utc_now


class BudgetStopped(RuntimeError):
    def __init__(self, case_id: int, stage: str, reason: str):
        super().__init__(reason)
        self.case_id, self.stage, self.reason = case_id, stage, reason


@dataclass(frozen=True)
class BudgetSnapshot:
    cap_eur: float
    spent_eur: float
    remaining_eur: float
    status: str


def _cap(case: Case) -> float:
    return float(case.total_cost_cap_eur or ai_config.budget.total_cap_eur)


class BudgetService:
    def __init__(self, db: Session):
        self.db = db

    def snapshot(self, case_id: int) -> BudgetSnapshot:
        case = self.db.get(Case, case_id)
        if case is None:
            raise ValueError("case not found")
        eur = self.db.query(func.coalesce(func.sum(ProviderCall.cost_eur), 0.0)).filter(
            ProviderCall.case_id == case_id,
            ProviderCall.is_test_data.is_(False),
        ).scalar() or 0.0
        # Legacy ledger rows have USD only; convert them once at read time.
        legacy_usd = self.db.query(func.coalesce(func.sum(ProviderCall.cost_usd), 0.0)).filter(
            ProviderCall.case_id == case_id,
            ProviderCall.cost_eur.is_(None),
            ProviderCall.is_test_data.is_(False),
        ).scalar() or 0.0
        spent = float(eur) + float(legacy_usd) * ai_config.budget.usd_to_eur
        cap = _cap(case)
        return BudgetSnapshot(cap, round(spent, 6), round(max(cap - spent, 0.0), 6), case.status)

    def check(self, case_id: int, stage: str, estimated_cost_eur: float) -> BudgetSnapshot:
        current = self.snapshot(case_id)
        actual_stage = self.db.query(func.coalesce(func.sum(ProviderCall.cost_eur), 0.0)).filter(
            ProviderCall.case_id == case_id, ProviderCall.stage == stage,
            ProviderCall.cost_eur.is_not(None), ProviderCall.estimated.is_(False),
            ProviderCall.is_test_data.is_(False),
        ).scalar() or 0.0
        required = max(float(estimated_cost_eur), float(actual_stage))
        if required > current.remaining_eur + 1e-9:
            case = self.db.get(Case, case_id)
            assert case is not None
            reason = (f"Budget stopped before {stage}: max(estimate €{estimated_cost_eur:.2f}, "
                      f"actual so far €{float(actual_stage):.2f}) = €{required:.2f} "
                      f"would exceed the remaining €{current.remaining_eur:.2f}.")
            case.status = "budget stopped"
            case.budget_stop_reason = reason
            self.db.commit()
            raise BudgetStopped(case_id, stage, reason)
        return current

    def record(self, *, case_id: int, stage: str, model: str | None,
               units: float | None, cost_usd: float | None = None,
               cost_eur: float | None = None, tokens_in: int | None = None,
               tokens_out: int | None = None, ok: bool = True,
               error: str | None = None, estimated: bool = False,
               documentary_job_id: int | None = None,
               is_test_data: bool = False) -> ProviderCall:
        converted = cost_eur
        if converted is None and cost_usd is not None:
            converted = float(cost_usd) * ai_config.budget.usd_to_eur
        row = ProviderCall(created_at=utc_now(), host="budget-ledger", method="CALL",
                           path=f"/{stage}", status=200 if ok else 402, ok=ok,
                           duration_ms=0, tokens_in=tokens_in, tokens_out=tokens_out,
                           cost_usd=cost_usd, error=error, case_id=case_id,
                           stage=stage, model=model, units=units, cost_eur=converted,
                           estimated=estimated, is_test_data=is_test_data,
                           documentary_job_id=documentary_job_id)
        self.db.add(row)
        self.db.commit()
        return row

    def set_cap(self, case_id: int, cap_eur: float) -> BudgetSnapshot:
        if cap_eur <= 0:
            raise ValueError("budget cap must be greater than zero")
        case = self.db.get(Case, case_id)
        if case is None:
            raise ValueError("case not found")
        case.total_cost_cap_eur = cap_eur
        case.budget_stop_reason = None
        if case.status == "budget stopped":
            case.status = "story_ready"
        self.db.commit()
        return self.snapshot(case_id)
