from __future__ import annotations

import json

import pytest

from app.db.models import Artifact, ArtifactDep, Case, DocumentaryJob, EditLog, StoryVersion
from app.services.stage_control import StageControlError, StageControlService


def _fixture(db):
    suffix = db.query(Case).count()
    case = Case(canonical_title=f"Stage control {suffix}", slug=f"stage-control-{suffix}", language="en")
    db.add(case)
    db.flush()
    versions = {}
    for language in ("en", "de", "fa", "ar"):
        version = StoryVersion(case_id=case.id, version=1, kind="localized", language=language,
                               narrative_angle="angle", story_text=f"old {language}",
                               narrative_structure=json.dumps({"sections": [{"text": f"old {language}"}]}),
                               status="approved")
        db.add(version)
        versions[language] = version
    db.flush()
    stages = [{"name": f"spoken:{language}", "status": "done",
               "detail": {"version_id": versions[language].id}}
              for language in ("en", "de", "fa", "ar")]
    job = DocumentaryJob(case_id=case.id, languages_json='["en","de","fa","ar"]',
                         status="completed", stages_json=json.dumps(stages))
    db.add(job)
    db.flush()
    roots = {}
    for language in ("en", "de", "fa", "ar"):
        row = Artifact(id=f"spoken-{language}-{case.id}", case_id=case.id, stage_job_id=job.id,
                       type="spoken_version", language=language,
                       scope_json=json.dumps({"stage": "spoken", "language": language}),
                       input_hash=f"in-{language}", output_hash=f"out-{language}", status="fresh")
        db.add(row)
        roots[language] = row
    child = Artifact(id=f"voice-de-{case.id}", case_id=case.id, stage_job_id=job.id,
                     type="voice_takes", language="de",
                     scope_json=json.dumps({"stage": "voice", "language": "de"}),
                     input_hash="voice-in", output_hash="voice-out", status="fresh")
    db.add(child)
    db.flush()
    db.add(ArtifactDep(artifact_id=child.id, depends_on_id=roots["de"].id))
    db.commit()
    return case, job, roots


def test_stage_control_actions_and_exact_language_scope(db_session, monkeypatch):
    case, _job, roots = _fixture(db_session)
    service = StageControlService(db_session)
    state = service.state(case.id, "spoken:de")
    assert state["status"] == "done"
    assert set(("redo_with_prompt", "edit", "run_automatically")) == set(
        state["available_actions"])

    preview = service.preview(case.id, "spoken:de", "edit",
                              edit={"language": "de", "chapter_index": 0,
                                    "chapter_text": "new German text"})
    untouched_hashes = {language: roots[language].output_hash for language in ("en", "fa", "ar")}
    assert {item["language"] for item in preview["estimated_scope"]} == {"de"}
    assert preview["estimated_provider_calls"]["total"] == 2
    monkeypatch.setattr("app.documentary.jobs.launch", lambda _job_id: None)
    result = service.apply(preview["preview_id"])
    assert result["status"] == "queued"
    assert db_session.query(StoryVersion).filter_by(case_id=case.id, language="de").one().story_text == "new German text"
    assert {db_session.query(StoryVersion).filter_by(case_id=case.id, language=language).one().story_text
            for language in ("en", "fa", "ar")} == {"old en", "old fa", "old ar"}
    assert {language: db_session.get(Artifact, roots[language].id).output_hash
            for language in ("en", "fa", "ar")} == untouched_hashes
    assert {db_session.query(Artifact).filter_by(case_id=case.id, language=language).one().status
            for language in ("en", "fa", "ar")} == {"fresh"}


@pytest.mark.parametrize("action", ["continue", "redo_with_prompt", "run_automatically"])
def test_stage_control_previews_each_non_edit_action(db_session, action):
    case, _job, _roots = _fixture(db_session)
    service = StageControlService(db_session)
    if action == "run_automatically":
        preview = service.preview(case.id, "spoken:de", action)
        assert preview["artifact_ids"] == []
    else:
        preview = service.preview(case.id, "spoken:de", action, prompt="make it clearer")
        assert preview["artifact_ids"]


def test_stage_control_refuses_changed_inputs_and_pending_lock(db_session):
    case, _job, roots = _fixture(db_session)
    service = StageControlService(db_session)
    preview = service.preview(case.id, "spoken:de", "redo_with_prompt")
    roots["de"].input_hash = "changed"
    db_session.commit()
    with pytest.raises(StageControlError, match="inputs changed"):
        service.apply(preview["preview_id"])

    db_session.rollback()
    case, _job, roots = _fixture(db_session)
    db_session.add(EditLog(scope_id=roots["de"].id, scope_preview_json=json.dumps({
        "artifact_ids": [roots["de"].id],
    }), status="previewed"))
    db_session.commit()
    assert service.state(case.id, "spoken:de")["blocked_actions"]["continue"] == "pending user edit or lock"
    with pytest.raises(StageControlError, match="pending user edit"):
        service.preview(case.id, "spoken:de", "redo_with_prompt")


