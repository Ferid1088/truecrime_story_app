import json
from pathlib import Path

import pytest
import soundness_template as templates
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.soundness import saved_output_snapshot


def test_template_manifest_tracks_config_code_and_mocks():
    before = templates.manifest()
    assert before == templates.manifest()
    assert set(before) == {
        "schema",
        "git_head",
        "code_hash",
        "config_hash",
        "mock_version",
        "languages",
        "incremental_mode",
        "chapter_mock",
    }
    assert all(len(before[key]) == 64 for key in ("code_hash", "config_hash", "mock_version"))


def test_template_stale_manifest_requires_rebuild(tmp_path, monkeypatch):
    identity = {"git_head": "old", "backend": "sqlite"}
    (tmp_path / "manifest.json").write_text(json.dumps({"identity": identity}))
    monkeypatch.setattr(templates, "manifest", lambda: identity)
    assert templates.ensure_template(tmp_path)[1]["rebuilt"] is False
    monkeypatch.setattr(templates, "manifest", lambda: {"git_head": "changed"})

    def refuse_run(*args, **kwargs):
        raise RuntimeError("rebuild requested")

    monkeypatch.setattr(templates.subprocess, "run", refuse_run)
    with pytest.raises(RuntimeError, match="rebuild requested"):
        templates.ensure_template(tmp_path)


def test_postgres_clone_rejects_live_database_names():
    with pytest.raises(ValueError, match="disposable"):
        templates.clone_postgres_template(None, "truecrime", "tc_soundness_copy")


@pytest.mark.soundness_slow
def test_real_template_restores_every_saved_output(tmp_path):
    template, timing = templates.ensure_template(templates.ROOT / "data/dev/soundness/template")
    baseline = json.loads((template / "snapshot.json").read_text())
    restored = templates.restore_template(template, tmp_path / "restored")
    engine = create_engine(f"sqlite:///{restored['database']}")
    with Session(engine) as db:
        actual = saved_output_snapshot(
            db, file_roots=[Path(restored["files"]) / name for name in ("cases", "audio", "lib")]
        )
        from sqlalchemy import text

        for table, column in (("visual_assets", "local_path"), ("videos", "file_path")):
            # Every stored reference must point into the RESTORED directory,
            # not merely hash the same as a now-moved source path.
            if column not in [
                c["name"] for c in __import__("sqlalchemy").inspect(engine).get_columns(table)
            ]:
                continue
            for (value,) in db.execute(text(f'SELECT "{column}" FROM "{table}"')):
                if value:
                    assert Path(value).is_relative_to(Path(restored["files"]))
                    assert Path(value).is_file()
    engine.dispose()
    assert actual == baseline
    print("REAL_TEMPLATE", json.dumps({**timing, **restored, "snapshot_keys": len(actual)}))
