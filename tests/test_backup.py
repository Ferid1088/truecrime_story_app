import asyncio
import os
import shutil
import sqlite3
import stat
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa

from app import backup as B
from app.db.base import SessionLocal, engine as app_engine
from app.db.models import AppSetting, BackupRun
from app.utils import utc_now


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        db = SessionLocal()
        db.query(BackupRun).delete()
        db.query(AppSetting).delete()
        db.commit()
        db.close()

    wipe()
    yield
    wipe()


@pytest.fixture
def small_db(tmp_path):
    path = tmp_path / "src.db"
    con = sqlite3.connect(path)
    con.execute("create table cases(id integer primary key, title text)")
    con.executemany("insert into cases(title) values (?)", [(f"case {i}",) for i in range(50)])
    con.commit()
    con.close()
    return sa.create_engine(f"sqlite:///{path}")


def local(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi).astimezone()


# ----------------------------------------------------------------------------------- the file
def test_backup_is_a_complete_verified_copy_with_a_timestamped_name(small_db, tmp_path):
    drive = tmp_path / "drive"
    drive.mkdir()
    out = B.make_backup(small_db, drive, now=local(2026, 10, 11, 22, 15))
    assert out.name == "truecrime-backup-20261011-221500.db"
    assert [p.name for p in drive.iterdir()] == [out.name]            # no .partial left behind
    con = sqlite3.connect(out)
    assert con.execute("select count(*) from cases").fetchone()[0] == 50
    assert con.execute("pragma integrity_check").fetchone()[0] == "ok"
    con.close()
    if os.name == "posix":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_backup_is_one_self_contained_file_even_when_the_source_uses_wal(small_db, tmp_path):
    drive = tmp_path / "drive"
    drive.mkdir()
    w = sqlite3.connect(small_db.url.database)
    w.execute("pragma journal_mode=wal")
    w.execute("insert into cases(title) values ('in the wal')")
    w.commit()
    out = B.make_backup(small_db, drive)
    header = out.read_bytes()[:100]
    assert header[18] == 1 and header[19] == 1                       # rollback-journal mode, not WAL (2)
    con = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    assert con.execute("select count(*) from cases where title='in the wal'").fetchone()[0] == 1
    con.close()
    w.close()
    assert sorted(p.name for p in drive.iterdir()) == [out.name]     # nothing else appears next to it


def test_backup_works_while_the_database_is_being_written(small_db, tmp_path):
    drive = tmp_path / "drive"
    drive.mkdir()
    writer = sqlite3.connect(small_db.url.database)
    writer.execute("pragma journal_mode=wal")
    writer.execute("begin")
    writer.execute("insert into cases(title) values ('uncommitted')")
    out = B.make_backup(small_db, drive)
    writer.rollback()
    writer.close()
    con = sqlite3.connect(out)
    assert con.execute("select count(*) from cases where title='uncommitted'").fetchone()[0] == 0
    con.close()


def test_missing_folder_is_reported_and_never_created(small_db, tmp_path):
    gone = tmp_path / "Volumes" / "ExternalDrive"
    with pytest.raises(B.BackupError, match="Is the drive connected"):
        B.make_backup(small_db, gone)
    assert not gone.exists() and not gone.parent.exists()


def test_a_file_instead_of_a_folder_is_refused(small_db, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(B.BackupError, match="Not a folder"):
        B.make_backup(small_db, f)


def test_not_enough_free_space_is_refused_before_copying(small_db, tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 99, 1))
    with pytest.raises(B.BackupError, match="Not enough free space"):
        B.make_backup(small_db, tmp_path)
    assert list(tmp_path.glob("truecrime-backup-*")) == []


def test_same_second_never_overwrites_an_existing_backup(small_db, tmp_path):
    t = local(2026, 10, 11, 8)
    first = B.make_backup(small_db, tmp_path, now=t)
    before = first.read_bytes()
    with pytest.raises(B.BackupError, match="already exists"):
        B.make_backup(small_db, tmp_path, now=t)
    assert first.read_bytes() == before


def test_a_failed_copy_leaves_no_partial_file(small_db, tmp_path, monkeypatch):
    monkeypatch.setattr(B, "_sha256", lambda p: p.name)       # different "checksums" for the two files
    with pytest.raises(B.BackupError, match="differs"):
        B.make_backup(small_db, tmp_path)
    assert list(tmp_path.glob("truecrime-backup-*")) == []


