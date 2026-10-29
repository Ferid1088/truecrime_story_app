"""The toolkit's own last step towards the share of the film its alternatives must fill: fact cards made straight from
the claims the Director stored, with no model call.

A claim the narration established, and that no card shows yet, becomes (with up to two more claims of the same beat) a
fact card placed after the words that complete it. Nothing is invented: the text on the card is the claim's text, and a
group the card's renderer refuses is never made. A beat takes cards only up to a share of its own narration, so the
film keeps room for pictures."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import DetectiveClaim, DetectiveScene, DetectiveToolAction, NarrationUnit, RevealNode
from app.documentary import memory as MEM
from app.documentary.detective import buildable as BD
from app.documentary.detective.reading import reading_need
from app.documentary.graphics.base import GraphicUnavailable

GROUNDED = ("established_in_text", "attributed")
MAX_FACTS_PER_CARD = 3
FACT_NODE_RELEASE_FRACTION = 0.5     # a fact with no anchor in the narration is shown in the second half of its beat


def _shown_claims(db: Session, graph_id: int) -> set[str]:
    shown: set[str] = set()
    for (keys,) in (db.query(DetectiveToolAction.content_bindings_json)
                    .join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
                    .filter(DetectiveScene.graph_id == graph_id)):
        shown.update(json.loads(keys or "[]"))
    return shown


def _claims_by_beat(db: Session, graph_id: int, units: list[NarrationUnit]) -> dict[str, list[DetectiveClaim]]:
    beat_of = {u.unit_key: u.beat_id for u in units}
    by_beat: dict[str, list[DetectiveClaim]] = {}
    for claim in db.query(DetectiveClaim).filter_by(graph_id=graph_id).order_by(DetectiveClaim.id):
        keys = [k for k in json.loads(claim.supporting_unit_keys_json or "[]") if k in beat_of]
        if claim.basis in GROUNDED and keys:
            by_beat.setdefault(beat_of[keys[0]], []).append(claim)
    return by_beat


def _seconds(claims: list[DetectiveClaim]) -> float:
    """How long a fact card of these claims is held; infinity when its layout cannot be read."""
    data = {"facts": [{"text": c.text, "fact_id": c.claim_key} for c in claims]}
    try:
        return reading_need("fact_card", data, "en").planned
    except GraphicUnavailable:
        return float("inf")


def _card_for(claims: list[DetectiveClaim], room: float) -> list[DetectiveClaim]:
    """The largest group of up to three of these claims that the fact card's renderer accepts and that fits `room`
    seconds."""
    group: list[DetectiveClaim] = []
    for claim in claims:
        candidate = group + [claim]
        data = {"facts": [{"text": c.text, "fact_id": c.claim_key} for c in candidate]}
        if BD.render_problem("fact_card", data) is None and _seconds(candidate) <= room:
            group = candidate
        if len(group) == MAX_FACTS_PER_CARD:
            break
    return group


def import_fact_nodes(db: Session, graph_id: int, units: list[NarrationUnit]) -> int:
    """The case's own verified facts the blueprint assigned to a beat (the graph's fact nodes) as claims, for the facts
    no claim of the Director already carries. The text is the fact's text."""
    known = {k for (keys,) in db.query(DetectiveClaim.evidence_fact_ids_json).filter_by(graph_id=graph_id)
             for k in json.loads(keys or "[]")}
    unit_keys: dict[str, list[str]] = {}
    for unit in units:
        unit_keys.setdefault(unit.beat_id, []).append(unit.unit_key)
    made = 0
    for node in db.query(RevealNode).filter_by(graph_id=graph_id, category="fact").order_by(RevealNode.id):
        if node.node_key in known or not node.label or node.first_revealed_beat_id not in unit_keys:
            continue
        if db.query(DetectiveClaim.id).filter_by(graph_id=graph_id, claim_key=f"fact-{node.node_key}").first():
            continue
        MEM.add_claim(db, graph_id, claim_key=f"fact-{node.node_key}", entity_node_key=node.node_key, attribute="fact",
                      text=node.label, basis="established_in_text",
                      supporting_unit_keys=unit_keys[node.first_revealed_beat_id], evidence_fact_ids=[node.node_key])
        made += 1
    db.flush()
    return made


