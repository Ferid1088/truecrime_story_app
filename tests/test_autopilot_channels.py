import pytest

from app.core.ai_config import ai_config
from app.db.models import ChannelSetting


@pytest.fixture(autouse=True)
def clean(db_session):
    db_session.query(ChannelSetting).delete()
    db_session.commit()
    yield
    db_session.query(ChannelSetting).delete()
    db_session.commit()


def test_every_language_and_platform_has_a_placeholder_that_is_not_connected(client):
    cfg = ai_config.autopilot_settings()
    body = client.get("/api/channel-settings").json()
    assert len(body["channels"]) == len(cfg.languages) * len(cfg.platforms)
    assert {(c["language"], c["platform"]) for c in body["channels"]} == {
        (lang, p) for lang in cfg.languages for p in cfg.platforms}
    assert all(c["handle"] == "" and c["connected"] is False for c in body["channels"])


def test_a_handle_is_saved_and_the_channel_stays_not_connected(client):
    r = client.put("/api/channel-settings/de/tiktok_video", json={"handle": "  @wahre_faelle  "})
    assert r.status_code == 200 and r.json()["handle"] == "@wahre_faelle"
    rows = {(c["language"], c["platform"]): c for c in client.get("/api/channel-settings").json()["channels"]}
    assert rows[("de", "tiktok_video")]["handle"] == "@wahre_faelle"
    assert rows[("de", "tiktok_video")]["connected"] is False


def test_an_unknown_language_or_platform_is_refused(client):
    assert client.put("/api/channel-settings/xx/tiktok_video", json={"handle": "a"}).status_code == 404
    assert client.put("/api/channel-settings/en/myspace", json={"handle": "a"}).status_code == 404


def test_a_connected_flag_cannot_be_set_because_no_integration_exists(client):
    r = client.put("/api/channel-settings/en/youtube_short", json={"handle": "a", "connected": True})
    assert r.status_code == 422


def test_a_secret_like_value_is_refused_as_a_handle(client):
    r = client.put("/api/channel-settings/en/youtube_short", json={"handle": "x" * 101})
    assert r.status_code == 422
