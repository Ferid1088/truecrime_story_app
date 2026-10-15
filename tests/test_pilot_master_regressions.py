import asyncio
import json
import time
import uuid

import pytest

from app.agents.story import StoryPipeline
from app.db.models import AgentRun, Case, DocumentaryJob, ProviderCall, StoryVersion
from app.providers.generation.recorded import RecordedE2EGenerationProvider
from app.services.cost_model import documentary_estimate
from app.utils import slugify


@pytest.fixture(autouse=True)
def _no_cross_test_ledger_calibration_pollution(db_session):
    """`_calibrated_master_story_line`/`_calibrated_master_approval_line`
    read ALL ProviderCall rows for a stage across every case (by design: a
    real-world calibration, not a per-case one). `db_session` is a plain,
    uncommitted-rollback-free session (tests/conftest.py), so any
    ProviderCall row a test commits for stage "master_story" or
    "master_approval" otherwise survives into every later test in the
    run and can silently change an unrelated test's calibrated estimate
    (this broke test_visual_production.py/test_visual_direction.py when
    this file's tests ran first alphabetically, back when a stage cost
    ceiling compared that estimate against a hard stop). Delete only the
    rows this test itself added, by id, so real historical data is
    untouched."""
    high_water_mark = db_session.query(ProviderCall.id).order_by(ProviderCall.id.desc()).first()
    start_id = high_water_mark[0] if high_water_mark else 0
    yield
    db_session.rollback()
    db_session.query(ProviderCall).filter(
        ProviderCall.id > start_id,
        ProviderCall.stage.in_(("master_story", "master_approval")),
    ).delete(synchronize_session=False)
    db_session.commit()


