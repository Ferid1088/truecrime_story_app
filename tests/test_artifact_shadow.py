import json
import uuid

from app.db.models import (Artifact, ArtifactDep, Case, EditorialBlueprint, StoryVersion,
                           VisualAsset, VisualPlan)
from app.core.ai_config import ai_config
from app.db.models import Artifact, DocumentaryJob
from app.documentary.jobs import plan_stages
from app.services.artifact_shadow import (STAGE_ARTIFACT_TYPES, register_stage_outputs,
                                          registry_consistency)


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


def test_shadow_registers_each_visual_asset_and_only_used_assets_are_dependencies(
        db_session, monkeypatch):
    monkeypatch.setattr(ai_config.artifacts, "shadow_mode", True)
    suffix = uuid.uuid4().hex[:8]
    case = Case(canonical_title=f"Asset test {suffix}", slug=f"asset-test-{suffix}", language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="master", narrative_angle="test",
                         language="en", story_text="test", status="approved")
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(case_id=case.id, story_version_id=story.id,
                                   status="valid", blueprint_json="{}")
    db_session.add(blueprint)
    db_session.flush()
    job = DocumentaryJob(case_id=case.id, languages_json=json.dumps(["en"]), stages_json="[]")
    db_session.add(job)
    db_session.flush()
    a = VisualAsset(case_id=case.id, asset_code="TEST_ASSET_A")
    b = VisualAsset(case_id=case.id, asset_code="TEST_ASSET_B")
    db_session.add_all([a, b])
    db_session.flush()
    plan = VisualPlan(case_id=case.id, blueprint_id=blueprint.id,
                      plan_json=json.dumps({"shots": [{"asset_id": a.asset_code}]}))
    db_session.add(plan)
    db_session.commit()
    register_stage_outputs(db_session, job, "visual_plan", None,
                           {"visual_plan_id": plan.id}, {})
    job.stages_json = json.dumps([{"name": "visual_plan", "detail": {
        "visual_plan_id": plan.id}}])
    db_session.commit()
    rows = db_session.query(Artifact).filter_by(stage_job_id=job.id,
                                                type="visual_asset").all()
    assert {json.loads(row.scope_json)["asset_id"] for row in rows} == {
        a.asset_code, b.asset_code}
    visual_plan = db_session.query(Artifact).filter_by(
        stage_job_id=job.id, type="visual_plan").one()
    edge_ids = {edge.depends_on_id for edge in db_session.query(ArtifactDep).filter_by(
        artifact_id=visual_plan.id).all()}
    assert len(edge_ids & {row.id for row in rows}) == 1
    assert registry_consistency(db_session, job.id) == []
    plan.plan_json = json.dumps({"shots": [{"asset_id": b.asset_code}]})
    db_session.commit()
    assert registry_consistency(db_session, job.id)
