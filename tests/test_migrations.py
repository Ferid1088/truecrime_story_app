"""Alembic migrations: a fresh database, an old (pre-Alembic) database and a wrong database must each behave."""
import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.db import migrate
from app.db.base import Base
import app.db.models  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _engine(path):
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


def _head():
    return ScriptDirectory(str(ROOT / "migrations")).get_current_head()


def _version(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()


def _legacy_db(path):
    con = sqlite3.connect(path)
    con.executescript(migrate.LEGACY_SQL.read_text(encoding="utf-8"))
    con.commit()
    con.close()


def _insert(con, table, **values):
    """Insert one row, filling every NOT NULL column that has no default with a dummy value."""
    row = dict(values)
    for _cid, name, ctype, notnull, default, pk in con.execute(f'PRAGMA table_info("{table}")').fetchall():
        if name in row or pk or not notnull or default is not None:
            continue
        t = (ctype or "").upper()
        row[name] = 0 if any(k in t for k in ("INT", "FLOAT", "BOOL", "REAL")) else ("2026-01-01 00:00:00" if "DATE" in t else "x")
    cols = ", ".join(f'"{c}"' for c in row)
    con.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({", ".join("?" * len(row))})', list(row.values()))


def _rows_by_name(path, table):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(f'SELECT * FROM "{table}" ORDER BY id')]
    finally:
        con.close()
    return rows


def test_fresh_database_gets_every_table_and_the_head_revision(tmp_path):
    db = tmp_path / "fresh.db"
    assert migrate.upgrade_database(_engine(db)) == "created"
    assert _version(db) == _head()
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert tables - {"alembic_version"} == set(Base.metadata.tables)


def test_migrations_and_models_describe_the_same_schema(tmp_path):
    """The equivalent of `alembic check`: autogenerate must find nothing left to do."""
    db = tmp_path / "m.db"
    engine = _engine(db)
    migrate.upgrade_database(engine)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diffs = compare_metadata(ctx, Base.metadata)
    assert diffs == [], f"models and migrations disagree: {diffs[:3]}"


def test_fresh_schema_equals_create_all_ignoring_server_defaults(tmp_path):
    migrated, created = tmp_path / "a.db", tmp_path / "b.db"
    migrate.upgrade_database(_engine(migrated))
    Base.metadata.create_all(_engine(created))
    sig = lambda p: migrate._live_signature(_engine(p))  # noqa: E731
    assert sig(migrated) == sig(created)


def test_upgrade_is_idempotent(tmp_path):
    db = tmp_path / "i.db"
    migrate.upgrade_database(_engine(db))
    assert migrate.upgrade_database(_engine(db)) == "upgraded"
    assert _version(db) == _head()


def test_old_database_is_adopted_and_keeps_every_row(tmp_path):
    db = tmp_path / "old.db"
    _legacy_db(db)
    con = sqlite3.connect(db)
    _insert(con, "cases", id=1, canonical_title="Der Fall Müller", slug="der-fall-mueller")
    _insert(con, "cases", id=2, canonical_title="قضية", slug="qadiya")
    _insert(con, "story_versions", id=1, case_id=1, version=1, narrative_angle="a", story_text="t", engagement_score=1.0)
    _insert(con, "story_versions", id=2, case_id=1, version=2, narrative_angle="b", story_text="u", engagement_score=2.0, master_version_id=1)
    _insert(con, "facts", id=1, case_id=1, claim="c")
    con.commit()
    con.close()
    before = {t: _rows_by_name(db, t) for t in ("cases", "story_versions", "facts")}

    assert migrate.upgrade_database(_engine(db)) == "adopted legacy database"
    assert _version(db) == _head()
    assert {t: _rows_by_name(db, t) for t in before} == before, "migration must not change any value"
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_case_uid_is_unique_after_migration(tmp_path):
    db = tmp_path / "u.db"
    _legacy_db(db)
    migrate.upgrade_database(_engine(db))
    con = sqlite3.connect(db)
    _insert(con, "cases", canonical_title="a", slug="a", case_uid="TC-1")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "cases", canonical_title="b", slug="b", case_uid="TC-1")
    con.close()


def test_database_already_at_current_models_is_stamped(tmp_path):
    db = tmp_path / "cur.db"
    Base.metadata.create_all(_engine(db))
    assert migrate.upgrade_database(_engine(db)) == "adopted database already at current models"
    assert _version(db) == _head()


def test_unknown_database_is_refused_and_left_untouched(tmp_path):
    db = tmp_path / "bad.db"
    _legacy_db(db)
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE cases ADD COLUMN surprise TEXT")
    _insert(con, "cases", id=1, canonical_title="t", slug="s")
    con.commit()
    con.close()
    before = db.read_bytes()
    with pytest.raises(migrate.SchemaMismatch) as exc:
        migrate.upgrade_database(_engine(db))
    assert "cases" in str(exc.value) and "Nothing was changed" in str(exc.value)
    assert db.read_bytes() == before


def test_legacy_baseline_file_matches_the_recorded_production_schema():
    """The frozen baseline has 55 tables and 163 indexes, exactly like the production database on 2026-10-10."""
    sig = migrate._legacy_signature()
    assert len(sig) == 55
    assert sum(len(t["indexes"]) for t in sig.values()) >= 100


def test_logging_setup_of_the_application_is_not_replaced(tmp_path):
    import logging

    root_handlers = list(logging.getLogger().handlers)
    migrate.upgrade_database(_engine(tmp_path / "l.db"))
    assert logging.getLogger().handlers == root_handlers
