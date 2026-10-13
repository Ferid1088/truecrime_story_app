"""No-send documentary preflight for one small, English-only case."""

from __future__ import annotations

from app.core.ai_config import ai_config
from app.db.models import Case
from app.documentary.jobs import plan_stages


def _budget_key(stage: str) -> str:
    base = stage.split(":", 1)[0]
    if base == "research":
        return "research"
    if base == "master_story":
        return "master_story"
    if base == "spoken":
        return "translation"
    if base in {"performance", "voice"}:
        return "tts"
    if base.startswith("host"):
        return "heygen"
    if base.startswith("short"):
        return "shorts"
    return ""


def documentary_dry_run(case: Case, languages: list[str] | None = None) -> dict:
    langs = [language for language in (languages or ["en"]) if language == "en"]
    if not langs:
        raise ValueError("The first dry run supports English only")
    stages = plan_stages(langs, from_zero=True)
    rows = []
    total = 0.0
    charged: set[str] = set()
    stop_stage_if_cap_hit = None
    for stage in stages:
        name = stage["name"]
        key = _budget_key(name)
        cost = float(ai_config.budget.stage_estimate_eur.get(key, 0.0)) if key and key not in charged else 0.0
        if key and cost:
            charged.add(key)
        total += cost
        if stop_stage_if_cap_hit is None and total > float(case.total_cost_cap_eur or ai_config.budget.total_cap_eur):
            stop_stage_if_cap_hit = name
        rows.append({
            "stage": name,
            "prompt": f"Prepare {name.replace('_', ' ')} for case: {case.canonical_title}",
            "estimated_cost_eur": round(cost, 2),
            "sends": False,
        })
    cap = float(case.total_cost_cap_eur or ai_config.budget.total_cap_eur)
    return {
        "dry_run": True,
        "case_id": case.id,
        "languages": langs,
        "cap_eur": cap,
        "estimated_cost_eur": round(total, 2),
        "within_cap": total <= cap,
        "stop_stage_if_cap_hit": stop_stage_if_cap_hit,
        "provider_calls": len([row for row in rows if row["estimated_cost_eur"] > 0]),
        "stages": rows,
        "sent": False,
    }
