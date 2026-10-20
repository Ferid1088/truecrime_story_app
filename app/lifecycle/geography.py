"""Country filtering for discovery (D1/D5): a text match of the extraction's
free-text location against config/parameters/countries.json's name/aliases —
not geocoding. Ambiguous or terse locations ("the coast", a city with no
country mentioned) can go either way; see docs/discover_checklist.md."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "parameters" / "countries.json"


@lru_cache(maxsize=1)
def _countries() -> list[dict]:
    data = json.loads(_CONFIG_PATH.read_text())
    return data["discovery_countries"]["list"]


@lru_cache(maxsize=1)
def _by_code() -> dict[str, dict]:
    return {c["code"]: c for c in _countries()}


def country_options() -> list[dict]:
    """The full list for the searchable dropdown: code, display name, aliases."""
    return _countries()


def country_name(code: str | None) -> str | None:
    if not code:
        return None
    entry = _by_code().get(code.upper())
    return entry["name"] if entry else None


def _names_for(code: str) -> list[str]:
    entry = _by_code().get(code.upper())
    if not entry:
        return []
    return [entry["name"], *entry.get("aliases", [])]


def location_matches_country(location: str | None, country_code: str) -> bool | None:
    """True/False once the free-text location is checked against the
    requested country's name/aliases; None when there is no location text
    to check at all (unknown — never silently treated as a match)."""
    text = (location or "").strip()
    if not text:
        return None
    names = _names_for(country_code)
    if not names:
        return None
    return any(re.search(r"\b" + re.escape(name) + r"\b", text, re.IGNORECASE) for name in names)
