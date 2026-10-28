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
from app.documentary.mvp import evidence as EV
from app.documentary.mvp import general as GI
from app.documentary.mvp import moments as MC
from app.documentary.mvp import needs as ND
from app.documentary.mvp import overlays as OV
from app.documentary.mvp import research as RS
from app.documentary.mvp import review as RV
from app.documentary.mvp import timing as TM
from app.documentary.mvp import topical as TP
from app.documentary.mvp import verify as VF
from app.documentary.mvp.library import build_candidates, build_gap_candidates
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


async def stage_needs(db: Session, case: Case, bp_row: EditorialBlueprint,
                      replan: bool = False) -> tuple[VisualPlan, RevealGraph, dict]:
    graph = ensure_graph(db, case, bp_row)
    blueprint = json.loads(bp_row.blueprint_json or "{}")
    plan = await ND.plan_needs(db, case, bp_row, graph, estimated_seconds(blueprint), replan=replan)
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


def host_required() -> bool:
    """The host is part of every film (host.required_for_film) whenever it can appear at all."""
    return bool(ai_config.host.required_for_film and ai_config.host.enabled and ai_config.avatar.enabled)


async def stage_host(db: Session, case: Case, version, language: str) -> dict:
    """The planner's host stage: every scene the Host plan placed has its voice and its avatar video
    BEFORE the film is directed and rendered. The Host text, position, outfit and direction stay the
    Host system's; this stage only makes sure its planned appearances exist as media, and fails the
    film (resumable) if one cannot be produced."""
    from app.agents.stage_director import StageDirector
    from app.db.models import HostScene
    from app.documentary.host import HostDirector, latest_host_segments
    from app.documentary.host_scenes import run_scene
    from app.documentary.production import host_timeline as HT

    if not host_required():
        return {"skipped": True, "reason": "host or avatar disabled"}
    seg = latest_host_segments(db, version.id)
    if seg is None:
        raise RuntimeError("The Host plan has no segments for this film; the host is required.")
    scenes = db.query(HostScene).filter(HostScene.host_segments_id == seg.id).order_by(HostScene.id).all()
    if not scenes:
        raise RuntimeError("The Host segments have no planned scenes; the host is required.")
    ready = {i["scene_id"] for i in HT.host_items(db, version)}
    produced: list[int] = []
    for scene in scenes:
        if scene.id in ready:
            continue
        await run_scene(db, scene, "avatar", retimer=HostDirector().retime_scene,
                        director=StageDirector().direct)
        produced.append(scene.id)
    items = HT.host_items(db, version)
    if len(items) < len(scenes):
        missing = sorted({s.id for s in scenes} - {i["scene_id"] for i in items})
        raise RuntimeError(f"Host scene(s) {missing} have no verified avatar video yet.")
    return {"scenes": len(scenes), "already_ready": len(ready), "produced": produced,
            "positions": [f"{i['position']}@{i['beat_id'] or '-'}" for i in items],
            "host_seconds": round(sum(i["seconds"] for i in items), 1)}


def case_region(case: Case) -> str | None:
    """The last two parts of the case's location ("Ohio, United States"): appended to a place that is
    named alone, so the geocoder resolves it near the case and not to a namesake elsewhere."""
    parts = [p.strip() for p in (case.location or "").split(",") if p.strip()]
    return ", ".join(parts[-2:]) if len(parts) >= 2 else None


async def scan_overlays(db: Session, case: Case, needs_plan: VisualPlan) -> dict:
    """Every approved picture and clip of the film is scanned for other people's logos, watermarks and caption
    strips (mvp/overlays.py): the film shows the part free of them, and what cannot be cut free is rejected."""
    if not ai_config.mvp().overlays.enabled:
        return {"enabled": False}
    seen: set[int] = set()
    assets = []
    for group in DR.approved_items(db, needs_plan).values():
        for item in group:
            if item.asset.id not in seen:
                seen.add(item.asset.id)
                assets.append(item.asset)
    scanner = OV.OverlayScanner(db, case)
    out = await scanner.run(assets)
    out.update(calls=scanner.calls, errors=scanner.errors[:10], stopped_for_budget=scanner.stopped_for_budget)
    return out


