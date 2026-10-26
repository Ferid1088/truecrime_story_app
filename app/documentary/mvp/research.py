"""Step 6 - bounded media research for unmet requirements: one initial round and at most two materially
different further rounds per requirement, every round recorded with its provider, query, yield and cost,
and an early stop when another search is unlikely to add a legitimate option."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    MvpRequirement,
    MvpSearchRound,
    ProviderCall,
    VisualAsset,
    VisualPlan,
)
from app.documentary.mvp.library import build_candidates
from app.documentary.mvp.needs import FOOTAGE_TYPES, fold
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

SEARCHABLE_ROLES = ("evidence", "context")
QueryPlanner = Callable[[Session, Case, MvpRequirement, dict, list[dict], int], Awaitable[list[dict]]]
Researcher = Callable[[Session, Case, list[dict]], Awaitable[dict]]
Verifier = Callable[[Session, Case, VisualPlan, dict], Awaitable[dict]]


def is_met(req: MvpRequirement) -> bool:
    """Met = an approved candidate whose role the requirement accepts."""
    accepted = set(json.loads(req.spec_json or "{}").get("acceptable_roles") or [req.role_needed])
    return any(c.final_status == "approved" and c.role in accepted for c in req.candidates)


def unmet(db: Session, plan: VisualPlan) -> list[MvpRequirement]:
    return [r for r in db.query(MvpRequirement).filter(MvpRequirement.visual_plan_id == plan.id,
                                                       MvpRequirement.status != "shortage")
            if r.role_needed in SEARCHABLE_ROLES and r.priority != "low" and not is_met(r)]


def _spent(db: Session, case_id: int) -> float:
    return float(db.query(func.coalesce(func.sum(ProviderCall.cost_eur), 0.0))
                 .filter(ProviderCall.case_id == case_id, ProviderCall.is_test_data.is_(False)).scalar())


def round_one_queries(entity: dict) -> list[dict]:
    return [{"query": q, "kind": "exact"} for q in entity.get("search_queries") or []][:3]


async def model_query_planner(db: Session, case: Case, req: MvpRequirement, entity: dict,
                              history: list[dict], round_no: int) -> list[dict]:
    payload = {"case": case.canonical_title, "round": round_no,
               "requirement": {"function": req.function, "role_needed": req.role_needed,
                               **json.loads(req.spec_json or "{}")},
               "entity": entity, "prior_rounds": history}
    try:
        with track_run(db, case.id, "Main Visual Planner: queries", input_summary=req.requirement_key) as run:
            data, res = await asyncio.wait_for(
                run_agent("mvp.queries", get_generation_provider(), json.dumps(payload, ensure_ascii=False)),
                ai_config.mvp().call_timeout_s)
            stamp_run(run, res, "mvp_query_planner")
    except (GenerationError, TimeoutError) as e:
        log.warning("query planner failed for %s: %s", req.requirement_key, e)
        return []
    return [q for q in (data or {}).get("queries") or [] if isinstance(q, dict) and q.get("query")]


def fresh_queries(candidates: list[dict], history: list[dict]) -> list[dict]:
    """Drop any query that was already tried (compared accent- and case-insensitively)."""
    seen = {fold(h["query"]) for h in history}
    out = []
    for q in candidates:
        key = fold(str(q["query"]))
        if key and key not in seen:
            seen.add(key)
            out.append({"query": str(q["query"])[:200],
                        "kind": q.get("kind") if q.get("kind") in ("exact", "context") else "exact"})
    return out[:3]


def _history(req: MvpRequirement) -> list[dict]:
    return [{"round": r.round_no, "provider": r.provider, "query": r.query,
             "new_candidates": r.new_candidates, "error": r.error} for r in req.search_rounds]


def query_item(entity: dict, query: str, kind: str = "exact") -> dict:
    """One search for VisualResearchAgent.run: what to search, for which
    entity (key and type: the asset's library fields and provisional
    tier), exact or context, and whether footage is searched too.

    must_name (P5): the candidate's own text (title, caption, page url, filename) must name the
    entity (by any alias) or the case; VisualResearchAgent.run also accepts a case-title match
    as an alternative, so a genuine source picture about the case is never dropped just because
    it does not repeat the entity's own name."""
    etype = entity.get("type") or "object"
    is_context = kind == "context"
    return {"query": query, "entity": entity["key"], "entities": [entity["key"]],
            "entity_name": entity.get("name"), "entity_type": etype, "kind": "context" if is_context else "exact",
            "footage": bool(entity.get("footage", etype in FOOTAGE_TYPES)),
            # a context query deliberately looks for a stand-in, never the exact thing's own
            # name, so the pre-filter does not apply to it
            "must_name": [] if is_context else [entity.get("name") or ""] + list(entity.get("aliases") or []),
            # the merged dossier fields the structured providers (open_data_archives) build
            # their per-alias, era-filtered, exclusion-marked queries from
            "aliases": list(entity.get("aliases") or []),
            "date_window": list(entity.get("date_window") or []),
            "not_this": list(entity.get("not_this") or []),
            "negative_terms": list(entity.get("negative_terms") or [])}


