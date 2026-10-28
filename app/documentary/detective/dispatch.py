"""Section D: dispatch a persisted `DetectiveToolAction` to its real renderer.

Only active when a blueprint's reveal graph actually has `DetectiveScene` rows (Section C). A
blueprint with no detective plan (every real case today) is untouched by this module - callers
must check `has_detective_plan` first and fall back to the pre-existing pipeline otherwise.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import DetectiveScene, DetectiveToolAction

# Tool kinds this pass can turn into a real rendered shot. The rest keep whatever reachability
# they already had through the pre-existing pipeline (Section A); wiring them through direct
# dispatch is explicitly left for a later pass (see docs/detective_toolkit_section_d_design.md).
DISPATCHABLE_KINDS = {"fact_card", "floor_plan"}


def has_detective_plan(db: Session, graph_id: int | None) -> bool:
    """True when this reveal graph has at least one Detective Director scene (Section C ran)."""
    if graph_id is None:
        return False
    return db.query(DetectiveScene.id).filter(DetectiveScene.graph_id == graph_id).first() is not None


def actions_for_graph(db: Session, graph_id: int) -> list[DetectiveToolAction]:
    """Every tool action of a graph's scenes, in scene then action order."""
    scenes = (db.query(DetectiveScene).filter(DetectiveScene.graph_id == graph_id)
              .order_by(DetectiveScene.scene_order).all())
    out: list[DetectiveToolAction] = []
    for scene in scenes:
        out.extend(scene.actions)
    return out


def record_planner_decisions(db: Session, graph_id: int, covered_beats: set[str]) -> int:
    """The Main Visual Planner's recorded decision on every tool action of this graph
    (architecture rule, 2026-10-28; checklist row D5): 'deferred' when the action has no
    resolvable narration anchor yet, 'accepted' when its beat made it into the final merged
    requirements (`covered_beats`, computed by `app.documentary.mvp.needs` after validation),
    'rejected' when its beat resolved but validation dropped it. Returns the number of actions
    updated. Idempotent: safe to call again after a later pass changes `covered_beats`."""
    updated = 0
    for action in actions_for_graph(db, graph_id):
        beat_id = _beat_id_of(db, action)
        if beat_id is None:
            decision, reason = "deferred", "no resolvable narration anchor for this action yet"
        elif beat_id in covered_beats:
            decision, reason = "accepted", f"beat {beat_id}'s requirement came from this action"
        else:
            decision, reason = "rejected", f"beat {beat_id} resolved but was dropped by requirement validation"
        if action.planner_decision != decision or action.planner_decision_reason != reason:
            action.planner_decision, action.planner_decision_reason = decision, reason
            updated += 1
    if updated:
        db.flush()
    return updated


def _covered_unit(db: Session, action: DetectiveToolAction):
    """The narration unit a tool action targets, from its scene's covered narration-unit keys.

    A scene may cover several narration units; Section F (narration/visual/attention sync) is
    where per-unit timing precision belongs. For this section, a tool action is placed at the
    first covered unit - good enough to prove real dispatch, not a timing claim."""
    from app.db.models import NarrationUnit

    unit_keys = json.loads(action.scene.covered_unit_keys_json or "[]")
    if not unit_keys:
        return None
    return (db.query(NarrationUnit)
            .filter(NarrationUnit.graph_id == action.scene.graph_id, NarrationUnit.unit_key == unit_keys[0])
            .first())


def _beat_id_of(db: Session, action: DetectiveToolAction) -> str | None:
    unit = _covered_unit(db, action)
    return unit.beat_id if unit else None