def test_keep_last_removes_only_our_oldest_backups(small_db, tmp_path):
    decoys = ["notes.txt", "other.db", "truecrime-backup-foo.db", "truecrime-backup-20260101-000000.db.bak"]
    for n in decoys:
        (tmp_path / n).write_text("mine")
    for day in range(1, 6):
        B.make_backup(small_db, tmp_path, keep_last=3, now=local(2026, 10, day, 3))
    names = sorted(p.name for p in tmp_path.iterdir() if B.NAME_RE.match(p.name))
    assert names == ["truecrime-backup-20261003-030000.db", "truecrime-backup-20261004-030000.db",
                     "truecrime-backup-20261005-030000.db"]
    assert all((tmp_path / n).read_text() == "mine" for n in decoys)     # lookalikes and other files untouched


def test_keep_last_zero_keeps_everything(small_db, tmp_path):
    for day in range(1, 4):
        B.make_backup(small_db, tmp_path, keep_last=0, now=local(2026, 10, day, 3))
    assert len(list(tmp_path.glob("truecrime-backup-*.db"))) == 3


# ----------------------------------------------------------------------------------- the schedule
def test_slots_follow_start_time_and_interval():
    s = B.BackupSettings(enabled=True, dir="x", every_hours=24, start_time="02:00")
    assert B.latest_slot(s, local(2026, 10, 11, 1, 0)) == datetime(2026, 10, 10, 2, 0)
    assert B.latest_slot(s, local(2026, 10, 11, 2, 0)) == datetime(2026, 10, 11, 2, 0)
    assert B.next_slot(s, local(2026, 10, 11, 3, 0)) == datetime(2026, 10, 12, 2, 0)
    s6 = B.BackupSettings(enabled=True, dir="x", every_hours=6, start_time="02:00")
    assert B.latest_slot(s6, local(2026, 10, 11, 15)) == datetime(2026, 10, 11, 14, 0)
    assert B.latest_slot(s6, local(2026, 10, 11, 1)) == datetime(2026, 10, 10, 20, 0)
    s48 = B.BackupSettings(enabled=True, dir="x", every_hours=48, start_time="23:30")
    a = B.latest_slot(s48, local(2026, 10, 11, 12))
    assert a.hour == 23 and a.minute == 30 and (B.next_slot(s48, local(2026, 10, 11, 12)) - a) == timedelta(hours=48)


@pytest.mark.parametrize("bad", [dict(every_hours=0), dict(every_hours=721), dict(every_hours="x"),
                                 dict(start_time="25:00"), dict(start_time="2:00"), dict(keep_last=-1),
                                 dict(keep_last=1001)])
def test_invalid_settings_are_rejected(bad):
    with pytest.raises(ValueError):
        B.validate(bad)


def test_due_logic(db_session):
    now = local(2026, 10, 11, 12)
    s = B.BackupSettings(enabled=True, dir="/x", every_hours=24, start_time="02:00")
    assert B.is_due(db_session, s, now)                                    # never ran
    assert not B.is_due(db_session, B.BackupSettings(enabled=False, dir="/x"), now)
    assert not B.is_due(db_session, B.BackupSettings(enabled=True, dir=""), now)
    slot = B._as_utc(B.latest_slot(s, now))
    db_session.add(BackupRun(status="ok", started_at=slot - timedelta(hours=1), dest_dir="/x"))
    db_session.commit()
    assert B.is_due(db_session, s, now)                                    # last success is before today's slot
    db_session.add(BackupRun(status="ok", started_at=slot + timedelta(minutes=5), dest_dir="/x"))
    db_session.commit()
    assert not B.is_due(db_session, s, now)                                # done for this slot


def test_failed_attempt_is_retried_after_thirty_minutes_not_every_minute(db_session):
    now = local(2026, 10, 11, 12)
    s = B.BackupSettings(enabled=True, dir="/x", every_hours=24, start_time="02:00")
    db_session.add(BackupRun(status="failed", started_at=utc_now() - timedelta(minutes=10), dest_dir="/x"))
    db_session.commit()
    assert not B.is_due(db_session, s, utc_now())
    db_session.query(BackupRun).delete()
    db_session.add(BackupRun(status="failed", started_at=utc_now() - timedelta(minutes=45), dest_dir="/x"))
    db_session.commit()
    assert B.is_due(db_session, s, utc_now())


