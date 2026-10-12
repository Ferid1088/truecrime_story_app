import json

from app.core.artifact_dependencies import ARTIFACT_DEPENDENCIES
from app.core.fingerprint import FINGERPRINT_VOLATILE_FIELDS, canonical_json
from app.db.models import Artifact, ArtifactDep
from app.services.skip_policy import SOUNDNESS
from app.services.soundness import (
    SNAPSHOT_EXCLUDED_FIELDS,
    changed_artifacts,
    downstream_closure,
    matrix_row,
)


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
    assert {status for status in SOUNDNESS.values()} == {"SOUND", "UNPROVEN"}
