import pytest

from app.core.provider_mode import (
    UnsafeProviderModeError,
    assert_safe_provider_mode,
    provider_mode,
)


def test_recorded_provider_mode_is_rejected_for_non_e2e_database(monkeypatch):
    monkeypatch.setenv("TC_TEST_NO_NETWORK", "1")
    monkeypatch.setenv(
        "TRUECRIME_DATABASE_URL",
        "postgresql+psycopg://truecrime@127.0.0.1:5432/truecrime_live",
    )
    monkeypatch.delenv("E2E_DB_NAME", raising=False)
    monkeypatch.delenv("TC_TEST_DATABASE_URL", raising=False)
    with pytest.raises(UnsafeProviderModeError, match="non-disposable database"):
        assert_safe_provider_mode()


def test_recorded_provider_mode_is_allowed_only_for_disposable_e2e_database(monkeypatch):
    monkeypatch.setenv("TC_TEST_NO_NETWORK", "1")
    monkeypatch.setenv(
        "TRUECRIME_DATABASE_URL",
        "postgresql+psycopg://truecrime@127.0.0.1:5432/truecrime_e2e",
    )
    assert provider_mode() == "recorded"
    assert_safe_provider_mode()


def test_recorded_provider_mode_is_allowed_for_a_differently_named_e2e_stack(monkeypatch):
    # A second, isolated E2E stack (its own database, its own ports — see
    # scripts/e2e_stack.sh) names its database via E2E_DB_NAME
    # rather than reusing the shared stack's "truecrime_e2e".
    monkeypatch.setenv("TC_TEST_NO_NETWORK", "1")
    monkeypatch.setenv(
        "TRUECRIME_DATABASE_URL",
        "postgresql+psycopg://truecrime@127.0.0.1:5432/truecrime_approve_pilot_e2e",
    )
    monkeypatch.setenv("E2E_DB_NAME", "truecrime_approve_pilot_e2e")
    assert_safe_provider_mode()


def test_recorded_provider_mode_rejects_a_mismatched_e2e_db_name(monkeypatch):
    # E2E_DB_NAME must match the database actually being used — it must not
    # make an unrelated, non-disposable database look safe just because
    # some other process set that variable to a different value.
    monkeypatch.setenv("TC_TEST_NO_NETWORK", "1")
    monkeypatch.setenv(
        "TRUECRIME_DATABASE_URL",
        "postgresql+psycopg://truecrime@127.0.0.1:5432/truecrime_live",
    )
    monkeypatch.setenv("E2E_DB_NAME", "truecrime_approve_pilot_e2e")
    with pytest.raises(UnsafeProviderModeError, match="non-disposable database"):
        assert_safe_provider_mode()


def test_clean_live_environment_reports_live(monkeypatch):
    for key in ("TC_TEST_NO_NETWORK", "TRUECRIME_TEST_NO_REAL_PROVIDERS", "TRUECRIME_PROVIDER_MODE"):
        monkeypatch.delenv(key, raising=False)
    assert provider_mode() == "live"
