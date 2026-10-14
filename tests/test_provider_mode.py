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


def test_clean_live_environment_reports_live(monkeypatch):
    for key in ("TC_TEST_NO_NETWORK", "TRUECRIME_TEST_NO_REAL_PROVIDERS", "TRUECRIME_PROVIDER_MODE"):
        monkeypatch.delenv(key, raising=False)
    assert provider_mode() == "live"
