"""The duplicate filter after research: the same picture found twice (by different searches, sources or as a frame of a
video) is kept once. Photos whose perceptual hashes are close are one group; the planner keeps the one the owner approved,
else the one most requirements approved, else the largest. The others are marked, never offered or used, and every
requirement they were offered for goes to the picture that is kept. A picture the owner has decided anything about is never
marked: the owner's decisions stand."""

from __future__ import annotations

import json
from collections import defaultdict
from functools import cache

import numpy as np
from PIL import Image
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import MvpCandidate, MvpRequirement, VisualAsset, VisualPlan
from app.documentary import storage
from app.documentary.mvp import verify as VF
from app.documentary.visuals import images as IM


@cache
def _thumb(path: str, side: int) -> np.ndarray | None:
    """The picture as a small grayscale array, or None when the file cannot be read."""
    file = storage.resolve(path)
    try:
        with Image.open(file) as img:
            return np.asarray(img.convert("L").resize((side, side), Image.BILINEAR), dtype=np.float32)
    except (OSError, ValueError, AttributeError):
        return None


def similar(a: VisualAsset, b: VisualAsset) -> bool:
    """The two photos show the same picture: their small grayscale versions correlate."""
    cfg = ai_config.main_visual_planner.duplicates
    x, y = _thumb(a.local_path, cfg.thumb_side), _thumb(b.local_path, cfg.thumb_side)
    if x is None or y is None or x.std() == 0 or y.std() == 0:
        return False
    return float(np.corrcoef(x.ravel(), y.ravel())[0, 1]) >= cfg.min_similarity


def groups_of(assets: list[VisualAsset], distance: int) -> list[list[VisualAsset]]:
    """Photos that are the same picture (hash distance within `distance` and a matching look), groups of two or more."""
    parent = {a.id: a.id for a in assets}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(assets):
        for b in assets[i + 1:]:
            if IM.hamming(a.phash, b.phash) <= distance and similar(a, b):
                parent[find(a.id)] = find(b.id)
    by_root: dict[int, list[VisualAsset]] = defaultdict(list)
    for a in assets:
        by_root[find(a.id)].append(a)
    return [g for g in by_root.values() if len(g) > 1]


def filter_duplicates(db: Session, plan: VisualPlan) -> dict:
    """Mark the copies of the case's photos and move their requirements to the kept picture. Returns the counts."""
    distance = ai_config.main_visual_planner.duplicates.max_hash_distance
    case_id = plan.case_id
    rows = (db.query(MvpCandidate).join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
            .filter(MvpRequirement.visual_plan_id == plan.id).all())
    by_asset: dict[int, list[MvpCandidate]] = defaultdict(list)
    for cand in rows:
        by_asset[cand.visual_asset_id].append(cand)
    photos = db.query(VisualAsset).filter(VisualAsset.case_id == case_id, VisualAsset.asset_type == "photo",
                                          VisualAsset.phash.isnot(None)).all()
    stats = {"groups": 0, "marked": 0, "moved": 0}
    for group in groups_of(photos, distance):
        if not any(a.id in by_asset for a in group):          # a group no requirement was offered is of no concern
            continue
        stats["groups"] += 1
        decided = {a.id for a in group if any(VF.owner_settled(c) is not None for c in by_asset[a.id])}
        approved_by_owner = [a for a in group if any(VF.owner_settled(c) == "approved" for c in by_asset[a.id])]
        free = [a for a in group if a.id not in decided]
        if not free:
            continue
        keeper = (approved_by_owner[0] if approved_by_owner else
                  max(free, key=lambda a: (sum(c.final_status == "approved" for c in by_asset[a.id]),
                                           (a.width or 0) * (a.height or 0), -a.id)))
        kept = {c.requirement_id: c for c in by_asset[keeper.id]}
        for copy in free:
            if copy.id == keeper.id:
                continue
            for cand in by_asset[copy.id]:
                if VF.is_duplicate(cand):
                    continue
                if cand.requirement_id not in kept:
                    moved = MvpCandidate(requirement_id=cand.requirement_id, visual_asset_id=keeper.id)
                    db.add(moved)
                    kept[cand.requirement_id] = moved
                    stats["moved"] += 1
                cand.verifiers_json = json.dumps(json.loads(cand.verifiers_json or "[]") + [
                    {"verifier": VF.DUPLICATE_FILTER, "decision": "duplicate", "of": keeper.asset_code}])
                cand.final_status = VF.final_status(cand)
                stats["marked"] += 1
    db.commit()
    return stats