async def label_timing(db: Session, case: Case, needs_plan: VisualPlan, bp_row: EditorialBlueprint) -> dict:
    """Every approved picture and clip gets the story phase it shows and the first beat that tells of it
    (mvp/timing.py), so none is shown before the story has got there."""
    if not ai_config.mvp().timing.enabled:
        return {"enabled": False}
    seen: set[int] = set()
    assets = []
    for group in DR.approved_items(db, needs_plan).values():
        for item in group:
            if item.asset.id not in seen:
                seen.add(item.asset.id)
                assets.append(item.asset)
    classifier = TM.TimingClassifier(db, case, json.loads(bp_row.blueprint_json or "{}"))
    out = await classifier.run(assets)
    out.update(calls=classifier.calls, errors=classifier.errors[:5], stopped_for_budget=classifier.stopped_for_budget)
    return out


async def stage_direct(db: Session, case: Case, needs_plan: VisualPlan, bp_row: EditorialBlueprint,
                       manifest: dict, language: str, use_agent: bool = True) -> tuple[VisualPlan, dict]:
    target = language_plan(db, needs_plan, language)
    RV.supersede(db, case.id, language)
    blueprint = json.loads(bp_row.blueprint_json or "{}")
    beats = {b["id"]: {"beat_id": b["id"], "purpose": b.get("purpose"), "summary": b.get("summary"),
                       "human_focus": b.get("human_focus")} for b in blueprint.get("beats") or []}
    order = {b["id"]: i for i, b in enumerate(blueprint.get("beats") or [])}
    graph_id = json.loads(needs_plan.validation_json)["mvp"].get("reveal_graph_id")

    async def director(case_, beat, slots, pool):
        return await DR.model_director(case_, beat, slots, pool, db)

    requirements = requirements_of(needs_plan)
    overlays = await scan_overlays(db, case, needs_plan)      # first the marks: cut away, or the picture is out
    timing = await label_timing(db, case, needs_plan, bp_row)  # then what is left is labelled for its place in the story
    offers, _evidence = await EV.build_offers(db, case, bp_row, needs_plan, manifest)
    region = case_region(case)
    async def direct(matches=None, last: bool = False) -> dict:
        return await DR.direct_film(db, case, needs_plan, manifest, graph_id, order, beats,
                                    director if use_agent else None, target, None, region, offers, matches,
                                    cover_last=last)

    report = await direct()
    for _ in range(ai_config.mvp().topup.passes):
        if not report["gaps"]:
            break
        made = build_gap_candidates(db, needs_plan, requirements, {g["beat"] for g in report["gaps"]})
        if not made["created"]:
            break
        verified = await verify_pending(db, case, needs_plan, requirements)
        report = await direct()
        report["topup"] = {**made, "judged": verified["judged"]}
    if report["gaps"] and ai_config.mvp().moments.enabled:
        used = {s["asset_id"] for b in json.loads(target.plan_json or "{}").get("beats") or []
                for s in b["shots"] if s.get("asset_id")}
        moments = await MC.MomentCutter(db, case, needs_plan.id, language).run(report["gaps"], used)
        if moments["approved"]:
            overlays = await scan_overlays(db, case, needs_plan)
            timing = await label_timing(db, case, needs_plan, bp_row)
            report = await direct()
        report["moments"] = moments
    if report["gaps"] and ai_config.mvp().general.enabled:
        general = await GI.GeneralImagery(db, case, needs_plan.id, language).run(report["gaps"])
        if general["approved"]:
            overlays = await scan_overlays(db, case, needs_plan)
            timing = await label_timing(db, case, needs_plan, bp_row)
        report["general"] = general
    matches = None
    if report["gaps"] and ai_config.mvp().topical.enabled:
        matcher = TP.TopicMatcher(db, case, needs_plan)
        matches = await matcher.run(report["gaps"], TP.catalog_of(DR.approved_items(db, needs_plan)))
        if matches:
            kept_before = {k: report[k] for k in ("moments", "general") if k in report}
            report = await direct(matches)
            report.update(kept_before)
        report["topical"] = {"moments_matched": len(matches), "calls": matcher.calls, "errors": matcher.errors}
    if report["gaps"] and DR.cover_approved(needs_plan):    # the very last resort, after every other source was tried
        kept_before = {k: report[k] for k in ("moments", "general", "topical") if k in report}
        report = await direct(matches, last=True)
        report.update(kept_before)
    report["overlays"], report["timing"] = overlays, timing
    kept = {k: report[k] for k in ("moments", "general", "topical", "overlays", "timing") if k in report}
    if kept:                   # what the extra passes did stays with the plan (the report above is the plan's own)
        validation = json.loads(target.validation_json or "{}")
        validation.setdefault("plan", {}).update(kept)
        target.validation_json = json.dumps(validation, ensure_ascii=False)
        db.commit()
    from app.documentary.visuals.generated import materialize

    report["graphics"] = await materialize(db, case, target)
    report["graphics_localized"] = await EV.localize_graphics(db, case, target, language)
    return target, report


