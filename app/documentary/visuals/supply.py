"""Supply of real pictures: how many distinct real pictures and video cuts a film needs, and the
search that goes on until it has them or the sources are exhausted.

The target comes from the editing rhythm (supply.*): stills of about still_shot_seconds, each real
picture used uses_per_picture times as different crops, and video_share of the film as cuts. When
the first research and check fall short the search runs round after round — the next slice of
query templates, never a query a provider already ran (the search ledger) — each followed by a
vision check of its new candidates. It ends when the target is reached, the sources are
exhausted (no new query, or supply.max_empty_rounds rounds adding nothing), supply.max_searches
or the research cost guard is spent, or the case budget / asset limit is reached. The report says
which, what ran out, and the shortage per film and per beat."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary.visuals import director as D
from app.documentary.visuals import gaps as G
from app.documentary.visuals.planner import supply_queries
from app.documentary.visuals.search_log import QUERY_PROVIDERS, SearchLedger
from app.services.budget import BudgetService

GENERATED = "generated"


@dataclass(frozen=True)
class Target:
    stills: int
    cuts: int

    @property
    def total(self) -> int:
        return self.stills + self.cuts

    def as_dict(self) -> dict:
        return {**asdict(self), "total": self.total}


def target(film_seconds: float) -> Target:
    cfg = ai_config.supply
    still_seconds = film_seconds * (1.0 - cfg.video_share)
    return Target(
        stills=math.ceil(still_seconds / cfg.still_shot_seconds / cfg.uses_per_picture),
        cuts=math.ceil(film_seconds * cfg.video_share / cfg.video_cut_seconds))


def is_usable_real(a: VisualAsset) -> bool:
    return (a.verification_status == "verified" and a.provider != GENERATED
            and a.asset_type in ("photo", "video"))


def usable_real(db: Session, case: Case) -> int:
    """Verified pictures and video cuts that are not generated."""
    return sum(is_usable_real(a) for a in db.query(VisualAsset).filter(VisualAsset.case_id == case.id))


class CostGuard:
    """Stops the search before a paid round when the case budget is spent or this supply run has
    spent its own research cap (actual recorded EUR, never an estimate)."""

    def __init__(self, db: Session, case: Case):
        self.budget, self.case_id = BudgetService(db), case.id
        self.start = self.budget.snapshot(case.id).spent_eur

    def spent(self) -> float:
        return round(self.budget.snapshot(self.case_id).spent_eur - self.start, 6)

    def reason(self) -> str | None:
        snap = self.budget.snapshot(self.case_id)
        if snap.remaining_eur <= 0:
            return "case_budget"
        if snap.spent_eur - self.start >= ai_config.supply.research_cost_cap_eur:
            return "cost_guard"
        return None


def beat_report(db: Session, case: Case, blueprint: dict, requirements: dict, film_seconds: float,
                profile: str | None = None) -> list[dict]:
    """Per beat: the distinct real pictures needed for its share of the film, the usable ones the
    director is offered, and the shortage."""
    total_seconds = sum(D.beat_seconds(b) for b in blueprint.get("beats") or []) or 1.0
    offered = D.beat_candidates(db, case, blueprint, requirements, profile)
    out = []
    for beat in blueprint.get("beats") or []:
        need = max(1, target(film_seconds * D.beat_seconds(beat) / total_seconds).total)
        found = sum(is_usable_real(a) for _, a in offered.get(beat["id"], []))
        out.append({"beat": beat["id"], "needed": need, "found": found,
                    "shortage": max(need - found, 0)})
    return out


def empty_beats(db: Session, case: Case, blueprint: dict, requirements: dict,
                beat_ids: list[str] | None, profile: str | None = None) -> list[str]:
    """Beats the director would be offered no candidate for."""
    offered = D.beat_candidates(db, case, blueprint, requirements, profile)
    return [b["id"] for b in blueprint.get("beats") or []
            if not offered.get(b["id"]) and (not beat_ids or b["id"] in beat_ids)]


def targeted_queries(case: Case, requirements: dict, beats: list[str]) -> list[dict]:
    """Searches for exactly the entities of the beats that have no candidate: the entity's name
    with the place of the case and its period (the exact variants of gaps.request_queries)."""
    entities = {e["key"]: e for e in requirements.get("entities") or []}
    place, region = G.case_place(case, requirements), G.case_region(case, requirements)
    out, seen = [], set()
    for b in requirements.get("beats") or []:
        if b["beat_id"] not in beats:
            continue
        for r in b["requirements"]:
            entity = entities.get(r["entity"])
            if entity is None or entity["key"] in seen:
                continue
            seen.add(entity["key"])
            out += G.request_queries({"entity": entity["key"], "entity_name": entity["name"]}, entity,
                                     place, ai_config.visual_direction.queries_per_request,
                                     getattr(case, "country", None), region)
    return out


def _fresh(ledger: SearchLedger, queries: list[dict]) -> list[dict]:
    """The queries at least one query provider has not run yet."""
    providers = [p for p in ai_config.visual_search.providers if p in QUERY_PROVIDERS]
    return [q for q in queries if any(not ledger.seen(p, q["query"]) for p in providers)]


async def top_up(db: Session, case: Case, requirements: dict, blueprint: dict,
                 beat_ids: list[str] | None, film_seconds: float, research, verify,
                 profile: str | None = None) -> dict:
    """research(queries) -> stats with "added"; verify() -> stats. Returns the supply report."""
    goal = target(film_seconds)
    ledger = SearchLedger(db, case.id)
    guard = CostGuard(db, case)
    have = usable_real(db, case)
    report: dict[str, Any] = {"target": goal.as_dict(), "usable_before": have, "searches": 1,
                              "rounds": [], "ran_out": []}
    stopped = None
    empty = 0
    if have >= goal.total:
        stopped = "reached"
    elif live_assets(db, case) >= ai_config.visual_search.max_assets:
        stopped, _ = "asset_limit", report["ran_out"].append(
            f"visual_search.max_assets ({ai_config.visual_search.max_assets}) is full")
    missing = empty_beats(db, case, blueprint, requirements, beat_ids, profile)
    targeted = _fresh(ledger, targeted_queries(case, requirements, missing)) if missing else []
    if targeted and stopped != "asset_limit" and not guard.reason():
        stats = await research(targeted)
        added = stats.get("added", 0)
        checked = await verify() if added else {"checked": 0}
        have = usable_real(db, case)
        report["rounds"].append({"round": 0, "targeted_beats": missing, "queries": len(targeted),
                                 "added": added, "checked": checked.get("checked", 0), "usable": have,
                                 "repeated_queries": stats.get("repeated_queries", 0)})
        stopped = "reached" if have >= goal.total else stopped
    for round_index in range(1, ai_config.supply.max_searches):
        if stopped:
            break
        if (why := guard.reason()):
            stopped = why
            break
        queries = _fresh(ledger, supply_queries(requirements, beat_ids, round_index))
        if not queries:
            stopped = "no_new_queries"
            report["ran_out"].append("query slices: every template of every entity has been searched")
            break
        stats = await research(queries)
        report["searches"] += 1
        added = stats.get("added", 0)
        checked = await verify() if added else {"checked": 0}
        have = usable_real(db, case)
        report["rounds"].append({"round": round_index, "queries": len(queries), "added": added,
                                 "checked": checked.get("checked", 0), "usable": have,
                                 "repeated_queries": stats.get("repeated_queries", 0)})
        if have >= goal.total:
            stopped = "reached"
        elif stats.get("budget_stopped"):
            stopped = "case_budget"
        else:
            empty = 0 if added else empty + 1
            if empty >= ai_config.supply.max_empty_rounds:
                stopped = "nothing_added"
                report["ran_out"].append(
                    f"{empty} searches in a row found no new picture: the providers are exhausted")
    report["stopped_because"] = stopped or "max_searches"
    if report["stopped_because"] == "max_searches":
        report["ran_out"].append(f"supply.max_searches ({ai_config.supply.max_searches})")
    report["usable_after"] = have
    report["reached"] = have >= goal.total
    report["shortage"] = max(goal.total - have, 0)
    report["research_cost_eur"] = guard.spent()
    report["beats"] = beat_report(db, case, blueprint, requirements, film_seconds, profile)
    return report


def live_assets(db: Session, case: Case) -> int:
    """Stored pictures that take an asset slot: everything found and not rejected."""
    return sum(a.verification_status != "rejected" and a.provider != GENERATED
               for a in db.query(VisualAsset).filter(VisualAsset.case_id == case.id))
