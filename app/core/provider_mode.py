"""Provider-mode selection and the live-database safety interlock."""

from __future__ import annotations

import os
from urllib.parse import urlsplit


class UnsafeProviderModeError(RuntimeError):
    """Raised when a test/recorded provider is aimed at a live database."""


def provider_mode() -> str:
    """Return the effective provider mode exposed to operators and the UI."""
    explicit = (os.environ.get("TRUECRIME_PROVIDER_MODE") or "").strip().lower()
    if explicit in {"recorded", "e2e", "fake", "test"}:
        return "recorded"
    if os.environ.get("TC_TEST_NO_NETWORK") == "1":
        return "recorded"
    if os.environ.get("TRUECRIME_TEST_NO_REAL_PROVIDERS") == "1":
        return "recorded"
    return "live"


def database_name(database_url: str | None = None) -> str:
    raw = database_url or os.environ.get("TRUECRIME_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    return (urlsplit(raw).path or "").rstrip("/").rsplit("/", 1)[-1]


def is_disposable_e2e_database(database_url: str | None = None) -> bool:
    configured = (os.environ.get("E2E_DB_NAME") or "").strip()
    name = database_name(database_url)
    return configured == "truecrime_e2e" or name == "truecrime_e2e"


def is_test_database() -> bool:
    """The pytest suite explicitly supplies a disposable target URL."""
    raw = os.environ.get("TRUECRIME_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    return bool(
        os.environ.get("TC_TEST_DATABASE_URL")
        or os.environ.get("TC_COPY_DATABASE_URL")
        or raw.startswith("sqlite")
    )


def assert_safe_provider_mode(database_url: str | None = None) -> None:
    """Fail API/worker startup before schema work when recorded mode is unsafe."""
    if provider_mode() != "recorded":
        return
    if is_disposable_e2e_database(database_url) or is_test_database():
        return
    raise UnsafeProviderModeError(
        "Refusing to start: recorded/fake providers are enabled for a non-disposable "
        "database. Unset TC_TEST_NO_NETWORK, TRUECRIME_TEST_NO_REAL_PROVIDERS, and "
        "TRUECRIME_PROVIDER_MODE, or use the disposable truecrime_e2e database."
    )