def test_nothing_is_due_while_a_backup_is_running(db_session):
    s = B.BackupSettings(enabled=True, dir="/x", every_hours=24, start_time="02:00")
    db_session.add(BackupRun(status="running", started_at=utc_now(), dest_dir="/x"))
    db_session.commit()
    assert not B.is_due(db_session, s, utc_now())


def test_interrupted_runs_are_marked_failed_at_startup(db_session):
    db_session.add(BackupRun(status="running", started_at=utc_now(), dest_dir="/x"))
    db_session.commit()
    assert B.recover_interrupted(db_session) == 1
    r = db_session.query(BackupRun).one()
    assert r.status == "failed" and "Interrupted" in r.error


# ----------------------------------------------------------------------------------- run + record
def test_run_backup_records_success_and_failure_and_never_raises(small_db, tmp_path):
    drive = tmp_path / "drive"
    drive.mkdir()
    ok = B.run_backup(SessionLocal, small_db, "manual", str(drive))
    assert ok.status == "ok" and ok.size_bytes > 0 and ok.file_path.startswith(str(drive)) and ok.error is None
    bad = B.run_backup(SessionLocal, small_db, "scheduled", str(tmp_path / "unplugged"))
    assert bad.status == "failed" and "Is the drive connected" in bad.error and bad.trigger == "scheduled"


def test_only_one_backup_runs_at_a_time(small_db, tmp_path):
    assert B._run_lock.acquire(blocking=False)
    try:
        with pytest.raises(B.BackupError, match="already running"):
            B.run_backup(SessionLocal, small_db, "manual", str(tmp_path))
    finally:
        B._run_lock.release()


def test_scheduler_tick_runs_a_due_backup_once(small_db, tmp_path):
    drive = tmp_path / "drive"
    drive.mkdir()
    db = SessionLocal()
    B.save_settings(db, B.BackupSettings(enabled=True, dir=str(drive), every_hours=24, start_time="00:00",
                                         keep_last=5))
    db.close()
    first = asyncio.run(B.tick(SessionLocal, small_db))
    second = asyncio.run(B.tick(SessionLocal, small_db))
    assert first is not None and first.status == "ok" and first.trigger == "scheduled"
    assert second is None
    assert len(list(drive.glob("truecrime-backup-*.db"))) == 1


# ----------------------------------------------------------------------------------- the API
@pytest.fixture
def pg_tools(monkeypatch):
    if app_engine.dialect.name == "postgresql":
        monkeypatch.setenv("PATH", "/usr/lib/postgresql/16/bin:" + os.environ["PATH"])
        if not shutil.which("pg_dump"):
            pytest.skip("pg_dump not installed")


def test_api_settings_roundtrip_and_validation(client):
    r = client.get("/api/backup").json()
    assert r["settings"]["enabled"] is False and r["next_run_at"] is None and r["runs"] == []
    body = {"enabled": True, "dir": "/Volumes/Backup", "every_hours": 12, "start_time": "06:30", "keep_last": 7}
    assert client.put("/api/backup/settings", json=body).status_code == 200
    r = client.get("/api/backup").json()
    assert r["settings"] == body and r["next_run_at"]
    assert client.put("/api/backup/settings", json={**body, "start_time": "25:99"}).status_code == 422
    assert client.put("/api/backup/settings", json={**body, "every_hours": 0}).status_code == 422
    assert client.get("/api/backup").json()["settings"] == body          # a rejected save changes nothing


def test_api_run_now_with_a_folder_writes_a_backup_and_remembers_the_folder(client, tmp_path, pg_tools):
    drive = tmp_path / "usb"
    drive.mkdir()
    r = client.post("/api/backup/run", json={"dir": str(drive)})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "ok" and run["trigger"] == "manual"
    assert len(list(drive.glob("truecrime-backup-*"))) == 1
    overview = client.get("/api/backup").json()
    assert overview["settings"]["dir"] == str(drive) and overview["last_ok_at"]
    assert overview["runs"][0]["id"] == run["id"]


def test_api_run_now_reports_an_unplugged_drive_as_a_failed_run(client, tmp_path):
    r = client.post("/api/backup/run", json={"dir": str(tmp_path / "not-there")})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert "Is the drive connected" in r.json()["error"]
    assert not (tmp_path / "not-there").exists()


def test_backup_endpoints_need_the_login_when_a_token_is_set(monkeypatch):
    import httpx

    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "access_token", "tok-123456789")

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return (await c.get("/api/backup")).status_code, (await c.post("/api/backup/run", json={})).status_code

    assert asyncio.run(go()) == (401, 401)
