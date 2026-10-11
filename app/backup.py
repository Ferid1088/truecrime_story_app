"""Database backups to a folder (typically an external drive), by hand or on a schedule.

* SQLite: SQLite's online backup API gives a consistent copy while the app runs. The copy is made and
  checked on local temporary disk, then copied byte for byte to the target and compared by checksum, so
  the final file name only ever appears fully written. (External and network drives often cannot host a
  live SQLite write, which is why the copy is made locally first.)
* PostgreSQL: `pg_dump -Fc` (needs the PostgreSQL client tools on PATH).
* Files are named `truecrime-backup-YYYYMMDD-HHMMSS.db|dump` (local time). Only files with that name
  pattern are ever deleted by the "keep the last N" rule.
* The target folder must already exist. It is never created: an unplugged drive would otherwise be
  replaced by a folder on the internal disk.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import AppSetting, BackupRun
from app.utils import ensure_utc, utc_now

log = logging.getLogger(__name__)

SETTINGS_KEY = "backup"
NAME_RE = re.compile(r"^truecrime-backup-\d{8}-\d{6}\.(db|dump)$")
RETRY_AFTER = timedelta(minutes=30)
_run_lock = threading.Lock()


class BackupError(Exception):
    """A backup that failed for a reason the operator can act on (message is shown in the UI)."""


# ------------------------------------------------------------------------------------------ settings
@dataclass
class BackupSettings:
    enabled: bool = False
    dir: str = ""
    every_hours: int = 24
    start_time: str = "02:00"
    keep_last: int = 14


def validate(raw: dict) -> BackupSettings:
    try:
        every = int(raw.get("every_hours", 24))
        keep = int(raw.get("keep_last", 14))
    except (TypeError, ValueError) as e:
        raise ValueError("every_hours and keep_last must be whole numbers") from e
    start = str(raw.get("start_time", "02:00"))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", start):
        raise ValueError("start_time must look like 02:00")
    if not 1 <= every <= 24 * 30:
        raise ValueError("every_hours must be between 1 and 720")
    if not 0 <= keep <= 1000:
        raise ValueError("keep_last must be between 0 (keep everything) and 1000")
    return BackupSettings(enabled=bool(raw.get("enabled", False)), dir=str(raw.get("dir", "")).strip(),
                          every_hours=every, start_time=start, keep_last=keep)


def load_settings(db: Session) -> BackupSettings:
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None:
        return BackupSettings()
    try:
        return validate(json.loads(row.value_json))
    except (ValueError, json.JSONDecodeError):
        return BackupSettings()


def save_settings(db: Session, s: BackupSettings) -> BackupSettings:
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None:
        row = AppSetting(key=SETTINGS_KEY)
        db.add(row)
    row.value_json = json.dumps(asdict(s))
    row.updated_at = utc_now()
    db.commit()
    return s


# ------------------------------------------------------------------------------------------ schedule
def _local_naive(now: datetime | None) -> datetime:
    return (now or utc_now()).astimezone().replace(tzinfo=None)


def latest_slot(s: BackupSettings, now: datetime | None = None) -> datetime:
    """Most recent scheduled time (local wall clock) at or before now: start_time + k * every_hours."""
    h, m = (int(x) for x in s.start_time.split(":"))
    ref = datetime(2000, 1, 1, h, m)
    step = timedelta(hours=s.every_hours)
    n = (_local_naive(now) - ref) // step
    return ref + n * step


def next_slot(s: BackupSettings, now: datetime | None = None) -> datetime:
    return latest_slot(s, now) + timedelta(hours=s.every_hours)


def _as_utc(naive_local: datetime) -> datetime:
    return naive_local.astimezone().astimezone(utc_now().tzinfo)


def is_due(db: Session, s: BackupSettings, now: datetime | None = None) -> bool:
    """Due when the latest slot has no successful backup after it. A failed attempt is retried after 30 minutes
    (so an unplugged drive does not cause a failing run every minute). A computer that was off at the slot
    makes one backup when it is back, never a pile-up."""
    if not s.enabled or not s.dir:
        return False
    now = now or utc_now()
    slot = _as_utc(latest_slot(s, now))
    if db.query(BackupRun).filter(BackupRun.status == "running").first():
        return False
    ok = (db.query(BackupRun).filter(BackupRun.status == "ok", BackupRun.started_at >= slot).first())
    if ok:
        return False
    last = db.query(BackupRun).order_by(BackupRun.started_at.desc()).first()
    if last is not None and ensure_utc(last.started_at) >= slot and now - ensure_utc(last.started_at) < RETRY_AFTER:
        return False
    return True


# ------------------------------------------------------------------------------------------ the backup
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _sqlite_file(engine) -> Path:
    name = engine.url.database
    if not name or name == ":memory:":
        raise BackupError("The database is in memory; there is nothing to back up.")
    return Path(name).resolve()


def _dump_sqlite(engine, tmp: Path) -> None:
    src_path = _sqlite_file(engine)
    if not src_path.exists():
        raise BackupError(f"Database file not found: {src_path}")
    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    target = sqlite3.connect(str(tmp))
    try:
        source.backup(target)
        # One self-contained file: without this the copy keeps the source's WAL mode and opening it
        # later would create -wal/-shm files next to it on the drive.
        target.execute("PRAGMA journal_mode=DELETE")
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("The copy failed its integrity check; nothing was written to the drive.")
        if target.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0:
            raise BackupError("The copy contains no tables; nothing was written to the drive.")
    finally:
        target.close()
        source.close()


def _dump_postgres(engine, tmp: Path) -> None:
    exe = shutil.which("pg_dump")
    if not exe:
        raise BackupError("pg_dump is not installed on this machine (PostgreSQL client tools are required).")
    u = engine.url
    env = {**os.environ, "PGPASSWORD": u.password or ""}
    cmd = [exe, "-Fc", "-h", u.host or "localhost", "-p", str(u.port or 5432), "-U", u.username or "postgres",
           "-f", str(tmp), u.database or ""]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise BackupError("pg_dump failed: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1][:300])
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise BackupError("pg_dump produced an empty file; nothing was written to the drive.")


def check_target(dest: Path, needed_bytes: int) -> None:
    if not str(dest).strip():
        raise BackupError("No backup folder is set.")
    if not dest.exists():
        raise BackupError(f"Folder not found: {dest}. Is the drive connected? (The folder is never created automatically.)")
    if not dest.is_dir():
        raise BackupError(f"Not a folder: {dest}")
    if not os.access(dest, os.W_OK):
        raise BackupError(f"No permission to write to {dest}")
    free = shutil.disk_usage(dest).free
    if free < needed_bytes * 1.1:
        raise BackupError(f"Not enough free space on the drive: {free // 1_000_000} MB free, "
                          f"{needed_bytes // 1_000_000} MB needed.")


def prune(dest: Path, keep_last: int) -> list[Path]:
    """Delete the oldest backups beyond keep_last. Touches only files named like our own backups."""
    if keep_last <= 0:
        return []
    mine = sorted((p for p in dest.iterdir() if p.is_file() and NAME_RE.match(p.name)), key=lambda p: p.name)
    doomed = mine[:-keep_last] if len(mine) > keep_last else []
    for p in doomed:
        p.unlink(missing_ok=True)
    return doomed


def make_backup(engine, dest: Path, keep_last: int = 0, now: datetime | None = None) -> Path:
    """Write one verified backup file into dest and return its path."""
    kind = engine.dialect.name
    if kind not in ("sqlite", "postgresql"):
        raise BackupError(f"Backups are not supported for {kind}.")
    size_hint = _sqlite_file(engine).stat().st_size if kind == "sqlite" and _sqlite_file(engine).exists() else 50_000_000
    check_target(dest, size_hint)
    stamp = (now or utc_now()).astimezone().strftime("%Y%m%d-%H%M%S")
    final = dest / f"truecrime-backup-{stamp}.{'db' if kind == 'sqlite' else 'dump'}"
    if final.exists():
        raise BackupError(f"A backup with this name already exists: {final.name}")
    partial = dest / (final.name + ".partial")
    work = Path(tempfile.mkdtemp(prefix="tc-backup-"))
    try:
        tmp = work / "backup"
        (_dump_sqlite if kind == "sqlite" else _dump_postgres)(engine, tmp)
        shutil.copyfile(tmp, partial)
        if _sha256(partial) != _sha256(tmp):
            raise BackupError("The file on the drive differs from the checked copy; it was discarded.")
        try:
            os.chmod(partial, 0o600)  # a backup holds everything: owner only
        except OSError:
            pass  # some drive formats have no permissions
        os.replace(partial, final)
    except OSError as e:
        raise BackupError(f"Could not write to {dest}: {e.strerror or e}") from e
    finally:
        partial.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
    prune(dest, keep_last)
    return final


def run_backup(session_factory, engine, trigger: str, dest_dir: str | None = None) -> BackupRun:
    """Run one backup, record it, never raise. Only one runs at a time."""
    if not _run_lock.acquire(blocking=False):
        raise BackupError("A backup is already running.")
    db = session_factory()
    try:
        settings = load_settings(db)
        folder = (dest_dir if dest_dir is not None else settings.dir).strip()
        run = BackupRun(trigger=trigger, status="running", dest_dir=folder)
        db.add(run)
        db.commit()
        try:
            path = make_backup(engine, Path(folder).expanduser(), settings.keep_last)
            run.status, run.file_path, run.size_bytes = "ok", str(path), path.stat().st_size
        except BackupError as e:
            run.status, run.error = "failed", str(e)
            log.warning("backup failed: %s", e)
        except Exception as e:  # noqa: BLE001  (record whatever happened; the schedule must keep going)
            run.status, run.error = "failed", f"{type(e).__name__}: {e}"[:500]
            log.exception("backup crashed")
        run.finished_at = utc_now()
        db.commit()
        db.refresh(run)
        return run
    finally:
        db.close()
        _run_lock.release()


def recover_interrupted(db: Session) -> int:
    """A backup row still 'running' after a restart was cut short."""
    n = 0
    for r in db.query(BackupRun).filter(BackupRun.status == "running").all():
        r.status, r.error, r.finished_at = "failed", "Interrupted (the app was stopped during the backup).", utc_now()
        n += 1
    db.commit()
    return n


# ------------------------------------------------------------------------------------------ scheduler
_task: asyncio.Task | None = None


async def tick(session_factory=None, engine=None) -> BackupRun | None:
    from app.db.base import SessionLocal, engine as default_engine

    factory = session_factory or SessionLocal
    db = factory()
    try:
        s = load_settings(db)
        if not is_due(db, s):
            return None
    finally:
        db.close()
    try:
        return await asyncio.to_thread(run_backup, factory, engine or default_engine, "scheduled")
    except BackupError:
        return None


async def _loop(poll_seconds: float):
    while True:
        try:
            await tick()
        except Exception as e:  # noqa: BLE001
            log.warning("backup scheduler tick failed: %s", e)
        await asyncio.sleep(poll_seconds)


def disabled() -> bool:
    from app.core.env import env_value

    return (env_value("TRUECRIME_DISABLE_SCHEDULER", "") or "").lower() in (
        "1", "true", "yes")


def start(poll_seconds: float = 60.0) -> bool:
    global _task
    if disabled() or (_task is not None and not _task.done()):
        return False
    _task = asyncio.get_event_loop().create_task(_loop(poll_seconds))
    return True


def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
