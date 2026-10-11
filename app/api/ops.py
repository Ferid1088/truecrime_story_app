"""Operator view: what the app has been calling, how it went, and what the job queue looks like."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import observability
from app.db.base import get_db
from app.db.models import DocumentaryJob, ProviderCall
from app.utils import utc_now

router = APIRouter(prefix="/api/ops", tags=["ops"])


def _pct(values: list[int], p: float) -> int:
    if not values:
        return 0
    v = sorted(values)
    return v[min(len(v) - 1, int(p * len(v)))]


@router.get("/summary")
def summary(hours: int = Query(24, ge=1, le=24 * 90), db: Session = Depends(get_db)):
    observability.flush()
    since = utc_now() - timedelta(hours=hours)
    rows = db.query(ProviderCall).filter(ProviderCall.created_at >= since).all()
    by_host: dict[str, dict] = {}
    for r in rows:
        h = by_host.setdefault(r.host, {"host": r.host, "calls": 0, "errors": 0, "tokens_in": 0, "tokens_out": 0,
                                        "cost_usd": 0.0, "_ms": []})
        h["calls"] += 1
        h["errors"] += 0 if r.ok else 1
        h["tokens_in"] += r.tokens_in or 0
        h["tokens_out"] += r.tokens_out or 0
        h["cost_usd"] = round(h["cost_usd"] + (r.cost_usd or 0.0), 6)
        h["_ms"].append(r.duration_ms)
    providers = []
    for h in by_host.values():
        ms = h.pop("_ms")
        providers.append({**h, "p50_ms": _pct(ms, 0.5), "p95_ms": _pct(ms, 0.95)})
    providers.sort(key=lambda x: -x["calls"])
    jobs = dict(db.query(DocumentaryJob.status, func.count()).group_by(DocumentaryJob.status).all())
    return {
        "window_hours": hours,
        "total_calls": len(rows),
        "total_errors": sum(1 for r in rows if not r.ok),
        "total_cost_usd": round(sum(r.cost_usd or 0.0 for r in rows), 6),
        "providers": providers,
        "jobs": jobs,
    }


@router.get("/provider-calls")
def provider_calls(limit: int = Query(100, ge=1, le=500), only_errors: bool = False, host: str | None = None,
                   db: Session = Depends(get_db)):
    observability.flush()
    q = db.query(ProviderCall)
    if only_errors:
        q = q.filter(ProviderCall.ok.is_(False))
    if host:
        q = q.filter(ProviderCall.host == host)
    return [
        {"id": r.id, "at": r.created_at.isoformat() if r.created_at else None, "host": r.host, "method": r.method,
         "path": r.path, "status": r.status, "ok": r.ok, "duration_ms": r.duration_ms, "tokens_in": r.tokens_in,
         "tokens_out": r.tokens_out, "cost_usd": r.cost_usd, "error": r.error, "request_id": r.request_id}
        for r in q.order_by(ProviderCall.id.desc()).limit(limit).all()
    ]
