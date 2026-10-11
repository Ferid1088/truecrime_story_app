import logging

from app.core import env as env_module
from app.core.env import env_value


def _reset_env_warnings():
    env_module._warned_aliases.clear()
    env_module._warned_conflicts.clear()


def test_legacy_alias_works_and_warns_once(monkeypatch, caplog):
    _reset_env_warnings()
    monkeypatch.delenv("TRUECRIME_APIMASTER_API_KEY", raising=False)
    monkeypatch.setenv("TrueCrime_APIMASTER_API_KEY", "legacy-secret")
    with caplog.at_level(logging.WARNING):
        assert env_value("TRUECRIME_APIMASTER_API_KEY") == "legacy-secret"
        assert env_value("TRUECRIME_APIMASTER_API_KEY") == "legacy-secret"
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 1
    assert "TrueCrime_APIMASTER_API_KEY" in messages[0]
    assert "TRUECRIME_APIMASTER_API_KEY" in messages[0]
    assert "legacy-secret" not in caplog.text


def test_canonical_wins_and_conflict_warning_has_no_values(monkeypatch, caplog):
    _reset_env_warnings()
    monkeypatch.setenv("TRUECRIME_YOUTUBE_API_KEY", "canonical-secret")
    monkeypatch.setenv("YOUTUBE_API_KEY", "legacy-secret")
    with caplog.at_level(logging.WARNING):
        assert env_value("TRUECRIME_YOUTUBE_API_KEY") == "canonical-secret"
    assert "canonical value wins" in caplog.text
    assert "canonical-secret" not in caplog.text
    assert "legacy-secret" not in caplog.text


def test_settings_accepts_canonical_and_legacy_names(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("TRUECRIME_YOUTUBE_API_KEY", "canonical")
    monkeypatch.setenv("YOUTUBE_API_KEY", "legacy")
    assert Settings(_env_file=None).youtube_api_key == "canonical"
