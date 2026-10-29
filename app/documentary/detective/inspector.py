"""Section I: inspection of a case's detective plan, targeted scene revision, and invalidation of
scenes whose content bindings point at a claim/question/relation that has since changed.

Reads only what Sections B-H already persist (DetectiveScene/DetectiveToolAction, the memory
tables, the compiled script's `detective_design_reviews`); it does not invent new planning data.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import DetectiveScene, DetectiveToolAction, ProductionScript, RevealGraph


def scene_dict(scene: DetectiveScene) -> dict:
    return {
        "scene_key": scene.scene_key, "scene_order": scene.scene_order, "mode": scene.mode,
        "aim": scene.aim, "change_in_understanding": scene.change_in_understanding,
        "why_selected": scene.why_selected, "expected_audience_gain": scene.expected_audience_gain,
        "covered_unit_keys": json.loads(scene.covered_unit_keys_json or "[]"),
        "active_question_keys": json.loads(scene.active_question_keys_json or "[]"),
        "active_entity_keys": json.loads(scene.active_entity_keys_json or "[]"),
        "alternatives_considered": json.loads(scene.alternatives_considered_json or "[]"),
        "stale": scene.stale, "stale_reason": scene.stale_reason,
        "actions": [action_dict(a) for a in scene.actions],
    }


def action_dict(action: DetectiveToolAction) -> dict:
    return {
        "action_key": action.action_key, "tool_id": action.tool_id, "purpose": action.purpose,
        "content_bindings": json.loads(action.content_bindings_json or "[]"),
        "reuse_reason": action.reuse_reason,
    }


def design_reviews_for(db: Session, graph: RevealGraph) -> list[dict]:
    """The Visual Design Director's outcomes already attached to the latest compiled script for
    this graph's blueprint (Section H's `review_resolved_actions`), if any exist yet."""
    script = (db.query(ProductionScript).filter_by(blueprint_id=graph.blueprint_id)
              .order_by(ProductionScript.id.desc()).first())
    if script is None:
        return []
    body = json.loads(script.script_json or "{}")
    return body.get("detective_design_reviews") or []


def plan_summary(db: Session, graph: RevealGraph) -> dict:
    """Everything the owner inspector shows for one case's detective plan: every scene with its
    aim, chosen tools, the planner's accept/combine/defer/reject decision and why, plus the design
    review outcomes already compiled for it. Mirrors what Section C/D/G already persist; adds
    nothing new except `stale`."""
    scenes = (db.query(DetectiveScene).filter_by(graph_id=graph.id)
              .order_by(DetectiveScene.scene_order).all())
    return {
        "graph_id": graph.id, "language": "unused",
        "scene_count": len(scenes),
        "stale_count": sum(1 for s in scenes if s.stale),
        "scenes": [scene_dict(s) for s in scenes],
        "design_reviews": design_reviews_for(db, graph),
    }


def invalidate_dependent_scenes(db: Session, graph_id: int, claim_key: str, reason: str) -> list[str]:
    """An upstream correction to `claim_key` (or an entity/question/relation key, since every one
    of them can appear in `content_bindings`) marks every scene whose actions actually bind to it
    as stale - and nothing else. Scenes are never rewritten or deleted (history survives for
    playback/review, per Section E's own rule); only the owner-facing `stale` flag changes, so an
    unrelated scene's content and its own history stay untouched."""
    scenes = db.query(DetectiveScene).filter_by(graph_id=graph_id).all()
    affected: list[str] = []
    for scene in scenes:
        if scene.stale:
            continue
        bound = any(
            claim_key in json.loads(a.content_bindings_json or "[]")
            for a in scene.actions
        )
        if bound:
            scene.stale = True
            scene.stale_reason = reason
            affected.append(scene.scene_key)
    if affected:
        db.commit()
    return affected