def _case(db):
    row = Case(
        canonical_title=f"Pilot regression {uuid.uuid4().hex[:8]}",
        slug=slugify(f"Pilot regression {uuid.uuid4().hex[:8]}"),
        language="en",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _master(db, case, status, failures, gates=None, approved_by=None):
    row = StoryVersion(
        case_id=case.id,
        version=1,
        kind="master",
        language="en",
        narrative_angle="{}",
        story_text="word " * 408,
        status=status,
        approved_by=approved_by,
        critic_notes=json.dumps({
            "quality_gates": {
                "pass": not failures,
                "failures": list(failures),
                **(gates or {}),
            }
        }),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_approve_story_endpoint_sets_ready_and_keeps_critic_notes(db_session, client):
    case = _case(db_session)
    story = _master(db_session, case, "needs_revision", ["grounding", "engagement_below_threshold"])

    r = client.post(
        f"/api/cases/{case.id}/stories/{story.id}/approve",
        json={"approved_by": "alice"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["approved_by"] == "alice"
    assert body["approved_at"] is not None
    db_session.refresh(story)
    assert story.status == "ready"
    assert story.approved_by == "alice"
    notes = json.loads(story.critic_notes)
    assert notes["quality_gates"]["failures"] == ["grounding", "engagement_below_threshold"]

    from app.db.models import OverrideLog
    override = db_session.query(OverrideLog).filter(
        OverrideLog.scope_type == "story_version", OverrideLog.scope_id == str(story.id),
    ).one()
    assert override.actor == "alice"
    assert json.loads(override.old_value_json) == "needs_revision"
    assert json.loads(override.new_value_json) == "ready"


def test_approve_story_endpoint_rejects_already_ready_version(db_session, client):
    case = _case(db_session)
    story = _master(db_session, case, "ready", [])

    r = client.post(f"/api/cases/{case.id}/stories/{story.id}/approve", json={})
    assert r.status_code == 409


def test_approve_story_endpoint_404_for_missing_version(db_session, client):
    case = _case(db_session)
    r = client.post(f"/api/cases/{case.id}/stories/999999/approve", json={})
    assert r.status_code == 404


def test_manually_approved_master_skips_critics_with_no_revision(db_session, monkeypatch):
    # A human can approve a master "as is" over its own critics' objections
    # (control at every stage). The endpoint keeps the original critic
    # scores/problems and just flips status + approved_by — this must be
    # honored as approved, without spending another writer/critic call.
    from app.documentary.jobs import approve_master

    case = _case(db_session)
    approved = _master(
        db_session, case, "ready", ["grounding", "engagement_below_threshold"],
        approved_by="operator",
    )

    async def improve(*args, **kwargs):
        raise AssertionError("a manually approved master must not be revised")

    monkeypatch.setattr(StoryPipeline, "improve", improve)
    version, history = asyncio.run(approve_master(
        db_session, case, approved, target_minutes=3, words_per_minute=150,
    ))

    assert version.id == approved.id
    assert version.status == "ready"
    assert history == []


def test_pilot_approval_passes_its_target_to_revision(db_session, monkeypatch):
    from app.documentary.jobs import approve_master

    case = _case(db_session)
    first = _master(db_session, case, "needs_revision", ["grounding"])
    seen = []

    async def improve(self, db, case_, version, instruction, **kwargs):
        seen.append(kwargs)
        return _master(db, case_, "ready", [])

    monkeypatch.setattr(StoryPipeline, "improve", improve)
    version, _ = asyncio.run(approve_master(
        db_session, case, first, target_minutes=3, words_per_minute=135,
    ))

    assert version.status == "ready"
    assert seen == [{"target_minutes": 3, "words_per_minute": 135}]


def test_length_failure_is_terminal_and_does_not_revise(db_session, monkeypatch):
    from app.documentary.jobs import MasterApprovalError, approve_master

    case = _case(db_session)
    first = _master(
        db_session, case, "needs_revision", ["length_out_of_range"],
        gates={
            "actual_minutes": 3.0,
            "target_minutes": 45.0,
            "allowed_minutes": {"min": 38.3, "max": 51.7},
        },
    )
    calls = []

    async def improve(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("terminal length failure must not be revised")

    monkeypatch.setattr(StoryPipeline, "improve", improve)
    with pytest.raises(MasterApprovalError) as exc:
        asyncio.run(approve_master(
            db_session, case, first, max_redos=2,
            target_minutes=45, words_per_minute=135,
        ))

    assert not calls
    assert exc.value.detail["attempts"][0]["quality_gates"]["actual_minutes"] == 3.0


def test_recorded_provider_keeps_case2_pilot_length_gate_deterministic():
    provider = RecordedE2EGenerationProvider()
    result = asyncio.run(provider.generate_text("writer", "system", "case 2"))
    assert result.provider == "recorded-e2e"

    # Stored pilot attempt: 408 words. At the measured English 150 wpm rate it is
    # 3.0 minutes, which must be judged against the pilot target, not 45 min.
    gates = StoryPipeline()._evaluate_gates(
        "word " * 408, "en", 3, engagement=95, wpm=150,
    )
    assert gates["actual_minutes"] == 2.7
    assert gates["allowed_minutes"] == {"min": 2.6, "max": 3.5}
    assert "length_out_of_range" not in gates["failures"]


def test_case2_v5_story_passes_the_pilot_length_gate():
    # Live job 4 (case 2) rejected story version 5 (445 words) as
    # length_out_of_range with target_words=6075 — a 45-minute master
    # target, not the job's own 3-minute pilot target. That target_words
    # value traces to StoryPipeline.improve() being called without
    # target_minutes/words_per_minute (its pre-b6b9632 behavior for a
    # caller outside a documentary job) — the documentary pipeline itself
    # (approve_master -> produce -> improve) already threads target_minutes
    # through correctly (see test_pilot_approval_passes_its_target_to_revision
    # above). At the job's real target (3 min, English 150 wpm) 445 words is
    # 2.97 minutes, inside the allowed 2.55-3.45 minute band.
    gates = StoryPipeline()._evaluate_gates(
        "word " * 445, "en", 3, engagement=95, wpm=150,
    )
    assert gates["words"] == 445
    assert gates["target_words"] == 450
    assert gates["actual_minutes"] == 3.0  # 445 / 150 wpm = 2.97, rounds to 3.0
    assert gates["allowed_minutes"] == {"min": 2.6, "max": 3.5}
    assert "length_out_of_range" not in gates["failures"]


def test_master_story_estimate_uses_provider_ledger_calibration(db_session):
    case = _case(db_session)
    for cost, input_tokens, output_tokens in (
        (0.01, 100, 200), (0.02, 200, 400), (0.03, 300, 600),
    ):
        db_session.add(ProviderCall(
            host="budget-ledger", method="CALL", path="/master_story",
            status=200, case_id=case.id, stage="master_story",
            model="gemini-3.8-flash", tokens_in=input_tokens,
            tokens_out=output_tokens, cost_eur=cost,
        ))
    db_session.commit()

    estimate = documentary_estimate(db_session, case, ["en"], pilot_seconds=180)
    line = next(row for row in estimate["lines"] if row["stage"] == "master_story")

    assert estimate["metrics"]["master_story_calibration"]["source"] == "provider_calls"
    assert estimate["metrics"]["master_story_calibration"]["observations"] == 3
    assert line["model"] == "ledger-calibrated"
    assert line["estimated_cost_eur"] == pytest.approx(0.14, abs=0.0001)
    assert line["estimated_cost_eur"] > 0.1


def test_record_provider_call_never_raises_a_stage_cost_ceiling(db_session):
    # The per-stage cost-ceiling formula (0.05 + 0.05 x target_minutes) is
    # removed: recording a provider call must never stop a job over a
    # per-stage figure. Only the case-wide actual-spend cap can stop a run
    # (see tests/test_budget.py) — and only the next call, never this one.
    from app.services.tracking import _record_provider_call

    case = _case(db_session)
    job = DocumentaryJob(
        case_id=case.id, mode="pilot", pilot_seconds=180,
        target_minutes=3, status="running",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add(ProviderCall(
        host="budget-ledger", method="CALL", path="/master_story",
        status=200, case_id=case.id, stage="master_story",
        model="gemini-3.8-flash", cost_eur=0.15,
        documentary_job_id=job.id,
    ))
    db_session.commit()
    run = AgentRun(
        case_id=case.id, documentary_job_id=job.id,
        pipeline_stage="master_story", agent_name="Writer", status="completed",
        provider="gemini", model="gemini-3.8-flash", estimated_cost_usd=0.1,
    )
    db_session.add(run)
    db_session.commit()

    _record_provider_call(db_session, run)

    assert db_session.query(ProviderCall).filter(
        ProviderCall.case_id == case.id, ProviderCall.stage == "master_story",
    ).count() == 2


def test_a_20s_pilot_stage_is_never_blocked_by_cost_estimate(db_session):
    # Case 2, job #5: "Pilot master_approval stopped before run: estimated
    # cost €0.12 exceeds stage ceiling €0.07" — that per-stage ceiling
    # formula (0.05 + 0.05 x target_minutes, ~0.067 EUR for a 20s/0.33min
    # pilot) is gone. A stage runs as long as the case's actual recorded
    # spend has not reached its EUR cap, no estimate involved.
    from app.documentary.pipeline.base import Stage, StageRunner

    case = _case(db_session)
    job = DocumentaryJob(
        case_id=case.id, mode="pilot", pilot_seconds=20,
        target_minutes=20 / 60, status="running",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    ran = []

    class Dummy(Stage):
        name = "master_approval"

        async def run(self):
            ran.append(True)
            return {"ok": True}

    result = asyncio.run(StageRunner(db_session, job).execute(Dummy(None)))
    assert ran == [True]
    assert result == {"ok": True}


def test_record_provider_call_keeps_the_agent_run_duration(db_session):
    from app.services.tracking import _record_provider_call

    case = _case(db_session)
    run = AgentRun(
        case_id=case.id, pipeline_stage="master_approval", agent_name="Writer",
        status="completed", provider="apimaster", model="claude-sonnet-5-5",
        input_tokens=10140, output_tokens=6289, duration_ms=334126,
    )
    db_session.add(run)
    db_session.commit()

    _record_provider_call(db_session, run)

    call = (db_session.query(ProviderCall)
            .filter(ProviderCall.case_id == case.id, ProviderCall.stage == "master_approval")
            .one())
    assert call.duration_ms == 334126


def test_master_approval_estimate_line_reflects_real_revision_spend(db_session):
    # Case 2 / job 4 ran two full revision cycles (8 real provider calls,
    # ~€0.27) but had no estimate line at all for "master_approval" — the
    # estimate-vs-actual table only had a "master_story" line, which this
    # job correctly never ran (it reused an existing master), so the real
    # spend was invisible on the job page. There must be a dedicated line.
    from app.services.cost_model import documentary_estimate, estimate_vs_actual_report

    case = _case(db_session)
    for cost, input_tokens, output_tokens in (
        (0.074, 10140, 6289), (0.006, 8292, 4041), (0.003, 4426, 2087),
        (0.063, 7153, 5670),
    ):
        db_session.add(ProviderCall(
            host="budget-ledger", method="CALL", path="/master_approval",
            status=200, case_id=case.id, stage="master_approval",
            model="claude-sonnet-5-5", tokens_in=input_tokens,
            tokens_out=output_tokens, cost_eur=cost,
        ))
    db_session.commit()

    estimate = documentary_estimate(db_session, case, ["en"], pilot_seconds=180)
    line = next(row for row in estimate["lines"] if row["stage"] == "master_approval")
    assert line["estimated_cost_eur"] > 0

    job = DocumentaryJob(
        case_id=case.id, mode="pilot", pilot_seconds=180, target_minutes=3,
        status="failed", from_zero=False,
        languages_json=json.dumps(["en"]),
        stages_json=json.dumps([{"name": "master_approval", "status": "failed", "detail": None}]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    for call in db_session.query(ProviderCall).filter(ProviderCall.case_id == case.id):
        call.documentary_job_id = job.id
    db_session.commit()

    report = estimate_vs_actual_report(db_session, job)
    row = next(r for r in report["rows"] if r["stage"] == "master_approval")
    assert row["status"] == "failed"
    assert row["actual_eur"] == pytest.approx(0.146, abs=0.001)


def test_pilot_stage_time_limits_scale_with_target_and_timeout_is_inactivity_based():
    from app.core.ai_config import ai_config
    from app.services.cost_model import configured_stage_time_budget_seconds

    # A single measured master writer/rewriter call takes 330-400s (job 4,
    # case 2: AgentRun ids 83 and 88 — 334.1s and 402.7s). The provider does
    # not stream, so the generation-provider timeout and the stage budget
    # must both clear the slowest observed call with margin, or every pilot
    # approval fails before a single revision can even run. (Time/inactivity
    # timeouts are not cost limits and are kept as-is.)
    SLOWEST_MEASURED_WRITER_CALL_S = 403
    assert ai_config.generation_provider().inactivity_timeout_s > SLOWEST_MEASURED_WRITER_CALL_S
    assert configured_stage_time_budget_seconds("master_approval", 3) > SLOWEST_MEASURED_WRITER_CALL_S
    assert configured_stage_time_budget_seconds("master_story", 3) > SLOWEST_MEASURED_WRITER_CALL_S
    assert configured_stage_time_budget_seconds("master_approval", 3) == pytest.approx(1050)
    assert configured_stage_time_budget_seconds("master_approval", 45) == pytest.approx(11550)


def test_stage_time_budget_error_is_structured_before_next_call():
    from app.services.tracking import _stage_time_error

    error = _stage_time_error({
        "pipeline_stage": "master_approval",
        "stage_budget_seconds": 1050,
        "stage_started_monotonic": time.monotonic() - 1051,
    }, 2)

    assert error is not None
    assert error.detail["stage_time_budget_exceeded"] is True
    assert error.detail["budget_seconds"] == 1050


def test_job_payload_exposes_active_provider_call_progress(db_session):
    from app.core.ai_config import ai_config
    from app.documentary.jobs import job_dict
    from app.services.tracking import documentary_run_context, track_run

    case = _case(db_session)
    job = DocumentaryJob(
        case_id=case.id, mode="pilot", pilot_seconds=180,
        target_minutes=3, status="running",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    with documentary_run_context(
        job.id, case.id, None, "master_approval", 150, time.monotonic()
    ), track_run(db_session, case.id, "Writer"):
        progress = job_dict(db_session.get(DocumentaryJob, job.id))["result"]["call_progress"]
        assert progress["call_number"] == 1
        assert progress["provider"] == ai_config.providers.generation
        assert progress["elapsed_seconds"] >= 0
        assert progress["stage_budget_seconds"] == 150

    assert "call_progress" not in job_dict(db_session.get(DocumentaryJob, job.id))["result"]
