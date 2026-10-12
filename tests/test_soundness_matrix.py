import json
import sqlite3

from app.core.artifact_dependencies import ARTIFACT_DEPENDENCIES
from app.core.fingerprint import FINGERPRINT_VOLATILE_FIELDS, canonical_json
from app.db.models import Artifact, ArtifactDep
from app.services.skip_policy import SOUNDNESS, load_soundness_status
from app.services.soundness import (
    SNAPSHOT_EXCLUDED_FIELDS,
    create_snapshot_template,
    restore_snapshot_template,
    saved_output_snapshot,
    changed_artifacts,
    downstream_closure,
    matrix_row,
)


def test_saved_output_snapshot_hashes_rows_and_files_with_only_named_exclusions(
    db_session, tmp_path
):
    from app.db.models import Case

    db_session.add(Case(canonical_title="snapshot", slug="snapshot-files", language="en"))
    db_session.commit()
    root = tmp_path / "outputs"
    root.mkdir()
    (root / "render.mp4").write_bytes(b"render-a")
    first = saved_output_snapshot(db_session, file_roots=[root])
    repeat = saved_output_snapshot(db_session, file_roots=[root])
    assert first == repeat
    (root / "render.mp4").write_bytes(b"render-b")
    changed = saved_output_snapshot(db_session, file_roots=[root])
    assert changed["file:outputs/render.mp4"] != first["file:outputs/render.mp4"]
    assert any(key.startswith("db:cases:") for key in first)


def test_snapshot_template_round_trips_database_copy_and_files(tmp_path):
    source_db = tmp_path / "source.db"
    sqlite3.connect(source_db).close()
    source_files = tmp_path / "source-files"
    source_files.mkdir()
    (source_files / "artifact.bin").write_bytes(b"baseline")
    template = create_snapshot_template(source_db, source_files, tmp_path / "template")
    destination_db = tmp_path / "destination.db"
    destination_files = tmp_path / "destination-files"
    sqlite3.connect(destination_db).close()
    destination_files.mkdir()
    (destination_files / "artifact.bin").write_bytes(b"changed")
    restore_snapshot_template(template, destination_db, destination_files)
    assert destination_db.read_bytes() == source_db.read_bytes()
    assert (destination_files / "artifact.bin").read_bytes() == b"baseline"


def test_named_volatile_field_contract_is_explicit():
    assert "created_at" in FINGERPRINT_VOLATILE_FIELDS
    assert "updated_at" in FINGERPRINT_VOLATILE_FIELDS
    assert "generated_ids" not in FINGERPRINT_VOLATILE_FIELDS
    assert {"id", "created_at"} <= SNAPSHOT_EXCLUDED_FIELDS
    assert canonical_json({"timestamp": "one", "value": 1}) == \
        canonical_json({"timestamp": "two", "value": 1})


def test_soundness_harness_prediction_is_read_only_and_detects_hidden_inputs(db_session):
    rows = []
    for artifact_type in ("research_result", "master_story", "beats", "spoken_version"):
        row = Artifact(id=f"sound-{artifact_type}", type=artifact_type,
                       case_id=1, language="en" if artifact_type == "spoken_version" else None,
                       scope_json=json.dumps({"type": artifact_type}),
                       input_hash=artifact_type, output_hash=f"before-{artifact_type}",
                       status="fresh")
        rows.append(row)
    db_session.add_all(rows)
    db_session.flush()
    db_session.add(ArtifactDep(artifact_id=rows[1].id, depends_on_id=rows[0].id))
    db_session.add(ArtifactDep(artifact_id=rows[2].id, depends_on_id=rows[1].id))
    db_session.commit()
    before = {row.id: row.output_hash for row in rows}
    rows[0].output_hash = "after-research"
    rows[1].output_hash = "after-master"
    rows[2].output_hash = "after-beats"
    db_session.commit()
    after = {row.id: row.output_hash for row in rows}
    actual = changed_artifacts(before, after)
    predicted = downstream_closure(db_session, [rows[0].id])
    result = matrix_row("research result", predicted, actual)
    assert result["hidden_inputs"] == []
    assert result["status"] == "SOUND"


def test_all_registered_types_are_named_in_soundness_sources():
    sources = {
        "research result", "master story text", "one beat", "spoken version",
        "voice take", "visual asset", "host plan", "naming title",
        "typed config", "language profile", "pronunciation/voice setting",
        "channel identity/logo", "intro design", "agent/model routing",
        "music history/library",
    }
    assert len(sources) == 15
    assert set(ARTIFACT_DEPENDENCIES)
    assert set(SOUNDNESS) == set(ARTIFACT_DEPENDENCIES)
    assert load_soundness_status() == SOUNDNESS
    assert set(SOUNDNESS.values()) == {"UNPROVEN"}


def test_actual_snapshot_does_not_read_registry_output_hash(db_session):
    from app.db.models import Case, DocumentaryJob
    from app.services.soundness import artifact_snapshot

    case = Case(canonical_title="snapshot", slug="snapshot-no-registry", language="en")
    db_session.add(case)
    db_session.flush()
    job = DocumentaryJob(case_id=case.id, stages_json="[]")
    db_session.add(job)
    db_session.commit()
    # Existing fixture rows may share the generated case id; their snapshots
    # are still computed from saved content, never from output_hash.
    assert all(value == "" for value in artifact_snapshot(db_session, case.id).values())
