"""The Film Experience Reviewer is told the exact finding field names, and a finding without its problem text is
refused (found by the real B13-B20 run, C3: the model wrote `finding_id`/`scene_ids`, and an earlier run's findings
were stored with an empty observed_problem because nothing required it). Real PostgreSQL; no model call."""

from __future__ import annotations

from app.documentary.detective import review as R
from app.documentary.detective import review_schema as RS
from app.documentary.memory import add_narration_unit
from tests.test_detective_director import _graph


def _raw(**finding):
    base = {"id": "f1", "severity": "major", "repair_owner": "narration_workflow",
            "observed_problem": "He is never named."}
    return {"schema_version": "1.0", "run_id": "r", "film_id": "f", "language": "en", "text_version": 1,
            "mode": "plan_audit", "outcome": "revise", "inspection_modes": ["plan"],
            "findings": [{**base, **finding}]}


def test_a_well_formed_finding_passes():
    assert RS.validate_review_output(_raw()) == []


def test_a_finding_with_the_models_own_id_field_is_refused():
    finding = {"finding_id": "f1", "severity": "major", "repair_owner": "narration_workflow",
               "observed_problem": "He is never named."}
    raw = _raw()
    raw["findings"] = [finding]
    assert any("missing its stable id" in v for v in RS.validate_review_output(raw))


def test_a_finding_with_no_problem_text_is_refused():
    violations = RS.validate_review_output(_raw(observed_problem=""))
    assert any("no observed_problem" in v and "scene_id" in v for v in violations)
    dropped = _raw()
    del dropped["findings"][0]["observed_problem"]
    assert any("no observed_problem" in v for v in RS.validate_review_output(dropped))


def test_the_reviewer_is_given_the_exact_field_names_and_vocabularies(db_session):
    graph = _graph(db_session, beats=[{"id": "B01"}])
    add_narration_unit(db_session, graph.id, "B01-1", "B01", 1, "en", "Detectives found a handgun.")
    hints = R.build_input(db_session, graph, mode="plan_audit", language="en", text_version=1, run_id="r",
                          film_id="f")["schema_hints"]
    assert hints["finding_fields"] == list(RS.FINDING_FIELDS) and "id" in hints["finding_fields"]
    assert hints["repair_owner_values"] == ["detective_director", "narration_workflow",
                                            "scheduler_renderer", "visual_design_director"]
    assert hints["inspection_mode_values"] == ["plan", "transcript_only"], "plan_audit can claim no more"
    assert "never copy" in hints["finding_format_rule"]
