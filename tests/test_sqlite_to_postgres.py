"""SQLite -> Postgres copy: exact, ordered by foreign keys, ids keep counting, and refuses a non-empty target.

Needs a Postgres server: set TC_TEST_DATABASE_URL (CI does). Skipped otherwise.
"""
import importlib.util
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db import migrate
from app.db.models import Case, Fact, StoryVersion

PG = os.environ.get("TC_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG, reason="needs a Postgres server (TC_TEST_DATABASE_URL)")

_spec = importlib.util.spec_from_file_location(
    "sqlite_to_postgres", Path(__file__).resolve().parents[1] / "scripts" / "sqlite_to_postgres.py")
copier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(copier)


@pytest.fixture
def pg_url():
    """A fresh empty database next to the test database."""
    name = "tc_copy_" + uuid.uuid4().hex[:8]
    admin = create_engine(make_url(PG).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(PG).set(database=name).render_as_string(hide_password=False)
    yield url
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def sqlite_db(tmp_path):
    path = tmp_path / "src.db"
    eng = create_engine(f"sqlite:///{path}")
    migrate.upgrade_database(eng)
    now = datetime(2026, 10, 10, 12, 30, 45, 123456, tzinfo=timezone.utc)
    with eng.begin() as c:
        for i, title in enumerate(["Der Fall Müller", "قضية خفية", "رد خاموش 🕵️"], start=1):
            c.execute(Case.__table__.insert().values(
                id=i, canonical_title=title, slug=f"s{i}", language="en", status="new",
                created_at=now, resolution_status="UNKNOWN",
                aliases_json="[]", people_json="[]", identifiers_json="[]", case_uid=f"TC-{i}"))
        for i in (1, 2, 3):
            c.execute(Fact.__table__.insert().values(
                id=i, case_id=1, claim=f"claim {i} — ü", category="context", confidence=0.1 * i,
                original_language="en", chunk_ids_json="[]", people_json="[]", locations_json="[]",
                transcript_claim_ids_json="[]"))
        # id 5 points at id 7, which is inserted later: ordering inside a table must not matter
        for i, master in ((5, 7), (6, None), (7, None)):
            c.execute(StoryVersion.__table__.insert().values(
                id=i, case_id=1, version=i, narrative_angle="a", story_text="t", engagement_score=1.5,
                created_at=now, language="en", status="draft", is_best=(i == 6), kind="direct",
                similarity_status="not_evaluated", master_version_id=master))
    eng.dispose()
    return path


def _count(url, table):
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return c.execute(text(f"SELECT count(*) FROM {table}")).scalar()
    finally:
        eng.dispose()


def test_copy_is_exact_and_ids_keep_counting(sqlite_db, pg_url):
    assert copier.copy_database(sqlite_db, pg_url, log=lambda *_: None) == 0
    assert _count(pg_url, "cases") == 3 and _count(pg_url, "facts") == 3 and _count(pg_url, "story_versions") == 3
    eng = create_engine(pg_url)
    with eng.begin() as c:
        new_id = c.execute(text(
            "INSERT INTO cases (canonical_title, slug, language, status, created_at, resolution_status,"
            " aliases_json, people_json, identifiers_json) VALUES ('n','n','en','new',now(),'UNKNOWN','[]','[]','[]')"
            " RETURNING id")).scalar()
        link = c.execute(text("SELECT master_version_id FROM story_versions WHERE id=5")).scalar()
    eng.dispose()
    assert new_id == 4, "the id counter must continue after the highest copied id"
    assert link == 7, "self-referencing pointers survive the copy"


def test_unicode_and_timestamps_survive(sqlite_db, pg_url):
    copier.copy_database(sqlite_db, pg_url, log=lambda *_: None)
    eng = create_engine(pg_url)
    with eng.connect() as c:
        titles = [r[0] for r in c.execute(text("SELECT canonical_title FROM cases ORDER BY id"))]
        created = c.execute(text("SELECT created_at FROM cases WHERE id=1")).scalar()
    eng.dispose()
    assert titles == ["Der Fall Müller", "قضية خفية", "رد خاموش 🕵️"]
    assert created == datetime(2026, 10, 10, 12, 30, 45, 123456, tzinfo=timezone.utc)


def test_a_difference_is_detected(sqlite_db, pg_url):
    copier.copy_database(sqlite_db, pg_url, log=lambda *_: None)
    eng = create_engine(pg_url)
    with eng.begin() as c:
        c.execute(text("UPDATE facts SET claim='tampered' WHERE id=2"))
    eng.dispose()
    src = create_engine(f"sqlite:///{sqlite_db}")
    dst = create_engine(pg_url)
    table = Fact.__table__
    assert copier.table_fingerprint(src, table) != copier.table_fingerprint(dst, table)


def test_non_empty_target_is_refused_and_untouched(sqlite_db, pg_url):
    copier.copy_database(sqlite_db, pg_url, log=lambda *_: None)
    with pytest.raises(SystemExit) as exc:
        copier.copy_database(sqlite_db, pg_url, log=lambda *_: None)
    assert "already has rows" in str(exc.value)
    assert _count(pg_url, "cases") == 3


def test_source_file_is_not_modified(sqlite_db, pg_url):
    import hashlib

    before = hashlib.sha256(sqlite_db.read_bytes()).hexdigest()
    copier.copy_database(sqlite_db, pg_url, log=lambda *_: None)
    assert hashlib.sha256(sqlite_db.read_bytes()).hexdigest() == before
    assert not list(sqlite_db.parent.glob("src.db-*"))


def test_too_long_value_is_reported_before_anything_is_written(sqlite_db, pg_url):
    import sqlite3

    con = sqlite3.connect(sqlite_db)
    con.execute("UPDATE cases SET language = ? WHERE id = 2", ("x" * 30,))  # column is VARCHAR(20)
    con.commit()
    con.close()
    logs = []
    assert copier.copy_database(sqlite_db, pg_url, log=logs.append) == 1
    assert any("cases.language" in line and "30 characters" in line for line in logs)
    eng = create_engine(pg_url)
    with eng.connect() as c:  # not even the schema was created
        assert c.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")).scalar() == 0
    eng.dispose()


def test_a_failure_halfway_leaves_the_target_empty(sqlite_db, pg_url, monkeypatch):
    real = copier.select
    calls = {"n": 0}

    class Boom(Exception):
        pass

    def exploding_select(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > len(copier._tables()) + 8:  # emptiness check done; several tables already copied
            raise Boom("disk on fire")
        return real(*args, **kwargs)

    monkeypatch.setattr(copier, "select", exploding_select)
    with pytest.raises(Boom):
        copier.copy_database(sqlite_db, pg_url, log=lambda *_: None)
    monkeypatch.undo()
    assert _count(pg_url, "cases") == 0 and _count(pg_url, "facts") == 0


def test_a_file_from_before_the_newest_migrations_copies_with_defaults(sqlite_db, pg_url):
    """The real database may still lack the newest columns when it is copied."""
    import sqlite3

    con = sqlite3.connect(sqlite_db)
    for col in ("worker_id", "heartbeat_at", "auto_resumes"):
        con.execute(f"ALTER TABLE documentary_jobs DROP COLUMN {col}")
    con.commit()
    con.close()
    assert copier.copy_database(sqlite_db, pg_url, log=lambda *_: None) == 0
    assert _count(pg_url, "cases") == 3
