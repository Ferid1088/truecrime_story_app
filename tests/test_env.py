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


def test_dev_ports_default(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("TRUECRIME_API_PORT", raising=False)
    monkeypatch.delenv("TRUECRIME_FRONTEND_PORT", raising=False)
    monkeypatch.delenv("TRUECRIME_CORS_ORIGINS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.api_port == 8001
    assert settings.frontend_port == 3001
    assert settings.cors_origins == "http://localhost:3001,http://127.0.0.1:3001"


def test_cors_origins_follows_overridden_frontend_port(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("TRUECRIME_FRONTEND_PORT", "3042")
    monkeypatch.delenv("TRUECRIME_CORS_ORIGINS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.frontend_port == 3042
    assert settings.cors_origins == "http://localhost:3042,http://127.0.0.1:3042"


def test_explicit_cors_origins_not_overridden_by_frontend_port(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("TRUECRIME_FRONTEND_PORT", "3042")
    monkeypatch.setenv("TRUECRIME_CORS_ORIGINS", "https://example.com")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == "https://example.com"
