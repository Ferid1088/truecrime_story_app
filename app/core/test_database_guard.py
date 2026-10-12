"""Fail-closed guard for test and harness database targets."""

from __future__ import annotations

import os

from sqlalchemy.engine import make_url


def is_safe_test_database_url(url: str) -> bool:
    """Return whether *url* names a disposable test database.

    SQLite URLs are always disposable for the test runner.  PostgreSQL tests
    must use a database whose name is explicitly test/temp-like; the live
    database is never accepted by default.
    """

    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite"):
        return True
    name = (parsed.database or "").lower()
    return (
        name.endswith("_test")
        or name.endswith("_tests")
        or name.startswith("test_")
        or "_tmp_" in name
        or name.endswith("_tmp")
    )


def assert_safe_test_database(url: str) -> None:
    """Reject live/non-disposable targets unless explicitly overridden."""

    if os.environ.get("TRUECRIME_ALLOW_LIVE_TESTS") == "1":
        return
    if not is_safe_test_database_url(url):
        parsed = make_url(url)
        raise RuntimeError(
            "Refusing to run tests against non-disposable database "
            f"{parsed.database!r}. Use a *_test/temp database or set "
            "TRUECRIME_ALLOW_LIVE_TESTS=1 explicitly."
        )
