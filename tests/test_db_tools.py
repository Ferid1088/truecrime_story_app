"""The migration's safety tools: baseline/compare/backup/verify must be exact and must never touch the source."""
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "db_tools.py"
_spec = importlib.util.spec_from_file_location("db_tools", _PATH)
db_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_tools)


def _make_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE cases (id INTEGER PRIMARY KEY, title TEXT, score REAL, payload BLOB, note TEXT);
        CREATE TABLE facts (id INTEGER PRIMARY KEY, case_id INTEGER REFERENCES cases(id), claim TEXT);
        CREATE INDEX ix_facts_case ON facts(case_id);
        CREATE TABLE empty_table (id INTEGER PRIMARY KEY);
        """
    )
    con.executemany(
        "INSERT INTO cases VALUES (?,?,?,?,?)",
        [(1, "Der Fall Müller", 0.5, b"\x00\x01\xff", None), (2, "قضية خفية", 1.25, b"", "ok"), (3, "رد خاموش", -3.0, None, "")],
    )
    con.executemany("INSERT INTO facts VALUES (?,?,?)", [(1, 1, "a"), (2, 1, "b"), (3, 2, "c")])
    con.commit()
    con.close()
    return path


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def db(tmp_path):
    return _make_db(tmp_path / "t.db")


def test_baseline_records_counts_checksums_and_health(db, tmp_path):
    out = tmp_path / "b.json"
    assert db_tools.main(["baseline", str(db), str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["table_count"] == 3 and data["total_rows"] == 6 and data["index_count"] == 1
    assert data["integrity_check"] == "ok" and data["foreign_key_violations"] == 0
    assert data["tables"]["cases"]["rows"] == 3 and data["tables"]["empty_table"]["rows"] == 0
    assert len(data["tables"]["cases"]["sha256"]) == 64


def test_baseline_and_compare_never_modify_the_source(db, tmp_path):
    before = _file_sha(db)
    db_tools.main(["baseline", str(db), str(tmp_path / "b.json")])
    db_tools.main(["compare", str(tmp_path / "b.json"), str(db)])
    assert _file_sha(db) == before
    assert not list(tmp_path.glob("t.db-*")), "no journal or wal side files may appear"


def test_compare_passes_on_identical_database(db, tmp_path, capsys):
    db_tools.main(["baseline", str(db), str(tmp_path / "b.json")])
    assert db_tools.main(["compare", str(tmp_path / "b.json"), str(db)]) == 0
    assert "OK" in capsys.readouterr().out


def test_compare_detects_changed_content_with_same_row_count(db, tmp_path, capsys):
    db_tools.main(["baseline", str(db), str(tmp_path / "b.json")])
    con = sqlite3.connect(db)
    con.execute("UPDATE cases SET title='changed' WHERE id=2")
    con.commit()
    con.close()
    assert db_tools.main(["compare", str(tmp_path / "b.json"), str(db)]) == 1
    assert "cases: same row count (3) but contents differ" in capsys.readouterr().out


def test_compare_detects_deleted_and_added_rows_and_tables(db, tmp_path, capsys):
    db_tools.main(["baseline", str(db), str(tmp_path / "b.json")])
    con = sqlite3.connect(db)
    con.execute("DELETE FROM facts WHERE id=3")
    con.execute("CREATE TABLE surprise (id INTEGER)")
    con.commit()
    con.close()
    assert db_tools.main(["compare", str(tmp_path / "b.json"), str(db)]) == 1
    out = capsys.readouterr().out
    assert "facts: row count 3 -> 2" in out and "unexpected table: surprise" in out


def test_compare_detects_a_missing_table(db, tmp_path, capsys):
    db_tools.main(["baseline", str(db), str(tmp_path / "b.json")])
    con = sqlite3.connect(db)
    con.execute("DROP TABLE empty_table")
    con.commit()
    con.close()
    assert db_tools.main(["compare", str(tmp_path / "b.json"), str(db)]) == 1
    assert "table missing: empty_table" in capsys.readouterr().out


def test_allow_exempts_only_the_named_table(db, tmp_path):
    db_tools.main(["baseline", str(db), str(tmp_path / "b.json")])
    con = sqlite3.connect(db)
    con.execute("UPDATE facts SET claim='x'")
    con.commit()
    con.close()
    assert db_tools.main(["compare", str(tmp_path / "b.json"), str(db), "--allow", "facts"]) == 0
    con = sqlite3.connect(db)
    con.execute("UPDATE cases SET score=9")
    con.commit()
    con.close()
    assert db_tools.main(["compare", str(tmp_path / "b.json"), str(db), "--allow", "facts"]) == 1


def test_schema_change_is_reported(db, tmp_path, capsys):
    db_tools.main(["baseline", str(db), str(tmp_path / "b.json")])
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE cases ADD COLUMN extra TEXT")
    con.commit()
    con.close()
    assert db_tools.main(["compare", str(tmp_path / "b.json"), str(db)]) == 1
    assert "schema differs" in capsys.readouterr().out


def test_backup_is_verified_identical_and_leaves_source_untouched(db, tmp_path):
    before = _file_sha(db)
    dest_dir = tmp_path / "backups"
    assert db_tools.main(["backup", str(db), str(dest_dir)]) == 0
    files = list(dest_dir.glob("t-*.db"))
    assert len(files) == 1
    assert _file_sha(db) == before
    assert db_tools.fingerprint(files[0])["tables"] == db_tools.fingerprint(db)["tables"]


def test_backup_to_an_explicit_file_refuses_to_overwrite(db, tmp_path):
    target = tmp_path / "keep.db"
    assert db_tools.main(["backup", str(db), str(target)]) == 0
    with pytest.raises(SystemExit):
        db_tools.main(["backup", str(db), str(target)])


def test_restore_drill_verifies_a_backup_against_the_baseline(db, tmp_path):
    base = tmp_path / "b.json"
    db_tools.main(["baseline", str(db), str(base)])
    backup = tmp_path / "restore.db"
    db_tools.main(["backup", str(db), str(backup)])
    assert db_tools.main(["verify", str(backup), str(base)]) == 0
    con = sqlite3.connect(backup)
    con.execute("DELETE FROM cases WHERE id=1")
    con.commit()
    con.close()
    assert db_tools.main(["verify", str(backup), str(base)]) == 1


def test_missing_database_is_a_clear_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        db_tools.main(["baseline", str(tmp_path / "nope.db"), str(tmp_path / "b.json")])
    assert "not found" in str(exc.value)


def test_checksum_is_stable_across_runs(db):
    assert db_tools.fingerprint(db)["tables"] == db_tools.fingerprint(db)["tables"]


def test_failed_copy_leaves_no_backup_and_no_partial_file(db, tmp_path, monkeypatch):
    dest_dir = tmp_path / "backups"
    dest_dir.mkdir()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(db_tools.shutil, "copyfile", boom)
    with pytest.raises(OSError):
        db_tools.main(["backup", str(db), str(dest_dir)])
    assert list(dest_dir.iterdir()) == [], "no backup, no .partial file may remain after a failure"


def test_corrupt_copy_is_never_kept(db, tmp_path, monkeypatch, capsys):
    dest = tmp_path / "x.db"
    real_copy = db_tools.shutil.copyfile

    def corrupting_copy(src, dst):
        real_copy(src, dst)
        with open(dst, "ab") as handle:
            handle.write(b"garbage")
        return dst

    monkeypatch.setattr(db_tools.shutil, "copyfile", corrupting_copy)
    assert db_tools.main(["backup", str(db), str(dest)]) == 1
    assert not dest.exists() and not (tmp_path / "x.db.partial").exists()
    assert "differs" in capsys.readouterr().out


def test_backup_leaves_no_temp_directories_behind(db, tmp_path):
    import tempfile
    before = set(Path(tempfile.gettempdir()).glob("tc-backup-*"))
    db_tools.main(["backup", str(db), str(tmp_path / "ok.db")])
    assert set(Path(tempfile.gettempdir()).glob("tc-backup-*")) == before