async def research_unmet(db: Session, case: Case, plan: VisualPlan, requirements: dict,
                         research: Researcher, verify: Verifier,
                         planner: QueryPlanner = model_query_planner) -> dict:
    """Run the bounded rounds. `research` performs the searches, `verify` judges the new candidates."""
    cfg = ai_config.mvp().search
    entities = {e["key"]: e for e in requirements["entities"]}
    total_rounds = cfg.initial_rounds + cfg.extra_rounds_max
    report = {"rounds": [], "shortages": 0}
    for round_no in range(1, total_rounds + 1):
        pending = [r for r in unmet(db, plan) if r.rounds_used < round_no]
        if not pending:
            break
        before_assets = db.query(func.coalesce(func.max(VisualAsset.id), 0)).scalar()
        spent_before = _spent(db, case.id)
        by_entity: dict[str, list[MvpRequirement]] = {}
        for req in pending:
            by_entity.setdefault(req.entity_key, []).append(req)
        items: list[dict] = []
        planned: dict[int, list[dict]] = {}
        for key, reqs in by_entity.items():
            entity = entities.get(key)
            if entity is None:
                continue
            history = [h for r in reqs for h in _history(r)]
            if round_no <= cfg.initial_rounds:
                queries = fresh_queries(round_one_queries(entity), history)
            else:
                queries = fresh_queries(await planner(db, case, reqs[0], entity, history, round_no), history)
            for req in reqs:
                planned[req.id] = queries
            items += [query_item(entity, q["query"], q["kind"]) for q in queries]
        stats = await research(db, case, items) if items else {}
        new_ids = [a.id for a in db.query(VisualAsset).filter(VisualAsset.case_id == case.id,
                                                              VisualAsset.id > before_assets)]
        cost_each = max(_spent(db, case.id) - spent_before, 0.0) / max(len(pending), 1)
        for req in pending:
            req.rounds_used = round_no
            for q in planned.get(req.id) or [{"query": "(no new query)", "kind": "exact"}]:
                db.add(MvpSearchRound(requirement_id=req.id, round_no=round_no, provider="mixed",
                                      query=q["query"], new_candidates=len(new_ids),
                                      cost_eur=round(cost_each, 6),
                                      error=None if planned.get(req.id) else "planner found nothing new"))
        db.commit()
        build_candidates(db, plan, requirements)
        verified = await verify(db, case, plan, requirements)
        report["rounds"].append({"round": round_no, "requirements": len(pending), "queries": len(items),
                                 "new_assets": len(new_ids), "verified": verified,
                                 "stats": {k: stats.get(k) for k in ("candidates", "added", "errors")}})
        if len(new_ids) < cfg.min_new_candidates_to_continue and round_no >= cfg.initial_rounds:
            break
    report["shortages"] = mark_shortages(db, plan)
    return report


def mark_shortages(db: Session, plan: VisualPlan) -> int:
    """Settle every requirement: met, or a shortage record saying what was tried. Never silently open."""
    short = 0
    for req in db.query(MvpRequirement).filter(MvpRequirement.visual_plan_id == plan.id):
        if is_met(req):
            req.status, req.shortage_json = "met", None
        elif req.role_needed == "illustration" or req.priority == "low":
            req.status = "met" if any(c.final_status == "approved" for c in req.candidates) else "open"
        else:
            req.status = "shortage"
            req.shortage_json = json.dumps({
                "rounds_used": req.rounds_used, "queries": [r.query for r in req.search_rounds],
                "candidates": {s: sum(1 for c in req.candidates if c.final_status == s)
                               for s in ("approved", "rejected", "needs_review", "pending",
                                         "transient_failure")}}, ensure_ascii=False)
            short += 1
    db.commit()
    return short


