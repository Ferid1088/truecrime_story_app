"""The owner's Instagram connection (Connect, Test connection, Disconnect, the registry row), against a fake Meta (an httpx MockTransport that speaks the documented Facebook Login and
Graph calls). No network and no real credentials: these tests prove the application's behaviour; the live behaviour of Meta is verified separately with the owner's Meta app."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.db.models import PublicationAccount
from app.meta_publishing.config import meta_config
from app.meta_publishing.connection import AppFile, ConnectError, InstagramConnection
from app.meta_publishing.token import TokenStore
from app.publishing.monitoring import connection_issues

IG_ID = meta_config().instagram.account.ig_account_id
USER = meta_config().instagram.account.username
SHORT, LONG, SECRET = "SHORT_CODE_TOKEN_aaaaaaaaaaaaaaaaaaaa", "LONG_LIVED_TOKEN_bbbbbbbbbbbbbbbbbbbbbbbb", "APP-SECRET-VALUE-123"


class FakeMeta:
    def __init__(self):
        self.account = {"id": IG_ID, "username": USER}
        self.granted = list(meta_config().instagram.required_permissions)
        self.requests: list[tuple[str, str, str]] = []
        self.code_ok, self.expired = True, False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, body = request.url.path, request.content.decode()
        self.requests.append((request.method, request.url.host + path, request.url.query.decode()))
        if path.endswith("/oauth/access_token"):
            form = parse_qs(body)
            if not self.code_ok:
                return httpx.Response(400, json={"error": {"message": "Invalid verification code format.", "code": 100}})
            if form.get("grant_type") == ["fb_exchange_token"]:
                return httpx.Response(200, json={"access_token": LONG, "token_type": "bearer", "expires_in": 5183944})
            return httpx.Response(200, json={"access_token": SHORT, "token_type": "bearer"})
        if self.expired:
            return httpx.Response(400, json={"error": {"message": "Error validating access token: Session has expired", "code": 190, "error_subcode": 463}})
        if path.endswith("/me/permissions"):
            return httpx.Response(200, json={"data": [{"permission": p, "status": "granted"} for p in self.granted]})
        if path.endswith("/content_publishing_limit"):
            return httpx.Response(200, json={"data": [{"quota_usage": 2, "config": {"quota_total": 100}}]})
        if path.endswith(f"/{IG_ID}"):
            return httpx.Response(200, json=self.account)
        return httpx.Response(404, json={"error": {"message": "unknown"}})


@pytest.fixture
def meta(tmp_path, db_session, monkeypatch):
    for name in (meta_config().instagram.token_env, meta_config().instagram.account_id_env, "TRUECRIME_META_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)                                      # the owner's real token in .env never reaches a test
    settings = meta_config().instagram.model_copy(deep=True)
    settings.token_file, settings.connection.state_file = str(tmp_path / "token"), str(tmp_path / "state_{language}.json")
    settings.connection.app_file = str(tmp_path / "app.json")
    (tmp_path / "app.json").write_text(json.dumps({"app_id": "1234567890", "app_secret": SECRET}))
    fake = FakeMeta()
    connection = InstagramConnection(settings, transport=httpx.MockTransport(fake.handle))
    yield type("Meta", (), {"fake": fake, "conn": connection, "settings": settings, "tmp": tmp_path})
    db_session.rollback()
    db_session.query(PublicationAccount).filter_by(platform="instagram", language="fa").update({"status": "not_connected", "secret_ref": ""})
    db_session.commit()


def connect(m, return_to: str = "/cases/15?tab=Publishing&language=fa") -> dict:
    state = parse_qs(urlparse(m.conn.authorization_url(return_to)).query)["state"][0]
    return asyncio.run(m.conn.complete("the-code", state))


def test_the_authorization_address_asks_for_exactly_the_required_permissions(meta):
    query = parse_qs(urlparse(meta.conn.authorization_url()).query)
    assert query["scope"] == [",".join(meta.settings.required_permissions)] and query["redirect_uri"] == [meta.settings.connection.redirect_uri] and query["client_id"] == ["1234567890"]
    assert "client_secret" not in query and query["response_type"] == ["code"] and query["state"][0]


def test_connecting_stores_the_long_lived_token_owner_only_and_never_returns_it(meta):
    status = connect(meta)
    assert status["connected"] and status["verified"] and status["return_to"] == "/cases/15?tab=Publishing&language=fa" and 59 < status["days_left"] < 61
    assert TokenStore(meta.settings).get() == LONG and oct(TokenStore(meta.settings).path.stat().st_mode & 0o777) == "0o600"
    assert not any(secret in json.dumps(status) for secret in (SHORT, LONG, SECRET)) and not any(secret in meta.conn.state_path.read_text() for secret in (SHORT, LONG, SECRET))
    assert all(SECRET not in url and SHORT not in url and LONG not in url for _, _, url in meta.fake.requests)        # secrets travel in request bodies, never in a URL


def test_a_token_for_another_account_stores_nothing(meta):
    meta.fake.account = {"id": "17841400000000000", "username": "someone.else"}
    with pytest.raises(ConnectError) as caught:
        connect(meta)
    assert caught.value.reason == "account_mismatch" and "someone.else" in str(caught.value)
    assert not TokenStore(meta.settings).path.exists() and not meta.conn.state_path.exists() and meta.conn.status()["connected"] is False


def test_missing_permissions_store_nothing(meta):
    meta.fake.granted = ["instagram_basic"]
    with pytest.raises(ConnectError) as caught:
        connect(meta)
    assert caught.value.reason == "permission_missing" and "instagram_content_publish" in str(caught.value) and not TokenStore(meta.settings).path.exists()


def test_a_forged_or_reused_state_and_a_refused_code_are_rejected(meta):
    with pytest.raises(ConnectError) as forged:
        asyncio.run(meta.conn.complete("code", "forged"))
    assert forged.value.reason == "state_invalid"
    state = parse_qs(urlparse(meta.conn.authorization_url()).query)["state"][0]
    meta.fake.code_ok = False
    with pytest.raises(ConnectError) as refused:
        asyncio.run(meta.conn.complete("bad", state))
    assert refused.value.reason == "token_rejected" and not TokenStore(meta.settings).path.exists()
    with pytest.raises(ConnectError):
        asyncio.run(meta.conn.complete("bad", state))                                     # the state is single-use


def test_without_the_meta_app_file_nothing_can_start(meta):
    meta.tmp.joinpath("app.json").unlink()
    assert meta.conn.status()["app_configured"] is False
    with pytest.raises(ConnectError) as caught:
        meta.conn.authorization_url()
    assert caught.value.reason == "not_configured"


def test_live_calls_off_blocks_every_call(meta):
    meta.settings.connection.live_enabled = False
    for call in (lambda: meta.conn.authorization_url(), lambda: asyncio.run(meta.conn.test())):
        with pytest.raises(ConnectError) as caught:
            call()
        assert caught.value.reason == "live_disabled"
    assert meta.fake.requests == []


def test_the_connection_test_is_read_only_and_checks_permissions_account_and_limit(meta):
    connect(meta)
    meta.fake.requests.clear()
    result = asyncio.run(meta.conn.test())
    assert result["account"] == {"id": IG_ID, "username": USER} and result["quota_usage"] == 2 and result["permissions"] == meta.settings.required_permissions
    assert {method for method, _, _ in meta.fake.requests} == {"GET"} and [p.rsplit("/", 1)[-1] for _, p, _ in meta.fake.requests] == ["permissions", IG_ID, "content_publishing_limit"]
    assert meta.conn.status()["last_test"]["ok"] is True


def test_a_token_without_the_publishing_only_permission_still_connects_and_says_what_is_missing(meta, monkeypatch):
    monkeypatch.setenv(meta.settings.token_env, "ENV_TOKEN_cccccccccccccccccccccccc")
    meta.fake.granted = ["instagram_basic", "instagram_content_publish"]
    result = asyncio.run(meta.conn.test())
    assert result["missing_for_publishing"] == ["pages_read_engagement"] and result["permissions"] == ["instagram_basic", "instagram_content_publish"]
    status = meta.conn.status()
    assert status["verified"] is True and status["missing_for_publishing"] == ["pages_read_engagement"] and status["last_test"]["ok"] is True


def test_a_token_without_a_connection_permission_is_refused(meta, monkeypatch):
    monkeypatch.setenv(meta.settings.token_env, "ENV_TOKEN_cccccccccccccccccccccccc")
    meta.fake.granted = ["instagram_basic"]
    with pytest.raises(ConnectError) as caught:
        asyncio.run(meta.conn.test())
    assert caught.value.reason == "permission_missing" and "instagram_content_publish" in str(caught.value)


def test_an_account_id_in_the_environment_must_name_the_configured_account(meta, monkeypatch):
    monkeypatch.setenv(meta.settings.token_env, "ENV_TOKEN_cccccccccccccccccccccccc")
    monkeypatch.setenv(meta.settings.account_id_env, "999")
    with pytest.raises(ConnectError) as caught:
        asyncio.run(meta.conn.test())
    assert caught.value.reason == "account_mismatch" and meta.conn.status()["verified"] is False
    monkeypatch.setenv(meta.settings.account_id_env, IG_ID)
    assert asyncio.run(meta.conn.test())["account"]["id"] == IG_ID


def test_a_failed_test_is_remembered_with_its_reason_and_no_token(meta):
    connect(meta)
    meta.fake.expired = True
    with pytest.raises(ConnectError) as caught:
        asyncio.run(meta.conn.test())
    assert caught.value.reason == "token_expired"
    last = meta.conn.status()["last_test"]
    assert last["ok"] is False and "token_expired" in last["error"] and LONG not in last["error"]


def test_testing_without_a_connection_says_so(meta):
    with pytest.raises(ConnectError) as caught:
        asyncio.run(meta.conn.test())
    assert caught.value.reason == "not_connected" and meta.fake.requests == []


def test_disconnecting_removes_the_token_and_the_facts_and_leaves_meta_alone(meta):
    connect(meta)
    meta.fake.requests.clear()
    assert meta.conn.disconnect() == {"disconnected": True, "still_set_by_environment": False}
    assert not TokenStore(meta.settings).path.exists() and not meta.conn.state_path.exists() and meta.conn.status()["connected"] is False and meta.fake.requests == []


def test_the_registry_row_is_the_existing_one_and_mirrors_the_real_connection(meta, db_session):
    row = meta.conn.sync(db_session)
    first_id = row.id
    assert row.status == "not_connected" and row.external_id == IG_ID and row.display_name == f"@{USER}"
    connect(meta)
    assert meta.conn.sync(db_session).status == "connected" and row.secret_ref == "meta/instagram_token"
    meta.conn.disconnect()
    assert meta.conn.sync(db_session).status == "not_connected" and row.secret_ref == ""
    assert db_session.query(PublicationAccount).filter_by(platform="instagram", language="fa").count() == 1 and row.id == first_id       # never a second record


def test_monitoring_shows_a_broken_connection_and_ignores_one_that_was_never_made(meta, monkeypatch):
    monkeypatch.setattr("app.meta_publishing.connection.meta_config", lambda: type("C", (), {"instagram": meta.settings})())
    monkeypatch.setattr("app.publishing.monitoring.InstagramConnection", lambda: meta.conn, raising=False)
    monkeypatch.setattr("app.meta_publishing.connection.InstagramConnection", lambda *a, **k: meta.conn)
    assert connection_issues() == []
    connect(meta)
    assert connection_issues() == []
    meta.fake.expired = True
    with pytest.raises(ConnectError):
        asyncio.run(meta.conn.test())
    issue = connection_issues()[0]
    assert issue.severity == "problem" and "connection test failed" in issue.title and "token_expired" in issue.detail
    facts = meta.conn._facts() | {"expires_at": "2000-01-01T00:00:00+00:00"}
    meta.conn._write_facts(facts)
    assert "has expired" in connection_issues()[0].title


def use_fake(monkeypatch, meta) -> None:
    monkeypatch.setattr("app.meta_publishing.api._connection", lambda language=None: meta.conn)


def test_the_api_reads_the_connection_without_any_call_to_meta_and_never_returns_a_token(meta, client, monkeypatch):
    use_fake(monkeypatch, meta)
    connect(meta)
    meta.fake.requests.clear()
    body = client.get("/api/meta-publishing/connection").text
    assert meta.fake.requests == [] and not any(secret in body for secret in (SHORT, LONG, SECRET)) and json.loads(body)["verified"] is True


def test_the_api_connects_tests_and_disconnects(meta, client, monkeypatch):
    use_fake(monkeypatch, meta)
    start = client.post("/api/meta-publishing/connection/connect", json={"return_to": "//evil.example"})
    assert start.status_code == 200 and "facebook.com" in start.json()["authorization_url"]
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]
    done = client.get("/api/meta-publishing/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert done.status_code in (302, 307) and urlparse(done.headers["location"]).path == "/" and "instagram=connected" in done.headers["location"]
    assert client.post("/api/meta-publishing/connection/test").json()["account"]["username"] == USER
    assert client.post("/api/meta-publishing/connection/disconnect").json()["disconnected"] is True
    assert client.post("/api/meta-publishing/connection/test").json()["detail"]["reason"] == "not_connected"


def test_every_failed_callback_returns_to_the_studio_with_the_reason(meta, client, monkeypatch):
    use_fake(monkeypatch, meta)
    start = lambda: parse_qs(urlparse(client.post("/api/meta-publishing/connection/connect", json={"return_to": "/cases/15?tab=Publishing&language=fa"}).json()["authorization_url"]).query)["state"][0]  # noqa: E731
    meta.fake.account = {"id": "17841400000000000", "username": "someone.else"}
    wrong = client.get("/api/meta-publishing/oauth/callback", params={"code": "c", "state": start()}, follow_redirects=False).headers["location"]
    query = parse_qs(urlparse(wrong).query)
    assert urlparse(wrong).path == "/cases/15" and query["instagram"] == ["error"] and query["reason"] == ["account_mismatch"] and "someone.else" in query["detail"][0]
    denied = client.get("/api/meta-publishing/oauth/callback", params={"error": "access_denied", "error_description": "The user denied", "state": start()}, follow_redirects=False).headers["location"]
    assert urlparse(denied).path == "/cases/15" and parse_qs(urlparse(denied).query)["reason"] == ["access_denied"]
    unknown = client.get("/api/meta-publishing/oauth/callback", params={"code": "c", "state": "forged"}, follow_redirects=False).headers["location"]
    assert "reason=state_invalid" in unknown or "instagram=error" in unknown
    assert not TokenStore(meta.settings).path.exists()


def test_the_app_file_is_read_from_the_environment_path_first(meta, monkeypatch, tmp_path):
    other = tmp_path / "elsewhere.json"
    other.write_text(json.dumps({"app_id": "999", "app_secret": "s"}))
    monkeypatch.setenv(meta.settings.connection.app_env, str(other))
    assert AppFile(meta.settings).load() == ("999", "s")
