"""Persist a validated Detective Director output (Section C) into Section B's investigation
memory plus the new `DetectiveScene`/`DetectiveToolAction` tables. Called only after
`app.documentary.detective.schema.validate_director_output` returns no violations."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.documentary import memory as MEM
from app.db.models import DetectiveScene, DetectiveToolAction


def persist_director_output(db: Session, graph_id: int, agent_run_id: int | None,
                             mode: str, payload: dict) -> dict:
    """Write every entity/claim/question/relation/knowledge_event/scene in `payload` through
    Section B's memory functions (which enforce basis/kind/first-introduction invariants) and
    the new scene/action tables. Returns counts for live-evidence reporting."""
    for e in payload.get("entities", []):
        node = MEM.ensure_entity(
            db, graph_id, node_key=e["id"], label=e["name"], category=e.get("type", "object"),
            beat_id=e.get("beat_id", "B00"), order=e.get("order", 0), aliases=e.get("aliases"),
        )
        _ = node

    for c in payload.get("claims", []):
        MEM.add_claim(
            db, graph_id, claim_key=c["id"], entity_node_key=c["entity_ids"][0],
            attribute=c["attribute"], text=c["text"], basis=c["basis"],
            attribution=c.get("attribution"),
            supporting_unit_keys=c.get("supporting_span_ids"),
            release_anchor_id=_anchor_row_id(db, graph_id, c.get("release_anchor")),
            evidence_fact_ids=c.get("evidence_ids"),
        )

    for q in payload.get("questions", []):
        MEM.add_question(
            db, graph_id, question_key=q["id"], entity_node_keys=q["entity_ids"],
            text=q["question_text"], kind=q["kind"],
            opening_anchor_id=_anchor_row_id(db, graph_id, q.get("opening_anchor")),
            supporting_claim_keys=q.get("supporting_span_ids"),
        )

    for r in payload.get("relations", []):
        MEM.add_relation(
            db, graph_id, relation_key=r["id"], from_node_key=r["endpoint_ids"][0],
            to_node_key=r["endpoint_ids"][1], relation_type=r["relation_type"], label=r["label"],
            supporting_unit_keys=r.get("supporting_span_ids"),
            release_anchor_id=_anchor_row_id(db, graph_id, r.get("release_anchor")),
        )

    for ev in payload.get("knowledge_events", []):
        MEM.record_knowledge_event(
            db, graph_id, subject_type=ev["subject_type"], subject_key=ev["subject_id"],
            operation=ev["operation"], new_state=ev.get("new_state", {}),
            anchor_id=_anchor_row_id(db, graph_id, ev.get("anchor")),
            supporting_claim_keys=ev.get("supporting_claim_ids"),
        )

    scene_count = 0
    updated_count = 0
    action_count = 0
    for i, scene in enumerate(payload.get("scenes", [])):
        row = db.query(DetectiveScene).filter_by(graph_id=graph_id, scene_key=scene["id"]).first()
        fields = dict(
            scene_order=i, agent_run_id=agent_run_id, mode=mode,
            covered_unit_keys_json=json.dumps(scene.get("covered_span_ids", [])),
            aim=scene.get("aim", ""),
            active_question_keys_json=json.dumps(scene.get("active_question_ids", [])),
            active_entity_keys_json=json.dumps(scene.get("active_thread_ids", [])),
            change_in_understanding=scene.get("change_in_understanding", ""),
            alternatives_considered_json=json.dumps(scene.get("alternatives_considered", [])),
            why_selected=scene.get("why_selected", ""),
            expected_audience_gain=scene.get("expected_audience_gain", ""),
            audio_clarity_findings_json=json.dumps(scene.get("audio_clarity_findings", [])),
        )
        if row is None:
            row = DetectiveScene(graph_id=graph_id, scene_key=scene["id"], **fields)
            db.add(row)
            db.flush()
            scene_count += 1
        else:
            # A later call (direct_scenes refining a map_film stub, or a repair) re-submitting the
            # same scene_key replaces its fields and ordered actions — never silently ignored,
            # since that would make direct_scenes unable to ever add the action detail map_film
            # itself did not produce.
            for key, value in fields.items():
                setattr(row, key, value)
            db.query(DetectiveToolAction).filter_by(scene_id=row.id).delete()
            db.flush()
            updated_count += 1
        for j, action in enumerate(scene.get("actions", [])):
            db.add(DetectiveToolAction(
                scene_id=row.id, action_key=action["id"], order_index=j,
                tool_id=action["tool_id"], purpose=action.get("purpose", ""),
                payload_json=json.dumps(action.get("payload", {})),
                content_bindings_json=json.dumps(action.get("content_bindings", [])),
                continuity_refs_json=json.dumps(action.get("continuity_refs", [])),
                board_operations_json=json.dumps(action["board_operations"])
                if action.get("board_operations") is not None else None,
                reading_requirements_json=json.dumps(action["reading_requirements"])
                if action.get("reading_requirements") is not None else None,
                relationship_to_neighbors=action.get("relationship_to_neighbors"),
                fallback_json=json.dumps(action["fallback"]) if action.get("fallback") is not None else None,
                reuse_reason=action.get("reuse_reason"),
            ))
            action_count += 1

    db.commit()
    return {
        "entities": len(payload.get("entities", [])), "claims": len(payload.get("claims", [])),
        "questions": len(payload.get("questions", [])), "relations": len(payload.get("relations", [])),
        "knowledge_events": len(payload.get("knowledge_events", [])),
        "scenes": scene_count, "scenes_updated": updated_count, "actions": action_count,
    }


def _anchor_row_id(db: Session, graph_id: int, anchor_key: str | None) -> int | None:
    if anchor_key is None:
        return None
    from app.db.models import ReleaseAnchor
    row = db.query(ReleaseAnchor).filter_by(graph_id=graph_id, anchor_key=anchor_key).first()
    return row.id if row is not None else None
