"""Cards the toolkit makes from the graph's own stored data, with no model call: a timeline from the case's dated
events, a map of unknowns from a question and what is already known about it, a conflict from a stored conflict
relation. Each card is made only where its builder accepts what is bound to it and the film's timeline has room for it,
and is shown only after the words that complete its claims. Nothing is invented: a card's text is the text of the
claims, questions and relations it binds."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import DetectiveClaim, DetectiveQuestion, DetectiveRelation, DetectiveScene, DetectiveToolAction
from app.db.models import NarrationUnit, RevealNode
from app.documentary import memory as MEM
from app.documentary.detective import buildable as BD
from app.documentary.detective import dispatch as D
from app.documentary.detective.filler import FACT_NODE_RELEASE_FRACTION, GROUNDED, place_card

TIMELINE_CLAIM = "timeline-"


class _Film:
    """The film's beats in story order and the beat each narration unit belongs to."""

    def __init__(self, units: list[NarrationUnit]) -> None:
        self.units = units
        self.beat_of = {u.unit_key: u.beat_id for u in units}
        self.order = list(dict.fromkeys(u.beat_id for u in units))

    def beats_of(self, keys: list[str]) -> list[str]:
        return [self.beat_of[k] for k in keys if k in self.beat_of]

    def last_beat(self, keys: list[str]) -> str | None:
        beats = self.beats_of(keys)
        return max(beats, key=self.order.index) if beats else None

    def units_in(self, beat: str, keys: list[str]) -> list[str]:
        return [k for k in keys if self.beat_of.get(k) == beat]


def _keys(raw: str | None) -> list[str]:
    return json.loads(raw or "[]")


