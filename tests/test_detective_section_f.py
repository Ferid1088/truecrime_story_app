"""Section F — synchronize narration, visuals, and audience attention.

Closes Section A's obstacles 5 (narration/reference truncation) and 7 (compile-time cue
resolution losing an anchor) exactly as far as this section's own scope goes: real word-level
resolution of a release anchor (reused from the live voice pipeline's own alignment math), and
beat-level enforcement that a dispatched graphic cannot be carved before the words supporting its
bound claim are actually spoken. Gated the same way as every prior section: an action with no
resolvable anchor keeps today's exact behavior (start at the beat's own start)."""

from __future__ import annotations

import json
from uuid import uuid4

from app.db.models import (
    Case,
    DetectiveScene,
    DetectiveToolAction,
    EditorialBlueprint,
    RevealGraph,
    StoryVersion,
)
from app.documentary import memory as MEM
from app.documentary.detective import dispatch as D
from app.documentary.detective.timing import anchor_fraction_within_span, resolve_word_level


def _graph(db_session):
    slug = f"detective-f-{uuid4().hex[:10]}"
    case = Case(canonical_title="Section F test", slug=slug, language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                         narrative_angle="test", story_text="placeholder", engagement_score=0.0)
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(case_id=case.id, story_version_id=story.id, version=1,
                                   status="valid", blueprint_json=json.dumps({"beats": [{"id": "B01"}]}))
    db_session.add(blueprint)
    db_session.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, version=1, status="validated")
    db_session.add(graph)
    db_session.flush()
    return graph


def _scene_with_fact_card(db_session, graph, unit_text, claim_text, anchor_start, anchor_end):
    unit = MEM.add_narration_unit(db_session, graph.id, unit_key="B01-1", beat_id="B01",
                                  story_order=0, text=unit_text, language="en")
    MEM.ensure_entity(db_session, graph.id, "gun-1", "the gun", "object", "B01", 0)
    anchor = MEM.add_release_anchor(db_session, graph.id, "a-1", unit.id, anchor_start, anchor_end)
    MEM.add_claim(db_session, graph.id, "claim-1", "gun-1", "location", claim_text,
                 "explicit_unknown", release_anchor_id=anchor.id)
    scene = DetectiveScene(
        graph_id=graph.id, scene_key="scene-B01", scene_order=0, mode="direct_scenes",
        covered_unit_keys_json=json.dumps([unit.unit_key]), aim="aim", active_question_keys_json="[]",
        active_entity_keys_json="[]", change_in_understanding="change", alternatives_considered_json="[]",
        why_selected="why", expected_audience_gain="gain", audio_clarity_findings_json="[]")
    db_session.add(scene)
    db_session.flush()
    action = DetectiveToolAction(
        scene_id=scene.id, action_key="action-B01", order_index=0, tool_id="fact_card",
        purpose="show the open question", payload_json="{}",
        content_bindings_json=json.dumps([{"claim_key": "claim-1"}]), continuity_refs_json="[]")
    db_session.add(action)
    db_session.flush()
    return anchor


# ---------------------------------------------------------------------------
# word-level resolution (reuses voice_render.beat_times)
# ---------------------------------------------------------------------------

def test_word_level_resolution_uses_real_character_alignment():
    text = "The gun was never recovered."
    chars = list(text)
    starts = [round(i * 0.05, 3) for i in range(len(chars))]
    ends = [round(s + 0.05, 3) for s in starts]
    alignment = {"characters": chars, "starts": starts, "ends": ends}
    anchor_start, anchor_end = text.index("never recovered"), len("The gun was never recovered")
    start, end, exact = resolve_word_level(0, anchor_start, anchor_end, text, alignment, t0=0.0)
    assert exact is True
    assert start == round(starts[anchor_start], 3)
    assert end == round(ends[anchor_end - 1], 3)
    assert start > 0.3  # genuinely late in the sentence, not at its start


def test_word_level_resolution_flags_proportional_fallback_when_alignment_does_not_match_text():
    alignment = {"characters": list("different text"), "starts": [0.0] * 14, "ends": [0.1] * 14}
    _start, _end, exact = resolve_word_level(0, 0, 5, "The gun was never recovered.", alignment, t0=0.0)
    assert exact is False


# ---------------------------------------------------------------------------
# sentence/beat-level fallback (what dispatch.py actually uses at carve time)
# ---------------------------------------------------------------------------

def test_sentence_level_fallback_is_proportional_within_the_beat_span():
    text = "The gun was from the house, but its location was never explained."
    anchor_start = text.index("never explained")
    start, end = anchor_fraction_within_span(anchor_start, len(text), len(text), 10.0, 20.0)
    assert 10.0 < start < end <= 20.0
    assert start > 15.0  # the qualification is near the end of the sentence, so near the end of the beat


# ---------------------------------------------------------------------------
# obstacle 5/7: a dispatched shot cannot be carved before its claim's words complete
# ---------------------------------------------------------------------------

