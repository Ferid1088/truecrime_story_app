import json

from app.db.models import Artifact, Case, DocumentaryJob, PolicyEvent
from app.services.artifacts import ArtifactRegistry


def test_scope_preview_is_read_only_and_classifies_rebuilds(db_session, client, monkeypatch):
    suffix = str(id(db_session))
    case = Case(canonical_title="Preview case", slug=f"preview-{suffix}", language="en")
    db_session.add(case)
    db_session.flush()
    job = DocumentaryJob(case_id=case.id, languages_json='["en"]')
    db_session.add(job)
    db_session.flush()
    root = Artifact(id=f"preview-root-{suffix}", case_id=case.id, stage_job_id=job.id,
                    type="master_story", scope_json=json.dumps({"stage": "master_story"}),
                    input_hash="root", output_hash="root", status="fresh")
    child = Artifact(id=f"preview-child-{suffix}", case_id=case.id, stage_job_id=job.id,
                     type="beats", scope_json=json.dumps({"stage": "blueprint"}),
                     input_hash="child", output_hash="child", status="fresh")
    db_session.add_all([root, child])
    db_session.commit()
    before = db_session.query(PolicyEvent).count()
    monkeypatch.setattr("app.services.skip_policy.SkipPolicy.reusable_reasons", lambda *args: [])
    preview = ArtifactRegistry(db_session).scope_preview(
        [root.id, child.id], changed_artifact_ids=[root.id])
    after = db_session.query(PolicyEvent).count()
    assert before == after
    assert preview["estimated_rebuild"]["total"] == 1
    assert preview["artifacts"][0]["will_rerun"] is True
    assert preview["artifacts"][0]["unproven_will_rerun"] is False
    response = client.post(
        f"/api/documentary/{case.id}/artifacts/scope-preview",
        json={"artifact_ids": [root.id, child.id], "changed_artifact_ids": [root.id]},
    )
    assert response.status_code == 200
    print("SCOPE_PREVIEW_RESPONSE", json.dumps(response.json(), sort_keys=True))
