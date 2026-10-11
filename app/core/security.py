"""Access control for the API: one operator, one secret.

* TRUECRIME_ACCESS_TOKEN set  -> every /api request needs it, as `Authorization: Bearer <token>` or as the
  signed session cookie that POST /api/auth/login hands out (httpOnly, SameSite=Strict).
* TRUECRIME_ACCESS_TOKEN empty -> no login, but only requests from this machine are answered, so a server started
  by mistake on 0.0.0.0 does not hand the studio to the network.

Also: unsafe requests carrying a cookie must come from an allowed origin (CSRF), failed logins are
rate-limited, and every response gets the usual hardening headers.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings

COOKIE = "tc_session"
PUBLIC_PATHS = {"/health", "/api/auth/login", "/api/auth/session", "/api/auth/logout"}
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}  # "testclient" = Starlette's in-process client
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

MAX_FAILURES = 5
WINDOW_SECONDS = 60.0
_failures: dict[str, deque] = defaultdict(deque)


# --- tokens and sessions -----------------------------------------------------------------------


def _key(token: str) -> bytes:
    return hashlib.sha256(b"truecrime-session|" + token.encode("utf-8")).digest()


def make_session(token: str, days: int | None = None, now: float | None = None) -> str:
    expires = int((now or time.time()) + 86400 * (days or settings.session_days))
    sig = hmac.new(_key(token), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def session_valid(value: str | None, token: str, now: float | None = None) -> bool:
    if not value or "." not in value or not token:
        return False
    expires, _, sig = value.partition(".")
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    good = hmac.new(_key(token), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good)


def token_matches(candidate: str | None, token: str) -> bool:
    return bool(candidate) and bool(token) and hmac.compare_digest(
        hashlib.sha256(candidate.encode()).digest(), hashlib.sha256(token.encode()).digest())


# --- rate limit --------------------------------------------------------------------------------


def blocked(ip: str, now: float | None = None) -> bool:
    now = now or time.time()
    q = _failures[ip]
    while q and q[0] < now - WINDOW_SECONDS:
        q.popleft()
    return len(q) >= MAX_FAILURES


def record_failure(ip: str, now: float | None = None) -> None:
    _failures[ip].append(now or time.time())


def reset_limits() -> None:
    _failures.clear()


# --- request checks ----------------------------------------------------------------------------


def allowed_origins() -> set[str]:
    return {o.strip().rstrip("/") for o in settings.cors_origins.split(",") if o.strip()}


def _headers(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


def _cookie(headers: dict[str, str], name: str) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def authorize(method: str, path: str, headers: dict[str, str], client_ip: str) -> tuple[int, str] | None:
    """None = allowed. Otherwise (status, message)."""
    token = settings.access_token
    if path in PUBLIC_PATHS or method == "OPTIONS":
        return None
    if not token:
        if client_ip not in LOOPBACK:
            return 403, "No access token is configured, so only this machine may use the API. Set TRUECRIME_ACCESS_TOKEN."
        return None
    if blocked(client_ip):
        return 429, "Too many failed attempts. Wait a minute."
    auth = headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    if bearer is not None:
        if token_matches(bearer, token):
            return None
        record_failure(client_ip)
        return 401, "Not authenticated"
    cookie = _cookie(headers, COOKIE)
    if session_valid(cookie, token):
        origin = headers.get("origin")
        if method in UNSAFE and origin and origin.rstrip("/") not in allowed_origins():
            return 403, "Request origin not allowed"
        return None
    return 401, "Not authenticated"


class SecurityMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        client = (scope.get("client") or ("", 0))[0]
        denied = authorize(scope["method"], scope["path"], headers, client)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                extra = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                         (b"referrer-policy", b"no-referrer")]
                if scope["path"].startswith("/api/auth"):
                    extra.append((b"cache-control", b"no-store"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        if denied is not None:
            status, detail = denied
            body = json.dumps({"detail": detail}).encode()
            await send_with_headers({"type": "http.response.start", "status": status, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send_with_headers)


# --- /api/auth ---------------------------------------------------------------------------------

router = APIRouter()


class LoginBody(BaseModel):
    token: str


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    token = settings.access_token
    if not token:
        return {"authenticated": True, "auth_required": False}
    ip = _ip(request)
    if blocked(ip):
        response.status_code = 429
        return {"detail": "Too many failed attempts. Wait a minute."}
    if not token_matches(body.token, token):
        record_failure(ip)
        response.status_code = 401
        return {"detail": "Wrong access token"}
    response.set_cookie(COOKIE, make_session(token), max_age=86400 * settings.session_days, httponly=True,
                        samesite="strict", secure=settings.cookie_secure, path="/")
    return {"authenticated": True, "auth_required": True}


@router.get("/api/auth/session")
def session(request: Request):
    token = settings.access_token
    if not token:
        return {"authenticated": True, "auth_required": False}
    ok = session_valid(request.cookies.get(COOKIE), token) or token_matches(
        (request.headers.get("authorization") or "")[7:].strip() or None, token)
    return {"authenticated": bool(ok), "auth_required": True}


@router.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"authenticated": False}
