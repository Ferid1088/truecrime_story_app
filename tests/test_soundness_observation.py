import json

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Artifact, Case
from soundness_observation import observe


def test_observation_reads_saved_outputs_never_registry_or_output_hash(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Case(canonical_title="one", slug="one", language="en", case_uid="one"))
        db.add(Artifact(id="untrusted", case_id=1, type="naming", language=None,
                        scope_json="{}", input_hash="irrelevant", output_hash="wrong", status="fresh"))
        db.commit()

        def forbid_registry(conn, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith("SELECT"):
                assert " artifacts" not in statement and '"artifacts"' not in statement
                assert "artifact_deps" not in statement

        event.listen(engine, "before_cursor_execute", forbid_registry)
        before = observe(db, tmp_path)
        db.query(Artifact).filter_by(id="untrusted").update({"output_hash": "different"})
        db.commit()
        assert observe(db, tmp_path) == before
        db.query(Case).filter_by(id=1).update({"canonical_title": "two"})
        db.commit()
        after = observe(db, tmp_path)
        assert [k for k in before["hashes"] if before["hashes"][k] != after["hashes"][k]] == ["naming:shared"]
    engine.dispose()


def test_observation_captures_file_bytes_and_rejects_unowned_files(tmp_path):
    import pytest

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    path = tmp_path / "cases/1/renders/de/render.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"one")
    with Session(engine) as db:
        before = observe(db, tmp_path)
        path.write_bytes(b"two")
        after = observe(db, tmp_path)
        assert [k for k in before["hashes"] if before["hashes"][k] != after["hashes"][k]] == ["final_render:de"]
        (tmp_path / "unexpected.json").write_text(json.dumps({"output": "must not disappear"}))
        with pytest.raises(AssertionError, match="Unowned output"):
            observe(db, tmp_path)
    engine.dispose()
