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
# dispatch is explicitly left for a later pass (see docs/detective_toolkit_section_d_design.md and
# docs/detective_toolkit_section_d2_builders_design.md for exactly which and why).
DISPATCHABLE_KINDS = {
    "fact_card", "floor_plan", "detective_board", "recap", "unknowns", "conflict",
    "conflict_board", "key_facts", "quote", "evidence_check", "custody",
    "investigation_clock", "forensic", "interrogation", "courtroom", "sentencing",
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


def _bound_claims(db: Session, action: DetectiveToolAction) -> list:
    """The `DetectiveClaim` rows an action's content_bindings/payload point to, in binding order.
    Shared by every builder below instead of repeating the lookup."""
    from app.db.models import DetectiveClaim

    bindings = json.loads(action.content_bindings_json or "[]")
    claim_keys = [b.get("claim_key") for b in bindings if isinstance(b, dict) and b.get("claim_key")]
    claim_keys = claim_keys or (json.loads(action.payload_json or "{}").get("claim_keys") or [])
    if not claim_keys:
        return []
    rows = (db.query(DetectiveClaim)
            .filter(DetectiveClaim.graph_id == action.scene.graph_id, DetectiveClaim.claim_key.in_(claim_keys))
            .all())
    by_key = {r.claim_key: r for r in rows}
    return [by_key[k] for k in claim_keys if k in by_key]


def _bound_questions(db: Session, action: DetectiveToolAction) -> list:
    """The `DetectiveQuestion` rows an action's content_bindings point to, in binding order."""
    from app.db.models import DetectiveQuestion

    bindings = json.loads(action.content_bindings_json or "[]")
    q_keys = [b.get("question_key") for b in bindings if isinstance(b, dict) and b.get("question_key")]
    if not q_keys:
        return []
    rows = (db.query(DetectiveQuestion)
            .filter(DetectiveQuestion.graph_id == action.scene.graph_id, DetectiveQuestion.question_key.in_(q_keys))
            .all())
    by_key = {r.question_key: r for r in rows}
    return [by_key[k] for k in q_keys if k in by_key]


def _bound_relations(db: Session, action: DetectiveToolAction) -> list:
    """The `DetectiveRelation` rows an action's content_bindings point to, in binding order."""
    from app.db.models import DetectiveRelation

    bindings = json.loads(action.content_bindings_json or "[]")
    r_keys = [b.get("relation_key") for b in bindings if isinstance(b, dict) and b.get("relation_key")]
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


def _unknowns_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """MapOfUnknowns needs at least one known and one unknown point - bound claims supply
    "known" (basis=established_in_text/attributed), bound questions supply "unknown"."""
    claims = _bound_claims(db, action)
    questions = _bound_questions(db, action)
    known = [{"text": c.text[:160], "basis": _ENGINE_BASIS.get(c.basis, "fact"), "fact_id": c.claim_key}
             for c in claims if c.basis in ("established_in_text", "attributed")]
    unknown = [{"text": q.text[:160], "basis": "unknown", "fact_id": q.question_key} for q in questions]
    if not known or not unknown:
        return None
    return {"known": known[:3], "unknown": unknown[:3]}


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


def _conflict_board_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """The board of conflicts already introduced by bound `relation_type="conflict"` relations -
    needs at least two to be worth a board (same rule as the pre-existing `board_offer`)."""
    relations = [r for r in _bound_relations(db, action) if r.relation_type == "conflict"]
    if len(relations) < 2:
        return None
    cards = [{"id": r.relation_key, "number": n, "title": r.label[:80],
              "a": r.from_node_key, "b": r.to_node_key, "status": "open"}
             for n, r in enumerate(relations[:12], 1)]
    return {"cards": cards, "new_card": cards[-1]["id"]}


def _key_facts_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """KeyFacts needs a short (<=14 char) `value` per fact - only a claim whose text actually
    starts with one is used; never truncated/invented to fit."""
    claims = _bound_claims(db, action)
    facts = []
    for c in claims:
        first = c.text.split(" ", 1)[0].strip(".,;:")
        if first and len(first) <= 14:
            facts.append({"value": first, "caption": c.text[:90], "fact_id": c.claim_key})
    return {"facts": facts[:3]} if facts else None


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


def _custody_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """ChainOfCustody's 2-5 stages from bound claims about one entity, in binding order - the
    entity's own node_key is the item name, each claim one documented stage."""
    claims = _bound_claims(db, action)
    if not 2 <= len(claims) <= 5:
        return None
    stages = [{"stage": s, "text": c.text[:80], "basis": _ENGINE_BASIS.get(c.basis, "fact"),
              "fact_id": c.claim_key}
             for s, c in zip(("discovery", "collection", "documentation", "transfer", "storage"), claims)]
    return {"item": {"name": claims[0].entity_node_key, "fact_id": claims[0].claim_key}, "stages": stages}


def _investigation_clock_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """2-N dated events from bound claims whose linked Fact has a real `event_date` - never an
    invented date. `claim.attribute` names the track ("crime"/"investigation", the same
    attribute-as-slot convention `_forensic_data`/`_courtroom_data`/`_sentencing_data` use below),
    since neither a claim nor a Fact otherwise distinguishes the two tracks."""
    from app.documentary.mvp.alternatives import _event_date
    from app.db.models import Fact

    events = []
    for c in _bound_claims(db, action):
        if c.attribute not in ("crime", "investigation"):
            continue
        fact_ids = json.loads(c.evidence_fact_ids_json or "[]")
        fact = db.query(Fact).filter(Fact.id.in_(fact_ids)).first() if fact_ids else None
        parsed = _event_date(fact.event_date) if fact else None
        if not parsed:
            continue
        events.append({"track": c.attribute, "date": parsed, "text": c.text[:70],
                       "basis": _ENGINE_BASIS.get(c.basis, "fact"), "fact_id": c.claim_key})
    events.sort(key=lambda e: (e["date"]["year"], e["date"].get("month") or 0, e["date"].get("day") or 0))
    return {"events": events} if len(events) >= 2 else None


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


def _interrogation_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """1-N question/answer turns: each bound `DetectiveQuestion` is the "question" turn, and a
    claim bound alongside it with `attribute="answer"` and matching `entity_node_key` is its
    "answer" turn - a question with no bound answer claim is skipped, never invented."""
    claims = _bound_claims(db, action)
    answers = {c.entity_node_key: c for c in claims if c.attribute == "answer"}
    turns = []
    for q in _bound_questions(db, action):
        keys = json.loads(q.entity_node_keys_json or "[]")
        who = keys[0] if keys else None
        turns.append({"role": "question", "mode": "paraphrase", "who": who or "investigator",
                      "text": q.text[:160], "basis": "unknown", "fact_id": q.question_key})
        a = answers.get(who) if who else None
        if a:
            turns.append({"role": "answer", "mode": "paraphrase", "who": who,
                          "text": a.text[:160], "basis": _ENGINE_BASIS.get(a.basis, "witness"),
                          "fact_id": a.claim_key})
    return {"turns": turns} if turns else None


def _courtroom_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """Issue + prosecution/defense statements + finding, again via `claim.attribute` as the slot
    name ("issue"/"prosecution"/"defense"/"finding") - the same convention as `_forensic_data`."""
    claims = _bound_claims(db, action)
    item = lambda c: {"text": c.text[:90], "basis": _ENGINE_BASIS.get(c.basis, "witness"), "fact_id": c.claim_key}
    issue = next((c for c in claims if c.attribute == "issue"), None)
    prosecution = [item(c) for c in claims if c.attribute == "prosecution"]
    defense = [item(c) for c in claims if c.attribute == "defense"]
    finding = next((c for c in claims if c.attribute == "finding"), None)
    if not issue or not prosecution or not defense or not finding:
        return None
    return {"issue": item(issue), "prosecution": prosecution, "defense": defense,
            "finding": {"text": finding.text[:110], "basis": "finding", "fact_id": finding.claim_key}}


def _sentencing_data(db: Session, action: DetectiveToolAction) -> dict | None:
    """Charges (>=1) plus the required final sentence, via `claim.attribute` slots ("charge" claims
    each become one charge with the claim text as its outcome; "final" is the closing sentence,
    always required; "aggravating"/"mitigating" are optional one-item lists - never invented when
    absent). `arrangement` (concurrent/consecutive) has its own non-claim shape and is left for a
    later pass rather than approximated."""
    claims = _bound_claims(db, action)
    charges = [{"charge": c.entity_node_key, "outcome": c.text[:90],
               "basis": _ENGINE_BASIS.get(c.basis, "fact"), "fact_id": c.claim_key}
              for c in claims if c.attribute == "charge"]
    final = next((c for c in claims if c.attribute == "final"), None)
    if not charges or not final:
        return None
    item = lambda c: {"text": c.text[:110], "basis": _ENGINE_BASIS.get(c.basis, "fact"), "fact_id": c.claim_key}
    data: dict = {"charges": charges, "final": {"text": final.text[:90], "basis": "finding",
                                                "fact_id": final.claim_key}}
    for slot in ("aggravating", "mitigating"):
        c = next((c for c in claims if c.attribute == slot), None)
        if c:
            data[slot] = [item(c)]
    if len(charges) < 2 and not (data.get("aggravating") or data.get("mitigating")):
        return None
    return data


BUILDERS = {
    "fact_card": _fact_card_data, "floor_plan": _floor_plan_data,
    "detective_board": _detective_board_data, "recap": _recap_data, "unknowns": _unknowns_data,
    "conflict": _conflict_data, "conflict_board": _conflict_board_data,
    "key_facts": _key_facts_data, "quote": _quote_data,
    "evidence_check": _evidence_check_data, "custody": _custody_data,
    "investigation_clock": _investigation_clock_data, "forensic": _forensic_data,
    "interrogation": _interrogation_data, "courtroom": _courtroom_data,
    "sentencing": _sentencing_data,
}


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
                  "reuse_reason": action.reuse_reason, "planner_decision": action.planner_decision}
        if action.planner_decision not in ("accepted", "combined"):
            # H5: the Main Visual Planner's recorded decision is authoritative. A 'rejected' or
            # 'deferred' action, or one `needs.py` has not processed yet (None), must never reach
            # a real shot just because a renderer happens to exist for its tool_id - that would be
            # exactly the unrecorded downstream bypass this checklist row exists to prevent.
            record["skip"] = "not_accepted_by_planner"
        elif build is None:
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
