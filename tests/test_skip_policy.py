import json
from uuid import uuid4

import pytest

from app.core.ai_config import ai_config
from app.core.fingerprint import input_fingerprint
from app.core.policy import PolicyError
from app.db.models import Artifact, Case, DocumentaryJob, EditLog
from app.services.skip_policy import SkipPolicy


def _job(db_session):
    case = Case(canonical_title="Skip policy case", slug=f"skip-policy-case-{uuid4().hex[:8]}",
                language="en", status="new")
    db_session.add(case)
    db_session.flush()
    job = DocumentaryJob(case_id=case.id, languages_json=json.dumps(["en"]),
                         stages_json=json.dumps([{"name": "master_story", "status": "done"}]))
    db_session.add(job)
    db_session.flush()
    source = {}
    row = Artifact(id=f"skip-{job.id}", case_id=case.id, stage_job_id=job.id,
                   type="master_story", language=None,
                   scope_json=json.dumps({"stage": "master_story"}),
                   input_hash=input_fingerprint("master_story", source, "artifact-input-v1"),
                   output_hash="output", status="fresh")
    db_session.add(row)
    db_session.commit()
    return job, row


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(ai_config.production_policies.incremental, "mode", "enforce")
    monkeypatch.setattr(ai_config.production_policies.incremental, "enforced_types", ["master_story"])
    monkeypatch.setattr("app.services.skip_policy._source_payload", lambda *args: {})
    monkeypatch.setattr("app.services.skip_policy._output_hash", lambda *args: "output")
    yield


def test_enforce_skips_only_when_all_hash_and_scope_gates_pass(db_session, configured):
    job, row = _job(db_session)
    result = SkipPolicy(db_session, soundness={"master_story": "SOUND"}).evaluate(job, "master_story")
    assert result.decision.decision == "skip"
    row.output_hash = "tampered"
    db_session.commit()
    assert SkipPolicy(db_session, soundness={"master_story": "SOUND"}).evaluate(
        job, "master_story").decision.decision == "run"
    row.output_hash = "output"
    row.status = "failed"
    db_session.commit()
    assert SkipPolicy(db_session, soundness={"master_story": "SOUND"}).evaluate(
        job, "master_story").decision.decision == "run"


def test_pending_edit_and_dependency_rerun_force_normal_execution(db_session, configured):
    job, row = _job(db_session)
    db_session.add(EditLog(scope_id=row.id, status="running"))
    db_session.commit()
    policy = SkipPolicy(db_session, soundness={"master_story": "SOUND"})
    assert policy.evaluate(job, "master_story").decision.decision == "run"
    db_session.query(EditLog).delete()
    db_session.commit()
    assert policy.evaluate(job, "master_story", dependencies_reran=True).decision.decision == "run"


def test_enforce_refuses_unproven_type(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.production_policies.incremental, "mode", "enforce")
    monkeypatch.setattr(ai_config.production_policies.incremental, "enforced_types", ["beats"])
    with pytest.raises(PolicyError, match="unproven"):
        SkipPolicy(db_session, soundness={"beats": "UNPROVEN"}).validate_configuration()


def test_verify_records_would_skip_but_does_not_authorize_skip(db_session, monkeypatch):
    job, _ = _job(db_session)
    monkeypatch.setattr(ai_config.production_policies.incremental, "mode", "verify")
    monkeypatch.setattr(ai_config.production_policies.incremental, "enforced_types", ["master_story"])
    monkeypatch.setattr("app.services.skip_policy._source_payload", lambda *args: {})
    monkeypatch.setattr("app.services.skip_policy._output_hash", lambda *args: "output")
    result = SkipPolicy(db_session, soundness={"master_story": "SOUND"}).evaluate(job, "master_story")
    assert result.decision.decision == "skip"


def test_stage_runner_enforce_reuses_a_sound_artifact(db_session, configured, monkeypatch):
    import asyncio

    from app.documentary.pipeline.base import Stage, StageRunner
    from app.services import skip_policy

    job, _ = _job(db_session)
    monkeypatch.setitem(skip_policy.SOUNDNESS, "master_story", "SOUND")
    called = []

    class Dummy(Stage):
        name = "master_story"

        async def run(self):
            called.append(True)
            return {"unexpected": True}

    result = asyncio.run(StageRunner(db_session, job).execute(Dummy(None)))
    assert result["reused"] is True
    assert called == []
