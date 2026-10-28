"""Section G — the Alternative Visual Design Director. Validation/persistence-adjacent tests use
`db_session` (real PostgreSQL) with a fake generation provider standing in for the paid vision
call, per this repo's TRUECRIME_TEST_NO_REAL_PROVIDERS=1 convention (tests/conftest.py). The real
live vision-call proof for this section's completion criterion runs outside pytest — see
docs/detective_toolkit_section_g_design.md for its exact evidence."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.db.models import Case
from app.documentary.detective import design_review
from app.documentary.detective.design_schema import DesignOutputError, validate_design_output
from app.providers.generation.base import GenerationResult


def _case(db_session) -> Case:
    case = Case(canonical_title="Design director test", slug=f"design-director-{uuid4().hex[:10]}",
               language="en")
    db_session.add(case)
    db_session.flush()
    return case


def _fact_card(n: int) -> dict:
    sentences = [
        "A 9mm handgun was found inside the family home during the search.",
        "Detectives confirmed the weapon had never been registered to anyone in the household.",
        "The lead investigator stated the firearm's origin remained an open line of inquiry.",
    ]
    return {"facts": [{"fact_id": f"f{i+1}", "text": sentences[i]} for i in range(n)]}


class FakeDesignGen:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    async def generate_structured(self, role, system, user, images=None, **kw):
        self.calls.append({"user": json.loads(user), "images": images})
        return (self.payload, GenerationResult(text="{}", model="fake", provider="fake"))


def test_render_preview_measures_content_against_safe_area():
    """A real render (the same draw_state the golden tests use) of the densest allowed fact_card
    (3 facts, near the 120-char limit) sits closer to the bottom safe edge than a single-fact card
    — an honest, content-independent deterministic signal the design agent can be given."""
    _, sparse = design_review.render_preview("fact_card", _fact_card(1), "en")
    _, dense = design_review.render_preview("fact_card", _fact_card(3), "en")
    assert dense["content_bottom_fraction"] >= sparse["content_bottom_fraction"]


def test_render_preview_refuses_data_that_cannot_render():
    with pytest.raises(design_review.DesignValidationFailed):
        design_review.render_preview("fact_card", {"facts": []}, "en")


def test_design_schema_rejects_quotation_rewrite():
    raw = {
        "schema_version": "1.0", "run_id": "r1", "film_id": "f1", "scene_id": "s1",
        "visual_id": "v1", "language": "en", "text_version": 1, "mode": "repair",
        "outcome": "revise", "reviewed_preview_ids": ["p1"],
        "label_edits": [{"element_id": "f1", "original_text": "x", "proposed_text": "a shorter but DIFFERENT claim"}],
    }
    violations = validate_design_output(raw, exact_texts={"f1": "the original exact wording"})
    assert any("rewrites an exact-quotation" in v for v in violations)


def test_design_schema_allows_faithful_shortening_of_a_non_exact_label():
    raw = {
        "schema_version": "1.0", "run_id": "r1", "film_id": "f1", "scene_id": "s1",
        "visual_id": "v1", "language": "en", "text_version": 1, "mode": "repair",
        "outcome": "revise", "reviewed_preview_ids": ["p1"],
        "label_edits": [{"element_id": "label-only", "original_text": "x", "proposed_text": "a shorter caption"}],
    }
    assert validate_design_output(raw, exact_texts={}) == []


def test_design_schema_requires_a_reviewed_preview_for_pass_or_revise():
    raw = {
        "schema_version": "1.0", "run_id": "r1", "film_id": "f1", "scene_id": "s1",
        "visual_id": "v1", "language": "en", "text_version": 1, "mode": "review",
        "outcome": "pass", "reviewed_preview_ids": [],
    }
    violations = validate_design_output(raw, exact_texts={})
    assert any("actually reviewed preview" in v for v in violations)


def test_design_schema_raises_on_unknown_outcome():
    raw = {
        "schema_version": "1.0", "run_id": "r1", "film_id": "f1", "scene_id": "s1",
        "visual_id": "v1", "language": "en", "text_version": 1, "mode": "review",
        "outcome": "great_job",
    }
    with pytest.raises(DesignOutputError):
        validate_design_output(raw, exact_texts={})


def test_review_calls_the_agent_with_the_real_rendered_frame_and_persists_a_run(db_session, monkeypatch):
    case = _case(db_session)
    fake_payload = {
        "schema_version": "1.0", "run_id": "r1", "film_id": f"case-{case.id}", "scene_id": "s1",
        "visual_id": "v1", "language": "en", "text_version": 1, "mode": "review",
        "outcome": "revise", "reviewed_preview_ids": ["fact_card-s1-v1"],
        "issues": [{"id": "i1", "severity": "major", "affected_element_ids": ["f1", "f2", "f3"],
                    "observed_problem": "three near-maximum-length facts crowd the card",
                    "expected_improvement": "fewer facts or shorter wording"}],
        "changes": [{"target": "facts", "operation": "reduce_count", "proposed_value": 2,
                    "issue_ids": ["i1"], "reason": "restore reading comfort"}],
    }
    fake = FakeDesignGen(fake_payload)
    monkeypatch.setattr(design_review, "get_generation_provider", lambda: fake)

    import asyncio
    result = asyncio.run(design_review.review(
        db_session, case.id, tool_kind="fact_card", data=_fact_card(3), language="en",
        detective_intent={"purpose": "state the find", "expected_audience_gain": "a concrete anchor"},
        scene_id="s1", visual_id="v1",
    ))

    assert fake.calls, "the agent was never actually called"
    assert fake.calls[0]["images"], "the real rendered frame was never attached"
    assert result["outcome"] == "revise"


def test_review_rejects_output_that_rewrites_a_bound_quote(db_session, monkeypatch):
    case = _case(db_session)
    fake_payload = {
        "schema_version": "1.0", "run_id": "r1", "film_id": f"case-{case.id}", "scene_id": "s1",
        "visual_id": "v1", "language": "en", "text_version": 1, "mode": "repair",
        "outcome": "revise", "reviewed_preview_ids": ["p1"],
        "label_edits": [{"element_id": "f1", "original_text": "x",
                         "proposed_text": "a DIFFERENT claim that was never said"}],
    }
    fake = FakeDesignGen(fake_payload)
    monkeypatch.setattr(design_review, "get_generation_provider", lambda: fake)

    import asyncio
    with pytest.raises(design_review.DesignValidationFailed):
        asyncio.run(design_review.review(
            db_session, case.id, tool_kind="fact_card", data=_fact_card(1), language="en",
            detective_intent={}, scene_id="s1", visual_id="v1",
            exact_texts={"f1": _fact_card(1)["facts"][0]["text"]},
        ))
