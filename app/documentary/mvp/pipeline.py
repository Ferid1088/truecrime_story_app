"""The Main Visual Planner's stages as the documentary job runs them. Shared stages (graph, needs, library,
verification, research) run once per blueprint; direction, coverage and owner review run per language,
against that language's real narration timing."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.concurrency import limit_size
from app.db.models import (
    Case,
    EditorialBlueprint,
    MvpCandidate,
    MvpRequirement,
    RevealGraph,
    VisualAsset,
    VisualPlan,
)
from app.documentary.mvp import coverage as CV
from app.documentary.mvp import direct as DR
from app.documentary.mvp import needs as ND
from app.documentary.mvp import research as RS
from app.documentary.mvp import review as RV
from app.documentary.mvp import verify as VF
from app.documentary.mvp.library import build_atmosphere_candidates, build_candidates, build_gap_candidates
from app.longform import service as LF
from app.services.budget import BudgetService

log = logging.getLogger(__name__)
WORDS_PER_SECOND = 2.6


def ensure_graph(db: Session, case: Case, bp_row: EditorialBlueprint) -> RevealGraph:
    """The case's validated reveal graph for this blueprint: reused when present, built from the blueprint
    otherwise, and never silently replaced by an invalid one."""
    graph = (db.query(RevealGraph).filter_by(case_id=case.id, blueprint_id=bp_row.id, status="validated")
             .order_by(RevealGraph.version.desc()).first())
    if graph is not None:
        return graph
    payload = LF.build_reveal_graph_payload(db, case.id, bp_row.id)
    count = db.query(RevealGraph).filter_by(case_id=case.id, blueprint_id=bp_row.id).count()
    return LF.create_reveal_graph(db, case.id, bp_row.id, {**payload, "version": count + 1})


def estimated_seconds(blueprint: dict) -> dict[str, float]:
    return {b["id"]: float(b.get("words") or 0) / WORDS_PER_SECOND for b in blueprint.get("beats") or []}


def entities_with_dossiers(db: Session, case: Case, requirements: dict) -> list[dict]:
    from app.documentary.visuals.dossier import merge_into_entities

    return merge_into_entities(db, case, list(requirements.get("entities") or []))


async def stage_needs(db: Session, case: Case, bp_row: EditorialBlueprint) -> tuple[VisualPlan, RevealGraph, dict]:
    graph = ensure_graph(db, case, bp_row)
    blueprint = json.loads(bp_row.blueprint_json or "{}")
    plan = await ND.plan_needs(db, case, bp_row, graph, estimated_seconds(blueprint))
    if plan.status == "invalid":
        raise RuntimeError("The Main Visual Planner produced no valid visual needs; see the plan's validation.")
    report = json.loads(plan.validation_json)["requirements"]
    return plan, graph, {"visual_plan_id": plan.id, "reveal_graph_id": graph.id,
                         "graph_nodes": len(graph.nodes), "entities": report["entities"],
                         "beats_with_needs": report["beats_with_needs"],
                         "warnings": len(report["warnings"])}


def requirements_of(plan: VisualPlan) -> dict:
    return json.loads(plan.requirements_json or "{}")


async def verify_pending(db: Session, case: Case, plan: VisualPlan, requirements: dict) -> dict:
    """Judge every candidate that has no final verdict yet (pending or a transient failure), one vision
    call per picture, stopping cleanly if the case's spend reached its cap."""

    pack, _ = ND.build_pack(db, case.id)
    facts = pack["facts"]
    entities = entities_with_dossiers(db, case, requirements)
    rows = (db.query(MvpCandidate, MvpRequirement)
            .join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
            .filter(MvpRequirement.visual_plan_id == plan.id,
                    MvpCandidate.final_status.in_(("pending", "transient_failure"))).all())
    by_asset: dict[int, list[tuple[MvpCandidate, MvpRequirement]]] = {}
    for cand, req in rows:
        by_asset.setdefault(cand.visual_asset_id, []).append((cand, req))
    stats = {"assets": len(by_asset), "judged": 0, "budget_stopped": False}
    money = BudgetService(db)
    # only this many pictures are in flight, so a call's timeout never includes waiting for a slot; the
    # provider's own vision slot is a different semaphore and is never held by this stage
    in_flight = asyncio.Semaphore(limit_size("vision"))

    async def one(asset_id: int, items):
        async with in_flight:
            if stats["budget_stopped"]:
                return
            snap = money.snapshot(case.id)
            if snap.spent_eur >= snap.cap_eur - 1e-9:
                stats["budget_stopped"] = True
                return
            asset = db.get(VisualAsset, asset_id)
            await VF.verify_asset(db, case, asset, items, entities, facts)
            stats["judged"] += 1

    await asyncio.gather(*(one(a, items) for a, items in by_asset.items()))
    stats["sync"] = VF.sync_asset_status(db, case.id)
    stats["by_status"] = {s: db.query(MvpCandidate).join(
        MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id).filter(
        MvpRequirement.visual_plan_id == plan.id, MvpCandidate.final_status == s).count()
        for s in ("approved", "rejected", "needs_review", "pending", "transient_failure")}
    return stats


async def stage_library_and_verify(db: Session, case: Case, plan: VisualPlan) -> dict:
    requirements = requirements_of(plan)
    built = build_candidates(db, plan, requirements)
    verified = await verify_pending(db, case, plan, requirements)
    return {**built, **verified}


