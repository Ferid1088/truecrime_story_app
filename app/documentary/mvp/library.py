"""Step 3/7 - the case's shared media library as the planner sees it: which stored assets are candidates
for which requirement. Original bytes are never touched; only decisions about them are recorded."""

from __future__ import annotations

import json

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    MediaUsage,
    MvpCandidate,
    MvpRequirement,
    ProductionScript,
    VisualAsset,
    VisualAudit,
    VisualPlan,
    VisualSearchLog,
)
from app.documentary.mvp.needs import fold

CANDIDATE_TYPES = ("photo", "video")


def _tokens(text: str | None) -> set[str]:
    return {t for t in fold(text or "").split() if len(t) > 2}


def relevance(asset: VisualAsset, entity: dict) -> float:
    """How strongly the stored asset points at this entity: its own entity key and claimed entities first,
    then the search it was found for, then its title and caption. Zero means not a candidate."""
    key = entity["key"]
    claimed = json.loads(asset.entities_json or "[]")
    score = 0.0
    if asset.entity_key == key:
        score += 3.0
    if key in claimed:
        score += 2.0
    names = [entity["name"], *entity.get("aliases", [])]
    wanted = set().union(*(_tokens(n) for n in names)) if names else set()
    if wanted:
        found = _tokens(asset.found_for)
        text = _tokens(f"{asset.title or ''} {asset.caption or ''} {asset.description or ''}")
        score += 1.5 * len(wanted & found) / len(wanted)
        score += 1.0 * len(wanted & text) / len(wanted)
    return score


def build_candidates(db: Session, plan: VisualPlan, requirements: dict) -> dict:
    """Attach the best stored assets to every requirement (never more than the configured cap), reusing
    candidate rows that already exist so a retry does not duplicate or reset a verdict."""
    cap = ai_config.mvp().verification.max_candidates_per_requirement
    entities = {e["key"]: e for e in requirements["entities"]}
    assets = [a for a in db.query(VisualAsset).filter(VisualAsset.case_id == plan.case_id,
                                                      VisualAsset.asset_type.in_(CANDIDATE_TYPES),
                                                      VisualAsset.local_path.isnot(None),
                                                      VisualAsset.file_removed_at.is_(None)).all()]
    made = reused = starved = 0
    for req in db.query(MvpRequirement).filter(MvpRequirement.visual_plan_id == plan.id):
        entity = entities.get(req.entity_key)
        if entity is None:
            continue
        ranked = sorted(((relevance(a, entity), a) for a in assets), key=lambda x: -x[0])
        have = {c.visual_asset_id for c in req.candidates}
        chosen = [a for score, a in ranked if score >= 1.0][:cap]
        if not chosen:
            starved += 1
        for asset in chosen:
            if asset.id in have:
                reused += 1
                continue
            db.add(MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id))
            made += 1
    db.commit()
    return {"created": made, "reused": reused, "requirements_without_candidates": starved}


CONTEXT_TYPES = ("place", "building", "event", "object", "vehicle", "document", "organization")


def build_gap_candidates(db: Session, plan: VisualPlan, requirements: dict, gap_beats: set[str]) -> dict:
    """The coverage top-up: stored pictures that were never judged (no requirement named them) become
    candidates for the place/context requirements of the beats the shot list left bare. A person is never
    attached this way; each picture is judged once for at most max_requirements_per_asset entities."""
    cfg = ai_config.mvp()
    entities = {e["key"]: e for e in requirements["entities"] if e.get("type") in CONTEXT_TYPES}
    reqs = [r for r in db.query(MvpRequirement).filter(MvpRequirement.visual_plan_id == plan.id,
                                                       MvpRequirement.beat_id.in_(gap_beats))
            if r.entity_key in entities and "context" in (json.loads(r.spec_json or "{}")
                                                           .get("acceptable_roles") or [r.role_needed])]
    judged = {c.visual_asset_id for r in db.query(MvpRequirement).filter(MvpRequirement.visual_plan_id == plan.id)
              for c in r.candidates}
    assets = [a for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == plan.case_id, VisualAsset.asset_type.in_(CANDIDATE_TYPES),
        VisualAsset.local_path.isnot(None), VisualAsset.file_removed_at.is_(None)).all()
        if a.id not in judged and a.verification_status != "rejected"]
    scored = []
    for asset in assets:
        best = sorted(((relevance(asset, entities[k]), k) for k in {r.entity_key for r in reqs}), reverse=True)
        top = [k for score, k in best if score >= cfg.topup.min_relevance][:cfg.verification.max_requirements_per_asset]
        if top:
            scored.append((best[0][0], asset, top))
    scored.sort(key=lambda x: -x[0])
    created = 0
    by_entity: dict[str, list[MvpRequirement]] = {}
    for r in reqs:
        by_entity.setdefault(r.entity_key, []).append(r)
    for _score, asset, keys in scored[:cfg.topup.max_assets]:
        for key in keys:
            for req in by_entity[key]:
                db.add(MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id))
                created += 1
    db.commit()
    return {"assets": min(len(scored), cfg.topup.max_assets), "created": created,
            "unjudged_left": max(len(scored) - cfg.topup.max_assets, 0)}


def reset_case_visual_state(db: Session, case_id: int) -> dict:
    """The cutover from the old visual pipeline: its plans, audits, search log, usage records and
    production scripts are removed, and the old inspectors' verdicts on stored pictures are cleared so
    the Main Visual Planner judges them afresh. Files, hashes, sources and provenance are kept; texts,
    voices, music, Host and the research are not touched."""
    removed = {}
    removed["visual_audits"] = db.execute(delete(VisualAudit).where(VisualAudit.case_id == case_id)).rowcount
    removed["visual_search_log"] = db.execute(
        delete(VisualSearchLog).where(VisualSearchLog.case_id == case_id)).rowcount
    removed["media_usages"] = db.execute(delete(MediaUsage).where(MediaUsage.case_id == case_id)).rowcount
    removed["production_scripts"] = db.execute(
        delete(ProductionScript).where(ProductionScript.case_id == case_id)).rowcount
    old_plans = [p.id for p in db.query(VisualPlan).filter(VisualPlan.case_id == case_id)
                 if "mvp" not in json.loads(p.validation_json or "{}")]
    if old_plans:
        removed["visual_plans"] = db.execute(delete(VisualPlan).where(VisualPlan.id.in_(old_plans))).rowcount
    removed["assets_reset"] = db.execute(
        update(VisualAsset).where(VisualAsset.case_id == case_id,
                                  VisualAsset.asset_type.in_(CANDIDATE_TYPES))
        .values(verification_status="unverified", verification_confidence=None,
                verification_json=None, relevance_tier=None, case_relevance=None)).rowcount
    db.commit()
    return removed

