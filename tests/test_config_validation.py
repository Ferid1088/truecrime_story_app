from pathlib import Path

import pytest

from app.core.ai_config import (
    AIConfig,
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
        "host.max_appearances",
        "host.max_total_share",
        "host.max_total_seconds",
        "host.min_acts_between",
        "host.words_per_minute",
        "host.max_repair_iterations",
        "host.max_writer_repairs",
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
    policy_keys = (
        "production_policies.user_media.require_rights_confirmation",
        "production_policies.user_media.preserve_original_audio",
        "production_policies.user_media.default_auto_audio_use",
        "production_policies.media_reuse.default_policy",
        "production_policies.media_reuse.anchor_reuse_enabled",
        "production_policies.media_reuse.min_anchor_reuse_minutes",
        "production_policies.audio_recovery.max_repair_attempts",
        "production_policies.audio_recovery.fallback_order",
        "production_policies.short_form.status",
        "production_policies.short_form.blocks_long_form_release",
        "production_policies.timeline.multi_year_threshold_years",
        "production_policies.timeline.hide_labels",
        "production_policies.timeline.empty_calendar_markers",
        "production_policies.publishing.default_visibility",
        "production_policies.publishing.live_mode_default",
        "production_policies.retry_policy.chapter_texts",
        "production_policies.retry_policy.spoken_versions",
        "production_policies.retry_policy.host_segments",
        "production_policies.retry_policy.video_pieces",
        "production_policies.retry_policy.audio_plan",
    )
    missing = [path for path in policy_keys if not _has_path(raw, path)]
    assert not missing, f"policy keys only exist as schema defaults: {missing}"


def test_production_policy_validators_reject_nonsense():
    raw = load_raw_config()
    raw["production_policies"]["media_reuse"]["min_anchor_reuse_minutes"] = -1
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)


def test_config_rejects_unknown_provider_references():
    raw = load_raw_config()
    raw["providers"]["generation"] = "missing-provider"
    with pytest.raises(ValueError, match="providers.generation"):
        AIConfig.model_validate(raw)

    raw = load_raw_config()
    raw["production_policies"]["audio_recovery"]["fallback_order"] = []
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)

    raw = load_raw_config()
    raw["production_policies"]["media_reuse"]["default_policy"] = "repeat_anything"
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)


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
    # The test validates unknown-name reporting, not PostgreSQL connectivity.
    # Do not let a developer's real database credential from .env.local alter
    # the expected set or make this test depend on a credential file.
    monkeypatch.delenv("TRUECRIME_PG_PASSWORD", raising=False)
    monkeypatch.setenv("TRUECRIME_TEST_UNKNOWN", "1")
    assert unknown_truecrime_env_vars({}) == ["TRUECRIME_TEST_UNKNOWN"]


def test_unused_openrouter_environment_warns_and_never_logs_value(monkeypatch, caplog):
    from app.core.ai_config import warn_unknown_configuration

    secret = "must-not-appear-in-logs"
    monkeypatch.setenv("TrueCrime_OPENROUTER_API_KEY", secret)
    with caplog.at_level("WARNING"):
        warn_unknown_configuration()
    records = [r.getMessage() for r in caplog.records
               if "OPENROUTER" in r.getMessage().upper()]
    assert len(records) == 1
    assert "unused, remove it" in records[0]
    assert secret not in caplog.text
