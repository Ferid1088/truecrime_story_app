"""Hard stop for production provider clients while pytest is running."""

from __future__ import annotations

import os


class RealProviderUseError(RuntimeError):
    """Raised before a test can construct or call a live provider client."""


def guard_real_provider(provider: str, action: str = "construct") -> None:
    if os.environ.get("TRUECRIME_TEST_NO_REAL_PROVIDERS") == "1":
        raise RealProviderUseError(
            f"Tests forbid real provider {provider}: cannot {action} a live client; "
            "inject a fake, recorded provider, or an explicit mock transport."
        )

