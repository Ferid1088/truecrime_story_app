"""The owner's connection of the Instagram account (Facebook Login web flow): Connect, Test connection, Disconnect, and the account row in `publication_accounts`.

Connect: the owner agrees on Facebook; the code is exchanged for a 60-day token (the Meta app's id and secret come from an owner-only file outside the repository); the token is
only kept when the Instagram account it reaches IS the configured account (id and username) and it holds every required permission. A wrong account stores nothing.
Test: read-only calls with the stored token: granted permissions, the account, the publishing limit. Disconnect removes the token here; the grant at Meta is left alone (it is
shared by every account the owner connects through Facebook), the owner can remove the app in the Facebook settings.
The token is never returned, logged or put in a URL by this module."""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.core.env import env_value
from app.db.models import PublicationAccount
from app.meta_publishing.config import InstagramSettings, meta_config
from app.meta_publishing.graph import MetaError, MetaGraphClient
from app.meta_publishing.token import TokenError, TokenStore
from app.publishing.registry import PublicationRegistry
from app.utils import utc_now

PLATFORM = "instagram"
_PENDING: dict[str, tuple[float, str]] = {}      # state -> (created at, where to return to): in memory on purpose (a restart ends an unfinished attempt)


def pending_return(state: str) -> str | None:
    """Where an unfinished connection attempt came from (None when the state is not one of ours)."""
    pending = _PENDING.get(state)
    return pending[1] if pending else None


def forget_attempt(state: str) -> None:
    _PENDING.pop(state, None)


