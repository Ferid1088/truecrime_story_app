"""Persistent real-pipeline templates; no production database is accessed.

Run: PYTHONPATH=.:tests .venv/bin/python tests/soundness_template.py
The child pytest process owns the SQLite connection and exits before file copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOCK_FILES = (
    "tests/mock_payload.py",
    "tests/test_visual_production.py",
    "tests/test_voice_render.py",
    "tests/test_spoken_audio.py",
    "tests/test_host.py",
)


def _digest(paths):
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def manifest() -> dict:
    tracked = subprocess.check_output(
        ["git", "ls-files", "app", "tests", "scripts"], cwd=ROOT, text=True
    ).splitlines()
    # Include new harness files before their phase commit, too.
    code = {ROOT / name for name in tracked if name.endswith(".py")}
    code.update((ROOT / "tests").glob("*.py"))
    code.update((Path(__file__), ROOT / "app/services/soundness.py"))
    return {
        "schema": 1,
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "code_hash": _digest(path for path in code if path.is_file()),
        "config_hash": _digest(path for path in (ROOT / "config").rglob("*") if path.is_file()),
        "mock_version": _digest(ROOT / name for name in MOCK_FILES),
        "languages": ["en", "de", "fa", "ar"],
        "incremental_mode": "off",
        "chapter_mock": "consumed-full-request-sha256-v1",
    }


def ensure_template(destination: Path, postgres_admin_url: str | None = None) -> tuple[Path, dict]:
    """Reuse only an exact code/config/mock match; build in a fresh process."""
    destination = destination.resolve()
    expected = manifest()
    expected["backend"] = "postgresql" if postgres_admin_url else "sqlite"
    manifest_path = destination / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text())["identity"] == expected:
        return destination, {"rebuilt": False, "rebuild_seconds": 0.0}
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Build outside the repo: storage.rel intentionally converts in-repo paths
    # to repo-relative references. External roots keep every movable media
    # reference absolute, allowing one unambiguous root rebase on restore.
    build = Path(tempfile.mkdtemp(prefix="tc-soundness-baseline-")).resolve()
    run = build / "files"
    run.mkdir()
    env = os.environ.copy()
    env.pop("TC_TEST_DATABASE_URL", None)
    database_url = f"sqlite:///{build / 'pipeline.db'}"
    database_name = None
    if postgres_admin_url:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        database_name = "tc_soundness_" + uuid.uuid4().hex
        admin = create_engine(postgres_admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))
        admin.dispose()
        database_url = (
            make_url(postgres_admin_url)
            .set(database=database_name)
            .render_as_string(hide_password=False)
        )
    env.update(
        {
            "TC_COPY_DATABASE_URL": database_url,
            "SOUNDNESS_CHAPTER_PAYLOAD": "1",
            "SOUNDNESS_CHILD": "1",
            "SOUNDNESS_FROM_ZERO": "1",
            "SOUNDNESS_SEED_MASTER": "1",
            "SOUNDNESS_RUN_ROOT": str(run),
            "SOUNDNESS_CAPTURE": str(build / "snapshot.json"),
            "SOUNDNESS_PAYLOAD_LOG": str(build / "requests.jsonl"),
            "SOUNDNESS_SNAPSHOT_VALUES": str(build / "snapshot-values.json"),
            "PYTHONPATH": str(ROOT),
        }
    )
    started = time.perf_counter()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-s",
            "tests/test_visual_production.py::test_four_language_shadow_pipeline_real_job",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    elapsed = time.perf_counter() - started
    (build / "run.log").write_text(completed.stdout + completed.stderr)
    if completed.returncode:
        raise RuntimeError(f"Baseline failed; evidence preserved at {build / 'run.log'}")
    # A file copy is valid only after all WAL data has reached the main file.
    if not postgres_admin_url:
        with sqlite3.connect(build / "pipeline.db") as db:
            assert db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    metadata = {
        "identity": expected,
        "source_files_root": str(run),
        "baseline_seconds": elapsed,
        "database_name": database_name,
    }
    (build / "manifest.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    if destination.exists():
        archive = Path(tempfile.mkdtemp(prefix="stale-template-", dir=destination.parent))
        destination.rename(archive / "template")  # old evidence remains recoverable
    build.rename(destination)
    return destination, {"rebuilt": True, "rebuild_seconds": elapsed}


def restore_template(
    template: Path, destination: Path, postgres_admin_url: str | None = None
) -> dict:
    """SQLite file copy + all output files into a NEW per-test directory.

    Rebase only the absolute harness directory in persisted paths, not content,
    IDs or hashes. Generated IDs stay intact, so source rows can be edited once.
    """
    started = time.perf_counter()
    destination.mkdir(parents=True, exist_ok=False)
    metadata = json.loads((template / "manifest.json").read_text())
    backend = metadata["identity"]["backend"]
    if backend == "sqlite":
        shutil.copy2(template / "pipeline.db", destination / "pipeline.db")
    shutil.copytree(template / "files", destination / "files")
    old, new = metadata["source_files_root"], str((destination / "files").resolve())
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import make_url

    database_url = f"sqlite:///{destination / 'pipeline.db'}"
    target = None
    if backend == "postgresql":
        if not postgres_admin_url:
            raise ValueError("Postgres restore requires explicit disposable admin connection")
        target = "tc_soundness_" + uuid.uuid4().hex
        admin = create_engine(postgres_admin_url)
        clone_postgres_template(admin, metadata["database_name"], target)
        admin.dispose()
        database_url = (
            make_url(postgres_admin_url).set(database=target).render_as_string(hide_password=False)
        )
    engine = create_engine(database_url)
    inspector = inspect(engine)
    with engine.begin() as db:
        for table in inspector.get_table_names():
            for column in inspector.get_columns(table):
                name = column["name"]
                if any(token in str(column["type"]).upper() for token in ("CHAR", "TEXT")):
                    db.execute(
                        text(
                            f'UPDATE "{table}" SET "{name}"=replace("{name}", :old, :new) '
                            f'WHERE "{name}" LIKE :pattern'
                        ),
                        {"old": old, "new": new, "pattern": f"%{old}%"},
                    )
    engine.dispose()
    for path in (destination / "files").rglob("*.json"):
        data = path.read_text()
        if old in data:
            path.write_text(data.replace(old, new))
    return {
        "restore_seconds": time.perf_counter() - started,
        "database": target or str(destination / "pipeline.db"),
        "files": new,
    }


def clone_postgres_template(admin_engine, source: str, target: str) -> None:
    """Clone a quiescent disposable soundness DB. Never accepts a live DB name.

    Caller closes ALL source sessions before calling. PostgreSQL itself refuses
    TEMPLATE cloning if any source session is still connected. No terminate or
    DROP operation is performed here.
    """
    import re

    from sqlalchemy import text

    for name in (source, target):
        if not re.fullmatch(r"tc_soundness_[a-z0-9_]+", name):
            raise ValueError("Only explicitly disposable tc_soundness_* databases may be cloned")
    if source == target:
        raise ValueError("Source and destination must differ")
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'CREATE DATABASE "{target}" TEMPLATE "{source}"'))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres-admin-url")
    args = parser.parse_args()
    template, timings = ensure_template(
        ROOT / "data/dev/soundness" / ("template-pg" if args.postgres_admin_url else "template"),
        args.postgres_admin_url,
    )
    restore_parent = Path(tempfile.mkdtemp(prefix="tc-soundness-restore-"))
    timings.update(restore_template(template, restore_parent / "run", args.postgres_admin_url))
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from app.services.soundness import saved_output_snapshot

    restored_url = (
        make_url(args.postgres_admin_url).set(database=timings["database"])
        if args.postgres_admin_url
        else f"sqlite:///{timings['database']}"
    )
    engine = create_engine(restored_url)
    with Session(engine) as db:
        actual = saved_output_snapshot(
            db,
            file_roots=[
                Path(timings["files"]) / name
                for name in ("cases", "audio", "lib", "intros")
            ],
        )
    engine.dispose()
    expected = json.loads((template / "snapshot.json").read_text())
    assert actual == expected, "Restored snapshot differs from baseline"
    timings["restored_snapshot_diff"] = 0
    print(json.dumps({"template": str(template), **timings}, sort_keys=True))