def test_stage_control_automatic_mode_is_persistent(db_session):
    case, _job, _roots = _fixture(db_session)
    service = StageControlService(db_session)
    assert service.set_automatic(case.id, "spoken:de", True)["automatic"] is True
    assert service.state(case.id, "spoken:de")["automatic"] is True


@pytest.mark.parametrize("stage", ["research", "master_story"])
def test_pre_artifact_redo_has_prompt_and_downstream_scope(db_session, monkeypatch, stage):
    case = Case(canonical_title=f"From zero {stage}", slug=f"from-zero-{stage}", language="en")
    db_session.add(case)
    db_session.flush()
    job = DocumentaryJob(
        case_id=case.id, from_zero=True, status="completed",
        stages_json=json.dumps([
            {"name": "research", "status": "done", "detail": None},
            {"name": "master_story", "status": "done", "detail": None},
            {"name": "spoken:en", "status": "done", "detail": None},
        ]),
    )
    db_session.add(job)
    db_session.commit()
    service = StageControlService(db_session)
    preview = service.preview(case.id, stage, "redo_with_prompt", prompt="Focus on the timeline.")
    assert preview["prompt"] == "Focus on the timeline."
    assert preview["estimated_scope"]
    assert any(item["will_change"] for item in preview["estimated_scope"])
    assert any(item["may_change"] for item in preview["estimated_scope"])
    monkeypatch.setattr("app.documentary.jobs.launch", lambda _job_id: None)
    result = service.apply(preview["preview_id"])
    assert result["status"] == "queued"
    assert result["changed_outside_scope"] == []


def test_stage_control_api_contract(db_session, client, monkeypatch):
    case, _job, _roots = _fixture(db_session)
    preview = client.post(f"/api/documentary/{case.id}/stage-control/preview", json={
        "stage": "spoken:de", "action": "redo_with_prompt", "prompt": "clearer",
    })
    assert preview.status_code == 200, preview.text
    assert preview.json()["estimated_scope"]
    monkeypatch.setattr("app.documentary.jobs.launch", lambda _job_id: None)
    applied = client.post(f"/api/documentary/{case.id}/stage-control/apply", json={
        "preview_id": preview.json()["preview_id"],
    })
    assert applied.status_code == 200, applied.text
    preview_id = preview.json()["preview_id"]
    result = client.get(f"/api/documentary/{case.id}/stage-control/result/{preview_id}")
    assert result.status_code == 200, result.text
    assert result.json()["result"]["status"] == "queued"
    assert "may_change" in result.json()["preview"]["estimated_scope"][0]
    audit = client.get(f"/api/documentary/{case.id}/stage-control/audit")
    assert audit.status_code == 200, audit.text
    assert any(item["rule_id"] == "STAGE_CONTROL_APPLIED" for item in audit.json())


def test_research_redo_scope_includes_selected_stage(db_session):
    case = Case(canonical_title="Research redo", slug="research-redo", language="en")
    db_session.add(case)
    db_session.flush()
    job = DocumentaryJob(case_id=case.id, languages_json='["en"]', status="completed",
                         stages_json=json.dumps([
                             {"name": "research", "status": "done", "detail": {}},
                             {"name": "master_story", "status": "done", "detail": {}},
                         ]))
    db_session.add(job)
    db_session.flush()
    root = Artifact(id="research-root", case_id=case.id, stage_job_id=job.id,
                    type="research_result", scope_json=json.dumps({"stage": "research"}),
                    input_hash="in", output_hash="out", status="fresh", version=2)
    child = Artifact(id="master-child", case_id=case.id, stage_job_id=job.id,
                     type="master_story", scope_json=json.dumps({"stage": "master_story"}),
                     input_hash="in2", output_hash="out2", status="fresh", version=1)
    db_session.add_all([root, child])
    db_session.add(ArtifactDep(artifact_id=child.id, depends_on_id=root.id))
    db_session.commit()
    preview = StageControlService(db_session).preview(case.id, "research", "redo_with_prompt",
                                                      prompt="Focus on the timeline.")
    assert "research-root" in preview["artifact_ids"]
    assert any(item["id"] == "research-root" and item["will_change"]
               for item in preview["estimated_scope"])
