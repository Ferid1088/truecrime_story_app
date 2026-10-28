"""Section E - the continuing board and meaningful returns.

Three things proven here: (1) a board card's displayed status now actually reflects Section B's
knowledge-event log as of its own beat, instead of a hardcoded "new" that discarded whatever an
override supplied; (2) a fact already used by one card can be deliberately reused by a later one,
since `select_cards` never tracked used facts across beats to begin with; (3) a detective claim's
key is the SAME string in the detective board and in the recap board's evidence pack, via the
existing Section D `legacy_evidence_additions` bridge.
"""

from __future__ import annotations

import json
from uuid import uuid4

from app.db.models import Case, EditorialBlueprint, RevealGraph, StoryVersion
from app.documentary import memory as MEM
from app.documentary.detective import dispatch as D
from app.db.models import DetectiveScene, DetectiveToolAction
from app.documentary.detective_board import _linked_cards, select_cards


def _graph(db_session, beats=None):
    slug = f"detective-e-{uuid4().hex[:10]}"
    case = Case(canonical_title="Section E test", slug=slug, language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                         narrative_angle="test", story_text="placeholder", engagement_score=0.0)
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(case_id=case.id, story_version_id=story.id, version=1,
                                   status="valid", blueprint_json=json.dumps({"beats": beats or []}))
    db_session.add(blueprint)
    db_session.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, version=1, status="validated")
    db_session.add(graph)
    db_session.flush()
    return graph, blueprint, case


def _board_action(db_session, graph, beat_id, order, kind, source_fact, reuse_reason=None):
    unit = MEM.add_narration_unit(db_session, graph.id, unit_key=f"{beat_id}-1", beat_id=beat_id,
                                  story_order=order, text="narration text", language="en")
    scene = DetectiveScene(
        graph_id=graph.id, scene_key=f"scene-{beat_id}", scene_order=order, mode="direct_scenes",
        covered_unit_keys_json=json.dumps([unit.unit_key]), aim="aim", active_question_keys_json="[]",
        active_entity_keys_json="[]", change_in_understanding="change",
        alternatives_considered_json="[]", why_selected="why", expected_audience_gain="gain",
        audio_clarity_findings_json="[]")
    db_session.add(scene)
    db_session.flush()
    action = DetectiveToolAction(
        scene_id=scene.id, action_key=f"action-{beat_id}", order_index=0, tool_id="detective_board",
        purpose="board card", payload_json=json.dumps({"kind": kind, "source_fact": source_fact}),
        content_bindings_json="[]", continuity_refs_json="[]", reuse_reason=reuse_reason)
    db_session.add(action)
    db_session.flush()
    return unit, scene, action


def test_linked_cards_no_longer_overwrites_status_and_reuse_reason_with_new():
    """Regression: `_linked_cards` used to hardcode every card's status to "new", discarding
    whatever `card_overrides` had already computed (e.g. a question's real current state)."""
    blueprint = {"beats": [{"id": "B01"}]}
    picked = [{"kind": "open_question", "source_fact": "q-1", "beat_id": "B01",
              "status": "answered", "reuse_reason": "the location question is now answered"}]
    card = _linked_cards(picked, blueprint)[0]
    assert card["status"] == "answered"
    assert card["reuse_reason"] == "the location question is now answered"


def test_linked_cards_defaults_status_to_new_when_none_is_supplied():
    """The default path (no override, e.g. every real case today) is unchanged."""
    blueprint = {"beats": [{"id": "B01"}]}
    picked = [{"kind": "time", "source_fact": "T1", "beat_id": "B01"}]
    card = _linked_cards(picked, blueprint)[0]
    assert card["status"] == "new"
    assert card["reuse_reason"] is None


