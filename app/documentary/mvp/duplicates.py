"""The duplicate filter after research: the same picture found twice (by different searches, sources or as a frame of a
video) is kept once. Photos whose perceptual hashes are close are one group; the planner keeps the one the owner approved,
else the one most requirements approved, else the largest. The others are marked, never offered or used, and every
requirement they were offered for goes to the picture that is kept. A picture the owner has decided anything about is never
marked: the owner's decisions stand."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
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


def _keeper_of(group: list[VisualAsset], by_asset: dict[int, list[MvpCandidate]]) -> VisualAsset:
    """The picture of a group that stays: the owner's approved one, else the one most requirements approved, else the largest."""
    owner = [a for a in group if any(VF.owner_settled(c) == "approved" for c in by_asset[a.id])]
    if owner:
        return max(owner, key=lambda a: (sum(c.final_status == "approved" for c in by_asset[a.id]), -a.id))
    return max(group, key=lambda a: (sum(c.final_status == "approved" for c in by_asset[a.id]),
                                     (a.width or 0) * (a.height or 0), -a.id))


def _into_repeated_folder(asset: VisualAsset) -> str | None:
    """The file of a repeated picture moves to the case's `repeated` folder (its path is updated); returns the new path."""
    source = storage.resolve(asset.local_path)
    if source is None or not source.is_file():
        return None
    target = storage.visuals_dir(asset.case_id, "repeated") / source.name
    shutil.move(source, target)
    asset.local_path = (str(target.relative_to(storage.ROOT)) if not Path(asset.local_path).is_absolute()
                        else str(target))
    return asset.local_path


def repeat_approved(db: Session, plan: VisualPlan) -> dict:
    """Of the approved pictures that are the same picture, one stays approved. The others (also the owner's approved ones: the
    owner asked for this) are marked as repeated, rejected, kept out of every list and moved to the `repeated` folder; what they
    were approved for goes to the picture that stays. Returns the counts and the files."""
    distance = ai_config.main_visual_planner.duplicates.max_hash_distance
    rows = (db.query(MvpCandidate).join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
            .filter(MvpRequirement.visual_plan_id == plan.id).all())
    by_asset: dict[int, list[MvpCandidate]] = defaultdict(list)
    for cand in rows:
        by_asset[cand.visual_asset_id].append(cand)
    approved = [a for a in db.query(VisualAsset).filter(VisualAsset.case_id == plan.case_id, VisualAsset.asset_type == "photo",
                                                        VisualAsset.phash.isnot(None)).all()
                if any(c.final_status == "approved" and not VF.is_duplicate(c) for c in by_asset.get(a.id, []))]
    stats = {"groups": 0, "repeated": 0, "moved_files": 0, "approvals_moved": 0, "kept": []}
    for group in groups_of(approved, distance):
        stats["groups"] += 1
        keeper = _keeper_of(group, by_asset)
        kept = {c.requirement_id: c for c in by_asset[keeper.id]}
        stats["kept"].append(keeper.asset_code)
        for copy in group:
            if copy.id == keeper.id:
                continue
            for cand in by_asset[copy.id]:
                if VF.is_duplicate(cand):
                    continue
                if cand.final_status == "approved" and cand.requirement_id not in kept:
                    moved = MvpCandidate(
                        requirement_id=cand.requirement_id, visual_asset_id=keeper.id, role=cand.role,
                        provenance_status=cand.provenance_status, identity_status=cand.identity_status,
                        rights_status=cand.rights_status, ethics_status=cand.ethics_status, scene_status=cand.scene_status,
                        final_status="approved", reasons_json=cand.reasons_json, verifiers_json=cand.verifiers_json)
                    db.add(moved)
                    kept[cand.requirement_id] = moved
                    stats["approvals_moved"] += 1
                cand.verifiers_json = json.dumps(json.loads(cand.verifiers_json or "[]") + [
                    {"verifier": VF.DUPLICATE_FILTER, "decision": "duplicate", "of": keeper.asset_code, "owner_ordered": True}])
                cand.final_status = VF.final_status(cand)
                stats["repeated"] += 1
            spec = json.loads(copy.spec_json or "{}")
            spec["repeated_of"] = keeper.asset_code
            copy.spec_json = json.dumps(spec)
            stats["moved_files"] += _into_repeated_folder(copy) is not None
    db.commit()
    return stats
