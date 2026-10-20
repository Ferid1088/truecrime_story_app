"""Deterministic display names for a discovery run ("Search", D2): built
only from its stored criteria (`ResearchJob.input_json`) and its id — never
a model call, never drifting with later config changes. A criterion whose
key never existed in `input_json` at all (a pre-D1 job) is genuinely
unknown and shown as "not recorded"; a key present with a null value means
the search explicitly asked for "any" on that axis.
"""
from __future__ import annotations

_RESOLUTION_LABELS = {
    "solved_only": "Solved",
    "unsolved_only": "Unsolved",
    "both": "Solved+Unsolved",
}


def _country_label(input_data: dict) -> str:
    if "country_code" not in input_data:
        return "not recorded"
    code = input_data.get("country_code")
    if not code:
        return "Worldwide"
    from app.lifecycle.geography import country_name

    return country_name(code) or code


def _year_label(input_data: dict) -> str:
    if "incident_year_from" not in input_data and "incident_year_to" not in input_data:
        return "not recorded"
    year_from = input_data.get("incident_year_from")
    year_to = input_data.get("incident_year_to")
    if year_from is None and year_to is None:
        return "Any year"
    return f"{year_from if year_from is not None else '…'}–{year_to if year_to is not None else 'now'}"


def _resolution_label(input_data: dict) -> str:
    resolution_filter = input_data.get("resolution_filter")
    if resolution_filter:
        return _RESOLUTION_LABELS.get(resolution_filter, "not recorded")
    if "include_unsolved" in input_data:
        return "Solved+Unsolved" if input_data["include_unsolved"] else "Solved"
    return "not recorded"


def _languages_label(input_data: dict) -> str:
    languages = input_data.get("languages") or []
    if not languages:
        return "not recorded"
    return "+".join(languages)


def generate_search_name(input_data: dict, job_id: int) -> str:
    theme = (input_data.get("theme") or "not recorded").strip()
    theme_label = theme if theme == "not recorded" else theme[:1].upper() + theme[1:]
    parts = [
        theme_label,
        _country_label(input_data),
        _year_label(input_data),
        _resolution_label(input_data),
        _languages_label(input_data),
        f"#{job_id}",
    ]
    return " · ".join(parts)
