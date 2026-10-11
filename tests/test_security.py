"""Access control: login, bearer token, cookie, CSRF, rate limit, loopback guard, headers."""
import time

import pytest
from fastapi.testclient import TestClient

from app.core import security as S
from app.core.config import settings
from app.main import app

TOKEN = "t0ken-" + "x9Zq" * 8
ORIGIN = "http://127.0.0.1:3000"


@pytest.fixture(autouse=True)
def _reset():
    S.reset_limits()
    yield
    S.reset_limits()


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setattr(settings, "access_token", TOKEN)
    return TestClient(app)


def test_without_a_token_this_machine_is_let_in(monkeypatch):
    monkeypatch.setattr(settings, "access_token", "")
    assert TestClient(app).get("/api/cases").status_code != 401


def test_without_a_token_other_machines_are_refused(monkeypatch):
    monkeypatch.setattr(settings, "access_token", "")
    import asyncio

    import httpx

    async def go():
        transport = httpx.ASGITransport(app=app, client=("10.1.2.3", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://studio") as c:
            return await c.get("/api/cases"), await c.get("/health")

    r, health = asyncio.run(go())
    assert r.status_code == 403 and "ACCESS_TOKEN" in r.json()["detail"]
    assert health.status_code == 200  # health stays reachable


def test_requests_without_credentials_are_refused(secured):
    assert secured.get("/api/cases").status_code == 401
    assert secured.post("/api/cases", json={}).status_code == 401
    assert secured.get("/openapi.json").status_code == 401


def test_health_and_login_are_public(secured):
    assert secured.get("/health").status_code == 200
    assert secured.get("/api/auth/session").json() == {"authenticated": False, "auth_required": True}


def test_bearer_token_works_and_a_wrong_one_does_not(secured):
    assert secured.get("/api/cases", headers={"Authorization": f"Bearer {TOKEN}"}).status_code != 401
    assert secured.get("/api/cases", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert secured.get("/api/cases", headers={"Authorization": TOKEN}).status_code == 401  # no scheme


def test_login_sets_a_locked_down_cookie_and_the_cookie_works(secured):
    r = secured.post("/api/auth/login", json={"token": TOKEN})
    assert r.status_code == 200 and r.json()["authenticated"] is True
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "tc_session=" in cookie
    assert TOKEN not in r.headers["set-cookie"], "the secret itself never goes into the cookie"
    assert secured.get("/api/cases").status_code != 401
    assert secured.get("/api/auth/session").json()["authenticated"] is True


def test_wrong_token_is_rejected_without_a_cookie(secured):
    r = secured.post("/api/auth/login", json={"token": "wrong"})
    assert r.status_code == 401 and "set-cookie" not in r.headers


def test_tampered_expired_and_rotated_sessions_are_refused(secured, monkeypatch):
    good = S.make_session(TOKEN)
    assert S.session_valid(good, TOKEN)
    expires, _, sig = good.partition(".")
    assert not S.session_valid(f"{int(expires) + 10}.{sig}", TOKEN), "changing the expiry breaks the signature"
    assert not S.session_valid(good, TOKEN, now=time.time() + 86400 * 400), "expired"
    assert not S.session_valid(good, "another-token"), "a rotated token invalidates every old session"
    assert not S.session_valid("garbage", TOKEN) and not S.session_valid(None, TOKEN)
    secured.cookies.set("tc_session", f"{int(expires) + 10}.{sig}")
    assert secured.get("/api/cases").status_code == 401


def test_logout_clears_the_cookie(secured):
    secured.post("/api/auth/login", json={"token": TOKEN})
    r = secured.post("/api/auth/logout")
    assert "tc_session" in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()


def test_cookie_requests_from_a_foreign_origin_cannot_change_things(secured):
    secured.post("/api/auth/login", json={"token": TOKEN})
    evil = secured.post("/api/cases/999999/archive", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    fine = secured.post("/api/cases/999999/archive", headers={"Origin": ORIGIN})
    assert fine.status_code != 403
    plain = secured.post("/api/cases/999999/archive")  # no Origin header: curl, scripts, same-origin tools
    assert plain.status_code != 403


def test_failed_attempts_lock_out_even_the_right_token(secured):
    for _ in range(S.MAX_FAILURES):
        assert secured.post("/api/auth/login", json={"token": "bad"}).status_code == 401
    assert secured.post("/api/auth/login", json={"token": TOKEN}).status_code == 429
    assert secured.get("/api/cases", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 429
    assert S.blocked("testclient", now=time.time() + S.WINDOW_SECONDS + 1) is False  # the lock expires


def test_bad_bearer_attempts_count_toward_the_lockout(secured):
    for _ in range(S.MAX_FAILURES):
        secured.get("/api/cases", headers={"Authorization": "Bearer nope"})
    assert secured.get("/api/cases", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 429


def test_preflight_passes_and_denials_carry_cors_headers(secured):
    pre = secured.options("/api/cases", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == ORIGIN
    denied = secured.get("/api/cases", headers={"Origin": ORIGIN})
    assert denied.status_code == 401 and denied.headers["access-control-allow-origin"] == ORIGIN
    foreign = secured.options("/api/cases", headers={"Origin": "https://evil.example",
                                                      "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in foreign.headers


def test_hardening_headers_on_every_response(secured):
    for r in (secured.get("/health"), secured.get("/api/cases")):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
    assert secured.get("/api/auth/session").headers["cache-control"] == "no-store"


def test_cors_origins_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", "https://studio.example/, http://localhost:3000")
    assert S.allowed_origins() == {"https://studio.example", "http://localhost:3000"}
