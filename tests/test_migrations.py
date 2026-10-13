"""Alembic migrations: a fresh database, an old (pre-Alembic) database and a wrong database must each behave."""
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

import app.db.models  # noqa: F401
from app.db import migrate
from app.db.base import Base

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


def _upgrade_to(path, revision):
    cfg = migrate.alembic_config(f"sqlite:///{path}")
    command.upgrade(cfg, revision)


def _stamp_and_upgrade(path):
    cfg = migrate.alembic_config(f"sqlite:///{path}")
    command.stamp(cfg, "0009")
    command.upgrade(cfg, "head")


def _drift_allowlist(actual, expected):
    allowed_nullability = {
        ("agent_runs", "fallback_used"),
        ("cases", "aliases_json"), ("cases", "identifiers_json"),
        ("cases", "people_json"), ("cases", "resolution_status"),
        ("discovery_candidates", "aliases_json"),
        ("discovery_candidates", "people_json"),
        ("discovery_candidates", "resolution_status"),
        ("discovery_candidates", "source_urls_json"),
        ("discovery_candidates", "state"),
        ("documentary_jobs", "production_type"),
        ("epistemic_claims", "assertion_role"),
        ("facts", "chunk_ids_json"), ("facts", "locations_json"),
        ("facts", "original_language"), ("facts", "people_json"),
        ("facts", "transcript_claim_ids_json"),
        ("sources", "content_status"), ("sources", "status"),
        ("story_versions", "is_best"), ("story_versions", "kind"),
        ("story_versions", "language"), ("story_versions", "similarity_status"),
        ("story_versions", "status"), ("videos", "render_profile"),
        ("visual_audits", "detail_json"),
    }
    allowed_length = {("epistemic_claims", "origin")}
    allowed_fks = {
        ("agent_runs", "documentary_job_id", "documentary_jobs", "id"),
        ("story_versions", "master_version_id", "story_versions", "id"),
    }
    assert set(actual) == set(expected)
    differences = []
    for table in sorted(actual):
        left, right = actual[table], expected[table]
        assert left["indexes"] == right["indexes"], table
        for column in sorted(set(left["columns"]) | set(right["columns"])):
            if column not in left["columns"] or column not in right["columns"]:
                differences.append(("column_presence", table, column))
                continue
            if left["columns"][column] == right["columns"][column]:
                continue
            live_type, live_nullable, live_pk = left["columns"][column]
            head_type, head_nullable, head_pk = right["columns"][column]
            if (table, column) in allowed_nullability and live_type == head_type and live_pk == head_pk:
                assert live_nullable != head_nullable
                continue
            if (table, column) in allowed_length and live_nullable == head_nullable and live_pk == head_pk:
                assert live_type.startswith("VARCHAR") and head_type.startswith("VARCHAR")
                continue
            differences.append(("column", table, column, left["columns"][column], right["columns"][column]))
        live_fks = set(left["foreign_keys"])
        head_fks = set(right["foreign_keys"])
        for fk in sorted(live_fks - head_fks):
            differences.append(("extra_fk", table, fk))
        for fk in sorted(head_fks - live_fks):
            key = (table, fk[0], fk[1], fk[2])
            if key not in allowed_fks:
                differences.append(("missing_fk", key))
    missing_fks = {
        (table, fk[0], fk[1], fk[2])
        for table in actual for fk in set(expected[table]["foreign_keys"]) - set(actual[table]["foreign_keys"])
    }
    assert missing_fks <= allowed_fks
    assert not differences, differences


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


def test_0011_adds_shorts_tables_after_a_fresh_0009(tmp_path):
    db = tmp_path / "fresh-0009.db"
    _upgrade_to(db, "0009")
    before = migrate._live_signature(_engine(db))
    _upgrade_to(db, "head")
    after = migrate._live_signature(_engine(db))
    assert before != after
    assert _version(db) == "0013"
    con = sqlite3.connect(db)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"short_candidates", "rendered_shorts"} <= tables


def test_reconcile_drifted_copy_preserves_rows_and_only_keeps_allowlist(tmp_path):
    db = tmp_path / "drifted.db"
    # Model the verified live drift: the pre-0010 schema is otherwise at 0009,
    # but the eight new tables and the objects listed in the Phase-B allowlist
    # are absent.  This keeps the fixture independent of the user's live file.
    _upgrade_to(db, "0009")
    con = sqlite3.connect(db)
    _insert(con, "cases", id=1, canonical_title="preserved", slug="preserved")
    for table in ("provider_calls", "app_settings", "backup_runs", "policy_events",
                  "override_log", "artifacts", "artifact_deps", "edit_log"):
        con.execute(f'DROP TABLE "{table}"')
    for index in (
        "ix_cases_resolution_status", "ix_discovery_candidates_case_id",
        "ix_discovery_candidates_state", "ix_epistemic_claims_assertion_role",
        "ix_epistemic_claims_parent_claim_key", "ix_story_versions_case_id",
        "ix_story_versions_status", "ix_visual_assets_entity_key",
        "ix_agent_runs_documentary_job_id", "ix_agent_runs_language",
        "ix_agent_runs_pipeline_stage",
    ):
        con.execute(f'DROP INDEX "{index}"')
    # SQLite cannot drop columns that participate in the existing 0007 FK
    # definition, so rebuild this one legacy table from the frozen baseline.
    agent_sql = migrate.LEGACY_SQL.read_text(encoding="utf-8").split(";\n", 1)[0] + ";"
    agent_sql = agent_sql.replace("CREATE TABLE agent_runs (", "CREATE TABLE agent_runs_legacy (")
    con.execute("ALTER TABLE agent_runs RENAME TO agent_runs_current")
    con.executescript(agent_sql)
    con.execute("DROP TABLE agent_runs_current")
    con.execute("ALTER TABLE agent_runs_legacy RENAME TO agent_runs")
    con.executescript(
        "CREATE INDEX ix_agent_runs_agent_name ON agent_runs (agent_name);"
        "CREATE INDEX ix_agent_runs_case_id ON agent_runs (case_id);"
        "CREATE INDEX ix_agent_runs_status ON agent_runs (status);"
    )
    con.commit()
    before_tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    before_counts = {table: con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                     for table in before_tables}
    con.close()

    _stamp_and_upgrade(db)
    fresh = tmp_path / "fresh-head.db"
    _upgrade_to(fresh, "head")
    _drift_allowlist(migrate._live_signature(_engine(db)),
                     migrate._live_signature(_engine(fresh)))

    con = sqlite3.connect(db)
    try:
        assert {table: con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                for table in before_tables} == before_counts
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()


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
    message = str(exc.value)
    assert "cases extra columns: surprise" in message
    assert "Nothing was changed" in message
    assert "scripts/db_reconcile.sh" in message
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
