"""Modern vs historical (P3/P4): the incident YEAR decides, not the case's open/closed
status or any other label. A modern case keeps Wikimedia's free-text search off (only
categories and Wikidata's own "depicts" claims are used for known entities) and gets
query phrasing for press conferences, courtrooms, campuses and house exteriors instead of
"historical photograph" / "old postcard"."""

from __future__ import annotations

import re

from app.core.ai_config import ai_config

YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")


def incident_year(case) -> int | None:
    """The first plausible year (1500-2099) named in the case's incident_date text, or None."""
    m = YEAR_RE.search(str(getattr(case, "incident_date", None) or ""))
    return int(m.group(1)) if m else None


def date_window(case) -> tuple[int, int] | None:
    """The case/event year window (start, end) for date-filtered archive searches
    (P-structured search). Only the incident year is used: follow-on developments are
    covered by the configured era padding, and a case with recent developments would
    otherwise get an upper bound of "now", making the filter a no-op."""
    year = incident_year(case)
    return (year, year) if year is not None else None


def era_of(case=None, incident_year_value: int | None = None) -> str:
    """"modern" or "historical". An unknown year (no incident_date, or none parseable) defaults
    to "historical": the full set of providers and templates every case used before era
    awareness existed, so a case whose date could not be read never silently loses a provider
    it would otherwise have had."""
    year = incident_year_value if incident_year_value is not None else incident_year(case)
    if year is None:
        return "historical"
    return "modern" if year >= ai_config.visual_search.era_boundary_year else "historical"


def providers_for_era(era: str) -> list[str]:
    """The provider list for this era: `providers_by_era` can only RESTRICT the configured
    `providers` list for an era (e.g. turn Wikimedia's free-text search off for modern cases),
    never add a provider that `providers` itself does not already enable. Falls back to the
    flat `providers` list unchanged when the era is not configured in `providers_by_era`."""
    base = ai_config.visual_search.providers
    by_era = ai_config.visual_search.providers_by_era.get(era)
    return [p for p in base if p in by_era] if by_era else base


def templates_for_era(era: str, entity_type: str) -> list[str]:
    """Expansion-query templates for this era and entity type; falls back to the flat
    `expansion_templates` when the era is not configured in `expansion_templates_by_era`."""
    by_era = ai_config.visual_search.expansion_templates_by_era.get(era)
    if by_era:
        return by_era.get(entity_type) or []
    return ai_config.visual_search.expansion_templates.get(entity_type) or []
