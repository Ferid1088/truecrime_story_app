from app.db.models import Artifact, ArtifactDep, EditLog


def test_artifact_table_models_have_required_columns():
    assert {"id", "case_id", "production_id", "language", "type", "scope_json", "input_hash",
            "output_hash", "output_ref", "status", "version", "created_by", "edit_id",
            "stage_job_id", "created_at", "updated_at"} <= set(Artifact.__table__.columns.keys())
    assert {"artifact_id", "depends_on_id"} <= set(ArtifactDep.__table__.columns.keys())
    assert {"instruction_text", "intent_json", "scope_preview_json", "parent_edit_id"} <= set(EditLog.__table__.columns.keys())
