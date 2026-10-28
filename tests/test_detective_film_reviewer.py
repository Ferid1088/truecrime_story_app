"""Section J - the Film Experience Reviewer: schema honesty, finding persistence, and the full
repair loop (defect -> finding -> owner repair -> recheck -> close), including regression,
interruption, and exhausted-retry variants. Closes checklist rows H3/H4/J1-J4.

Validation/persistence tests use `db_session` (real PostgreSQL, no mocks) with a fake generation
provider standing in for the paid call, same convention as test_detective_director.py; the real
live paid-provider proof is run outside pytest (see docs/detective_toolkit_section_j_design.md)
since this repo's test suite always runs under TRUECRIME_TEST_NO_REAL_PROVIDERS=1."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from app.db.models import (
    Case,
    DetectiveFinding,
    EditorialBlueprint,
    RevealGraph,
    RevealNode,
    StoryVersion,
)
from app.documentary.detective import director, review
from app.documentary.detective.persist import persist_director_output
from app.documentary.detective.review_schema import ReviewOutputError, validate_review_output
from app.documentary.memory import add_narration_unit
from app.providers.generation.base import GenerationResult


def _graph(db_session, beats=None):
    slug = f"detective-reviewer-{uuid4().hex[:10]}"
    case = Case(canonical_title="Film reviewer test", slug=slug, language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(
        case_id=case.id, version=1, kind="final", language="en",
        narrative_angle="test", story_text="placeholder", engagement_score=0.0,
    )
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(
        case_id=case.id, story_version_id=story.id, version=1, status="valid",
        blueprint_json=json.dumps({"beats": beats or []}),
    )
    db_session.add(blueprint)
    db_session.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, version=1, status="validated")
    db_session.add(graph)
    db_session.flush()
    return graph


def _entity(db_session, graph, node_key, label, beat_id, order, category="object"):
    node = RevealNode(graph_id=graph.id, node_key=node_key, label=label, category=category,
                      first_revealed_beat_id=beat_id, first_revealed_order=order)
    db_session.add(node)
    db_session.flush()
    return node


def _scene_fixture(db_session, graph):
    """One real scene+action, built through the same persist path a live map_film call uses."""
    add_narration_unit(db_session, graph.id, "B01-1", "B01", 1, "en",
                       "Detectives found a 9mm handgun inside the house.")
    db_session.commit()
    raw = {
        "schema_version": "1.0", "run_id": "r1", "film_id": f"case-{graph.case_id}",
        "language": "en", "text_version": 1, "mode": "map_film",
        "entities": [], "claims": [], "questions": [], "relations": [], "knowledge_events": [],
        "scenes": [{
            "id": "s1", "covered_span_ids": ["B01-1"], "aim": "state the find",
            "active_question_ids": [], "active_thread_ids": [],
            "change_in_understanding": "the audience learns the gun's origin",
            "alternatives_considered": [], "why_selected": "a fact card states it plainly",
            "expected_audience_gain": "a concrete anchor", "audio_clarity_findings": [],
            "actions": [{"id": "a1", "tool_id": "fact_card", "purpose": "state the find",
                        "payload": {}, "content_bindings": []}],
        }],
        "requests": [], "affected_scene_ids": [], "checks": {},
    }
    persist_director_output(db_session, graph.id, None, "map_film", raw)
    db_session.commit()


class FakeReviewGen:
    """Returns successive payloads in order, one per call — enough to script a repair/recheck
    sequence (first call finds a problem, second call after repair finds none)."""

    def __init__(self, payloads: list[dict]):
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    async def generate_structured(self, role, system, user, images=None, **kw):
        self.calls.append(json.loads(user))
        payload = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        return (payload, GenerationResult(text="{}", model="fake", provider="fake"))


def _envelope(**over):
    base = {"schema_version": "1.0", "run_id": "r1", "film_id": "f1", "language": "en",
           "text_version": 1, "mode": "plan_audit", "outcome": "pass",
           "inspection_modes": ["plan"], "findings": []}
    base.update(over)
    return base


def test_schema_rejects_plan_audit_claiming_motion_or_audio():
    violations = validate_review_output(_envelope(inspection_modes=["plan", "audio"]))
    assert any("plan_audit must never claim" in v for v in violations)


def test_schema_rejects_unknown_repair_owner():
    violations = validate_review_output(_envelope(
        outcome="needs_attention",
        findings=[{"id": "f1", "repair_owner": "not_a_real_owner", "severity": "major"}],
    ))
    assert any("invalid repair_owner" in v for v in violations)


def test_schema_rejects_pass_outcome_with_open_findings():
    violations = validate_review_output(_envelope(
        outcome="pass",
        findings=[{"id": "f1", "repair_owner": "detective_director", "severity": "minor"}],
    ))
    assert any("pass' with non-empty findings" in v for v in violations)


def test_schema_accepts_clean_plan_audit():
    assert validate_review_output(_envelope()) == []


def test_schema_raises_on_missing_envelope_field():
    bad = _envelope()
    del bad["mode"]
    with pytest.raises(ReviewOutputError):
        validate_review_output(bad)


def _plan_audit_payload(graph, finding_id="f-dd-1", findings=None):
    return {
        "schema_version": "1.0", "run_id": "r1", "film_id": f"case-{graph.case_id}",
        "language": "en", "text_version": 1, "mode": "plan_audit", "outcome": "needs_attention",
        "inspection_modes": ["plan"],
        "scene_results": [], "findings": findings if findings is not None else [
            {"id": finding_id, "scene_id": "s1", "action_id": "a1", "severity": "major",
             "observed_problem": "the fact card never states what the gun's origin establishes",
             "repair_owner": "detective_director", "recommended_repair": "bind a supporting claim"},
        ],
        "affected_scene_ids": ["s1"], "checks_required_after_repair": [],
        "unresolved_questions_summary": "none yet", "limitations": [], "uninspected_areas": [],
        "summary": "the plan exists but one action has no content binding",
    }


def test_review_calls_agent_and_persists_a_finding(db_session, monkeypatch):
    graph = _graph(db_session, beats=[{"id": "B01"}])
    _scene_fixture(db_session, graph)
    fake = FakeReviewGen([_plan_audit_payload(graph)])
    monkeypatch.setattr(review, "get_generation_provider", lambda: fake)

    raw = asyncio.run(review.review(db_session, graph.id, mode="plan_audit",
                                    language="en", text_version=1))
    assert fake.calls, "the agent was never actually called"
    assert raw["outcome"] == "needs_attention"

    rows = review.persist_findings(db_session, graph.id, raw)
    assert len(rows) == 1 and rows[0].status == "open"
    assert rows[0].repair_owner == "detective_director"
    persisted = db_session.query(DetectiveFinding).filter_by(graph_id=graph.id).all()
    assert len(persisted) == 1


def test_full_repair_loop_closes_a_verified_repair(db_session, monkeypatch):
    graph = _graph(db_session, beats=[{"id": "B01"}])
    _scene_fixture(db_session, graph)
    finding = DetectiveFinding(graph_id=graph.id, finding_key="f-dd-1", mode="plan_audit",
                               severity="major", repair_owner="detective_director",
                               observed_problem="missing content binding", scene_key="s1",
                               action_key="a1", status="open")
    db_session.add(finding)
    db_session.commit()

    async def fake_repair(db, graph_id, previous, issues, language, text_version):
        return {"agent_run_id": 999, "output": {}, "counts": {"scenes": 1}}

    monkeypatch.setattr(director, "repair", fake_repair)
    fixed_payload = _plan_audit_payload(graph, findings=[])
    fixed_payload["mode"] = "repair_check"
    fixed_payload["outcome"] = "pass"
    fake = FakeReviewGen([fixed_payload])
    monkeypatch.setattr(review, "get_generation_provider", lambda: fake)

    status = asyncio.run(review.repair_and_recheck_until(
        db_session, finding, language="en", text_version=1, max_attempts=2))
    assert status == "closed"
    db_session.refresh(finding)
    assert finding.status == "closed"
    assert finding.repair_result_json is not None


def test_a_repair_that_introduces_a_regression_opens_a_new_finding_without_closing_silently(
        db_session, monkeypatch):
    graph = _graph(db_session, beats=[{"id": "B01"}])
    _scene_fixture(db_session, graph)
    finding = DetectiveFinding(graph_id=graph.id, finding_key="f-dd-1", mode="plan_audit",
                               severity="major", repair_owner="detective_director",
                               observed_problem="missing content binding", scene_key="s1",
                               action_key="a1", status="open")
    db_session.add(finding)
    db_session.commit()

    async def fake_repair(db, graph_id, previous, issues, language, text_version):
        return {"agent_run_id": 999, "output": {}, "counts": {}}

    monkeypatch.setattr(director, "repair", fake_repair)
    # the recheck finds the ORIGINAL problem gone, but a NEW, different finding introduced by the repair
    regressed = _plan_audit_payload(graph, findings=[
        {"id": "f-new-regression", "scene_id": "s1", "action_id": "a1", "severity": "minor",
         "observed_problem": "the repaired fact card now overlaps the board",
         "repair_owner": "visual_design_director"},
    ])
    regressed["mode"] = "repair_check"
    fake = FakeReviewGen([regressed])
    monkeypatch.setattr(review, "get_generation_provider", lambda: fake)

    status = asyncio.run(review.repair_and_recheck_until(
        db_session, finding, language="en", text_version=1, max_attempts=1))
    assert status == "closed", "the original finding IS resolved and must close"
    new_rows = db_session.query(DetectiveFinding).filter_by(
        graph_id=graph.id, finding_key="f-new-regression").all()
    assert len(new_rows) == 1 and new_rows[0].status == "open", \
        "the regression must be its own open finding, never merged into the closed one"


def test_an_interrupted_repair_call_leaves_a_resumable_finding_not_a_false_close(
        db_session, monkeypatch):
    graph = _graph(db_session, beats=[{"id": "B01"}])
    _scene_fixture(db_session, graph)
    finding = DetectiveFinding(graph_id=graph.id, finding_key="f-dd-1", mode="plan_audit",
                               severity="major", repair_owner="detective_director",
                               observed_problem="missing content binding", scene_key="s1",
                               action_key="a1", status="open")
    db_session.add(finding)
    db_session.commit()

    async def failing_repair(db, graph_id, previous, issues, language, text_version):
        raise RuntimeError("provider timeout")

    monkeypatch.setattr(director, "repair", failing_repair)
    result = asyncio.run(review.apply_repair(db_session, finding, language="en", text_version=1))
    assert result["interrupted"] is True
    db_session.refresh(finding)
    assert finding.status == "repair_in_progress", "never silently closed or lost on a failed call"


def test_exhausted_retries_leave_an_incomplete_state_not_a_false_pass(db_session, monkeypatch):
    graph = _graph(db_session, beats=[{"id": "B01"}])
    _scene_fixture(db_session, graph)
    finding = DetectiveFinding(graph_id=graph.id, finding_key="f-dd-1", mode="plan_audit",
                               severity="major", repair_owner="detective_director",
                               observed_problem="missing content binding", scene_key="s1",
                               action_key="a1", status="open")
    db_session.add(finding)
    db_session.commit()

    async def fake_repair(db, graph_id, previous, issues, language, text_version):
        return {"agent_run_id": 1, "output": {}, "counts": {}}

    monkeypatch.setattr(director, "repair", fake_repair)
    still_broken = _plan_audit_payload(graph)  # the SAME finding id, still reported every time
    still_broken["mode"] = "repair_check"
    fake = FakeReviewGen([still_broken])
    monkeypatch.setattr(review, "get_generation_provider", lambda: fake)

    status = asyncio.run(review.repair_and_recheck_until(
        db_session, finding, language="en", text_version=1, max_attempts=2))
    assert status == "incomplete"
    db_session.refresh(finding)
    assert finding.status == "incomplete", "exhausted retries must never be reported as success"


def test_routing_covers_all_four_repair_owners(db_session, monkeypatch):
    graph = _graph(db_session, beats=[{"id": "B01"}])
    _scene_fixture(db_session, graph)

    async def fake_design_review(db, case_id, **kw):
        return {"outcome": "pass"}

    from app.documentary.detective import dispatch as D
    monkeypatch.setattr(D, "resolve_dispatchable_actions",
                        lambda db, graph_id: [{"action_key": "a1", "tool_id": "fact_card",
                                              "data": {"sentences": ["x"]}, "purpose": "p"}])
    import app.documentary.detective.design_review as DR
    monkeypatch.setattr(DR, "review", fake_design_review)

    for owner in ("visual_design_director", "scheduler_renderer", "narration_workflow"):
        finding = DetectiveFinding(graph_id=graph.id, finding_key=f"f-{owner}", mode="plan_audit",
                                   severity="minor", repair_owner=owner,
                                   observed_problem="x", scene_key="s1", action_key="a1",
                                   status="open")
        db_session.add(finding)
        db_session.commit()
        result = asyncio.run(review.apply_repair(db_session, finding, language="en", text_version=1))
        assert result.get("owner") == owner
        assert not result.get("error")
