#!/usr/bin/env python3
"""Database safety tools for the migration: baseline, compare, backup, verify.

Standard library only, so it runs anywhere Python 3.10+ does (including the
Mac, outside the app's virtualenv). Nothing here ever writes to the source
database: it is opened read-only, and a backup goes to a NEW file.

    python scripts/db_tools.py baseline truecrime.db baseline.json
    python scripts/db_tools.py compare  baseline.json truecrime.db
    python scripts/db_tools.py backup   truecrime.db backups/
    python scripts/db_tools.py verify   backups/truecrime-20261010-120000.db baseline.json

A baseline records, per table: row count, a SHA-256 over every row in rowid
order, and the schema. `compare` and `verify` fail (exit 1) on any difference,
so a migration step that is meant to leave data unchanged can prove it.
Use --allow TABLE (repeatable) for tables a step is meant to change.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

FORMAT_VERSION = 1


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"database not found: {path}")
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def _jsonable(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes__": bytes(value).hex()}
    return value


def _table_names(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _table_fingerprint(con: sqlite3.Connection, table: str) -> dict:
    quoted = '"' + table.replace('"', '""') + '"'
    try:
        cursor = con.execute(f"SELECT * FROM {quoted} ORDER BY rowid")
    except sqlite3.OperationalError:  # WITHOUT ROWID table
        cursor = con.execute(f"SELECT * FROM {quoted} ORDER BY 1")
    digest = hashlib.sha256()
    count = 0
    for row in cursor:
        digest.update(json.dumps([_jsonable(v) for v in row], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    columns = [d[0] for d in cursor.description]
    return {"rows": count, "sha256": digest.hexdigest(), "columns": columns}


def fingerprint(path: Path) -> dict:
    con = _open_readonly(path)
    try:
        tables = {t: _table_fingerprint(con, t) for t in _table_names(con)}
        schema_rows = con.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        schema_hash = hashlib.sha256(json.dumps(schema_rows, ensure_ascii=False).encode("utf-8")).hexdigest()
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        fk_violations = len(con.execute("PRAGMA foreign_key_check").fetchall())
        indexes = sum(1 for r in schema_rows if r[0] == "index")
    finally:
        con.close()
    return {
        "format": FORMAT_VERSION,
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source_name": path.name,
        "integrity_check": integrity,
        "foreign_key_violations": fk_violations,
        "table_count": len(tables),
        "index_count": indexes,
        "total_rows": sum(t["rows"] for t in tables.values()),
        "schema_sha256": schema_hash,
        "tables": tables,
    }


def diff(expected: dict, actual: dict, allow: set[str], check_schema: bool = True) -> list[str]:
    problems: list[str] = []
    if actual["integrity_check"] != "ok":
        problems.append(f"integrity_check is {actual['integrity_check']!r}, expected 'ok'")
    if actual["foreign_key_violations"]:
        problems.append(f"{actual['foreign_key_violations']} foreign-key violations")
    exp_t, act_t = expected["tables"], actual["tables"]
    for name in sorted(set(exp_t) | set(act_t)):
        if name in allow:
            continue
        if name not in act_t:
            problems.append(f"table missing: {name}")
        elif name not in exp_t:
            problems.append(f"unexpected table: {name}")
        else:
            e, a = exp_t[name], act_t[name]
            if e["rows"] != a["rows"]:
                problems.append(f"{name}: row count {e['rows']} -> {a['rows']}")
            elif e["sha256"] != a["sha256"]:
                problems.append(f"{name}: same row count ({a['rows']}) but contents differ")
    if check_schema and not allow and expected["schema_sha256"] != actual["schema_sha256"]:
        problems.append("schema differs from the baseline")
    return problems


def _report(problems: list[str], label: str) -> int:
    if problems:
        print(f"FAIL {label}: {len(problems)} difference(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"OK {label}: every table matches the baseline")
    return 0


def schema_dump(path: Path) -> dict:
    """Structure only (no row data): columns, indexes and foreign keys per table, read-only."""
    con = _open_readonly(path)
    try:
        out: dict = {}
        for table in _table_names(con):
            q = '"' + table.replace('"', '""') + '"'
            cols = {}
            for _cid, name, ctype, notnull, default, pk in con.execute(f"PRAGMA table_info({q})"):
                cols[name] = {"type": (ctype or "").upper(), "notnull": bool(notnull),
                              "default": default, "pk": int(pk)}
            idx = {}
            for row in con.execute(f"PRAGMA index_list({q})").fetchall():
                iname, unique, origin = row[1], bool(row[2]), row[3]
                if origin == "pk":
                    continue
                icols = [r[2] for r in con.execute(f'PRAGMA index_info("{iname}")')]
                idx[iname if origin == "c" else "|".join(icols)] = {"unique": unique, "columns": icols}
            fks = sorted(
                (r[3], r[2], r[4]) for r in con.execute(f"PRAGMA foreign_key_list({q})")
            )
            out[table] = {"columns": cols, "indexes": idx, "foreign_keys": [list(f) for f in fks]}
        return out
    finally:
        con.close()


def schema_diff(a: dict, b: dict, label_a: str = "A", label_b: str = "B") -> list[str]:
    """Differences between two schema dumps; column ORDER is ignored (ALTER ADD appends)."""
    problems: list[str] = []
    for t in sorted(set(a) | set(b)):
        if t not in b:
            problems.append(f"table only in {label_a}: {t}")
            continue
        if t not in a:
            problems.append(f"table only in {label_b}: {t}")
            continue
        ca, cb = a[t]["columns"], b[t]["columns"]
        for c in sorted(set(ca) | set(cb)):
            if c not in cb:
                problems.append(f"{t}.{c}: only in {label_a}")
            elif c not in ca:
                problems.append(f"{t}.{c}: only in {label_b}")
            elif ca[c] != cb[c]:
                problems.append(f"{t}.{c}: {label_a}={ca[c]} {label_b}={cb[c]}")
        ia, ib = a[t]["indexes"], b[t]["indexes"]
        for i in sorted(set(ia) | set(ib)):
            if i not in ib:
                problems.append(f"{t} index only in {label_a}: {i}")
            elif i not in ia:
                problems.append(f"{t} index only in {label_b}: {i}")
            elif ia[i] != ib[i]:
                problems.append(f"{t} index {i} differs: {ia[i]} vs {ib[i]}")
        if a[t]["foreign_keys"] != b[t]["foreign_keys"]:
            problems.append(f"{t}: foreign keys differ")
    return problems


def cmd_schema(args) -> int:
    Path(args.out).write_text(json.dumps(schema_dump(Path(args.db)), indent=2, sort_keys=True), encoding="utf-8")
    print(f"schema written: {args.out}")
    return 0


def cmd_schema_diff(args) -> int:
    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))
    return _report(schema_diff(a, b, "A", "B"), f"schema diff {args.a} vs {args.b}")


def cmd_baseline(args) -> int:
    fp = fingerprint(Path(args.db))
    Path(args.out).write_text(json.dumps(fp, indent=2, sort_keys=True), encoding="utf-8")
    print(f"baseline written: {args.out}")
    print(f"  tables={fp['table_count']} indexes={fp['index_count']} rows={fp['total_rows']} "
          f"integrity={fp['integrity_check']} fk_violations={fp['foreign_key_violations']}")
    return 0 if fp["integrity_check"] == "ok" else 1


def cmd_compare(args) -> int:
    expected = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    actual = fingerprint(Path(args.db))
    return _report(diff(expected, actual, set(args.allow or []), check_schema=not args.data_only), f"compare {args.db}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def cmd_backup(args) -> int:
    src = Path(args.db)
    dest_arg = Path(args.dest)
    if dest_arg.suffix == ".db":  # an explicit file name
        dest = dest_arg
    else:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = dest_arg / f"{src.stem}-{stamp}.db"
    if dest.exists():
        raise SystemExit(f"refusing to overwrite an existing backup: {dest}")
    source_fp = fingerprint(src)  # read-only snapshot of what we expect to copy

    # SQLite cannot always write where the backup should live (network and FUSE mounts fail
    # with "disk I/O error"). So: back up and verify on local temp disk, then copy plain bytes.
    work = Path(tempfile.mkdtemp(prefix="tc-backup-"))
    partial = dest.with_name(dest.name + ".partial")
    try:
        tmp = work / "backup.db"
        source = _open_readonly(src)
        target = sqlite3.connect(str(tmp))
        try:
            source.backup(target)  # SQLite's online backup API: consistent even if the app is writing
        finally:
            target.close()
            source.close()
        problems = diff(source_fp, fingerprint(tmp), set())
        if problems:
            return _report(problems, "backup verification (nothing written)")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp, partial)
        if _sha256_file(partial) != _sha256_file(tmp):
            print("FAIL backup: the copy differs from the verified file (nothing kept)")
            return 1
        os.replace(partial, dest)  # the final name only ever appears fully written and checked
    finally:
        partial.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
    print(f"backup written: {dest} ({dest.stat().st_size} bytes)")
    print(f"  verified: tables={source_fp['table_count']} rows={source_fp['total_rows']} integrity={source_fp['integrity_check']}")
    return 0


def cmd_verify(args) -> int:
    expected = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    actual = fingerprint(Path(args.backup))
    return _report(diff(expected, actual, set(args.allow or [])), f"restore check {args.backup}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("baseline", help="record row counts, checksums and schema of a database")
    p.add_argument("db")
    p.add_argument("out")
    p.set_defaults(func=cmd_baseline)

    p = sub.add_parser("schema", help="dump table structure (no row data) to JSON")
    p.add_argument("db")
    p.add_argument("out")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("schema-diff", help="compare two schema dumps (column order ignored)")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=cmd_schema_diff)

    p = sub.add_parser("compare", help="compare a database with a saved baseline")
    p.add_argument("baseline")
    p.add_argument("db")
    p.add_argument("--allow", action="append", help="table that is allowed to differ (repeatable)")
    p.add_argument("--data-only", action="store_true", help="skip the schema comparison (for steps that change structure on purpose)")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("backup", help="consistent online backup to a new file, verified after writing")
    p.add_argument("db")
    p.add_argument("dest", help="a directory (timestamped file) or an explicit .db path")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("verify", help="check that a backup file matches a baseline (restore drill)")
    p.add_argument("backup")
    p.add_argument("baseline")
    p.add_argument("--allow", action="append")
    p.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
