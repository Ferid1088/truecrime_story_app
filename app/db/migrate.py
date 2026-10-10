"""Bring the database to the latest schema with Alembic. Called once at startup (app/db/schema.py).

Three situations are handled, and nothing else is guessed:

* empty database            -> run every migration
* has an alembic_version    -> run the migrations that are missing
* tables but no version     -> an older database from before Alembic. Its structure is compared with
  the frozen legacy baseline (or with the current models); on an exact match it is stamped and
  upgraded, otherwise startup stops with a clear message and the database is left untouched.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[2]
LEGACY_SQL = ROOT / "migrations" / "legacy_schema.sql"
BASELINE_REVISION = "0001"


class SchemaMismatch(RuntimeError):
    """The existing database does not match any known starting point."""


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False  # never replace the application's logging setup
    return cfg


def _signature(con: sqlite3.Connection) -> dict:
    """Structure that must match for a database to count as 'the same schema' (order and defaults ignored)."""
    sig: dict = {}
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'")]
    for t in tables:
        q = '"' + t.replace('"', '""') + '"'
        cols = {r[1]: ((r[2] or "").upper(), bool(r[3]), int(r[5])) for r in con.execute(f"PRAGMA table_info({q})")}
        idx = []  # a list, not a dict: two indexes (plain + unique) may cover the same columns
        for row in con.execute(f"PRAGMA index_list({q})").fetchall():
            if row[3] == "pk":
                continue
            idx.append((tuple(r[2] for r in con.execute(f'PRAGMA index_info("{row[1]}")')), bool(row[2])))
        idx.sort()
        fks = sorted((r[3], r[2], r[4]) for r in con.execute(f"PRAGMA foreign_key_list({q})"))
        sig[t] = {"columns": cols, "indexes": idx, "foreign_keys": fks}
    return sig


def _legacy_signature() -> dict:
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(LEGACY_SQL.read_text(encoding="utf-8"))
        return _signature(con)
    finally:
        con.close()


def _models_signature() -> dict:
    import app.db.models  # noqa: F401
    from app.db.base import Base

    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        raw = engine.raw_connection()
        try:
            return _signature(raw.driver_connection)
        finally:
            raw.close()
    finally:
        engine.dispose()


def _live_signature(engine: Engine) -> dict:
    raw = engine.raw_connection()
    try:
        return _signature(raw.driver_connection)
    finally:
        raw.close()


def _first_difference(a: dict, b: dict) -> str:
    for t in sorted(set(a) | set(b)):
        if t not in a:
            return f"table '{t}' is missing from the database"
        if t not in b:
            return f"unexpected table '{t}'"
        if a[t] != b[t]:
            for part in ("columns", "indexes", "foreign_keys"):
                if a[t][part] != b[t][part]:
                    return f"table '{t}' differs in {part}"
    return "unknown difference"


def upgrade_database(engine: Engine) -> str:
    """Upgrade `engine`'s database to the newest revision. Returns what it did."""
    url = engine.url.render_as_string(hide_password=False)
    cfg = alembic_config(url)
    names = set(inspect(engine).get_table_names())

    if "alembic_version" in names or not names:
        command.upgrade(cfg, "head")
        return "upgraded" if names else "created"

    if engine.dialect.name != "sqlite":
        raise SchemaMismatch("Database has tables but no alembic_version; stamp it manually after checking it.")

    live = _live_signature(engine)
    if live == _legacy_signature():
        command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")
        return "adopted legacy database"
    if live == _models_signature():
        command.stamp(cfg, "head")
        return "adopted database already at current models"
    raise SchemaMismatch(
        "The database was created before Alembic and does not match the known baseline: "
        + _first_difference(live, _legacy_signature())
        + ". Nothing was changed. Restore a backup or compare with scripts/db_tools.py schema-diff."
    )
