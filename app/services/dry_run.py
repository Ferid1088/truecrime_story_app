"""No-send documentary preflight with the same language scope as a run."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Case
from app.services.cost_model import documentary_estimate


def documentary_dry_run(db: Session, case: Case, languages: list[str] | None = None,
                        pilot_seconds: float | None = None) -> dict:
    langs = list(dict.fromkeys(languages or ["en"]))
    if "en" not in langs:
        raise ValueError("English is required as the canonical master language")
    estimate = documentary_estimate(db, case, langs, pilot_seconds=pilot_seconds)
    cap = float(case.total_cost_cap_eur or 20.0)
    rows = [{**line, "prompt": f"Prepare {line['stage'].replace('_', ' ')} for case: {case.canonical_title}", "sends": False}
            for line in estimate["lines"]]
    total = estimate["totals"]["expected"]
    stop_stage_if_cap_hit = None
    running = 0.0
    for row in rows:
        running += float(row["eur"]["expected"])
        if stop_stage_if_cap_hit is None and running > cap and row["status"] == "run":
            stop_stage_if_cap_hit = row["stage"]
    return {
        "dry_run": True,
        "case_id": case.id,
        "languages": langs,
        "cap_eur": cap,
        "estimated_cost_eur": round(total, 2),
        "estimate_range_eur": estimate["totals"],
        "metrics": estimate["metrics"],
        "pricing_last_verified": estimate["pricing_last_verified"],
        "pilot_seconds": pilot_seconds,
        "price_warnings": estimate["price_warnings"],
        "token_assumptions": estimate["metrics"].get("tokens", {}),
        "token_formulas": estimate["token_formulas"],
        "within_cap": total <= cap,
        "stop_stage_if_cap_hit": stop_stage_if_cap_hit,
        "provider_calls": len([row for row in rows if row["status"] == "run"]),
        "stages": rows,
        "sent": False,
    }
