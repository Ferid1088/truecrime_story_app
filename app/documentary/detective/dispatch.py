"""Section D: dispatch a persisted `DetectiveToolAction` to its real renderer.

Only active when a blueprint's reveal graph actually has `DetectiveScene` rows (Section C). A
blueprint with no detective plan (every real case today) is untouched by this module - callers
must check `has_detective_plan` first and fall back to the pre-existing pipeline otherwise.
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.db.models import DetectiveScene, DetectiveToolAction

# Tool kinds this pass can turn into a real rendered shot. The rest keep whatever reachability
# they already had through the pre-existing pipeline (Section A); wiring them through direct
# dispatch is explicitly left for a later pass (see docs/detective_toolkit_section_d_design.md and
# docs/detective_toolkit_section_d2_builders_design.md for exactly which and why).
DISPATCHABLE_KINDS = {
    "fact_card", "detective_board", "recap", "conflict", "timeline", "quote", "evidence_check", "forensic",
    "document", "headline",
}

# Section B's claim/question basis vocabulary has no direct equivalent in the cinematic engines'
# older, narrower vocabulary (fact/finding/allegation/witness/expert/unknown/disputed) - this is
# the one honest mapping used by every engine builder below, not a per-tool guess.
_ENGINE_BASIS = {
    "established_in_text": "fact", "attributed": "witness", "disputed": "disputed",
    "inference": "finding", "explicit_unknown": "unknown",
}


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


def payload_of(action: DetectiveToolAction) -> dict:
    """The action's payload as a dict: a payload that is not an object (a damaged output) is no payload."""
    try:
        payload = json.loads(action.payload_json or "{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


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
    """The beat a card is shown in: the one the toolkit named (a card about the end of a beat is shown at the start of the
    next), else the beat of the first sentence it covers."""
    named = payload_of(action).get("beat")
    if named:
        return named
    unit = _covered_unit(db, action)
    return unit.beat_id if unit else None


def binding_keys(action: DetectiveToolAction, field: str) -> list[str]:
    """The stable IDs an action's `content_bindings` hold, in binding order. The Detective Director
    returns a flat JSON list of ID strings of every kind (claims, questions, relations - see
    director.py's `content_bindings_format`); a string that is not a key of the kind being looked up
    simply matches no row there. The `{"claim_key": ...}` object form is accepted as well. Every
    reader of a binding goes through here: the object-only readers this replaces never matched the
    Director's real output, so no real action resolved its claims."""
    keys: list[str] = []
    for b in json.loads(action.content_bindings_json or "[]"):
        if isinstance(b, dict):
            # the wrappers schema.py's `_binding_id` tolerates: {"claim_key": ..}, {"claim_id": ..}, {"id": ..}
            key = b.get(field) or b.get(field.replace("_key", "_id")) or b.get("id")
        else:
            key = b
        if isinstance(key, str) and key:
            keys.append(key)
    return keys


def _fact_card_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """`{"facts": [{"text", "fact_id"}, ...]}` from the claims this action is bound to."""
    from app.db.models import DetectiveClaim

    claim_keys = binding_keys(action, "claim_key")
    claim_keys = claim_keys or (payload_of(action).get("claim_keys") or [])
    if not claim_keys:
        return None
    rows = (db.query(DetectiveClaim)
            .filter(DetectiveClaim.graph_id == action.scene.graph_id, DetectiveClaim.claim_key.in_(claim_keys))
            .all())
    by_key = {r.claim_key: r for r in rows}
    facts = [{"text": by_key[k].text, "fact_id": by_key[k].claim_key} for k in claim_keys if k in by_key]
    return {"facts": facts} if facts else None


def _bound_claims(db: Session, action: DetectiveToolAction) -> list:
    """The `DetectiveClaim` rows an action's content_bindings/payload point to, in binding order.
    Shared by every builder below instead of repeating the lookup."""
    from app.db.models import DetectiveClaim

    claim_keys = binding_keys(action, "claim_key")
    claim_keys = claim_keys or (payload_of(action).get("claim_keys") or [])
    if not claim_keys:
        return []
    rows = (db.query(DetectiveClaim)
            .filter(DetectiveClaim.graph_id == action.scene.graph_id, DetectiveClaim.claim_key.in_(claim_keys))
            .all())
    by_key = {r.claim_key: r for r in rows}
    return [by_key[k] for k in claim_keys if k in by_key]


def _bound_relations(db: Session, action: DetectiveToolAction) -> list:
    """The `DetectiveRelation` rows an action's content_bindings point to, in binding order."""
    from app.db.models import DetectiveRelation

    r_keys = binding_keys(action, "relation_key")
    if not r_keys:
        return []
    rows = (db.query(DetectiveRelation)
            .filter(DetectiveRelation.graph_id == action.scene.graph_id, DetectiveRelation.relation_key.in_(r_keys))
            .all())
    by_key = {r.relation_key: r for r in rows}
    return [by_key[k] for k in r_keys if k in by_key]


def _detective_board_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """`{"cards": [{"id", "title", "fact_id"}, ...]}` - one card per bound claim/entity, the same
    shape `board_card_overrides` already feeds into the existing board renderer."""
    claims = _bound_claims(db, action)
    if not claims:
        return None
    cards = [{"id": c.claim_key, "title": c.text[:120], "fact_id": c.claim_key} for c in claims]
    return {"cards": cards}


def _recap_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """RecapBoard's three sections, split by the claim's own `basis` - never invented grouping.
    Only a claim whose text already fits the card's real 38-char budget is used; a longer one is
    skipped rather than truncated into something it didn't say."""
    claims = _bound_claims(db, action)
    if not claims:
        return None
    caps = {"confirmed": 3, "disputed": 2, "unknown": 2}
    sections: dict[str, list[dict]] = {"confirmed": [], "disputed": [], "unknown": []}
    key_by_basis = {"disputed": "disputed", "explicit_unknown": "unknown", "established_in_text": "confirmed"}
    for c in claims:
        section = key_by_basis.get(c.basis)
        if section is None or len(c.text) > 38 or len(sections[section]) >= caps[section]:
            continue
        sections[section].append({"text": c.text, "fact_id": c.claim_key})
    return sections if any(sections.values()) else None


def _conflict_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """One ConflictPage from a bound `relation_type="conflict"` relation linking exactly two
    claims - the conflict's two sides, each with its own attribution as "who"/"source"."""
    relations = [r for r in _bound_relations(db, action) if r.relation_type == "conflict"]
    if not relations:
        return None
    rel = relations[0]
    claims = {c.entity_node_key: c for c in _bound_claims(db, action)}
    a, b = claims.get(rel.from_node_key), claims.get(rel.to_node_key)
    if not a or not b:
        return None
    sides = [{"who": c.attribution or c.entity_node_key, "claim": c.text[:160],
              "source": c.attribution or "narration", "fact_id": c.claim_key} for c in (a, b)]
    return {"conflict": {"id": rel.relation_key, "title": rel.label[:70], "kind": "evidence", "status": "open",
                         "summary": rel.label[:60]},
            "sides": sides}


def _quote_origin(db: Session, claim) -> dict | None:
    """The first stored source of the facts a claim links to that is a listed newspaper or TV broadcaster (its
    address and configured name), else None - any other source is not allowed on a quote card."""
    from app.db.models import Fact, Source
    from app.documentary.graphics.outlets import outlet_of

    fact_ids = [int(i) for i in json.loads(claim.evidence_fact_ids_json or "[]") if str(i).isdigit()]
    for fact in db.query(Fact).filter(Fact.id.in_(fact_ids)).all() if fact_ids else []:
        ids = [int(i) for i in json.loads(fact.source_ids_json or "[]") if str(i).isdigit()]
        for source in db.query(Source).filter(Source.id.in_(ids)).all() if ids else []:
            name = outlet_of(source.url or "")
            if name:
                return {"url": source.url, "source": name}
    return None


def _quote_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """A QuoteCard from one attributed claim (<=220 chars) - never a bare `established_in_text`
    assertion, since a quote card is specifically for someone's own words - whose facts come from a listed
    newspaper or TV broadcaster."""
    for c in _bound_claims(db, action):
        if c.basis == "attributed" and c.attribution and len(c.text) <= 220:
            origin = _quote_origin(db, c)
            if origin:
                return {"text": c.text, "fact_id": c.claim_key, "speaker": c.attribution, **origin}
    return None


def _evidence_check_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """EvidenceCheck: one physical/documentary item (the entity bound claims describe), what it
    establishes (established_in_text claims about it) and what it does not (explicit_unknown /
    disputed claims), with a closing assessment - never a percentage or score (qualitative only)."""
    claims = _bound_claims(db, action)
    if not claims:
        return None
    establishes = [{"text": c.text[:90], "basis": "fact", "fact_id": c.claim_key}
                   for c in claims if c.basis == "established_in_text"][:2]
    if not establishes:
        return None
    not_establishes = [{"text": c.text[:90], "basis": _ENGINE_BASIS.get(c.basis, "unknown"), "fact_id": c.claim_key}
                       for c in claims if c.basis in ("disputed", "explicit_unknown")][:2]
    assessment = next((c for c in claims if c.basis in ("inference", "established_in_text")), claims[0])
    return {"item": {"name": claims[0].entity_node_key, "fact_id": claims[0].claim_key},
            "establishes": establishes, "not_establishes": not_establishes,
            "assessment": {"text": assessment.text[:110], "basis": _ENGINE_BASIS.get(assessment.basis, "finding"),
                          "fact_id": assessment.claim_key}}


def _event_date(value) -> dict | None:
    """A stored event date (year, year-month or a full date) as the parts the clock card shows, or None."""
    from app.documentary.graphics import dates

    m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$", str(value or ""))
    if not m:
        return None
    parsed = {"year": int(m.group(1))}
    if m.group(2):
        parsed["month"] = int(m.group(2))
    if m.group(3):
        parsed["day"] = int(m.group(3))
    return parsed if dates.valid(parsed) else None


def claim_event_date(db: Session, claim) -> dict | None:
    """The real date of the event a claim stands on, as its parts: from the stored Fact it cites, or from the
    case's timeline entry (a reveal node of the graph) it cites. None when the claim cites no dated event."""
    from app.db.models import Fact, RevealNode

    cited = json.loads(claim.evidence_fact_ids_json or "[]")
    fact_ids = [i for i in cited if isinstance(i, int)]
    fact = db.query(Fact).filter(Fact.id.in_(fact_ids)).first() if fact_ids else None
    if fact is not None and _event_date(fact.event_date):
        return _event_date(fact.event_date)
    keys = [i for i in cited if isinstance(i, str)]
    node = (db.query(RevealNode).filter(RevealNode.graph_id == claim.graph_id, RevealNode.category == "timeline",
                                        RevealNode.node_key.in_(keys)).first() if keys else None)
    return _event_date(((json.loads(node.metadata_json or "{}").get("evidence") or {}).get("event_date"))) if node else None


def _timeline_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """The time points of a timeline card from bound claims whose linked Fact (or case timeline entry) has a real date, never
    an invented one: claims of one date share a point, the points run later. At least two points make a line."""
    from app.documentary.graphics.timeline import merge_points

    events = []
    for c in _bound_claims(db, action):
        parsed = claim_event_date(db, c)
        if parsed:
            events.append({"key": c.claim_key, "label": c.text, "date": parsed})
    points = merge_points(events)
    return {"points": points} if len(points) >= 2 else None


def _forensic_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """One examination from bound claims tagged by `attribute` ("exam" names the EXAMS member,
    "sample"/"method"/"result"/"limits" fill the matching slot) - a convention, not a schema field,
    since forensic structure has no dedicated place on a text-grounded claim."""
    from app.documentary.graphics.engines.evidence import EXAMS

    claims = _bound_claims(db, action)
    by_slot = {c.attribute: c for c in claims}
    exam = next((c.text.strip().lower() for c in claims if c.attribute == "exam"
                and c.text.strip().lower() in EXAMS), None)
    sample, method, result = by_slot.get("sample"), by_slot.get("method"), by_slot.get("result")
    if not exam or not sample or not method or not result:
        return None
    item = lambda c: {"text": c.text[:110], "basis": _ENGINE_BASIS.get(c.basis, "fact"), "fact_id": c.claim_key}
    # "method" only accepts basis "illustration"/"expert" (an examiner's own procedural description,
    # not a narration-style fact/claim) - no Section B basis maps there, so it's forced here, same
    # as an examiner's methodology is inherently expert content regardless of how the underlying
    # claim was classified.
    data = {"exam": exam, "sample": item(sample),
            "method": {"text": method.text[:110], "basis": "expert", "fact_id": method.claim_key},
            "result": item(result)}
    if "limits" in by_slot:
        data["limits"] = item(by_slot["limits"])
    return data


def _linked_sources(db: Session, action: DetectiveToolAction) -> list[tuple]:
    """(claim, Source) pairs for every real `Source` the bound claims reach through their optional
    `evidence_fact_ids_json` link to a stored `Fact`'s `source_ids_json`. A claim with no linked
    fact/source yields nothing - a document or headline is never made from narration alone."""
    from app.db.models import Fact, Source

    out = []
    for claim in _bound_claims(db, action):
        fact_ids = json.loads(claim.evidence_fact_ids_json or "[]")
        for fact in (db.query(Fact).filter(Fact.id.in_(fact_ids)).all() if fact_ids else []):
            source_ids = json.loads(fact.source_ids_json or "[]")
            for src in (db.query(Source).filter(Source.id.in_(source_ids)).all() if source_ids else []):
                out.append((claim, src))
    return out


def _document_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """The same data `alternatives.document_offer` builds: a claim's text must stand word for word
    in the stored text of an official source it is linked to (`verify_verbatim`), else None."""
    from app.documentary.graphics import dates
    from app.documentary.graphics.document import verify_verbatim
    from app.documentary.mvp import headlines as HL

    for claim, src in _linked_sources(db, action):
        if src.source_type != "official" or not (src.raw_text and src.publisher and src.title):
            continue
        if not verify_verbatim(src.raw_text, claim.text):
            continue
        parsed = HL.parse_date(src.published_at)
        return {"document": {"id": src.id, "type": HL.clean_title(src.title, src.publisher, src.publisher),
                             "issuer": src.publisher, "language": src.language or "en",
                             **({"date": " ".join(dates.display(parsed, "en")).strip()} if parsed else {})},
                "quote": {"text": claim.text, "fact_id": claim.claim_key, "verified": True},
                "context_before": [], "context_after": [], "redactions": []}
    return None


def _headline_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """A listed newspaper's own headline for a source the bound claims link to, with a full date and
    an English page - exactly `headlines.candidates`, which already refuses anything else."""
    from app.documentary.mvp import headlines as HL

    sources = {s.id: {"url": s.url, "title": s.title, "publisher": s.publisher,
                      "published_at": s.published_at, "language": s.language}
               for _, s in _linked_sources(db, action)}
    found = HL.candidates(sources)
    return found[0] if found else None


BUILDERS = {
    "fact_card": _fact_card_data, "detective_board": _detective_board_data, "recap": _recap_data,
    "conflict": _conflict_data, "quote": _quote_data, "timeline": _timeline_data,
    "evidence_check": _evidence_check_data, "forensic": _forensic_data,
    "document": _document_data, "headline": _headline_data,
}


def build_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """The renderer data of a card: the data the toolkit stored with the card, else what the builder of its kind makes from
    the claims the action binds."""
    stored = payload_of(action).get("data")
    if stored:
        return stored
    build = BUILDERS.get(action.tool_id)
    return build(db, action) if build else None


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

    claim_keys = binding_keys(action, "claim_key")
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
        payload = payload_of(action)
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


def _map_place_of(db: Session, action: DetectiveToolAction) -> str | None:
    """The place a `map` action points at: an explicit `payload.place`, else the label of the first
    place/building entity among the bound claims' entities and the scene's active entities. A map is
    never made for an entity that is not a stored place - no geography is invented from narration."""
    from app.db.models import RevealNode

    explicit = (payload_of(action).get("place") or "").strip()
    if explicit:
        return explicit[:150]
    keys = [c.entity_node_key for c in _bound_claims(db, action)]
    keys += json.loads(action.scene.active_entity_keys_json or "[]")
    for key in dict.fromkeys(keys):
        node = (db.query(RevealNode)
                .filter(RevealNode.graph_id == action.scene.graph_id, RevealNode.node_key == key,
                        RevealNode.category.in_(("place", "building"))).first())
        if node:
            return node.label[:150]
    return None


def resolve_dispatchable_actions(db: Session, graph_id: int) -> list[dict]:
    """DB-dependent half: for each tool action this pass can dispatch, resolve its beat and real
    renderer data. Plain-dict output (no DB handle) so the pure `compose()` can place it without
    breaking its no-DB contract - the same split `board_cards_for`/`compose` already use."""
    resolved: list[dict] = []
    for action in actions_for_graph(db, graph_id):
        build = BUILDERS.get(action.tool_id)
        record = {"action_key": action.action_key, "tool_id": action.tool_id, "purpose": action.purpose,
                  "reuse_reason": action.reuse_reason}
        if action.tool_id == "map":
            place, beat_id = _map_place_of(db, action), _beat_id_of(db, action)
            if place is None or beat_id is None:
                record["skip"] = "no_stored_place" if place is None else "no_beat_span"
            else:
                record["beat_id"], record["data"] = beat_id, {"place": place}
                record["min_release_fraction"] = _min_release_fraction(db, action) or json.loads(
                    action.payload_json or "{}").get("release_fraction")
        elif action.tool_id == "detective_board":
            record["skip"] = "placed_by_the_board_plan"      # the board is drawn and placed by its own subsystem
        elif build is None:
            record["skip"] = "not_dispatched_in_this_pass"
        else:
            beat_id = _beat_id_of(db, action)
            data = build_data(db, action) if beat_id else None
            if beat_id is None:
                record["skip"] = "no_beat_span"
            elif data is None:
                record["skip"] = "insufficient_data"
            else:
                record["beat_id"], record["data"] = beat_id, data
                record["min_release_fraction"] = _min_release_fraction(db, action) or json.loads(
                    action.payload_json or "{}").get("release_fraction")
        resolved.append(record)
    return resolved


def place_detective_graphics(shots: list[dict], spans: list[dict], language: str,
                             resolved: list[dict]) -> list[dict]:
    """Pure: carve each resolved action's graphic into its beat's span. Fixes Section A's
    obstacle 1 (a graphic otherwise only ever gets photo/map leftover time) for the kinds this
    pass wires. Returns a decision record per action, placed or skipped, for live evidence.

    A card is held for the time its own text needs to be read (`reading.reading_need`), never longer
    than its beat; a card that cannot be read in the room left, or that is read over dense narration it
    is not about, reports a `timing_requests` entry instead of failing silently (Section F, F2)."""
    from app.documentary.detective import reading as R
    from app.documentary.graphics import shots as GS
    from app.documentary.graphics.base import GraphicUnavailable
    from app.documentary.graphics.registry import GRAPHICS, graphic
    from app.documentary.production.script import _carve

    by_beat = {sp["beat_id"]: sp for sp in spans}
    records: list[dict] = []
    placed = {sh["detective_action_key"] for sh in shots if sh.get("detective_action_key")}
    for item in resolved:
        record = dict(item)
        if "skip" in record:
            records.append(record)
            continue
        if record["action_key"] in placed:                # the planner fixed this card in its timeline already
            record["skip"] = "already_placed_by_the_planner"
            records.append(record)
            continue
        span = by_beat.get(record["beat_id"])
        if span is None:
            record["skip"] = "beat_not_in_compiled_timeline"
            records.append(record)
            continue
        if record["tool_id"] not in GRAPHICS:             # the map is placed by the planner's map shot
            record["skip"] = "placed_by_the_planner"
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
        try:
            need = R.reading_need(record["tool_id"], record["data"], language)
        except GraphicUnavailable as e:
            record["skip"] = f"unrenderable: {e}"
            records.append(record)
            continue
        requests = []
        if remaining < need.fastest:
            # the viewer could not read it even at the fastest streaming pace, and `finalize` would drop
            # the card; the card never stretches past its own beat into unrelated narration
            record["skip"] = "insufficient_reading_time"
            record["timing_requests"] = [R.timing_request(
                record["action_key"], record["beat_id"], "insufficient_reading_time",
                f"{need.words} words need at least {need.fastest:.1f}s to read; only {remaining:.1f}s remain "
                "in the beat after the claim's release point",
                need.fastest, remaining, "give the beat more time, or split the card across beats")]
            records.append(record)
            continue
        hold = min(need.planned, remaining)
        if hold < need.planned:
            requests.append(R.timing_request(
                record["action_key"], record["beat_id"], "reading_time_compressed",
                f"{need.words} words are planned for {need.planned:.1f}s; the beat leaves {hold:.1f}s so the "
                "text streams faster than the reading pace",
                need.planned, hold, "give the beat more time, or shorten the card's text"))
        competing = R.competing_speech(need.words, record["data"], shots, start, start + hold)
        if competing:
            requests.append(R.timing_request(
                record["action_key"], record["beat_id"], "competing_dense_speech",
                f"a {competing['card_words']}-word card is read over narration at "
                f"{competing['speech_words_per_second']} words/s that shares only "
                f"{competing['shared_content_fraction']:.0%} of its content words",
                hold, hold, "move the card to a quieter stretch, or fold it into the narrated point"))
        if requests:
            record["timing_requests"] = requests
        shot = {"kind": "graphic", "command": "SHOW_GRAPHIC", **GS.plan_fields(record["tool_id"], record["data"]),
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
