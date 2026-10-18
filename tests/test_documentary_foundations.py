"""Foundations for the documentary engine: stable evidence meaning, act
structure that survives every edit step and every language, pilot-length
stories, and reviewer independence.

All generation calls are scripted — no real credits are spent.
"""
import json

import pytest

from app.core.ai_config import ai_config
from tests.factories import _add_fact, _mk_case

TWO_ACTS = {
    "title": "t", "central_question": "q",
    "acts": [
        {"id": "act1", "purpose": "p1", "evidence_ids": []},
        {"id": "act2", "purpose": "p2", "evidence_ids": []},
    ],
}






# ---------------------------------------------------------------------------
# Evidence IDs: deterministic order + fingerprint of what they mean
# ---------------------------------------------------------------------------


def test_evidence_ids_follow_database_order(db_session):
    from app.agents.story import build_evidence_pack

    case = _mk_case(db_session)
    first = _add_fact(db_session, case, "first fact")
    second = _add_fact(db_session, case, "second fact")
    # Rows handed over in reverse order must still get the same IDs.
    pack = build_evidence_pack([second, first], [], [])
    assert [(f["id"], f["claim"]) for f in pack["facts"]] == [
        ("F001", "first fact"), ("F002", "second fact"),
    ]


def test_evidence_fingerprint_tracks_meaning(db_session):
    from app.agents.story import build_evidence_pack, evidence_fingerprint

    case = _mk_case(db_session)
    a = _add_fact(db_session, case, "alpha")
    b = _add_fact(db_session, case, "beta")
    fp = evidence_fingerprint(build_evidence_pack([a, b], [], []))
    assert fp == evidence_fingerprint(build_evidence_pack([b, a], [], []))
    b.claim = "beta, re-extracted differently"
    assert fp != evidence_fingerprint(build_evidence_pack([a, b], [], []))


# ---------------------------------------------------------------------------
# realign_sections / stored_sections units
# ---------------------------------------------------------------------------


PREV = [
    {"id": "act1", "text": "One.\n\nTwo.", "meta": {"evidence_ids": ["F001"]}},
    {"id": "act2", "text": "Three."},
]


def test_realign_uses_markers_and_keeps_metadata():
    from app.agents.story import realign_sections

    raw = "[[ACT:act1]]\n\nUno.\n\nDos.\n\n[[ACT:act2]]\n\nTres."
    out, ok = realign_sections(PREV, raw)
    assert ok
    assert [s["id"] for s in out] == ["act1", "act2"]
    assert out[0]["text"] == "Uno.\n\nDos."
    assert out[0]["meta"] == {"evidence_ids": ["F001"]}


def test_realign_folds_model_lead_in_into_first_act():
    from app.agents.story import realign_sections

    raw = "Here it is.\n\n[[ACT:act1]]\n\nUno.\n\n[[ACT:act2]]\n\nTres."
    out, ok = realign_sections(PREV, raw)
    assert ok and out[0]["text"].startswith("Here it is.")


def test_realign_paragraph_fallback_only_when_counts_match():
    from app.agents.story import realign_sections

    out, ok = realign_sections(PREV, "Eins.\n\nZwei.\n\nDrei.")
    assert ok and [s["text"] for s in out] == ["Eins.\n\nZwei.", "Drei."]

    out, ok = realign_sections(PREV, "Eins. Zwei. Drei.")
    assert not ok and out == [{"id": "full", "text": "Eins. Zwei. Drei."}]

    # Across languages the caller disables the paragraph fallback.
    out, ok = realign_sections(
        PREV, "Eins.\n\nZwei.\n\nDrei.", allow_paragraph_fallback=False
    )
    assert not ok


def test_realign_unstructured_input_is_not_a_loss():
    from app.agents.story import realign_sections

    out, ok = realign_sections([{"id": "full", "text": "x"}], "new text")
    assert ok and out[0]["id"] == "full"


def test_stored_sections_require_exact_story_text():
    from app.agents.story import stored_sections
    from app.db.models import StoryVersion

    v = StoryVersion(
        story_text="A.\n\nB.",
        narrative_structure=json.dumps({"sections": [
            {"id": "act1", "text": "A."}, {"id": "act2", "text": "B."},
        ]}),
    )
    assert [s["id"] for s in stored_sections(v)] == ["act1", "act2"]
    v.story_text = "A.\n\nB, edited later."
    assert stored_sections(v) is None
    v.narrative_structure = json.dumps({"sections": [{"id": "x", "words": 2}]})
    assert stored_sections(v) is None  # legacy: no section text stored


# ---------------------------------------------------------------------------
# Master pipeline keeps act structure
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# Localization keeps act structure across languages
# ---------------------------------------------------------------------------


















# ---------------------------------------------------------------------------
# Film length comes from config: every documentary is 45–120 minutes;
# pilots are short renders of a full-length story, not short stories.
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Reviewer independence: a critic never falls back to the writer model
# ---------------------------------------------------------------------------


def _raw_config():
    from app.core.ai_config import load_raw_config

    return load_raw_config()


def test_reviewer_fallbacks_skip_author_models():
    models = ai_config.generation_provider().models
    for role in ai_config.review_independence.reviewer_roles:
        chain = ai_config.fallback_models_for(role)
        assert models["premium"] not in chain, role
        assert ai_config.model_for(role) not in chain, role
    # Author-side roles keep their normal chain.
    assert ai_config.fallback_models_for("story_director") == [models["writer"]]


def test_reviewer_routed_to_writer_model_is_a_config_error():
    from app.core.ai_config import AIConfig

    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["routing"][
        "grounding_validator"
    ] = "premium"
    with pytest.raises(Exception, match="also writes"):
        AIConfig.model_validate(raw)


def test_reviewer_with_only_author_fallback_gets_no_fallback():
    from app.core.ai_config import AIConfig

    raw = _raw_config()
    raw["review_independence"]["reviewer_fallbacks"] = {"cheap": ["premium"]}
    cfg = AIConfig.model_validate(raw)
    # Better to fail loudly than to let the writer grade its own text.
    assert cfg.fallback_models_for("grounding_validator") == []


def test_review_independence_can_be_disabled():
    from app.core.ai_config import AIConfig

    raw = _raw_config()
    raw["review_independence"]["enabled"] = False
    cfg = AIConfig.model_validate(raw)
    models = cfg.generation_provider().models
    assert cfg.fallback_models_for("grounding_validator") == [models["premium"]]




def test_requested_film_length_comes_from_config():
    from app.language_pipeline.lengths import check_requested_minutes

    lo = ai_config.story.min_target_minutes
    hi = ai_config.language_pipeline.long_draft_minutes - ai_config.language_pipeline.rewrite_headroom_minutes
    check_requested_minutes(lo)
    check_requested_minutes(hi)
    with pytest.raises(ValueError):
        check_requested_minutes(lo - 1)
    with pytest.raises(ValueError):
        check_requested_minutes(hi + 1)


def test_the_pipeline_endpoint_rejects_out_of_range_minutes(client, db_session):
    from tests.factories import _mk_case

    case = _mk_case(db_session)
    too_long = ai_config.language_pipeline.long_draft_minutes
    r = client.post(f"/api/cases/{case.id}/language-pipeline/run",
                    json={"requested_minutes": too_long})
    assert r.status_code == 422
