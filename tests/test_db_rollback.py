"""Copy-only coverage for the explicit rollback command."""
from __future__ import annotations

import sqlite3
import sys

from test_phase_c_database import _fresh_0009

from scripts import db_reconcile, db_rollback


def test_db_rollback_command_restores_copy(tmp_path, monkeypatch):
    backup = tmp_path / "backup.db"
    target = tmp_path / "target.db"
    _fresh_0009(backup)
    db_reconcile.copy_database(backup, target)
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE unwanted (id INTEGER PRIMARY KEY)")
        connection.commit()
    (tmp_path / "target.db-wal").write_text("stale", encoding="utf-8")
    (tmp_path / "target.db-shm").write_text("stale", encoding="utf-8")

    monkeypatch.setattr(db_rollback.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(db_reconcile, "ensure_live_quiescent", lambda root, live: None)
    monkeypatch.setattr(sys, "argv", [
        "db_rollback.py", str(backup), "--target", str(target),
    ])

    assert db_rollback.main() == 0
    db_reconcile.verify_database_pair(backup, target)
    assert not (tmp_path / "target.db-wal").exists()
    assert not (tmp_path / "target.db-shm").exists()
