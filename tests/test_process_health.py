"""The worker has no HTTP endpoint of its own; /health must still be able
to show its commit and flag a mismatch against the API's own commit,
so an operator never has to separately inspect two running processes."""

import os

from app.core import process_health as ph


def test_write_then_read_worker_health_round_trips(tmp_path, monkeypatch):
    path = tmp_path / "worker.health.json"
    monkeypatch.setattr(ph, "WORKER_HEALTH_PATH", path)
    monkeypatch.setenv("TRUECRIME_PROCESS_COMMIT", "a" * 40)

    ph.write_worker_health("host:123:abcdef")

    reported = ph.read_worker_health()
    assert reported["worker_id"] == "host:123:abcdef"
    assert reported["pid"] == os.getpid()
    assert reported["commit"] == "a" * 40
    assert reported["started_at"]


def test_read_worker_health_is_none_when_never_written(tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "WORKER_HEALTH_PATH", tmp_path / "missing.json")
    assert ph.read_worker_health() is None


def test_read_worker_health_is_none_for_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "worker.health.json"
    path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(ph, "WORKER_HEALTH_PATH", path)
    assert ph.read_worker_health() is None


def test_pid_alive_true_for_this_process_false_for_a_dead_one():
    assert ph.pid_alive(os.getpid()) is True
    # A pid essentially never reused immediately in a test run.
    assert ph.pid_alive(999_999) is False


def test_pid_alive_false_for_none_or_zero():
    assert ph.pid_alive(None) is False
    assert ph.pid_alive(0) is False


def test_commit_hash_prefers_the_explicit_env_override(monkeypatch):
    monkeypatch.setenv("TRUECRIME_PROCESS_COMMIT", "b" * 40)
    assert ph.commit_hash() == "b" * 40


def test_commit_hash_ignores_a_malformed_env_override(monkeypatch):
    monkeypatch.setenv("TRUECRIME_PROCESS_COMMIT", "not-a-real-hash")
    # Falls back to the real `git rev-parse HEAD` — still a 40-char hash.
    value = ph.commit_hash()
    assert value is None or len(value) == 40