def _add_card(db: Session, graph_id: int, beat: str, group: list[DetectiveClaim], order: int, units: list[NarrationUnit],
              serial: int, release: float | None, covered: list[str]) -> DetectiveToolAction:
    scene = DetectiveScene(
        graph_id=graph_id, scene_key=f"fill-{beat}-{serial}", scene_order=order, mode="direct_scenes",
        covered_unit_keys_json=json.dumps(covered), aim="Show the facts the narration has just established",
        active_question_keys_json="[]", active_entity_keys_json=json.dumps(sorted({c.entity_node_key for c in group})),
        change_in_understanding="The viewer sees the established facts written down",
        alternatives_considered_json="[]", why_selected="Sourced claims no card has shown yet",
        expected_audience_gain="The facts stay in mind while the narration moves on", audio_clarity_findings_json="[]")
    db.add(scene)
    db.flush()
    action = DetectiveToolAction(
        scene_id=scene.id, action_key=f"fill-{beat}-{serial}-a1", order_index=0, tool_id="fact_card",
        purpose="State the facts the narration has just established",
        payload_json=json.dumps({"release_fraction": release}) if release is not None else "{}",
        content_bindings_json=json.dumps([c.claim_key for c in group]), continuity_refs_json="[]")
    db.add(action)
    db.flush()
    return action


def fill_with_fact_cards(db: Session, graph_id: int, language: str, units: list[NarrationUnit],
                         windows: list[dict] | None = None) -> dict:
    """Add fact cards, weakest beat first, until the cards fill the configured share of the film once placed, or no
    sourced claim is left that fits. A card the timeline then cannot place is taken out again. Returns what was made."""
    cfg = ai_config.detective_toolkit
    narration = ({w["beat_id"]: w["end"] - w["start"] for w in windows} if windows
                 else BD.narration_seconds(units))
    shown = _shown_claims(db, graph_id)
    pending = _claims_by_beat(db, graph_id, units)
    order = db.query(DetectiveScene).filter_by(graph_id=graph_id).count()
    made: list[str] = []
    serial, last, imported = 0, None, False
    order_of_beats = list(narration)
    previous = {b: order_of_beats[i - 1] for i, b in enumerate(order_of_beats) if i}
    while True:
        timeline = BD.planned_timeline(db, graph_id, language, units, windows)
        if last is not None and last.action_key in {s["action_key"] for s in timeline.skipped}:
            made.remove(last.action_key)               # the timeline has no room for it: not a card of the film
            db.delete(last.scene)
            db.delete(last)
            db.flush()
            timeline = BD.planned_timeline(db, graph_id, language, units, windows)
        last = None
        if timeline.alternative_share >= cfg.min_alternative_share:
            break
        placed = BD.placed_card_seconds_by_beat(timeline)
        for beat in sorted(narration, key=lambda b: (placed.get(b, 0.0) / narration[b], b)):
            room = cfg.max_beat_card_share * narration[beat] - placed.get(beat, 0.0)
            own = [c for c in pending.get(beat, []) if c.claim_key not in shown]
            # a fact the previous beat has told may be shown at the start of this one: nothing is revealed early
            after = [c for c in pending.get(previous.get(beat), []) if c.claim_key not in shown
                     and c.claim_key.startswith("fact-")]
            for claims, release, covered in (
                    (own, FACT_NODE_RELEASE_FRACTION if any(c.claim_key.startswith("fact-") for c in own) else None,
                     None),
                    (after, 0.0, [u.unit_key for u in units if u.beat_id == beat])):
                group = _card_for(claims, room)
                if group:
                    covered = covered or sorted({k for c in group for k in json.loads(c.supporting_unit_keys_json or "[]")
                                                 if any(u.unit_key == k and u.beat_id == beat for u in units)})
                    serial += 1
                    order += 1
                    last = _add_card(db, graph_id, beat, group, order, units, serial, release, covered)
                    shown.update(c.claim_key for c in group)
                    made.append(last.action_key)
                    break
            if last is not None:
                break
        if last is None:
            if imported or not import_fact_nodes(db, graph_id, units):
                break
            imported, pending = True, _claims_by_beat(db, graph_id, units)       # the facts of the case, once
    db.commit()
    return {"cards": len(made), "share": BD.alternative_share(db, graph_id, language, units, windows)}
