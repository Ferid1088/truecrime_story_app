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