class ConnectError(RuntimeError):
    """`reason`: not_configured, live_disabled, state_invalid, token_rejected, account_mismatch, permission_missing, not_connected, token_expired, meta_error, wrong_language."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass
class _HeldToken:
    """A token that is only in memory (not yet stored): it fits the interface the Graph client wants."""

    value: str

    def get(self) -> str:
        return self.value

    def redact(self, text: str) -> str:
        return text.replace(self.value, "[token]")


class AppFile:
    """The Meta app's id and secret: {"app_id": "...", "app_secret": "..."} in an owner-only file (path from the environment variable named in the config, else the config's)."""

    def __init__(self, settings: InstagramSettings):
        self.s = settings.connection

    @property
    def path(self) -> Path:
        return Path(env_value(self.s.app_env) or self.s.app_file).expanduser()

    def configured(self) -> bool:
        return self.path.is_file()

    def load(self) -> tuple[str, str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return str(raw["app_id"]), str(raw["app_secret"])
        except (OSError, ValueError, KeyError) as exc:
            raise ConnectError("not_configured", f"The Meta app file {self.path} is missing or unreadable. It holds {{\"app_id\": ..., \"app_secret\": ...}} (see the setup steps).") from exc


class InstagramConnection:
    def __init__(self, settings: InstagramSettings | None = None, *, tokens: TokenStore | None = None, app: AppFile | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 now=utc_now):
        self.s = settings or meta_config().instagram
        self.tokens = tokens or TokenStore(self.s)
        self.app = app or AppFile(self.s)
        self.transport, self.now = transport, now

    # -- the stored facts about the connection (never the token) ---------------------------------------
    @property
    def state_path(self) -> Path:
        return Path(self.s.connection.state_file.format(language=self.s.language)).expanduser()

    def _facts(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_facts(self, facts: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            json.dump(facts, f)
        os.replace(temporary, self.state_path)

    def _client(self, tokens=None) -> MetaGraphClient:
        return MetaGraphClient(self.s, tokens or self.tokens, transport=self.transport)

    def _live(self) -> None:
        if not self.s.connection.live_enabled:
            raise ConnectError("live_disabled", "Live Meta calls are off (config/meta_publishing.json: instagram.connection.live_enabled is false).")

    def _url(self, *parts: str) -> str:
        return "/".join([self.s.graph_host.rstrip("/"), self.s.api_version, *parts])

    # -- connect -----------------------------------------------------------------------------------------
    def authorization_url(self, return_to: str = "/") -> str:
        self._live()
        app_id, _ = self.app.load()
        for old in [k for k, (made, _) in _PENDING.items() if time.monotonic() - made > self.s.connection.state_ttl_seconds]:
            del _PENDING[old]
        state = secrets.token_urlsafe(24)
        _PENDING[state] = (time.monotonic(), return_to)
        query = {"client_id": app_id, "redirect_uri": self.s.connection.redirect_uri, "state": state, "response_type": "code", "scope": ",".join(self.s.required_permissions)}
        return f"{self.s.connection.authorize_url.format(version=self.s.api_version)}?{urlencode(query)}"

    async def _exchange(self, form: dict) -> dict:
        """The app secret and the codes travel in the request body, never in a URL."""
        async with httpx.AsyncClient(transport=self.transport, timeout=self.s.request_timeout_s) as http:
            response = await http.post(self._url("oauth", "access_token"), data=form)
        body = response.json() if response.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {}
        if response.status_code != 200 or "access_token" not in body:
            message = ((body.get("error") or {}).get("message") if isinstance(body, dict) else None) or f"HTTP {response.status_code}"
            raise ConnectError("token_rejected", f"Meta refused the token request: {message[:200]}")
        return body

    async def complete(self, code: str, state: str) -> dict:
        """Exchange the code, check permissions and the account, store the token. Returns the status."""
        self._live()
        pending = _PENDING.pop(state, None)
        if pending is None or time.monotonic() - pending[0] > self.s.connection.state_ttl_seconds:
            raise ConnectError("state_invalid", "The connection attempt expired or did not start here. Press Connect Instagram again.")
        app_id, app_secret = self.app.load()
        short = await self._exchange({"client_id": app_id, "client_secret": app_secret, "redirect_uri": self.s.connection.redirect_uri, "code": code})
        long = await self._exchange({"grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret, "fb_exchange_token": short["access_token"]})
        held = _HeldToken(long["access_token"])
        info = await self._verify(self._client(held))
        expires = self.now() + timedelta(seconds=int(long["expires_in"])) if long.get("expires_in") else None
        self.tokens.set(held.value)
        self._write_facts({"connected_at": self.now().isoformat(), "expires_at": expires.isoformat() if expires else None, "username": info["username"], "ig_account_id": info["id"],
                           "last_test": {"ok": True, "at": self.now().isoformat(), "error": None}})
        return {**self.status(), "return_to": pending[1]}

    async def _verify(self, client: MetaGraphClient) -> dict:
        """Permissions and the account, with whatever token the client holds. Raises ConnectError; nothing is stored here."""
        try:
            granted = await client.granted_permissions()
            missing = [p for p in self.s.required_permissions if p not in granted]
            if missing:
                raise ConnectError("permission_missing", f"Meta did not grant: {', '.join(missing)}. Connect again and allow every permission.")
            info = await client.account()
        except MetaError as exc:
            raise ConnectError("token_expired" if exc.kind == "token_expired" else "meta_error", f"Meta answered: {exc}") from exc
        if str(info.get("id")) != self.s.account.ig_account_id or info.get("username") != self.s.account.username:
            raise ConnectError("account_mismatch", f"The token reaches Instagram account '{info.get('username')}' ({info.get('id')}), not the {self.s.language} account "
                                                   f"{self.s.account.username} ({self.s.account.ig_account_id}). Nothing was stored.")
        return info

    # -- test, disconnect, status ---------------------------------------------------------------------------
    async def test(self) -> dict:
        """Read-only: granted permissions, the account and the publishing limit, with the stored token. The outcome is remembered for the Monitoring page."""
        self._live()
        try:
            self.tokens.get()
        except TokenError as exc:
            raise ConnectError("not_connected", "Instagram is not connected.") from exc
        facts = self._facts()
        client = self._client()
        try:
            info = await self._verify(client)
            limit = await client.publishing_limit()
        except ConnectError as exc:
            self._remember_test(facts, exc)
            raise
        except MetaError as exc:
            error = ConnectError("meta_error", f"Meta answered: {self.tokens.redact(str(exc))}")
            self._remember_test(facts, error)
            raise error from exc
        usage = ((limit.get("data") or [{}])[0]).get("quota_usage") if isinstance(limit.get("data"), list) else None
        facts.update({"username": info["username"], "ig_account_id": info["id"], "last_test": {"ok": True, "at": self.now().isoformat(), "error": None}})
        self._write_facts(facts)
        return {"account": {"id": info["id"], "username": info["username"]}, "permissions": list(self.s.required_permissions), "quota_usage": usage, "checked_at": self.now().isoformat()}

    def _remember_test(self, facts: dict, error: ConnectError) -> None:
        facts["last_test"] = {"ok": False, "at": self.now().isoformat(), "error": f"{error.reason}: {self.tokens.redact(str(error))}"[:400]}
        self._write_facts(facts)

    def disconnect(self) -> dict:
        source = self.tokens.source()
        removed = self.tokens.clear()
        if self.state_path.is_file():
            self.state_path.unlink()
        return {"disconnected": removed, "still_set_by_environment": source == "environment"}

    def status(self) -> dict:
        """What the interface may show: never the token."""
        facts, source = self._facts(), self.tokens.source()
        expires = datetime.fromisoformat(facts["expires_at"]).replace(tzinfo=UTC) if facts.get("expires_at") else None
        days_left = (expires - self.now()).total_seconds() / 86400 if expires else None
        c = self.s.connection
        return {"language": self.s.language, "brand": self.s.account.brand, "expected_account": {"id": self.s.account.ig_account_id, "username": self.s.account.username},
                "app_configured": self.app.configured(), "live_enabled": c.live_enabled, "publishing_live_enabled": self.s.live_enabled, "token_source": source, "connected": source is not None,
                "verified": bool(facts.get("ig_account_id") == self.s.account.ig_account_id and facts.get("username") == self.s.account.username),
                "connected_at": facts.get("connected_at"), "expires_at": facts.get("expires_at"), "days_left": round(days_left, 1) if days_left is not None else None,
                "expired": bool(days_left is not None and days_left <= 0), "expiring_soon": bool(days_left is not None and 0 < days_left <= c.expiry_warning_days),
                "last_test": facts.get("last_test"), "required_permissions": self.s.required_permissions, "redirect_uri": c.redirect_uri}

    # -- the account row of the registry -----------------------------------------------------------------------
    def sync(self, db: Session) -> PublicationAccount:
        """The registry's account row (the one that already exists for the language: never a second one) mirrors the real connection."""
        account = PublicationRegistry(db).ensure_accounts(self.s.language)[PLATFORM]
        status = self.status()
        good = status["connected"] and status["verified"] and not status["expired"]
        wanted = ("connected" if good else "not_connected", self.s.account.ig_account_id, f"@{self.s.account.username}", "meta/instagram_token" if status["connected"] else "")
        if (account.status, account.external_id, account.display_name, account.secret_ref) != wanted:
            account.status, account.external_id, account.display_name, account.secret_ref = wanted
            db.commit()
        return account
