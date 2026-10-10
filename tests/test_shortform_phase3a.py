from app.core.ai_config import ai_config


def test_short_form_settings_endpoint_defaults(client):
    response = client.get("/api/short-form/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["distribution"]["youtube_short"]["count"] == 5
    assert data["distribution"]["instagram_reel"]["target_seconds"] == 30
    assert data["candidate_count"] == 14


def test_short_form_settings_validation_rejects_invalid_lengths(client):
    payload = {
        "language_mode": "episode",
        "distribution": {
            name: value.model_dump()
            for name, value in ai_config.short_form.distribution.items()
        },
    }
    payload["distribution"]["youtube_short"]["min_seconds"] = 50
    payload["distribution"]["youtube_short"]["target_seconds"] = 20
    response = client.patch("/api/short-form/settings", json=payload)
    assert response.status_code == 422


def test_short_form_settings_patch_changes_candidate_count(client):
    payload = {
        "language_mode": "all",
        "distribution": {
            name: value.model_dump()
            for name, value in ai_config.short_form.distribution.items()
        },
    }
    payload["distribution"]["tiktok_video"]["count"] = 10
    response = client.patch("/api/short-form/settings", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["candidate_count"] == 18
    assert data["language_mode"] == "all"
    assert data["level"] == "episode_override"