NEIGHBOUR_NOTE = " On resume the last round covers what is still bare from neighbours (withdraw it in the review)."


def request_review(db: Session, needs_plan: VisualPlan) -> None:
    """The run stops for the owner's picture review: when it is resumed, the last planning round may cover what is still
    bare from the neighbouring pictures (the owner can withdraw that in the review)."""
    validation = json.loads(needs_plan.validation_json or "{}")
    validation.setdefault("mvp", {})["review_requested"] = True
    needs_plan.validation_json = json.dumps(validation, ensure_ascii=False)
    db.commit()


def stage_owner_attention(db: Session, needs_plan: VisualPlan, language: str) -> None:
    """The gate before direction: pictures the verifiers could not settle (needs_review) are the owner's to decide, so
    the run stops until they have been reviewed. Approving or rejecting a picture takes it out of needs_review."""
    from app.documentary.pipeline.errors import AwaitingPictureReview

    pictures = {asset_id for (asset_id,) in (
        db.query(MvpCandidate.visual_asset_id)
        .join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
        .filter(MvpRequirement.visual_plan_id == needs_plan.id, MvpCandidate.final_status == "needs_review"))}
    if not pictures:
        return
    raise AwaitingPictureReview(
        f"Awaiting picture review: {len(pictures)} picture(s) need your attention before the {language} film is "
        "directed. Open Review pictures, approve or reject each one marked Not sure, then resume.")


def stage_complete(plan_row: VisualPlan, language: str) -> dict:
    """The gate before anything is made from the plan: every moment has a picture, a video or a graphic. Moments
    that are still empty after all alternatives stop the run for the owner's picture review."""
    from app.documentary.pipeline.errors import AwaitingPictureReview

    plan = json.loads(plan_row.validation_json or "{}").get("plan") or {}
    gaps = plan.get("gaps") or []
    if not gaps:
        return {"gaps": 0, "alternatives": plan.get("alternatives", {})}
    seconds = round(sum(g["end"] - g["start"] for g in gaps), 1)
    beats = sorted({g["beat"] for g in gaps})
    raise AwaitingPictureReview(
        f"Awaiting picture review: {len(gaps)} moment(s) ({seconds}s) of the {language} film have no picture, "
        f"video or graphic, in beats {', '.join(beats[:12])}{'...' if len(beats) > 12 else ''}. Review the pictures "
        "the planner rejected or was unsure about (Review pictures), then resume." + NEIGHBOUR_NOTE)


def stage_coverage(db: Session, needs_plan: VisualPlan, script: dict, manifest: dict) -> dict:
    report = CV.measure(script, needs_plan, db, manifest["timeline"].get("sentences") or [])
    verdict = CV.verdict(report)
    planned_music = len((manifest.get("mix") or {}).get("placements") or [])
    music = {"planned": planned_music, "in_film": len(script.get("music") or [])}
    host = {"required": host_required(), "avatar_in_film": bool(script.get("avatar_used")),
            "seconds": round(sum(h.get("seconds", 0.0) for h in (script.get("host") or [])
                                 if isinstance(h, dict)), 1)}
    overridden = sorted({sh["asset_id"] for sh in script.get("shots") or [] if sh.get("asset_id")} & {
        a.asset_code for a in db.query(VisualAsset).filter(
            VisualAsset.case_id == needs_plan.case_id,
            VisualAsset.rights_reason.like(f"{VF.OVERRIDE_PREFIX}%")).all()})
    if overridden:
        verdict["publication_blockers"].append(
            f"{len(overridden)} picture(s) are used on the owner's rights decision and need a licence before publication")
    if host["required"] and not host["avatar_in_film"]:
        verdict["blocking"].append("the film carries no host avatar (the host is required)")
    if music["planned"] != music["in_film"]:
        verdict["blocking"].append(f"{music['planned']} music placement(s) planned, {music['in_film']} in the film")
    verdict["review_ready"] = not verdict["blocking"]
    return {**report, **verdict, "host": host, "music": music}


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
