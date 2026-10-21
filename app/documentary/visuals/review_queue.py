"""P10: review triage for the owner. The pipeline already resolves most needs_review pictures
automatically before a human ever sees them: the second, independent pass (S3.b) decides
non-person place/object/document/map pictures it has not yet tried; here, an obviously-junk
picture (confidence hugging the reject floor, no case-source provenance) is auto-rejected with
a stored reason instead of shown, and the rest are grouped by near-duplicate (the same
perceptual-hash closeness the download-time dedupe already uses) so one decision can resolve a
whole group. An approve/reject decision goes through the existing PATCH /api/visuals/{id}
(human_override=True) — already its own audit trail; nothing new is needed for that."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import EntityDossier, VisualAsset
from app.documentary.visuals import images as IM

CASE_SOURCE_PROVIDERS = {"source_page", "lead_source"}


def is_obvious_junk(asset: VisualAsset) -> bool:
    """Needs_review by a hair, nothing else recommending it: within `auto_reject_margin` of the
    reject floor, and not from the case's own sources (a source-page picture earns a second
    look even at low confidence — it is already known to be about the case)."""
    if asset.provider in CASE_SOURCE_PROVIDERS:
        return False
    conf = asset.verification_confidence if asset.verification_confidence is not None else 1.0
    floor = ai_config.visual_verification.reject_below_confidence
    margin = ai_config.review_queue.auto_reject_margin
    return conf < floor + margin


def auto_reject_obvious_junk(db: Session, case_id: int) -> int:
    """Rejects every obvious-junk needs_review picture of the case now, with a stored reason;
    returns how many. Never a person (not that this rule would ever fire for a confident one —
    identity keeps its own strict thresholds regardless)."""
    assets = (db.query(VisualAsset)
             .filter(VisualAsset.case_id == case_id, VisualAsset.verification_status == "needs_review")
             .all())
    n = 0
    for a in assets:
        if not is_obvious_junk(a):
            continue
        stored = json.loads(a.verification_json or "{}")
        a.verification_status = "rejected"
        stored["reason"] = "triage_auto_reject"
        stored["triage_note"] = (f"confidence {a.verification_confidence} within "
                                 f"{ai_config.review_queue.auto_reject_margin} of the reject floor")
        a.verification_json = json.dumps(stored, ensure_ascii=False)
        n += 1
    db.commit()
    return n


def group_duplicates(assets: list[VisualAsset]) -> list[list[VisualAsset]]:
    """Clusters by perceptual-hash closeness (the same threshold the download-time dedupe uses):
    one review decision can resolve a whole group. A picture with no phash (a video piece, a
    generated card) is always its own group."""
    threshold = ai_config.review_queue.duplicate_hamming_threshold
    groups: list[list[VisualAsset]] = []
    used: set[int] = set()
    for a in assets:
        if a.id in used or not a.phash:
            continue
        group = [a]
        used.add(a.id)
        for b in assets:
            if b.id not in used and b.phash and IM.hamming(a.phash, b.phash) <= threshold:
                group.append(b)
                used.add(b.id)
        groups.append(group)
    for a in assets:
        if a.id not in used:
            groups.append([a])
            used.add(a.id)
    return groups


def review_queue(db: Session, case_id: int) -> dict:
    """The true human queue, after the automated steps: every stored needs_review picture
    ("before"), minus the ones the second pass has not tried yet (S3.b resolves those on its
    own next run — a human need not see them first), grouped by near-duplicate."""
    from app.agents.visual_verification import VisualVerificationAgent

    assets = (db.query(VisualAsset)
             .filter(VisualAsset.case_id == case_id, VisualAsset.verification_status == "needs_review")
             .all())
    second_pass_pending = {a.id for a in VisualVerificationAgent.second_pass_candidates(assets)}
    queue_assets = [a for a in assets if a.id not in second_pass_pending]
    groups = group_duplicates(queue_assets)
    return {
        "before": len(assets),
        "second_pass_pending": len(second_pass_pending),
        "items": len(queue_assets),
        "groups": len(groups),
        "queue": [{"group_id": i, "lead": g[0].asset_code, "subject_type": g[0].subject_type,
                  "confidence": g[0].verification_confidence,
                  "asset_codes": [a.asset_code for a in g]}
                 for i, g in enumerate(groups)],
    }


def beats_needing_entity(requirements: dict, blueprint: dict, entity_key: str) -> list[dict]:
    """The beats of the plan whose requirements name this entity, each with its narration
    summary (from the blueprint, which the plan's own requirements do not carry) — P10's "the
    beat text it was chosen for"."""
    blueprint_by_id = {b.get("id"): b for b in blueprint.get("beats") or []}
    out = []
    for b in requirements.get("beats") or []:
        if any(r.get("entity") == entity_key for r in b.get("requirements") or []):
            bp = blueprint_by_id.get(b.get("beat_id")) or {}
            out.append({"beat_id": b.get("beat_id"),
                       "summary": bp.get("summary") or bp.get("human_focus") or ""})
    return out


def mark_wrong_person(db: Session, asset_id: int, entity_key: str) -> dict:
    """P10: "Wrong person" — rejects the picture and writes a negative example into the
    entity's own dossier (P1's not_this list), so the same look-alike is recognised next time,
    in ranking (director.entity_score) and in the verifier's own input."""
    asset = db.get(VisualAsset, asset_id)
    if asset is None:
        raise ValueError(f"no visual asset {asset_id}")
    stored = json.loads(asset.verification_json or "{}")
    asset.verification_status = "rejected"
    asset.human_override = True
    stored["reason"] = "wrong_person_owner_review"
    asset.verification_json = json.dumps(stored, ensure_ascii=False)
    note = f"{asset.title or asset.caption or asset.source_url or asset.asset_code} is not {entity_key}"[:150]
    dossier = (db.query(EntityDossier)
              .filter(EntityDossier.case_id == asset.case_id, EntityDossier.entity_key == entity_key)
              .first())
    if dossier is not None:
        not_this = json.loads(dossier.not_this_json or "[]")
        if note not in not_this:
            not_this.append(note)
            limit = ai_config.entity_dossier.max_not_this
            dossier.not_this_json = json.dumps(not_this[-limit:], ensure_ascii=False)
    db.commit()
    return {"asset_code": asset.asset_code, "entity_key": entity_key, "not_this_note": note,
           "dossier_updated": dossier is not None}


def review_queue_for_ui(db: Session, case_id: int) -> dict:
    """The enriched, batch-review-page listing: one entry per group, with the beat(s) it would
    fill, the entity it should show and the verifier's own reason in one line — sorted by how
    many beats it would fill, most first (P10)."""
    from app.db.models import EditorialBlueprint, VisualPlan

    base = review_queue(db, case_id)
    plan = (db.query(VisualPlan).filter(VisualPlan.case_id == case_id)
           .order_by(VisualPlan.id.desc()).first())
    requirements = json.loads(plan.requirements_json or "{}") if plan else {}
    blueprint = {}
    if plan and plan.blueprint_id:
        row = db.get(EditorialBlueprint, plan.blueprint_id)
        if row is not None:
            blueprint = json.loads(row.blueprint_json or "{}")
    entities = {e["key"]: e for e in requirements.get("entities") or []}
    items = []
    for g in base["queue"]:
        asset = db.query(VisualAsset).filter_by(case_id=case_id, asset_code=g["lead"]).first()
        if asset is None:
            continue
        entity_key = asset.entity_key
        beats = beats_needing_entity(requirements, blueprint, entity_key) if entity_key else []
        stored = json.loads(asset.verification_json or "{}")
        reason = stored.get("depicts") or stored.get("reason") or "no reason recorded"
        items.append({
            **g,
            "entity_key": entity_key,
            "entity_name": (entities.get(entity_key) or {}).get("name") or entity_key,
            "beats": beats,
            "beats_count": len(beats),
            "verifier_reason": reason,
            "asset": asset_dict(asset),
        })
    items.sort(key=lambda x: -x["beats_count"])
    return {**base, "queue": items}


def asset_dict(asset: VisualAsset) -> dict:
    """A minimal, review-page-sized view of the picture (the full one lives in
    app.documentary.api.asset_dict; kept separate so this module has no UI-layer import)."""
    return {
        "id": asset.id, "asset_id": asset.asset_code, "title": asset.title,
        "caption": asset.caption, "subject_type": asset.subject_type,
        "image_url": f"/api/visuals/{asset.id}/file",
        "verification_confidence": asset.verification_confidence,
    }
