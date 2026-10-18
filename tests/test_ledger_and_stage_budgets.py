import asyncio
import json
import time
import uuid

import pytest

from app.db.models import AgentRun, Case, DocumentaryJob, ProviderCall, StoryVersion
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
        kind="final",
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




def test_stage_time_limits_scale_with_target_and_the_timeout_is_an_inactivity_timeout(monkeypatch):
    from app.core.ai_config import ScaledStageTimeBudgetConfig, ai_config
    from app.services.cost_model import configured_stage_time_budget_seconds

    # A single long writer call takes minutes; the models that stream reset the read timeout with
    # every chunk, so the timeout is an inactivity timeout and must clear a quiet reasoning phase.
    assert ai_config.generation_provider().inactivity_timeout_s >= 900
    assert ai_config.capabilities_of("gpt-6.1-sol").streaming
    monkeypatch.setitem(ai_config.cost_control.stage_time_budgets, "demo",
                        ScaledStageTimeBudgetConfig(base_seconds=300, seconds_per_target_minute=250))
    assert configured_stage_time_budget_seconds("demo", 3) == pytest.approx(1050)
    assert configured_stage_time_budget_seconds("demo:de", 45) == pytest.approx(11550)
    assert configured_stage_time_budget_seconds("no_such_stage", 45) is None


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
