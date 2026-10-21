"""The QueryRefinementAgent agent (P11): for entities whose useful share stays low, a model
reads the TITLES/SNIPPETS of their own stored candidates (never an image) and writes better
queries — aliases, local-language spellings, official terms, the specific problem it sees.
Budgeted per case (calls and EUR); stops when the budget is used, never on its own past that."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import Case, VisualAsset
from app.documentary.visuals.research import low_yield_entities
from app.providers.generation import get_generation_provider
from app.services.budget import BudgetService
from app.services.tracking import stamp_run, track_run

REFINER_SYSTEM = prompt("documentary/visuals/refinement/refiner_system")


class QueryRefinementAgent:
    def __init__(self):
        self.gen = get_generation_provider()

    async def refine(self, db: Session, case: Case, entity: dict,
                     sample: list[VisualAsset]) -> list[str]:
        """One model call: better queries for this entity, from its own sample results' text."""
        cfg = ai_config.visual_search
        payload = {
            "case": case.canonical_title,
            "entity": {"key": entity["key"], "name": entity.get("name"), "type": entity.get("type"),
                      "aliases": entity.get("aliases") or [],
                      "likely_sources": entity.get("likely_sources") or [],
                      "expected_captions": entity.get("expected_captions") or []},
            "sample_results": [{"title": a.title, "caption": a.caption, "source": a.source_name}
                              for a in sample[:cfg.refinement_sample_results]],
        }
        with track_run(db, case.id, "Visual Query Refiner", input_summary=entity["key"]) as run:
            raw, res = await run_agent("visuals.refine_queries", self.gen,
                                       json.dumps(payload, ensure_ascii=False), system=REFINER_SYSTEM)
            stamp_run(run, res, "visual_query_refiner")
        queries = [str(q).strip()[:160] for q in (raw.get("queries") if isinstance(raw, dict) else None) or []]
        return [q for q in queries if q][:5]

    async def refine_low_yield_entities(self, db: Session, case: Case, requirements: dict,
                                        assets: list[VisualAsset]) -> dict:
        """Picks the entities to refine (P11), spends the budget on them in order, worst first;
        returns the queries written per entity and what the budget allowed."""
        cfg = ai_config.visual_search
        keys = low_yield_entities(assets, cfg.refinement_min_sample, cfg.refinement_min_share)
        out: dict[str, Any] = {"candidates": keys, "refined": {}, "calls_made": 0, "spent_eur": 0.0,
                              "stopped_because": None}
        if not keys or cfg.refinement_max_calls_per_case <= 0:
            out["stopped_because"] = "no low-yield entity" if not keys else "budget is zero"
            return out
        entities = {e["key"]: e for e in requirements.get("entities") or []}
        by_entity: dict[str, list[VisualAsset]] = {}
        for a in assets:
            if a.entity_key in keys:
                by_entity.setdefault(a.entity_key, []).append(a)
        start = BudgetService(db).snapshot(case.id).spent_eur
        for key in keys:
            if out["calls_made"] >= cfg.refinement_max_calls_per_case:
                out["stopped_because"] = "max_calls_per_case reached"
                break
            spent_so_far = BudgetService(db).snapshot(case.id).spent_eur - start
            if spent_so_far >= cfg.refinement_max_eur_per_case:
                out["stopped_because"] = "max_eur_per_case reached"
                break
            entity = entities.get(key)
            if entity is None:
                continue
            queries = await self.refine(db, case, entity, by_entity.get(key) or [])
            out["calls_made"] += 1
            if queries:
                out["refined"][key] = queries
        out["spent_eur"] = round(BudgetService(db).snapshot(case.id).spent_eur - start, 4)
        if out["stopped_because"] is None:
            out["stopped_because"] = "every low-yield entity refined"
        return out
