import asyncio
import json
import uuid

import pytest

from app.agents.story import StoryPipeline
from app.db.models import AgentRun, Case, DocumentaryJob, ProviderCall, StoryVersion
from app.providers.generation.recorded import RecordedE2EGenerationProvider
from app.services.cost_model import documentary_estimate
from app.utils import slugify


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


def _master(db, case, status, failures, gates=None):
    row = StoryVersion(
        case_id=case.id,
        version=1,
        kind="master",
        language="en",
        narrative_angle="{}",
        story_text="word " * 408,
        status=status,
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


def test_pilot_master_story_stage_ceiling_stops_after_recorded_spend(db_session):
    from app.services.budget import StageCostCeilingExceeded
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

    with pytest.raises(StageCostCeilingExceeded) as exc:
        _record_provider_call(db_session, run)

    assert exc.value.detail["cost_ceiling_exceeded"] is True
    assert exc.value.detail["ceiling_eur"] == pytest.approx(0.20)
    assert exc.value.detail["spent_eur"] > 0.20
