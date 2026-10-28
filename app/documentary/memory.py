"""The detective toolkit's text-based investigation memory (Section B of
docs/TrueCrime_Detective_Planner_Implementation_Prompt.md): narration units, release anchors,
attribute-level claims, questions and typed relations between entities (RevealNode), plus the
single ordered knowledge-event log used to replay what the audience knows at any story position.

Claims/questions/relations never require a pre-existing Fact/Contradiction row — they are
grounded directly in narration text spans. A Fact/Contradiction id may be linked in when one
exists (`evidence_fact_ids_json` on DetectiveClaim), but it is never a prerequisite.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import (
    DetectiveClaim,
    DetectiveKnowledgeEvent,
    DetectiveQuestion,
    DetectiveRelation,
    NarrationUnit,
    ReleaseAnchor,
    RevealNode,
)

CLAIM_BASES = {"established_in_text", "attributed", "disputed", "inference", "explicit_unknown"}
QUESTION_KINDS = {"not_yet_explained", "explicit_unresolved", "interpretive_question"}
QUESTION_STATUSES = {"open", "partial", "answered", "explicitly_unresolved", "reframed"}
KNOWLEDGE_SUBJECT_TYPES = {"entity", "claim", "question", "relation"}


class MemoryValidationError(ValueError):
    """An investigation-memory write would violate a Section B invariant."""


def add_narration_unit(
    db: Session, graph_id: int, unit_key: str, beat_id: str, story_order: int,
    language: str, text: str,
) -> NarrationUnit:
    """Register one stable narration span. Idempotent on `unit_key`: a repeat call with the same
    key and text returns the existing row; a repeat call with different text is rejected, since
    silently rewriting an already-anchored span would invalidate anything that references it."""
    existing = db.query(NarrationUnit).filter_by(graph_id=graph_id, unit_key=unit_key).first()
    if existing is not None:
        if existing.text != text:
            raise MemoryValidationError(
                f"narration unit {unit_key!r} already exists with different text"
            )
        return existing
    unit = NarrationUnit(
        graph_id=graph_id, unit_key=unit_key, beat_id=beat_id, story_order=story_order,
        language=language, text=text,
    )
    db.add(unit)
    db.flush()
    return unit


def add_release_anchor(
    db: Session, graph_id: int, anchor_key: str, narration_unit_id: int,
    char_start: int, char_end: int,
) -> ReleaseAnchor:
    """Register the exact words, within one narration unit, that establish a meaning."""
    unit = db.query(NarrationUnit).filter_by(id=narration_unit_id, graph_id=graph_id).first()
    if unit is None:
        raise MemoryValidationError(f"narration unit {narration_unit_id} not found in graph {graph_id}")
    if not (0 <= char_start < char_end <= len(unit.text)):
        raise MemoryValidationError(
            f"anchor span [{char_start}, {char_end}) is out of bounds for unit {unit.unit_key!r} "
            f"(length {len(unit.text)})"
        )
    existing = db.query(ReleaseAnchor).filter_by(graph_id=graph_id, anchor_key=anchor_key).first()
    if existing is not None:
        return existing
    anchor = ReleaseAnchor(
        graph_id=graph_id, anchor_key=anchor_key, narration_unit_id=narration_unit_id,
        char_start=char_start, char_end=char_end,
    )
    db.add(anchor)
    db.flush()
    return anchor


def _require_entity(db: Session, graph_id: int, node_key: str) -> RevealNode:
    node = db.query(RevealNode).filter_by(graph_id=graph_id, node_key=node_key).first()
    if node is None:
        raise MemoryValidationError(f"entity {node_key!r} is not a known node of graph {graph_id}")
    return node


def ensure_entity(
    db: Session, graph_id: int, node_key: str, label: str, category: str,
    beat_id: str, order: int, aliases: list[str] | None = None,
) -> RevealNode:
    """Get-or-create one `RevealNode` for an entity the Detective Director (Section C) introduces
    that the reveal graph (Section A/2) did not already register. An existing node's identity
    (label/category/first-reveal position) is never overwritten here — only `aliases_json` may
    grow, since adding an alias cannot change what has already been revealed."""
    node = db.query(RevealNode).filter_by(graph_id=graph_id, node_key=node_key).first()
    if node is not None:
        if aliases:
            existing_aliases = set(json.loads(node.aliases_json))
            merged = sorted(existing_aliases | set(aliases))
            if merged != sorted(existing_aliases):
                node.aliases_json = json.dumps(merged)
                db.flush()
        return node
    node = RevealNode(
        graph_id=graph_id, node_key=node_key, label=label, category=category,
        first_revealed_beat_id=beat_id, first_revealed_order=order,
        aliases_json=json.dumps(aliases or []),
    )
    db.add(node)
    db.flush()
    return node


def add_claim(
    db: Session, graph_id: int, claim_key: str, entity_node_key: str, attribute: str,
    text: str, basis: str, attribution: str | None = None,
    supporting_unit_keys: list[str] | None = None, release_anchor_id: int | None = None,
    evidence_fact_ids: list[int] | None = None,
) -> DetectiveClaim:
    """Register one attribute-level, text-grounded claim. Never requires a pre-existing Fact
    row — pass `evidence_fact_ids` only when one genuinely exists."""
    if basis not in CLAIM_BASES:
        raise MemoryValidationError(f"unknown claim basis {basis!r}; must be one of {sorted(CLAIM_BASES)}")
    _require_entity(db, graph_id, entity_node_key)
    existing = db.query(DetectiveClaim).filter_by(graph_id=graph_id, claim_key=claim_key).first()
    if existing is not None:
        return existing
    claim = DetectiveClaim(
        graph_id=graph_id, claim_key=claim_key, entity_node_key=entity_node_key,
        attribute=attribute, text=text, basis=basis, attribution=attribution,
        supporting_unit_keys_json=json.dumps(supporting_unit_keys or []),
        release_anchor_id=release_anchor_id,
        evidence_fact_ids_json=json.dumps(evidence_fact_ids or []),
    )
    db.add(claim)
    db.flush()
    return claim


def add_question(
    db: Session, graph_id: int, question_key: str, entity_node_keys: list[str], text: str,
    kind: str, opening_anchor_id: int | None = None,
    supporting_claim_keys: list[str] | None = None,
) -> DetectiveQuestion:
    """Register one question. `kind` never changes after creation — its *state* (open, partial,
    answered, explicitly_unresolved, reframed) lives only in knowledge events."""
    if kind not in QUESTION_KINDS:
        raise MemoryValidationError(f"unknown question kind {kind!r}; must be one of {sorted(QUESTION_KINDS)}")
    for node_key in entity_node_keys:
        _require_entity(db, graph_id, node_key)
    existing = db.query(DetectiveQuestion).filter_by(graph_id=graph_id, question_key=question_key).first()
    if existing is not None:
        return existing
    question = DetectiveQuestion(
        graph_id=graph_id, question_key=question_key,
        entity_node_keys_json=json.dumps(entity_node_keys), text=text, kind=kind,
        opening_anchor_id=opening_anchor_id,
        supporting_claim_keys_json=json.dumps(supporting_claim_keys or []),
    )
    db.add(question)
    db.flush()
    return question


def add_relation(
    db: Session, graph_id: int, relation_key: str, from_node_key: str, to_node_key: str,
    relation_type: str, label: str, supporting_unit_keys: list[str] | None = None,
    release_anchor_id: int | None = None,
) -> DetectiveRelation:
    """Register one typed, labeled connection between two entities. A shared name or decorative
    detail is not by itself a relation — callers must supply a concrete `relation_type`."""
    _require_entity(db, graph_id, from_node_key)
    _require_entity(db, graph_id, to_node_key)
    existing = db.query(DetectiveRelation).filter_by(graph_id=graph_id, relation_key=relation_key).first()
    if existing is not None:
        return existing
    relation = DetectiveRelation(
        graph_id=graph_id, relation_key=relation_key, from_node_key=from_node_key,
        to_node_key=to_node_key, relation_type=relation_type, label=label,
        supporting_unit_keys_json=json.dumps(supporting_unit_keys or []),
        release_anchor_id=release_anchor_id,
    )
    db.add(relation)
    db.flush()
    return relation


def record_knowledge_event(
    db: Session, graph_id: int, subject_type: str, subject_key: str, operation: str,
    new_state: dict, anchor_id: int | None = None, prior_state: dict | None = None,
    supporting_claim_keys: list[str] | None = None,
) -> DetectiveKnowledgeEvent:
    """Append one event to the single ordered knowledge-event log for this graph.

    `operation == "introduce"` is enforced unique per (subject_type, subject_key): reusing an
    entity/claim/question/relation in a later event must use a different operation (`reference`,
    `update`, `state_change`, `resolve`, `revisit`, ...) — this is the mechanism that keeps first
    introduction distinct from a later, equally valid return to the same subject.
    """
    if subject_type not in KNOWLEDGE_SUBJECT_TYPES:
        raise MemoryValidationError(
            f"unknown knowledge-event subject_type {subject_type!r}; "
            f"must be one of {sorted(KNOWLEDGE_SUBJECT_TYPES)}"
        )
    status = new_state.get("status") if isinstance(new_state, dict) else None
    if subject_type == "question" and status is not None and status not in QUESTION_STATUSES:
        raise MemoryValidationError(
            f"question {subject_key!r}: status {status!r} is not one of {sorted(QUESTION_STATUSES)}")
    if operation == "introduce":
        duplicate = db.query(DetectiveKnowledgeEvent).filter_by(
            graph_id=graph_id, subject_type=subject_type, subject_key=subject_key,
            operation="introduce",
        ).first()
        if duplicate is not None:
            raise MemoryValidationError(
                f"{subject_type} {subject_key!r} was already introduced at order_seq "
                f"{duplicate.order_seq}; use a reference/update/state_change event instead"
            )
    last_order = db.query(DetectiveKnowledgeEvent).filter_by(graph_id=graph_id).count()
    event = DetectiveKnowledgeEvent(
        graph_id=graph_id, order_seq=last_order + 1, anchor_id=anchor_id,
        subject_type=subject_type, subject_key=subject_key, operation=operation,
        prior_state_json=json.dumps(prior_state) if prior_state is not None else None,
        new_state_json=json.dumps(new_state),
        supporting_claim_keys_json=json.dumps(supporting_claim_keys or []),
    )
    db.add(event)
    db.flush()
    return event


def replay_state(db: Session, graph_id: int, up_to_order_seq: int | None = None) -> dict:
    """Reconstruct what is known as of `up_to_order_seq` (inclusive; None = everything so far) by
    scanning the knowledge-event log in order. A later event is simply never read when replaying
    an earlier cutoff, so a future answer cannot leak into an earlier reconstruction.
    """
    query = db.query(DetectiveKnowledgeEvent).filter_by(graph_id=graph_id)
    if up_to_order_seq is not None:
        query = query.filter(DetectiveKnowledgeEvent.order_seq <= up_to_order_seq)
    events = query.order_by(DetectiveKnowledgeEvent.order_seq).all()

    entities_introduced: dict[str, int] = {}
    claims: dict[str, dict] = {}
    questions: dict[str, dict] = {}
    relations: dict[str, dict] = {}
    by_subject_type = {"entity": entities_introduced, "claim": claims,
                        "question": questions, "relation": relations}

    for ev in events:
        state = json.loads(ev.new_state_json)
        if ev.subject_type == "entity":
            if ev.operation == "introduce":
                entities_introduced[ev.subject_key] = ev.order_seq
            continue
        bucket = by_subject_type[ev.subject_type]
        record = bucket.setdefault(ev.subject_key, {"history": []})
        record["state"] = state
        record["last_operation"] = ev.operation
        record["last_order_seq"] = ev.order_seq
        record["history"].append({"order_seq": ev.order_seq, "operation": ev.operation, "state": state})

    return {
        "as_of_order_seq": up_to_order_seq,
        "entities_introduced": entities_introduced,
        "claims": claims,
        "questions": questions,
        "relations": relations,
    }


def get_narration_span(
    db: Session, graph_id: int, beat_id: str | None = None, unit_keys: list[str] | None = None,
) -> list[dict]:
    """Return the *complete* text of the matching narration units, in story order, with no
    length truncation and no cap on how many units are returned — the defect this replaces is
    `app/documentary/mvp/engines.py:beat_view`, which hard-truncates narration to 900 characters
    and facts to ~28 entries, silently dropping a decisive clause past those limits.
    """
    if not beat_id and not unit_keys:
        raise MemoryValidationError("get_narration_span requires beat_id and/or unit_keys")
    query = db.query(NarrationUnit).filter_by(graph_id=graph_id)
    if beat_id is not None:
        query = query.filter_by(beat_id=beat_id)
    if unit_keys is not None:
        query = query.filter(NarrationUnit.unit_key.in_(unit_keys))
    units = query.order_by(NarrationUnit.story_order).all()
    return [
        {"unit_key": u.unit_key, "beat_id": u.beat_id, "story_order": u.story_order, "text": u.text}
        for u in units
    ]
