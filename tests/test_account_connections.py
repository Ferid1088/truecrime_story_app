"""The account registry of every language: Instagram and Facebook accounts are per language, each Facebook Page is linked to the Instagram account of its own language, no id serves two
languages, an account that does not exist is reported as such, and a token that reaches another language's account is refused. No network: the live checks are made through the Test
connection endpoints and reported separately."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.meta_publishing.config import meta_config
from app.meta_publishing.connection import ConnectError, InstagramConnection
from app.meta_publishing.token import TokenStore
from app.publishing.connections import connection_table

EXPECTED = {
    "fa": ("17841431693135313", "radekhamush", "1524968800689127"),
    "en": ("17841466656462894", "clue.vera", "1551002194753910"),
    "de": ("17841466157044582", "fall.spur", "1403440062859312"),
}


def test_each_language_maps_to_its_own_instagram_account_and_facebook_page():
    config = meta_config()
    for language, (ig_id, username, page_id) in EXPECTED.items():
        assert (config.instagram.accounts[language].ig_account_id, config.instagram.accounts[language].username) == (ig_id, username)
        assert config.facebook.pages[language].page_id == page_id and config.facebook.pages[language].linked_instagram_id == ig_id
    assert set(config.instagram.accounts) == set(config.facebook.pages) == set(EXPECTED)


def test_no_account_id_serves_two_languages():
    config = meta_config()
    ids = [a.ig_account_id for a in config.instagram.accounts.values()] + [p.page_id for p in config.facebook.pages.values()]
    assert len(ids) == len(set(ids))


def test_a_language_without_an_instagram_account_is_refused():
    with pytest.raises(ValueError, match="no Instagram account is configured for 'es'"):
        meta_config().instagram.for_language("es")
    assert meta_config().instagram.for_language("de").account.username == "fall.spur"


def test_a_token_that_reaches_another_languages_account_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv(meta_config().instagram.token_env, raising=False)
    settings = meta_config().instagram.for_language("en").model_copy(deep=True)
    settings.token_file, settings.connection.state_file = str(tmp_path / "token"), str(tmp_path / "state_{language}.json")
    TokenStore(settings).set("A" * 40)
    german = meta_config().instagram.accounts["de"]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me/permissions"):
            return httpx.Response(200, json={"data": [{"permission": p, "status": "granted"} for p in settings.required_permissions]})
        return httpx.Response(200, json={"id": german.ig_account_id, "username": german.username})

    with pytest.raises(ConnectError) as caught:
        asyncio.run(InstagramConnection(settings, transport=httpx.MockTransport(handle)).test())
    assert caught.value.reason == "account_mismatch" and "clue.vera" in str(caught.value)


def test_the_connection_table_has_every_language_and_platform_and_marks_missing_accounts(db_session):
    rows = {(r["language"], r["platform"]): r for r in connection_table(db_session)}
    assert len(rows) == 27 and {lang for lang, _ in rows} == {"fa", "en", "de", "ar", "es", "it", "fr", "pt", "tr"}
    for language in ("ar", "es", "it", "fr", "pt", "tr"):
        for platform in ("instagram", "facebook"):
            assert rows[(language, platform)]["state"] == "not_created" and rows[(language, platform)]["account_id"] is None
    assert rows[("pt", "youtube")]["state"] == "awaiting_creation"
    assert rows[("es", "youtube")]["state"] == "needs_owner_authorization" and rows[("es", "youtube")]["account_id"] is None
    for language, (ig_id, _, page_id) in EXPECTED.items():
        assert rows[(language, "instagram")]["account_id"] == ig_id and rows[(language, "facebook")]["account_id"] == page_id
        assert rows[(language, "instagram")]["state"] != "verified" or rows[(language, "instagram")]["last_verified"]


def test_a_known_account_without_a_token_is_never_reported_as_verified(db_session, monkeypatch):
    monkeypatch.setattr(TokenStore, "source", lambda self: None)
    rows = {(r["language"], r["platform"]): r for r in connection_table(db_session)}
    for language in EXPECTED:
        for platform in ("instagram", "facebook"):
            assert rows[(language, platform)]["state"] == "known_not_authenticated" and not rows[(language, platform)]["identity_verified"]
