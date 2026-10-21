import requests

from app.core.ai_config import ai_config
from app.providers.transcript import youtube as Y


def test_every_request_of_the_transcript_session_has_the_configured_timeout(monkeypatch):
    seen = {}

    def fake_request(self, method, url, **kwargs):
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr(requests.Session, "request", fake_request)
    assert Y.TimeoutSession().get("https://example.invalid/") == "ok"
    assert seen["timeout"] == ai_config.transcript_ingestion.request_timeout_seconds


def test_an_explicit_timeout_is_kept(monkeypatch):
    seen = {}
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, method, url, **kw: seen.update(kw) or "ok")
    Y.TimeoutSession().get("https://example.invalid/", timeout=3)
    assert seen["timeout"] == 3
