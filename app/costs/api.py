"""The cost dashboard's API: an overview over every case, and for one case the matrix, the vendors, the drill-down to the single calls, the live feed, the analysis,
and the vendors' bills the owner types in."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.costs import dedupe
from app.costs import queries as Q
from app.costs import registry as R
from app.db.base import get_db
from app.db.models import Case, CostInvoice
from app.utils import utc_now

router = APIRouter(prefix="/api/costs", tags=["costs"])
RECENT = timedelta(hours=2)


class InvoiceIn(BaseModel):
    vendor: str = Field(min_length=1, max_length=40)
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    amount_eur: float = Field(ge=0, le=1_000_000)
    note: str | None = Field(default=None, max_length=300)


def _fresh(db: Session, case_id: int | None = None) -> None:
    """Marks the repeats of the last hours first, so what is shown never counts a call twice."""
    dedupe.mark_duplicates(db, case_id=case_id, since=utc_now() - RECENT)


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _filters(step, language, vendor, model, subject, accuracy, day) -> dict:
    return {"step": step, "language": language, "vendor": vendor, "model": model, "subject": subject, "accuracy": accuracy, "day": day}


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    _fresh(db)
    return {**Q.overview(db), "reconciliation": Q.reconciliation(db), "vendors": [{"key": k, "label": v} for k, v in R.vendor_labels().items()]}


@router.get("/cases/{case_id}")
def case_summary(case_id: int, allocation: str = Query("none", pattern="^(none|even)$"), db: Session = Depends(get_db)):
    case = _case(db, case_id)
    _fresh(db, case_id)
    return {"case_id": case_id, "title": case.canonical_title, "totals": Q.totals(db, case_id), "budget": Q.budget_of(db, case_id), "matrix": Q.matrix(db, case_id, allocation),
            "vendors": Q.vendor_matrix(db, case_id), "by_step": Q.grouped(db, case_id, "step"), "by_vendor": Q.grouped(db, case_id, "vendor"), "accuracy": Q.grouped(db, case_id, "accuracy"), "daily": Q.grouped(db, case_id, "day", limit=60)}


@router.get("/series")
def series(by: str = Query("vendor", pattern="^(vendor|step)$"), bucket: str = Query("day", pattern="^(day|hour)$"), span: int | None = Query(None, ge=2, le=90),
           case_id: int | None = None, db: Session = Depends(get_db)):
    """The real money over time, one line per vendor or step: the last 30 days, or (bucket hour) the last 24 hours."""
    if case_id is not None:
        _case(db, case_id)
    _fresh(db, case_id)
    return Q.series(db, case_id, by, bucket, span or (24 if bucket == "hour" else 30))


@router.get("/phases")
def phases(case_id: int | None = None, db: Session = Depends(get_db)):
    """The real money by pipeline phase, split by vendor and by language (all cases, or one)."""
    if case_id is not None:
        _case(db, case_id)
    _fresh(db, case_id)
    return Q.phases(db, case_id)


@router.get("/cases/{case_id}/group")
def group(case_id: int, by: str = Query(pattern="^(step|language|vendor|model|subject|accuracy|day)$"), step: str | None = None, language: str | None = None, vendor: str | None = None,
          model: str | None = None, subject: str | None = None, accuracy: str | None = None, day: str | None = None, db: Session = Depends(get_db)):
    """One step down: the same money split by the next dimension, inside what has been clicked so far."""
    _case(db, case_id)
    filters = _filters(step, language, vendor, model, subject, accuracy, day)
    return {"by": by, "filters": {k: v for k, v in filters.items() if v}, "rows": Q.grouped(db, case_id, by, filters), "totals": _within(db, case_id, filters)}


def _within(db: Session, case_id: int, filters: dict) -> dict:
    rows = Q.grouped(db, case_id, "accuracy", filters)
    return {"eur": round(sum(r["eur"] for r in rows), 4), "estimated_eur": round(sum(r["estimated_eur"] for r in rows), 4), "calls": sum(r["calls"] for r in rows)}


@router.get("/cases/{case_id}/calls")
def calls(case_id: int, step: str | None = None, language: str | None = None, vendor: str | None = None, model: str | None = None, subject: str | None = None,
          accuracy: str | None = None, day: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    _case(db, case_id)
    return Q.calls(db, case_id, _filters(step, language, vendor, model, subject, accuracy, day), limit, offset)


@router.get("/live")
def live_all(after_id: int | None = None, db: Session = Depends(get_db)):
    _fresh(db)
    return Q.live(db, None, after_id)


@router.get("/cases/{case_id}/live")
def live_case(case_id: int, after_id: int | None = None, db: Session = Depends(get_db)):
    _case(db, case_id)
    _fresh(db, case_id)
    return Q.live(db, case_id, after_id)


@router.get("/cases/{case_id}/analysis")
def analysis(case_id: int, db: Session = Depends(get_db)):
    _case(db, case_id)
    _fresh(db, case_id)
    return Q.analysis(db, case_id)


@router.get("/invoices")
def invoices(db: Session = Depends(get_db)):
    return {"reconciliation": Q.reconciliation(db), "vendors": [{"key": k, "label": v} for k, v in R.vendor_labels().items()]}


@router.put("/invoices")
def save_invoice(body: InvoiceIn, db: Session = Depends(get_db)):
    """The vendor's real bill for one month; typing it again for the same vendor and month replaces it."""
    if body.vendor not in R.vendor_labels() and body.vendor != "other":
        raise HTTPException(status_code=422, detail="Unknown vendor")
    row = db.query(CostInvoice).filter(CostInvoice.vendor == body.vendor, CostInvoice.period == body.period).first()
    if row is None:
        row = CostInvoice(vendor=body.vendor, period=body.period, amount_eur=body.amount_eur, note=body.note)
        db.add(row)
    else:
        row.amount_eur, row.note = body.amount_eur, body.note
    db.commit()
    return {"reconciliation": Q.reconciliation(db)}


@router.delete("/invoices/{invoice_id}")
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    row = db.get(CostInvoice, invoice_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    db.delete(row)
    db.commit()
    return {"reconciliation": Q.reconciliation(db)}
