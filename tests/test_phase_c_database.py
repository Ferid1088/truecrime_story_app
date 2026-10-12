"""Copy-only tests for the guarded Phase-C database scripts."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from app.db import migrate
from scripts import db_reconcile

ROOT = Path(__file__).resolve().parents[1]


def _fresh_0009(path: Path) -> None:
    command.upgrade(migrate.alembic_config(f"sqlite:///{path}"), "0009")
    with sqlite3.connect(path) as connection:
        for table in ("provider_calls", "app_settings", "backup_runs", "policy_events",
                      "override_log", "artifacts", "artifact_deps", "edit_log"):
            connection.execute(f'DROP TABLE "{table}"')
        for index in (
            "ix_cases_resolution_status", "ix_discovery_candidates_case_id",
            "ix_discovery_candidates_state", "ix_epistemic_claims_assertion_role",
            "ix_epistemic_claims_parent_claim_key", "ix_story_versions_case_id",
            "ix_story_versions_status", "ix_visual_assets_entity_key",
        ):
            connection.execute(f'DROP INDEX "{index}"')


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_apply_live_success_uses_only_a_copy(tmp_path, monkeypatch):
    live = tmp_path / "truecrime.db"
    _fresh_0009(live)
    monkeypatch.setattr(db_reconcile, "ensure_live_quiescent", lambda root, target: None)

    backup = db_reconcile.apply_live(root=ROOT, live=live, backup_dir=tmp_path / "backups")

    assert backup.exists()
    with sqlite3.connect(live) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0010"
        assert all(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 0
                   for table in db_reconcile.NEW_TABLES)


def test_apply_live_aborts_when_a_service_is_running(tmp_path, monkeypatch):
    live = tmp_path / "truecrime.db"
    _fresh_0009(live)
    before = _sha(live)
    monkeypatch.setattr(
        db_reconcile, "ensure_live_quiescent",
        lambda root, target: (_ for _ in ()).throw(RuntimeError("api: running")),
    )

    with pytest.raises(RuntimeError, match="api: running"):
        db_reconcile.apply_live(root=ROOT, live=live, backup_dir=tmp_path / "backups")
    assert _sha(live) == before
    assert not (tmp_path / "backups").exists()


def test_apply_live_aborts_on_backup_mismatch(tmp_path, monkeypatch):
    live = tmp_path / "truecrime.db"
    _fresh_0009(live)
    monkeypatch.setattr(db_reconcile, "ensure_live_quiescent", lambda root, target: None)
    original = db_reconcile.copy_database

    def stale_backup(source: Path, destination: Path) -> None:
        original(source, destination)
        with sqlite3.connect(destination) as connection:
            connection.execute("CREATE TABLE mismatch_marker (id INTEGER PRIMARY KEY)")
            connection.commit()

    monkeypatch.setattr(db_reconcile, "copy_database", stale_backup)
    with pytest.raises(RuntimeError, match="row counts differ"):
        db_reconcile.apply_live(root=ROOT, live=live, backup_dir=tmp_path / "backups")
    assert not (tmp_path / "truecrime.db-wal").exists()


def test_apply_live_rolls_back_and_removes_stale_sidecars(tmp_path, monkeypatch):
    live = tmp_path / "truecrime.db"
    _fresh_0009(live)
    monkeypatch.setattr(db_reconcile, "ensure_live_quiescent", lambda root, target: None)
    monkeypatch.setattr(
        db_reconcile, "verify_post_migration",
        lambda live_path, backup_path: (_ for _ in ()).throw(RuntimeError("forced verification failure")),
    )
    (tmp_path / "truecrime.db-wal").write_text("stale", encoding="utf-8")
    (tmp_path / "truecrime.db-shm").write_text("stale", encoding="utf-8")

    with pytest.raises(db_reconcile.LiveApplyError, match="rolled back"):
        db_reconcile.apply_live(root=ROOT, live=live, backup_dir=tmp_path / "backups")
    backup = next((tmp_path / "backups").glob("truecrime-pre-phaseC-*.db"))
    db_reconcile.verify_database_pair(backup, live)
    assert not (tmp_path / "truecrime.db-wal").exists()
    assert not (tmp_path / "truecrime.db-shm").exists()


def test_rollback_restores_a_copy(tmp_path):
    backup = tmp_path / "backup.db"
    target = tmp_path / "target.db"
    _fresh_0009(backup)
    db_reconcile.copy_database(backup, target)
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE unwanted (id INTEGER PRIMARY KEY)")
        connection.commit()
    (tmp_path / "target.db-wal").write_text("stale", encoding="utf-8")
    (tmp_path / "target.db-shm").write_text("stale", encoding="utf-8")

    db_reconcile.restore_database(backup, target)

    db_reconcile.verify_database_pair(backup, target)
    assert not (tmp_path / "target.db-wal").exists()
    assert not (tmp_path / "target.db-shm").exists()
