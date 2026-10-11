from pathlib import Path

from app.core.ai_config import (
    load_raw_config,
    unknown_config_keys,
    unknown_truecrime_env_vars,
)


def _get(raw: dict, path: str):
    value = raw
    for part in path.split("."):
        value = value[part]
    return value


def test_decision_limits_are_explicit_json_keys():
    raw = load_raw_config()
    required = (
        "documentary.max_redos",  # chapter and other pipeline redo limit
        "documentary.min_film_minutes",
        "host.enabled",
        "host.max_mid_segments",
        "host.max_total_share",
        "host.max_total_seconds",
        "host.min_beats_between",
        "host.pace_factor",
        "host.max_repair_iterations",
        "visual_audit.enabled",
        "visual_audit.max_redos",
        "chapters.enabled",
        "chapters.title_card",
        "chapters.card_seconds",
        "chapters.min_card_seconds",
        "chapters.lead_out_seconds",
        "chapters.max_title_chars",
        "chapters.max_label_chars",
        "chapters.max_timeline_events",
        "chapters.max_labelled_events",
        "chapters.intro_enabled",
        "chapters.intro_dir",
        "chapters.intro_duck_db",
    )
    missing = [path for path in required if not _has_path(raw, path)]
    assert not missing, f"decision config keys only exist as schema defaults: {missing}"


def _has_path(raw: dict, path: str) -> bool:
    try:
        _get(raw, path)
    except (KeyError, TypeError):
        return False
    return True


def test_unknown_json_keys_are_reported_without_being_rejected(tmp_path: Path):
    source = tmp_path / "connections.json"
    source.write_text('{"providers": {"research": "truecrime", "generation": "apimaster"}, "unknown": 1}')
    for name in ("models.json", "agents.json"):
        (tmp_path / name).write_text("{}")
    (tmp_path / "parameters").mkdir()
    assert unknown_config_keys(tmp_path) == ["connections.json:unknown"]


def test_unknown_truecrime_environment_names_are_reported(monkeypatch):
    monkeypatch.delenv("TrueCrime_OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("TRUECRIME_TEST_UNKNOWN", "1")
    assert unknown_truecrime_env_vars({}) == ["TRUECRIME_TEST_UNKNOWN"]