def _spread(items: list, count: int) -> list:
    """`count` of the items, spread over the film."""
    if count >= len(items):
        return items
    return [items[i * len(items) // count] for i in range(count)]


def _release(claims: list[DetectiveClaim]) -> float | None:
    """A card of claims that carry release anchors is timed from them; one of claims without any is shown in the
    second half of its beat, so that nothing is shown before the words that tell it."""
    return None if all(c.release_anchor_id for c in claims) else FACT_NODE_RELEASE_FRACTION


def import_timeline_nodes(db: Session, graph_id: int, units: list[NarrationUnit]) -> int:
    """The case's dated events (the graph's timeline nodes) as claims, for the events no claim carries yet."""
    unit_keys: dict[str, list[str]] = {}
    for unit in units:
        unit_keys.setdefault(unit.beat_id, []).append(unit.unit_key)
    made = 0
    for node in db.query(RevealNode).filter_by(graph_id=graph_id, category="timeline").order_by(RevealNode.id):
        key = f"{TIMELINE_CLAIM}{node.node_key}"
        if not node.label or node.first_revealed_beat_id not in unit_keys \
                or db.query(DetectiveClaim.id).filter_by(graph_id=graph_id, claim_key=key).first():
            continue
        MEM.add_claim(db, graph_id, claim_key=key, entity_node_key=node.node_key, attribute="event", text=node.label,
                      basis="established_in_text", supporting_unit_keys=unit_keys[node.first_revealed_beat_id],
                      evidence_fact_ids=[node.node_key])
        made += 1
    db.flush()
    return made


class GraphCards:
    """Adds the cards of one graph; `made` says how many of each kind were kept."""

    def __init__(self, db: Session, graph_id: int, language: str, units: list[NarrationUnit],
                 windows: list[dict] | None = None) -> None:
        self.db, self.graph_id, self.language, self.units, self.windows = db, graph_id, language, units, windows
        self.film = _Film(units)
        self.cfg = ai_config.detective_toolkit.graph_cards
        self.order = db.query(DetectiveScene).filter_by(graph_id=graph_id).count()
        self.serial = 0
        self.taken = {k for (k,) in db.query(DetectiveScene.scene_key).filter_by(graph_id=graph_id)}
        self.added: list[DetectiveToolAction] = []

    def _claims(self) -> list[DetectiveClaim]:
        return self.db.query(DetectiveClaim).filter_by(graph_id=self.graph_id).order_by(DetectiveClaim.id).all()

    def _shown(self, tool_id: str) -> set[str]:
        shown: set[str] = set()
        for action in (self.db.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
                       .filter(DetectiveScene.graph_id == self.graph_id, DetectiveToolAction.tool_id == tool_id)):
            shown.update(_keys(action.content_bindings_json))
        return shown

    def _place(self, tool_id: str, beat: str, bindings: list[str], claims: list[DetectiveClaim], aim: str, purpose: str,
               release: float | None) -> bool:
        """The card, kept in the first beat from `beat` on where its builder accepts what it binds and the film's
        timeline has room for it."""
        start = self.film.order.index(beat)
        for later in self.film.order[start:start + 1 + self.cfg.later_beats]:
            covered = sorted({k for c in claims for k in self.film.units_in(later, _keys(c.supporting_unit_keys_json))})
            covered = covered or [u.unit_key for u in self.units if u.beat_id == later]
            self.serial += 1
            while f"graph-{tool_id}-{self.serial}" in self.taken:
                self.serial += 1
            self.order += 1
            action = place_card(self.db, self.graph_id, f"graph-{tool_id}-{self.serial}", self.order, tool_id, bindings,
                                covered, list({c.entity_node_key for c in claims}),
                                release if later == beat else 0.0, aim,
                                "The viewer sees the stored facts set side by side",
                                "Built from what the case's record holds", "The facts are easier to follow", purpose)
            if BD.issue_of(self.db, action) is None and not self._skipped(action):
                self.added.append(action)
                return True
            self.db.delete(action.scene)
            self.db.delete(action)
            self.db.flush()
            self.order -= 1
        return False

    def _skipped(self, action: DetectiveToolAction) -> bool:
        timeline = BD.planned_timeline(self.db, self.graph_id, self.language, self.units, self.windows)
        return action.action_key in {s["action_key"] for s in timeline.skipped}

    def timelines(self) -> None:
        shown = self._shown("timeline")
        dated = [(c, self.film.last_beat(_keys(c.supporting_unit_keys_json))) for c in self._claims()
                 if c.claim_key not in shown and D.claim_event_date(self.db, c)]
        dated = sorted((x for x in dated if x[1]), key=lambda x: self.film.order.index(x[1]))
        size = self.cfg.timeline_events
        groups = [dated[i:i + size] for i in range(0, len(dated), size)]
        for group in _spread([g for g in groups if len(g) >= 2], self.cfg.timeline_cards):
            claims = [c for c, _ in group]
            self._place("timeline", group[-1][1], [c.claim_key for c in claims], claims,
                        "Put the events in the order they happened", "Set the dated events on one line",
                        _release(claims))

    def unknowns(self) -> None:
        from app.documentary.graphics.engines import kit

        fits = kit.settings()["text_max_chars"]
        known = [c for c in self._claims() if c.basis in GROUNDED and len(" ".join(c.text.split())) <= fits]
        shown = self._shown("unknowns")
        candidates = []
        for q in self.db.query(DetectiveQuestion).filter_by(graph_id=self.graph_id).order_by(DetectiveQuestion.id):
            beat = self.film.last_beat(_keys(q.supporting_claim_keys_json))
            if q.question_key in shown or beat is None or len(" ".join(q.text.split())) > fits:
                continue
            about = set(_keys(q.entity_node_keys_json))
            told = [c for c in known if c.entity_node_key in about and self.film.last_beat(
                _keys(c.supporting_unit_keys_json)) in self.film.order[:self.film.order.index(beat) + 1]]
            if told:
                candidates.append((q, beat, told[-2:]))
        for q, beat, told in _spread(candidates, self.cfg.unknowns_cards):
            self._place("unknowns", beat, [c.claim_key for c in told] + [q.question_key], told,
                        "Show what is known and the question that remains", "Set what is known beside what is not",
                        _release(told))

    def conflicts(self) -> None:
        claims = self._claims()
        shown = self._shown("conflict")
        candidates = []
        for rel in self.db.query(DetectiveRelation).filter_by(graph_id=self.graph_id, relation_type="conflict") \
                .order_by(DetectiveRelation.id):
            beat = self.film.last_beat(_keys(rel.supporting_unit_keys_json))
            if rel.relation_key in shown or beat is None:
                continue
            reach = self.film.order[:self.film.order.index(beat) + 1]
            sides = []
            for node in (rel.from_node_key, rel.to_node_key):
                told = [c for c in claims if c.entity_node_key == node
                        and self.film.last_beat(_keys(c.supporting_unit_keys_json)) in reach]
                told.sort(key=lambda c: len(set(_keys(c.supporting_unit_keys_json)) & set(_keys(rel.supporting_unit_keys_json))))
                if told:
                    sides.append(told[-1])
            if len(sides) == 2:
                candidates.append((rel, beat, sides))
        for rel, beat, sides in _spread(candidates, self.cfg.conflict_cards):
            self._place("conflict", beat, [rel.relation_key] + [c.claim_key for c in sides], sides,
                        "Isolate the disagreement between two accounts", "Set the two accounts against each other",
                        _release(sides))


def add_graph_cards(db: Session, graph_id: int, language: str, units: list[NarrationUnit],
                    windows: list[dict] | None = None) -> dict[str, int]:
    """Make the cards the graph's stored data can fill, once. Returns how many of each kind the film kept."""
    import_timeline_nodes(db, graph_id, units)
    cards = GraphCards(db, graph_id, language, units, windows)
    cards.timelines()
    cards.unknowns()
    cards.conflicts()
    db.commit()
    kept: dict[str, int] = {}
    for action in cards.added:
        kept[action.tool_id] = kept.get(action.tool_id, 0) + 1
    return kept
