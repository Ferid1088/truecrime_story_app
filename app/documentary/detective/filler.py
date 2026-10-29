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
from app.db.models import DetectiveClaim, DetectiveScene, DetectiveToolAction, NarrationUnit
from app.documentary.detective import buildable as BD

GROUNDED = ("established_in_text", "attributed")
MAX_FACTS_PER_CARD = 3


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


def _card_for(claims: list[DetectiveClaim]) -> list[DetectiveClaim]:
    """The largest group of up to three of these claims the fact card's renderer accepts."""
    group: list[DetectiveClaim] = []
    for claim in claims:
        candidate = group + [claim]
        data = {"facts": [{"text": c.text, "fact_id": c.claim_key} for c in candidate]}
        if BD.render_problem("fact_card", data) is None:
            group = candidate
        if len(group) == MAX_FACTS_PER_CARD:
            break
    return group


def _add_card(db: Session, graph_id: int, beat: str, group: list[DetectiveClaim], order: int, units: list[NarrationUnit],
              serial: int) -> DetectiveToolAction:
    covered = sorted({k for c in group for k in json.loads(c.supporting_unit_keys_json or "[]")
                      if any(u.unit_key == k and u.beat_id == beat for u in units)})
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
        purpose="State the facts the narration has just established", payload_json="{}",
        content_bindings_json=json.dumps([c.claim_key for c in group]), continuity_refs_json="[]")
    db.add(action)
    db.flush()
    return action


def fill_with_fact_cards(db: Session, graph_id: int, language: str, units: list[NarrationUnit]) -> dict:
    """Add fact cards, weakest beat first, until the cards fill the configured share of the film or no sourced claim
    is left. Returns what was made."""
    cfg = ai_config.detective_toolkit
    narration = BD.narration_seconds(units)
    shown = _shown_claims(db, graph_id)
    pending = _claims_by_beat(db, graph_id, units)
    order = (db.query(DetectiveScene).filter_by(graph_id=graph_id).count())
    made, serial = [], 0
    while BD.alternative_share(db, graph_id, language, units) < cfg.min_alternative_share:
        cards = BD.card_seconds_by_beat(db, graph_id, language)    # raw: the cap is checked below
        beats = [b for b in sorted(narration, key=lambda b: (cards.get(b, 0.0) / narration[b], b))
                 if cards.get(b, 0.0) < cfg.max_beat_card_share * narration[b]
                 and any(c.claim_key not in shown for c in pending.get(b, []))]
        if not beats:
            break
        beat = beats[0]
        group = _card_for([c for c in pending[beat] if c.claim_key not in shown])
        if not group:
            pending[beat] = [c for c in pending[beat] if c.claim_key in shown]
            continue
        serial += 1
        order += 1
        action = _add_card(db, graph_id, beat, group, order, units, serial)
        shown.update(c.claim_key for c in group)
        if BD.issue_of(db, action) is not None:        # never keep a card that cannot be built
            db.delete(action.scene)
            db.delete(action)
            db.flush()
            continue
        made.append(action.action_key)
    db.commit()
    return {"cards": len(made), "share": BD.alternative_share(db, graph_id, language, units)}
