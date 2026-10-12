#!/usr/bin/env python3
"""Reconcile and smoke-test a SQLite database copy, never the source file."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from alembic import command
from sqlalchemy import create_engine

from app.db import migrate

NULLABILITY_ALLOWLIST = {
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
LENGTH_ALLOWLIST = {("epistemic_claims", "origin")}
FOREIGN_KEY_ALLOWLIST = {
    ("agent_runs", "documentary_job_id", "documentary_jobs", "id"),
    ("story_versions", "master_version_id", "story_versions", "id"),
}
NEW_TABLES = {
    "app_settings", "artifact_deps", "artifacts", "backup_runs",
    "edit_log", "override_log", "policy_events", "provider_calls",
}
SERVICE_NAMES = ("searxng", "api", "worker", "frontend")


class LiveApplyError(RuntimeError):
    """A live apply failed; the message includes whether rollback succeeded."""


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve()}"


def table_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {
            table: int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in sorted(tables)
            if table != "alembic_version"
        }


def integrity(path: Path) -> tuple[str, list[tuple]]:
    with sqlite3.connect(path) as connection:
        return (
            connection.execute("PRAGMA integrity_check").fetchone()[0],
            connection.execute("PRAGMA foreign_key_check").fetchall(),
        )


def _same_rows(reference: Path, candidate: Path) -> None:
    expected = table_counts(reference)
    actual = table_counts(candidate)
    if expected != actual:
        raise RuntimeError("row counts differ")


def verify_database_pair(reference: Path, candidate: Path) -> None:
    for path in (reference, candidate):
        check, foreign_keys = integrity(path)
        if check != "ok" or foreign_keys:
            raise RuntimeError(f"{path}: integrity={check}, foreign_keys={foreign_keys}")
    _same_rows(reference, candidate)


def _pidfile_paths(root: Path) -> list[Path]:
    return [root / "data" / "dev" / f"{name}.pid" for name in ("api", "worker", "frontend")] + [
        root / "data" / "searxng" / "searxng.pid"
    ]


def ensure_live_quiescent(root: Path, live: Path) -> None:
    status = subprocess.run(
        [str(root / "scripts" / "dev_up.sh"), "--status"],
        cwd=root, check=False, capture_output=True, text=True,
    )
    report = (status.stdout or "") + (status.stderr or "")
    print("Pre-flight service status:")
    print(report.rstrip())
    if status.returncode != 0:
        raise RuntimeError("dev_up.sh --status failed")
    running = [line for line in report.splitlines()
               if any(line.startswith(f"{name}: running") for name in SERVICE_NAMES)]
    pid_running = []
    for pidfile in _pidfile_paths(root):
        if not pidfile.exists():
            continue
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
        except ValueError:
            pid_running.append(f"{pidfile} contains an invalid PID")
            continue
        if subprocess.run(["kill", "-0", str(pid)], check=False,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            pid_running.append(f"{pidfile} -> {pid}")
        else:
            print(f"stale pidfile reported (not blocking): {pidfile}")
    if running or pid_running:
        raise RuntimeError("TrueCrime service is running: " + "; ".join(running + pid_running))

    lsof = shutil.which("lsof")
    if not lsof:
        raise RuntimeError("lsof is required for the live database open-handle guard")
    result = subprocess.run([lsof, "-nP", "--", str(live)], check=False,
                            capture_output=True, text=True)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if lines:
        print("Open database handles:")
        print("\n".join(lines))
    writable = []
    for line in lines[1:]:
        fields = line.split()
        fd = fields[3] if len(fields) > 3 else "?"
        if not fd.endswith("r"):
            writable.append(line)
    if writable:
        raise RuntimeError("truecrime.db has non-read-only open handles")
    if lines:
        print("Only read-only database handles found; continuing.")
    else:
        print("No database open handles found.")


def copy_database(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        raise SystemExit("Refusing to reconcile in place: --copy must differ from --source.")
    if not source.is_file():
        raise SystemExit(f"Source database does not exist: {source}")
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite existing copy: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_connection, sqlite3.connect(destination) as target:
        source_connection.backup(target)


def restore_database(backup: Path, target: Path) -> None:
    """Restore through SQLite backup API and atomically replace the target."""
    for sidecar in (Path(f"{target}-wal"), Path(f"{target}-shm")):
        if sidecar.exists():
            sidecar.unlink()
            print(f"Removed stale sidecar: {sidecar}")
    temporary = target.with_name(f".{target.name}.restore-{uuid.uuid4().hex}.db")
    try:
        copy_database(backup, temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    verify_database_pair(backup, target)


def compare_with_fresh_head(path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="truecrime-fresh-head-") as directory:
        fresh = Path(directory) / "fresh.db"
        command.upgrade(migrate.alembic_config(sqlite_url(fresh)), "head")
        actual = migrate._live_signature(create_engine(sqlite_url(path)))
        expected = migrate._live_signature(create_engine(sqlite_url(fresh)))
    if set(actual) != set(expected):
        raise RuntimeError("reconciled schema differs in table set")
    differences: list[tuple] = []
    missing_fks: set[tuple[str, str, str, str]] = set()
    for table in sorted(actual):
        left, right = actual[table], expected[table]
        if left["indexes"] != right["indexes"]:
            differences.append(("indexes", table))
        for column in sorted(set(left["columns"]) | set(right["columns"])):
            if column not in left["columns"] or column not in right["columns"]:
                differences.append(("column_presence", table, column))
                continue
            live = left["columns"][column]
            head = right["columns"][column]
            if live == head:
                continue
            if (table, column) in NULLABILITY_ALLOWLIST and live[0] == head[0] and live[2] == head[2]:
                continue
            if (table, column) in LENGTH_ALLOWLIST and live[1:] == head[1:]:
                continue
            differences.append(("column", table, column, live, head))
        live_fks = set(left["foreign_keys"])
        head_fks = set(right["foreign_keys"])
        for fk in live_fks - head_fks:
            differences.append(("extra_fk", table, fk))
        for fk in head_fks - live_fks:
            missing_fks.add((table, fk[0], fk[1], fk[2]))
    if not missing_fks <= FOREIGN_KEY_ALLOWLIST:
        differences.append(("unexpected_missing_fks", sorted(missing_fks - FOREIGN_KEY_ALLOWLIST)))
    if differences:
        raise RuntimeError(f"schema differences outside SQLite allowlist: {differences}")
    print("Schema equality: only the documented SQLite allowlist remains.")


def reconcile(path: Path) -> dict[str, int]:
    before = table_counts(path)
    cfg = migrate.alembic_config(sqlite_url(path))
    command.stamp(cfg, "0009")
    command.upgrade(cfg, "head")
    after = table_counts(path)
    if before != {table: after[table] for table in before}:
        raise RuntimeError("row counts changed during reconciliation")
    check, foreign_keys = integrity(path)
    if check != "ok" or foreign_keys:
        raise RuntimeError(f"integrity failure: {check}; foreign keys: {foreign_keys}")
    compare_with_fresh_head(path)
    with sqlite3.connect(path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    print(f"Reconciled {path}: alembic_version={revision}, tables={len(after)}")
    return before


def verify_post_migration(live: Path, backup: Path) -> dict[str, object]:
    backup_counts = table_counts(backup)
    live_counts = table_counts(live)
    if backup_counts != {table: live_counts.get(table) for table in backup_counts}:
        raise RuntimeError("post-migration row counts differ from the backup")
    if not set(live_counts) >= NEW_TABLES:
        raise RuntimeError("one or more reconciliation tables are missing")
    nonempty = {table: live_counts[table] for table in NEW_TABLES if live_counts[table] != 0}
    if nonempty:
        raise RuntimeError(f"new reconciliation tables are not empty: {nonempty}")
    check, foreign_keys = integrity(live)
    if check != "ok" or foreign_keys:
        raise RuntimeError(f"post-migration integrity={check}, foreign_keys={foreign_keys}")
    with sqlite3.connect(live) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    from alembic.script import ScriptDirectory

    head = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "migrations")).get_current_head()
    if revision != head:
        raise RuntimeError(f"alembic_version={revision}, expected {head}")
    compare_with_fresh_head(live)
    return {"pre_existing_tables": len(backup_counts), "new_tables": sorted(NEW_TABLES),
            "alembic_version": revision}


def apply_live(root: Path | None = None, live: Path | None = None,
               backup_dir: Path | None = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    live = live or root / "truecrime.db"
    backup_dir = backup_dir or root / "data" / "backups"
    ensure_live_quiescent(root, live)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S%z")
    backup = backup_dir / f"truecrime-pre-phaseC-{timestamp}.db"
    if backup.exists():
        raise RuntimeError(f"backup already exists: {backup}")
    copy_database(live, backup)
    print(f"Created backup: {backup}")
    verify_database_pair(backup, live)
    print(f"Pre-migration backup verification passed for {len(table_counts(backup))} tables.")
    live_url = sqlite_url(live)
    try:
        cfg = migrate.alembic_config(live_url)
        command.stamp(cfg, "0009")
        command.upgrade(cfg, "head")
        result = verify_post_migration(live, backup)
        print("Live migration verification passed: " + json.dumps(result, sort_keys=True))
        return backup
    except Exception as error:
        print(f"Live migration verification failed: {error}", file=sys.stderr)
        try:
            restore_database(backup, live)
        except Exception as rollback_error:
            raise LiveApplyError(
                f"automatic rollback FAILED after {error}: {rollback_error}"
            ) from rollback_error
        print("Automatic rollback completed and backup row counts/integrity verified.", file=sys.stderr)
        raise LiveApplyError(f"live migration rolled back: {error}") from error


def job_snapshot(path: Path) -> dict:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        result = {}
        for table in ("documentary_jobs", "research_jobs"):
            rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY id')]
            result[table] = rows
        result["provider_calls"] = connection.execute("SELECT count(*) FROM provider_calls").fetchone()[0]
        return result


def safe_start(path: Path) -> None:
    before = job_snapshot(path)
    environment = os.environ.copy()
    environment.update({
        "TRUECRIME_DATABASE_URL": sqlite_url(path),
        "TRUECRIME_JOB_RUNNER": "external",
        "TRUECRIME_JOB_MAX_AUTO_RESUMES": "0",
        "TRUECRIME_DISABLE_SCHEDULER": "1",
    })
    with tempfile.NamedTemporaryFile(prefix="truecrime-worker-", suffix=".log") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.worker"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(2)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        log_file.seek(0)
        worker_log = log_file.read().decode("utf-8", errors="replace")
    if process.returncode not in (0, -signal.SIGTERM):
        raise RuntimeError(f"safe worker exited {process.returncode}: {worker_log[-2000:]}")
    after = job_snapshot(path)
    if before != after:
        raise RuntimeError("safe worker changed job state or provider-call count")
    print("Safe worker start: no job state or provider-call changes.")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get_json(url: str) -> tuple[int, object]:
    try:
        with urlopen(Request(url, method="GET"), timeout=10) as response:
            return response.status, json.loads(response.read())
    except (HTTPError, URLError) as error:
        raise RuntimeError(f"GET {url} failed: {error}") from error


def post_json(url: str, body: object) -> tuple[int, object]:
    request = Request(url, data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except (HTTPError, URLError) as error:
        raise RuntimeError(f"POST {url} failed: {error}") from error


def api_smoke(path: Path) -> None:
    environment = os.environ.copy()
    environment.update({
        "TRUECRIME_DATABASE_URL": sqlite_url(path),
        "TRUECRIME_JOB_RUNNER": "external",
        "TRUECRIME_DISABLE_SCHEDULER": "1",
    })
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        cases = None
        for _ in range(30):
            try:
                status, cases = get_json(f"{base}/api/cases")
                if status == 200:
                    break
            except RuntimeError:
                time.sleep(0.5)
        if not isinstance(cases, list):
            raise RuntimeError("API did not become ready")
        checks = {
            "/api/cases": cases,
            "/api/documentary/jobs": get_json(f"{base}/api/documentary/jobs")[1],
            "/api/documentary/settings": get_json(f"{base}/api/documentary/settings")[1],
            "/api/ops/summary": get_json(f"{base}/api/ops/summary")[1],
            "/api/backup": get_json(f"{base}/api/backup")[1],
        }
        case_id = cases[0]["id"] if cases else None
        if case_id is not None:
            checks[f"/api/documentary/{case_id}/artifacts"] = get_json(
                f"{base}/api/documentary/{case_id}/artifacts")[1]
        with tempfile.TemporaryDirectory(prefix="truecrime-dashboard-backup-") as directory:
            status, backup = post_json(f"{base}/api/backup/run", {"dir": directory})
            if status != 200 or backup.get("status") != "ok":
                raise RuntimeError(f"dashboard backup failed: {backup}")
        print("API smoke: " + ", ".join(checks))
        print("Dashboard backup: ok")
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def mocked_job(path: Path) -> None:
    environment = os.environ.copy()
    environment.update({
        "TC_COPY_DATABASE_URL": sqlite_url(path),
        "TRUECRIME_DATABASE_URL": sqlite_url(path),
        "TRUECRIME_JOB_RUNNER": "external",
        "TRUECRIME_DISABLE_SCHEDULER": "1",
    })
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
         "tests/test_visual_production.py::test_one_button_pipeline_pilot_end_to_end"],
        cwd=Path(__file__).resolve().parents[1], env=environment, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"mocked documentary job test failed with exit {result.returncode}")
    print("Mocked documentary job: completed through the real pilot pipeline.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("truecrime.db"))
    parser.add_argument("--copy", type=Path)
    parser.add_argument("--apply-live", action="store_true")
    parser.add_argument("--safe-start", action="store_true")
    parser.add_argument("--api-smoke", action="store_true")
    parser.add_argument("--mocked-job", action="store_true")
    args = parser.parse_args()
    if args.apply_live:
        apply_live()
        return 0
    if args.copy is None:
        parser.error("--copy is required unless --apply-live is used")
    source = args.source.resolve()
    destination = args.copy.resolve()
    copy_database(source, destination)
    counts = reconcile(destination)
    print("Pre-existing row counts: " + json.dumps(counts, sort_keys=True))
    if args.safe_start:
        safe_start(destination)
    if args.api_smoke:
        api_smoke(destination)
    if args.mocked_job:
        mocked_job(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
