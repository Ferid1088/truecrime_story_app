"""Environment variable resolution for application-specific settings.

Canonical names use the ``TRUECRIME_*`` prefix. Older names remain readable
for existing deployments, but they are never preferred over a canonical
value and their use is logged without exposing secret values.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "TRUECRIME_DATABASE_URL": ("DATABASE_URL",),
    "TRUECRIME_APIMASTER_API_KEY": ("TrueCrime_APIMASTER_API_KEY",),
    "TRUECRIME_ELEVENLABS_API_KEY": ("TrueCrime_ELEVENLABS_API_KEY",),
    "TRUECRIME_YOUTUBE_API_KEY": ("TrueCrime_YOUTUBE_API_KEY", "YOUTUBE_API_KEY"),
    "TRUECRIME_HEYGEN_API_KEY": ("HEYGEN_API_KEY",),
    "TRUECRIME_AVATAR_ID_HEYGEN": ("TrueCrime_Avatar_ID_Heygen",),
    "TRUECRIME_SEARXNG_URL": (),
    "TRUECRIME_DISABLE_SCHEDULER": (),
    "TRUECRIME_ACCESS_TOKEN": ("ACCESS_TOKEN",),
    "TRUECRIME_SESSION_DAYS": ("SESSION_DAYS",),
    "TRUECRIME_COOKIE_SECURE": ("COOKIE_SECURE",),
    "TRUECRIME_CORS_ORIGINS": ("CORS_ORIGINS",),
    "TRUECRIME_JOB_RUNNER": ("JOB_RUNNER",),
    "TRUECRIME_WORKER_POLL_SECONDS": ("WORKER_POLL_SECONDS",),
    "TRUECRIME_JOB_LEASE_SECONDS": ("JOB_LEASE_SECONDS",),
    "TRUECRIME_JOB_MAX_AUTO_RESUMES": ("JOB_MAX_AUTO_RESUMES",),
    "TRUECRIME_LOG_LEVEL": ("LOG_LEVEL",),
    "TRUECRIME_LOG_FORMAT": ("LOG_FORMAT",),
    "TRUECRIME_PROVIDER_LEDGER": ("PROVIDER_LEDGER",),
    "TRUECRIME_PROVIDER_LEDGER_DAYS": ("PROVIDER_LEDGER_DAYS",),
}

_CANONICAL_BY_ALIAS = {
    alias: canonical
    for canonical, aliases in ENV_ALIASES.items()
    for alias in aliases
}
_warned_aliases: set[str] = set()
_warned_conflicts: set[tuple[str, str]] = set()


def canonical_env_name(name: str) -> str:
    return _CANONICAL_BY_ALIAS.get(name, name)


def env_value(name: str, default: str | None = None) -> str | None:
    """Return a canonical environment value, accepting legacy aliases."""
    canonical = canonical_env_name(name)
    aliases = ENV_ALIASES.get(canonical, ())
    if canonical in os.environ:
        value = os.environ[canonical]
        for alias in aliases:
            if alias in os.environ and os.environ[alias] != value:
                pair = (canonical, alias)
                if pair not in _warned_conflicts:
                    log.warning(
                        "Environment variable %s conflicts with alias %s; canonical value wins.",
                        canonical,
                        alias,
                    )
                    _warned_conflicts.add(pair)
        return value
    for alias in aliases:
        if alias in os.environ:
            if alias not in _warned_aliases:
                log.warning(
                    "Deprecated environment variable %s; use %s instead.",
                    alias,
                    canonical,
                )
                _warned_aliases.add(alias)
            return os.environ[alias]
    return default


def known_environment_names() -> set[str]:
    """Names accepted by the application, including compatibility aliases."""
    return set(ENV_ALIASES) | set(_CANONICAL_BY_ALIAS)
