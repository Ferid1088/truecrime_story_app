"""Keeps the repository small: big media and generated data must never be committed again."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 8 * 1024 * 1024  # largest single tracked file allowed
# The channels' own media is committed on purpose (the owner's decision of 2026-10-27): the host's outfit pictures, the
# channel intros (video with logo and music) and the logos. They may be larger, but never near GitHub's 100 MB limit.
OWNER_MEDIA = ("data/outfits/", "data/intros/", "data/logos/")
MAX_OWNER_MEDIA_BYTES = 20 * 1024 * 1024
GENERATED_DIRS = ("data/studio_archive/", "data/intro_previews/", "data/cases/")


def _tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return [p for p in out.decode().split("\0") if p]


def test_no_large_tracked_files():
    limit = lambda p: MAX_OWNER_MEDIA_BYTES if p.startswith(OWNER_MEDIA) else MAX_BYTES  # noqa: E731
    big = [(p, (ROOT / p).stat().st_size) for p in _tracked() if (ROOT / p).is_file() and (ROOT / p).stat().st_size > limit(p)]
    assert not big, f"files over their size limit are tracked (8 MB, owner media 20 MB): {big}"


def test_the_channels_own_media_is_tracked_so_that_every_checkout_has_it():
    tracked = _tracked()
    assert any(p.startswith("data/outfits/en/") for p in tracked)
    assert any(p.startswith("data/intros/") and p.endswith(".mp4") for p in tracked)
    assert any(p.startswith("data/logos/") for p in tracked)


def test_generated_media_is_not_tracked():
    bad = [p for p in _tracked() if p.startswith(GENERATED_DIRS)]
    assert not bad, f"generated media is tracked: {bad[:5]}"


def test_no_database_files_tracked():
    bad = [p for p in _tracked() if p.endswith((".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite"))]
    assert not bad, bad