def _fact_card_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """`{"facts": [{"text", "fact_id"}, ...]}` from the claims this action is bound to."""
    from app.db.models import DetectiveClaim

    bindings = json.loads(action.content_bindings_json or "[]")
    claim_keys = [b.get("claim_key") for b in bindings if isinstance(b, dict) and b.get("claim_key")]
    claim_keys = claim_keys or (json.loads(action.payload_json or "{}").get("claim_keys") or [])
    if not claim_keys:
        return None
    rows = (db.query(DetectiveClaim)
            .filter(DetectiveClaim.graph_id == action.scene.graph_id, DetectiveClaim.claim_key.in_(claim_keys))
            .all())
    by_key = {r.claim_key: r for r in rows}
    facts = [{"text": by_key[k].text, "fact_id": by_key[k].claim_key} for k in claim_keys if k in by_key]
    return {"facts": facts} if facts else None


def _floor_plan_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """Pass the action's own payload through: a floor plan is never invented from narration, so
    it must already carry real room/marker/source geometry (from stored plan-document data)."""
    data = json.loads(action.payload_json or "{}")
    return data if data.get("rooms") else None


BUILDERS = {"fact_card": _fact_card_data, "floor_plan": _floor_plan_data}


def _min_release_fraction(db: Session, action: DetectiveToolAction) -> float | None:
    """How far into its beat (0..1) this action's content may earliest appear, from the latest
    `char_end` of any release anchor its bound claims carry - Section F, obstacle 5/7: a shot
    must not reveal a claim before the words that complete its meaning are spoken, not merely be
    somewhere in the right beat. `None` when no bound claim has a resolvable anchor (today's
    only honest answer for an action with no content bindings, or claims made before Section B's
    anchors existed) - callers fall back to the beat's own start, unchanged from before this
    section."""
    from app.documentary.detective.timing import anchor_fraction_within_span
    from app.db.models import DetectiveClaim, NarrationUnit, ReleaseAnchor

    bindings = json.loads(action.content_bindings_json or "[]")
    claim_keys = [b.get("claim_key") for b in bindings if isinstance(b, dict) and b.get("claim_key")]
    if not claim_keys:
        return None
    rows = (db.query(DetectiveClaim, ReleaseAnchor, NarrationUnit)
            .join(ReleaseAnchor, DetectiveClaim.release_anchor_id == ReleaseAnchor.id)
            .join(NarrationUnit, ReleaseAnchor.narration_unit_id == NarrationUnit.id)
            .filter(DetectiveClaim.graph_id == action.scene.graph_id,
                    DetectiveClaim.claim_key.in_(claim_keys)).all())
    fractions = []
    for _claim, anchor, unit in rows:
        _start, end = anchor_fraction_within_span(anchor.char_start, anchor.char_end, len(unit.text), 0.0, 1.0)
        fractions.append(end)
    return max(fractions) if fractions else None


def legacy_evidence_additions(db: Session, graph_id: int) -> tuple[list[dict], list[dict]]:
    """Detective claims/questions re-shaped as the legacy `Fact`-dict / blueprint-question
    contract `engines.py:reference_texts` and the offer builders already read - obstacle 3
    (`check_item` rejects basis="unknown" with no Fact/Q row backing it): a `DetectiveQuestion`
    becomes a `category="question"` ref by construction, so the existing check passes with no
    change to `check_item` itself. Returns ([fact-shaped dicts], [question-shaped dicts])."""
    from app.db.models import DetectiveClaim, DetectiveQuestion

    facts = [{"id": c.claim_key, "claim": c.text, "category": "disputed" if c.basis == "disputed" else "general",
             "disputed": c.basis == "disputed"}
            for c in db.query(DetectiveClaim).filter(DetectiveClaim.graph_id == graph_id).all()]
    questions = [{"id": q.question_key, "question": q.text}
                for q in db.query(DetectiveQuestion).filter(DetectiveQuestion.graph_id == graph_id).all()]
    return facts, questions


BOARD_CARD_KINDS = {"time", "open_question", "person", "place", "object", "contradiction", "event"}


