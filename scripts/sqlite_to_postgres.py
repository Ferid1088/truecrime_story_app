#!/usr/bin/env python3
"""Copy a TrueCrime SQLite database into an empty Postgres database and prove the copy is exact.

    python scripts/sqlite_to_postgres.py truecrime.db postgresql+psycopg://user:pw@host:5432/truecrime

What it does, in order:
  1. refuses to start unless the target is empty (no rows in any table);
  2. brings the target to the newest schema with Alembic (same migrations the app runs);
  3. copies every table in foreign-key order, reading and writing through the ORM table
     definitions so booleans, timestamps and JSON text convert correctly;
  4. resets the id counters so new rows get fresh ids;
  5. re-reads BOTH databases and compares row count and a checksum of every row, table by table.

The SQLite file is opened read-only and never modified. Exit code 1 on any difference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

CHUNK = 500


def _tables():
    import app.db.models  # noqa: F401
    from app.db.base import Base

    return Base.metadata.sorted_tables


def _self_fk_columns(table) -> list[str]:
    return [c.name for fk in table.foreign_keys if fk.column.table is table for c in [fk.parent]]


def _norm(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return {"dt": value.isoformat()}
    if isinstance(value, date):
        return {"d": value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"b": bytes(value).hex()}
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, float):
        return repr(value)
    return value


def table_fingerprint(engine: Engine, table) -> tuple[int, str]:
    pk = [c for c in table.primary_key.columns]
    order = pk or [table.c[next(iter(table.c.keys()))]]
    digest, count = hashlib.sha256(), 0
    with engine.connect() as conn:
        for row in conn.execute(select(table).order_by(*order)).mappings():
            digest.update(json.dumps([_norm(row[c.name]) for c in table.c], ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
            count += 1
    return count, digest.hexdigest()


def _target_is_empty(engine: Engine, tables) -> bool:
    with engine.connect() as conn:
        return all(conn.execute(select(func.count()).select_from(t)).scalar() == 0 for t in tables)


def preflight(src: Engine, tables) -> list[str]:
    """Things SQLite accepts that Postgres refuses, found before anything is written."""
    from sqlalchemy import String

    problems: list[str] = []
    with src.connect() as conn:
        for table in tables:
            for col in table.c:
                if isinstance(col.type, String) and getattr(col.type, "length", None):
                    n = col.type.length
                    rows = conn.exec_driver_sql(
                        f'SELECT rowid, length("{col.name}") FROM "{table.name}" '
                        f'WHERE length("{col.name}") > {n} ORDER BY 2 DESC LIMIT 3').fetchall()
                    for rid, length in rows:
                        problems.append(f"{table.name}.{col.name} (max {n}): row {rid} has {length} characters")
    return problems


def copy_database(sqlite_path: Path, pg_url: str, log=print) -> int:
    from app.db.migrate import upgrade_database

    if not sqlite_path.exists():
        raise SystemExit(f"SQLite file not found: {sqlite_path}")
    src = create_engine(f"sqlite:///file:{sqlite_path.resolve()}?mode=ro&uri=true")
    dst = create_engine(pg_url)
    tables = _tables()

    found = preflight(src, tables)
    if found:
        log(f"FAIL preflight: {len(found)} value(s) Postgres would refuse; nothing was written")
        for f in found:
            log(f"  - {f}")
        return 1
    log(f"schema: {upgrade_database(dst)} (target)")
    if not _target_is_empty(dst, tables):
        raise SystemExit("target database already has rows; refusing to copy into it")

    # One transaction for everything: if any table fails, the target is left exactly as it was (empty).
    with dst.begin() as dconn:
        for table in tables:
            self_fks = _self_fk_columns(table)
            with src.connect() as sconn:
                rows = [dict(r) for r in sconn.execute(select(table)).mappings()]
            deferred: list[tuple[dict, dict]] = []
            if self_fks and rows:
                # a row may point at a row inserted later: insert with the pointer empty, then set it
                for r in rows:
                    pk = {c.name: r[c.name] for c in table.primary_key.columns}
                    deferred.append((pk, {c: r[c] for c in self_fks if r[c] is not None}))
                    for c in self_fks:
                        r[c] = None
            for i in range(0, len(rows), CHUNK):
                dconn.execute(table.insert(), rows[i:i + CHUNK])
            for pk, values in deferred:
                if values:
                    cond = [table.c[k] == v for k, v in pk.items()]
                    dconn.execute(table.update().where(*cond).values(**values))
            log(f"  copied {table.name}: {len(rows)} rows")

        for table in tables:
            for col in table.primary_key.columns:
                if col.autoincrement is not False and col.type.python_type is int:
                    dconn.execute(text(
                        "SELECT setval(pg_get_serial_sequence(:t, :c), "
                        f"COALESCE((SELECT MAX({col.name}) FROM {table.name}), 1), "
                        f"(SELECT MAX({col.name}) FROM {table.name}) IS NOT NULL)"
                    ), {"t": table.name, "c": col.name})

    problems = []
    total = 0
    for table in tables:
        a, b = table_fingerprint(src, table), table_fingerprint(dst, table)
        total += a[0]
        if a[0] != b[0]:
            problems.append(f"{table.name}: row count {a[0]} -> {b[0]}")
        elif a[1] != b[1]:
            problems.append(f"{table.name}: same row count ({a[0]}) but contents differ")
    src.dispose()
    dst.dispose()
    if problems:
        log(f"FAIL copy verification: {len(problems)} difference(s)")
        for p in problems:
            log(f"  - {p}")
        return 1
    log(f"OK copy verified: {len(tables)} tables, {total} rows identical on both sides")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sqlite_db")
    parser.add_argument("postgres_url")
    args = parser.parse_args(argv)
    return copy_database(Path(args.sqlite_db), args.postgres_url)


if __name__ == "__main__":
    sys.exit(main())