async def stage_research(db: Session, case: Case, plan: VisualPlan) -> dict:
    from app.agents.visual_research import VisualResearchAgent

    requirements = requirements_of(plan)

    async def research(db_, case_, items):
        return await VisualResearchAgent().run(db_, case_, items)

    async def verify(db_, case_, plan_, reqs):
        return await verify_pending(db_, case_, plan_, reqs)

    report = await RS.research_unmet(db, case, plan, requirements, research, verify)
    report["shortage_ids"] = [r.requirement_key for r in db.query(MvpRequirement).filter_by(
        visual_plan_id=plan.id, status="shortage")]
    return report


def language_plan(db: Session, needs_plan: VisualPlan, language: str) -> VisualPlan:
    """The language's own plan row (its shot list follows its own narration timing); the requirements,
    candidates and verdicts stay on the shared needs plan."""
    rows = db.query(VisualPlan).filter(VisualPlan.blueprint_id == needs_plan.blueprint_id).all()
    for row in rows:
        mvp = json.loads(row.validation_json or "{}").get("mvp") or {}
        if mvp.get("language") == language and mvp.get("needs_plan_id") == needs_plan.id:
            return row
    row = VisualPlan(case_id=needs_plan.case_id, blueprint_id=needs_plan.blueprint_id,
                     version=len(rows) + 1, status="requirements",
                     requirements_json=needs_plan.requirements_json,
                     validation_json=json.dumps({"mvp": {"language": language, "needs_plan_id": needs_plan.id,
                                                          "reveal_graph_id": json.loads(
                                                              needs_plan.validation_json)["mvp"].get(
                                                              "reveal_graph_id")}}))
    db.add(row)
    db.commit()
    return row


async def stage_direct(db: Session, case: Case, needs_plan: VisualPlan, bp_row: EditorialBlueprint,
                       manifest: dict, language: str, use_agent: bool = True) -> tuple[VisualPlan, dict]:
    target = language_plan(db, needs_plan, language)
    blueprint = json.loads(bp_row.blueprint_json or "{}")
    beats = {b["id"]: {"beat_id": b["id"], "purpose": b.get("purpose"), "summary": b.get("summary"),
                       "human_focus": b.get("human_focus")} for b in blueprint.get("beats") or []}
    order = {b["id"]: i for i, b in enumerate(blueprint.get("beats") or [])}
    graph_id = json.loads(needs_plan.validation_json)["mvp"].get("reveal_graph_id")

    async def director(case_, beat, slots, pool):
        return await DR.model_director(case_, beat, slots, pool, db)

    requirements = requirements_of(needs_plan)
    report = await DR.direct_film(db, case, needs_plan, manifest, graph_id, order, beats,
                                  director if use_agent else None, target)
    for _ in range(ai_config.mvp().topup.passes):
        if not report["gaps"]:
            break
        made = build_gap_candidates(db, needs_plan, requirements, {g["beat"] for g in report["gaps"]})
        if not made["created"]:
            break
        verified = await verify_pending(db, case, needs_plan, requirements)
        report = await DR.direct_film(db, case, needs_plan, manifest, graph_id, order, beats,
                                      director if use_agent else None, target)
        report["topup"] = {**made, "judged": verified["judged"]}
    if report["gaps"] and ai_config.mvp().topup.atmosphere_max_assets:
        place = (db.get(Case, case.id).location or "").strip() or None
        made = build_atmosphere_candidates(db, needs_plan, case.canonical_title, place)
        if made["atmosphere_candidates"]:
            requirements = requirements_of(needs_plan)
            verified = await verify_pending(db, case, needs_plan, requirements)
            report = await DR.direct_film(db, case, needs_plan, manifest, graph_id, order, beats,
                                          director if use_agent else None, target)
            report["atmosphere"] = {**made, "judged": verified["judged"]}
    return target, report


def stage_coverage(db: Session, needs_plan: VisualPlan, script: dict, manifest: dict) -> dict:
    report = CV.measure(script, needs_plan, db, manifest["timeline"].get("sentences") or [])
    return {**report, **CV.verdict(report)}


def stage_review(db: Session, case: Case, language: str, production_script_id: int, render_path: Path,
                 coverage: dict, file_probe: dict) -> dict:
    """Record the rendered file and move to owner review only if every technical check passed."""
    cfg = ai_config.mvp().coverage
    problems = list(coverage.get("blocking") or [])
    if file_probe["black_seconds"] > cfg.max_black_frame_seconds * 4:
        problems.append(f"{file_probe['black_seconds']}s of black frames in the file")
    if file_probe["frozen_stretches"]:
        problems.append(f"{len(file_probe['frozen_stretches'])} frozen stretch(es) in the file")
    if not file_probe.get("audio_codec"):
        problems.append("the file has no audio stream")
    validation = {"review_ready": not problems, "blocking": problems, "coverage": coverage,
                  "file": file_probe, "publication_blockers": coverage.get("publication_blockers", [])}
    review = RV.open_review(db, case.id, language)
    if review.state == "IMPLEMENTING":
        RV.advance(db, review, "PRODUCING")
    if review.state == "PRODUCING":
        RV.advance(db, review, "VALIDATING")
    RV.submit_for_review(db, review, production_script_id=production_script_id, render_path=render_path,
                         validation=validation)
    return {"review_id": review.id, "state": review.state, "review_ready": validation["review_ready"],
            "blocking": problems, "render_sha256": review.render_sha256}
