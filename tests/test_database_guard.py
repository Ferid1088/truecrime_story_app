import pytest

from app.core.test_database_guard import assert_safe_test_database, is_safe_test_database_url


def test_guard_accepts_sqlite_and_disposable_postgres_names(monkeypatch):
    monkeypatch.delenv("TRUECRIME_ALLOW_LIVE_TESTS", raising=False)
    assert is_safe_test_database_url("sqlite:////tmp/truecrime.db")
    assert is_safe_test_database_url("postgresql+psycopg://u@localhost/truecrime_test")
    assert is_safe_test_database_url("postgresql+psycopg://u@localhost/test_truecrime")
    assert is_safe_test_database_url("postgresql+psycopg://u@localhost/truecrime_tmp")


def test_guard_rejects_live_database_by_default(monkeypatch):
    monkeypatch.delenv("TRUECRIME_ALLOW_LIVE_TESTS", raising=False)
    with pytest.raises(RuntimeError, match="Refusing to run tests"):
        assert_safe_test_database("postgresql+psycopg://u@localhost/truecrime")


def test_guard_explicit_override_is_visible(monkeypatch):
    monkeypatch.setenv("TRUECRIME_ALLOW_LIVE_TESTS", "1")
    assert_safe_test_database("postgresql+psycopg://u@localhost/truecrime")
