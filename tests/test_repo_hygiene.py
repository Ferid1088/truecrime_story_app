"""Keeps the repository small: big media and generated data must never be committed again."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 8 * 1024 * 1024  # largest single tracked file allowed
GENERATED_DIRS = ("data/outfits/", "data/studio_archive/", "data/intro_previews/", "data/cases/")


def _tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return [p for p in out.decode().split("\0") if p]


def test_no_large_tracked_files():
    big = [(p, (ROOT / p).stat().st_size) for p in _tracked() if (ROOT / p).is_file() and (ROOT / p).stat().st_size > MAX_BYTES]
    assert not big, f"files over {MAX_BYTES // 1024 // 1024} MB are tracked: {big}"


def test_generated_media_is_not_tracked():
    bad = [p for p in _tracked() if p.startswith(GENERATED_DIRS)]
    assert not bad, f"generated media is tracked: {bad[:5]}"


def test_no_database_files_tracked():
    bad = [p for p in _tracked() if p.endswith((".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite"))]
    assert not bad, bad