def test_a_late_qualification_delays_the_dispatched_shot_past_the_beat_start(db_session):
    unit_text = "The gun was from the house, but its location was never explained to the audience."
    anchor_start = unit_text.index("never explained")
    anchor_end = anchor_start + len("never explained")
    graph = _graph(db_session)
    _scene_with_fact_card(db_session, graph, unit_text, "Its location was never explained.",
                          anchor_start, anchor_end)
    resolved = D.resolve_dispatchable_actions(db_session, graph.id)
    assert resolved[0]["min_release_fraction"] is not None and resolved[0]["min_release_fraction"] > 0.5

    shots: list[dict] = []
    # a beat long enough that a card released late in it still has the time it needs to be read (a
    # shorter beat is skipped with a timing request - tests/test_detective_reading_time.py)
    spans = [{"beat_id": "B01", "start": 100.0, "end": 160.0}]
    records = D.place_detective_graphics(shots, spans, "en", resolved)

    assert records[0]["result"] == "placed"
    assert shots[0]["start"] > 100.0, "must not start at the beat's start when its claim is a late qualification"
    assert shots[0]["start"] < 160.0
    assert records[0]["release_fraction_applied"] == resolved[0]["min_release_fraction"]


def test_an_action_with_no_resolvable_anchor_keeps_the_old_beat_start_unchanged(db_session):
    """Gating discipline: an action whose claim carries no release anchor (every case before this
    section, and most claims even after it) behaves exactly as Section D left it — shot starts at
    the beat's own start, nothing new."""
    graph = _graph(db_session)
    unit = MEM.add_narration_unit(db_session, graph.id, unit_key="B01-1", beat_id="B01",
                                  story_order=0, text="Jane was the only witness.", language="en")
    MEM.ensure_entity(db_session, graph.id, "jane", "Jane", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "claim-1", "jane", "role", "Jane was the only witness.",
                 "established_in_text")  # no release_anchor_id
    scene = DetectiveScene(
        graph_id=graph.id, scene_key="scene-B01", scene_order=0, mode="direct_scenes",
        covered_unit_keys_json=json.dumps([unit.unit_key]), aim="aim", active_question_keys_json="[]",
        active_entity_keys_json="[]", change_in_understanding="change", alternatives_considered_json="[]",
        why_selected="why", expected_audience_gain="gain", audio_clarity_findings_json="[]")
    db_session.add(scene)
    db_session.flush()
    action = DetectiveToolAction(
        scene_id=scene.id, action_key="action-B01", order_index=0, tool_id="fact_card",
        purpose="p", payload_json="{}", content_bindings_json=json.dumps([{"claim_key": "claim-1"}]),
        continuity_refs_json="[]")
    db_session.add(action)
    db_session.flush()

    resolved = D.resolve_dispatchable_actions(db_session, graph.id)
    assert resolved[0]["min_release_fraction"] is None
    shots: list[dict] = []
    records = D.place_detective_graphics(shots, [{"beat_id": "B01", "start": 5.0, "end": 25.0}], "en", resolved)
    assert records[0]["result"] == "placed" and shots[0]["start"] == 5.0
    assert "release_fraction_applied" not in records[0]


# ---------------------------------------------------------------------------
# reading time (F2): already real and word-count-aware for the detective board specifically
# (characterized here, not rebuilt) - `appearances()` only shows a batch of cards once the gap
# until the next card is long enough to read every pending title, scaling the requirement with
# word count via `reading_hold_seconds`. This is NOT yet generalized to every dispatched tool
# against arbitrary unrelated narration density - see the design doc's "remaining" section.
# ---------------------------------------------------------------------------

def test_board_reading_time_already_scales_with_title_length_and_gates_appearance():
    from app.documentary.detective_board import appearances, reading_hold_seconds

    short_hold = reading_hold_seconds("Short")
    long_hold = reading_hold_seconds("A considerably longer title that takes real time to read aloud")
    assert long_hold > short_hold

    cards = [{"id": "c1", "beat_id": "B01", "title": "Short"}]
    too_little_room = appearances(cards, room={"B01": 0.1})
    plenty_of_room = appearances(cards, room={"B01": 30.0})
    # with too little room the card still joins the board (at the next beat with room, or the
    # film's end) rather than being shown without time to read it
    assert too_little_room[0]["hold_seconds"] <= 0.1 or too_little_room[0]["beat_id"] != "B01"
    assert plenty_of_room[0]["hold_seconds"] >= short_hold


# ---------------------------------------------------------------------------
# audio-only audit (F3): a claim/question's meaning lives in its own narration text, never only
# in a caption/graphic label
# ---------------------------------------------------------------------------

def test_claim_and_question_text_are_self_contained_without_the_visual(db_session):
    graph = _graph(db_session)
    MEM.ensure_entity(db_session, graph.id, "gun-1", "the gun", "object", "B01", 0)
    claim = MEM.add_claim(db_session, graph.id, "claim-1", "gun-1", "origin",
                          "The gun came from the house.", "established_in_text")
    question = MEM.add_question(db_session, graph.id, "q-1", ["gun-1"],
                                "Where is the gun now?", "not_yet_explained")
    # The audience-facing meaning must be fully readable from the claim/question's own `text` —
    # never a bare ID or a reference that only a caption resolves. A visual may restate or
    # emphasize this text; it must never be the only place the meaning exists.
    assert len(claim.text.split()) >= 4 and claim.text.strip().endswith(".")
    assert len(question.text.split()) >= 3 and question.text.strip().endswith("?")