def board_card_overrides(db: Session, graph_id: int) -> dict[str, dict]:
    """beat_id -> {"kind", "source_fact", ["status"], ["reuse_reason"]} from `detective_board`
    tool actions.

    Section D, obstacle 6: when the Detective Director chose this beat's card kind, honor it
    instead of `select_cards`'s hardcoded dated-event-always-wins rule.

    Section E: an `open_question` card's `status` is the question's actual current state as of
    this beat (Section B's knowledge-event log, replayed up to here - never a later answer), not
    a permanent "new"/"open" label. A `reuse_reason` carried on the action (the Detective
    Director's own justification for returning to an already-used fact) is passed through so the
    board can mark this card as a deliberate return rather than a fresh discovery."""
    from app.documentary.detective.board_state import question_status_as_of

    overrides: dict[str, dict] = {}
    for action in actions_for_graph(db, graph_id):
        if action.tool_id != "detective_board":
            continue
        payload = json.loads(action.payload_json or "{}")
        kind, source_fact = payload.get("kind"), payload.get("source_fact")
        unit = _covered_unit(db, action)
        if unit is None or kind not in BOARD_CARD_KINDS or not source_fact:
            continue
        override = {"kind": kind, "source_fact": source_fact}
        if kind == "open_question":
            override["status"] = question_status_as_of(db, graph_id, source_fact, unit.story_order)
        if action.reuse_reason:
            override["reuse_reason"] = action.reuse_reason
        overrides[unit.beat_id] = override
    return overrides


# tool_id -> (needs.py FUNCTIONS value, needs.py ALTERNATIVES values) - the closest legacy
# "what does the viewer need" vocabulary for each detective tool, used only to translate an
# already-made detective choice into the existing MvpRequirement contract (Section D hand-off
# seam). See docs/detective_toolkit_capability_matrix.md for the seam this replaces.
_FUNCTION_BY_TOOL = {
    "map": ("orientation", ["map"]), "detective_board": ("investigation", ["board"]),
    "recap": ("orientation", ["board"]), "unknowns": ("investigation", []),
    "conflict": ("investigation", []), "conflict_board": ("investigation", ["board"]),
    "timeline": ("time", ["timeline"]), "investigation_clock": ("time", ["timeline"]),
    "fact_card": ("evidence", []), "key_facts": ("evidence", []),
    "document": ("evidence", ["document"]), "quote": ("evidence", ["quote_card"]),
    "headline": ("orientation", []), "floor_plan": ("orientation", []),
    "evidence_check": ("evidence", []), "custody": ("evidence", []),
    "forensic": ("evidence", []), "interrogation": ("investigation", []),
    "courtroom": ("investigation", []), "sentencing": ("investigation", []),
    "photo": ("human_connection", ["photo"]),
    # No "host" entry: Host is owned by the Main Visual Planner's own Host system, never a
    # detective-dispatchable tool (see the comment in tool_catalog.py). A detective action can no
    # longer carry tool_id="host" - it is rejected upstream, in schema.py, as an unknown tool_id.
}
_ENTITY_TYPE_BY_CATEGORY = {"person": "person", "place": "place", "building": "building",
                           "object": "object", "vehicle": "vehicle", "event": "event",
                           "document": "document", "organization": "organization"}


def detective_raw_needs(db: Session, graph) -> dict:
    """The Detective Director's scenes/actions re-shaped as the SAME raw payload
    `app.documentary.mvp.needs.validate_needs` already validates agent output against - no new
    validation path, no new trust boundary. `graph` is the loaded `RevealGraph` (its `.nodes`
    give every entity; Section B's `RevealNode` already IS the Entity contract)."""
    entities_raw = [{"key": n.node_key, "name": n.label,
                     "type": _ENTITY_TYPE_BY_CATEGORY.get(n.category, "object")}
                    for n in graph.nodes]
    fallback_entity = entities_raw[0]["key"] if entities_raw else None
    beats: dict[str, list[dict]] = {}
    for scene in (db.query(DetectiveScene).filter(DetectiveScene.graph_id == graph.id)
                  .order_by(DetectiveScene.scene_order).all()):
        active = json.loads(scene.active_entity_keys_json or "[]")
        entity = active[0] if active else fallback_entity
        if entity is None:
            continue
        for action in scene.actions:
            beat_id = _beat_id_of(db, action)
            if beat_id is None:
                continue
            function, alternatives = _FUNCTION_BY_TOOL.get(action.tool_id, ("investigation", []))
            beats.setdefault(beat_id, []).append({
                "entity": entity, "function": function, "role_needed": "context",
                "priority": "high", "alternatives": alternatives, "need": action.purpose[:200]})
    return {"entities": entities_raw,
           "beats": [{"beat_id": bid, "requirements": reqs[:4]} for bid, reqs in beats.items()]}


