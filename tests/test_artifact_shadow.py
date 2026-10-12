import json

from app.core.ai_config import ai_config
from app.db.models import Artifact, DocumentaryJob
from app.documentary.jobs import plan_stages
from app.services.artifact_shadow import STAGE_ARTIFACT_TYPES, register_stage_outputs


def test_shadow_registration_is_best_effort_and_write_only(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.artifacts, "shadow_mode", True)
    job = DocumentaryJob(case_id=1, languages_json=json.dumps(["en"]), stages_json="[]")
    db_session.add(job)
    db_session.commit()
    register_stage_outputs(db_session, job, "blueprint", None, {"blueprint_id": 1}, {})
    row = db_session.query(Artifact).filter(Artifact.stage_job_id == job.id).one()
    assert row.type == "beats" and row.input_hash and json.loads(row.scope_json)["language"] is None


def test_shadow_registration_can_be_disabled(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.artifacts, "shadow_mode", False)
    job = DocumentaryJob(case_id=1, languages_json=json.dumps(["en"]), stages_json="[]")
    db_session.add(job)
    db_session.commit()
    register_stage_outputs(db_session, job, "blueprint", None, {}, {})
    assert db_session.query(Artifact).filter(Artifact.stage_job_id == job.id).count() == 0


def test_every_planned_stage_has_shadow_coverage(monkeypatch):
    monkeypatch.setattr(ai_config.host, "enabled", False)
    monkeypatch.setattr(ai_config.avatar, "enabled", False)
    stages = plan_stages(["en", "de"])
    covered = set(STAGE_ARTIFACT_TYPES)
    assert {entry["name"].split(":", 1)[0] for entry in stages} <= covered
    assert set(STAGE_ARTIFACT_TYPES["visual_plan"]) >= {"visual_shots", "visual_plan"}
    assert set(STAGE_ARTIFACT_TYPES["voice"]) >= {"voice_takes", "audio_timing", "music_cues"}