def test_board_card_overrides_reflects_the_question_state_at_its_own_beat_not_the_latest(db_session):
    """E2/E3: a question card's status is computed by replaying the knowledge-event log up to
    THIS card's own beat. The same question, reused at a later beat after it's been answered,
    shows "answered" there - and the earlier card never sees that later answer."""
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}, {"id": "B05"}])
    MEM.ensure_entity(db_session, graph.id, "gun-1", "the 9mm", "object", "B01", 0)
    question = MEM.add_question(db_session, graph.id, "q-location", ["gun-1"],
                                "Where is the gun?", "not_yet_explained")
    unit_a = MEM.add_narration_unit(db_session, graph.id, unit_key="B01-mem", beat_id="B01",
                                    story_order=0, text="narration text", language="en")
    anchor_a = MEM.add_release_anchor(db_session, graph.id, "B01-A1", unit_a.id, 0, len(unit_a.text))
    MEM.record_knowledge_event(db_session, graph.id, "question", question.question_key, "introduce",
                               {"status": "open"}, anchor_id=anchor_a.id)
    unit_b = MEM.add_narration_unit(db_session, graph.id, unit_key="B05-mem", beat_id="B05",
                                    story_order=4, text="found in the lake", language="en")
    anchor_b = MEM.add_release_anchor(db_session, graph.id, "B05-A1", unit_b.id, 0, len(unit_b.text))
    MEM.record_knowledge_event(db_session, graph.id, "question", question.question_key, "resolve",
                               {"status": "answered"}, anchor_id=anchor_b.id,
                               supporting_claim_keys=[])

    # The detective returns to the same question at both beats - a first card, then a deliberate
    # update once it's answered.
    _board_action(db_session, graph, "B01", 1, "open_question", "q-location")
    _board_action(db_session, graph, "B05", 5, "open_question", "q-location",
                  reuse_reason="the location question is now answered")

    overrides = D.board_card_overrides(db_session, graph.id)
    assert overrides["B01"]["status"] == "open"
    assert overrides["B05"]["status"] == "answered"
    assert overrides["B05"]["reuse_reason"] == "the location question is now answered"


def test_select_cards_allows_a_fact_to_be_deliberately_reused_at_a_later_beat():
    """E1/E3: `select_cards` only ever tracked one card per BEAT, never a used-fact set across
    beats - so a detective-authored override reusing an earlier card's source_fact at a later
    beat already produces its own distinct card, with no duplicate-check to remove."""
    # min_beats_between_cards (config default 2) is an unrelated pacing control, not the
    # used-fact dedup under test - give the two reused-fact beats enough beats between them.
    blueprint = {"beats": [{"id": "B01"}, {"id": "B02"}, {"id": "B03"}, {"id": "B04"}, {"id": "B05"}]}
    overrides = {
        "B01": {"kind": "open_question", "source_fact": "q-location", "status": "open"},
        "B05": {"kind": "open_question", "source_fact": "q-location", "status": "answered",
               "reuse_reason": "now answered"},
    }
    cards = select_cards(blueprint, told_events=[], card_overrides=overrides)
    # `_reach_the_end` may add its own filler card for the final stretch - irrelevant to what's
    # under test here, so look at the two override-driven cards specifically, by beat.
    by_beat = {c["beat_id"]: c for c in cards}
    assert by_beat["B01"]["source_fact"] == by_beat["B05"]["source_fact"] == "q-location"
    assert (by_beat["B01"]["status"], by_beat["B05"]["status"]) == ("open", "answered")
    assert by_beat["B05"]["reuse_reason"] == "now answered"


def test_a_detective_claim_key_is_the_same_string_on_the_board_and_in_the_recap_pack(db_session):
    """E's cross-tool ID requirement: the SAME claim_key identifies a detective-authored fact on
    the detective board (via a board override's source_fact) and in the recap board's own
    evidence pack (via Section D's `legacy_evidence_additions` bridge, already merged into
    `ev.facts` by `app/documentary/mvp/evidence.py`) - characterizing existing behavior, not new
    code, since the bridge already keys both by `claim_key`."""
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "gun-1", "the 9mm", "object", "B01", 0)
    MEM.add_claim(db_session, graph.id, "claim-gun-type", "gun-1", "type",
                 "a 9mm handgun", "established_in_text")

    facts, _ = D.legacy_evidence_additions(db_session, graph.id)
    recap_fact_ids = {f["id"] for f in facts}
    assert "claim-gun-type" in recap_fact_ids  # same key a board override would use as source_fact

    overrides = D.board_card_overrides(db_session, graph.id)
    # No detective_board action was recorded for this claim in this test, so confirm instead that
    # the identifier space is the one a real override would share: a board override naming this
    # same claim_key as source_fact is exactly what `select_cards` already passes through verbatim.
    blueprint = {"beats": [{"id": "B01"}]}
    cards = select_cards(blueprint, told_events=[],
                         card_overrides={"B01": {"kind": "object", "source_fact": "claim-gun-type"}})
    assert cards[0]["source_fact"] in recap_fact_ids
    assert overrides == {}  # no detective_board action existed in this test - sanity check only