def resolve_dispatchable_actions(db: Session, graph_id: int) -> list[dict]:
    """DB-dependent half: for each tool action this pass can dispatch, resolve its beat and real
    renderer data. Plain-dict output (no DB handle) so the pure `compose()` can place it without
    breaking its no-DB contract - the same split `board_cards_for`/`compose` already use."""
    resolved: list[dict] = []
    for action in actions_for_graph(db, graph_id):
        build = BUILDERS.get(action.tool_id)
        record = {"action_key": action.action_key, "tool_id": action.tool_id, "purpose": action.purpose,
                  "reuse_reason": action.reuse_reason}
        if build is None:
            record["skip"] = "not_dispatched_in_this_pass"
        else:
            beat_id = _beat_id_of(db, action)
            data = build(db, action) if beat_id else None
            if beat_id is None:
                record["skip"] = "no_beat_span"
            elif data is None:
                record["skip"] = "insufficient_data"
            else:
                record["beat_id"], record["data"] = beat_id, data
                record["min_release_fraction"] = _min_release_fraction(db, action)
        resolved.append(record)
    return resolved


def place_detective_graphics(shots: list[dict], spans: list[dict], language: str,
                             resolved: list[dict]) -> list[dict]:
    """Pure: carve each resolved action's graphic into its beat's span. Fixes Section A's
    obstacle 1 (a graphic otherwise only ever gets photo/map leftover time) for the kinds this
    pass wires. Returns a decision record per action, placed or skipped, for live evidence."""
    from app.documentary.graphics import shots as GS
    from app.documentary.graphics.registry import graphic
    from app.documentary.production.script import _carve

    by_beat = {sp["beat_id"]: sp for sp in spans}
    records: list[dict] = []
    for item in resolved:
        record = dict(item)
        if "skip" in record:
            records.append(record)
            continue
        span = by_beat.get(record["beat_id"])
        if span is None:
            record["skip"] = "beat_not_in_compiled_timeline"
            records.append(record)
            continue
        problem = graphic(record["tool_id"]).can_render(record["data"])
        if problem:
            record["skip"] = f"unrenderable: {problem}"
            records.append(record)
            continue
        fraction = record.get("min_release_fraction") or 0.0
        start = span["start"] + (span["end"] - span["start"]) * fraction
        remaining = span["end"] - start
        if remaining < 1.0:
            record["skip"] = "insufficient_room_after_release_point"
            records.append(record)
            continue
        hold = min(GS.hold_seconds(record["tool_id"]), remaining)
        shot = {"kind": "graphic", **GS.plan_fields(record["tool_id"], record["data"]),
               "beat_id": record["beat_id"], "start": round(start, 3),
               "end": round(start + max(hold, 1.0), 3), "motion": "NONE", "speed": 1.0,
               "transition_in": "FADE_BLACK", "why": record["purpose"],
               "detective_action_key": record["action_key"],
               "detective_reuse_reason": record["reuse_reason"]}
        if fraction:
            record["release_fraction_applied"] = round(fraction, 4)
        shot["graphic"]["language"] = language
        _carve(shots, shot)
        record["result"] = "placed"
        records.append(record)
    return records
