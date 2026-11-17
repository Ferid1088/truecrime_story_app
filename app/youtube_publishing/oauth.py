"""Google OAuth 2.0 for one YouTube channel (one language): authorization-code flow with PKCE, refresh, reconnect and disconnect.

One Google OAuth client serves every channel; each language has its own token file and its own expected channel id (from `publication_accounts`). A connection is only kept when the
authorized channel IS that language's channel: right after the code is exchanged the channel is read (channels.list mine=true) and compared with the expected id; on a mismatch the tokens are revoked and nothing is stored. A refresh token that Google rejects (revoked, expired) turns the connection into
"reconnect required": nothing is retried silently."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from collections.abc import Callable
from datetime import timedelta
from typing import NamedTuple
from urllib.parse import urlencode

import httpx

from app.utils import utc_now
from app.youtube_publishing.config import YouTubeConfig, youtube_config
from app.youtube_publishing.credentials import ClientFile, CredentialsError, Tokens, TokenStore

HttpFactory = Callable[[], httpx.AsyncClient]


class Pending(NamedTuple):
    verifier: str
    made: float
    return_to: str
    language: str


_PENDING: dict[str, Pending] = {}      # state -> the attempt: short-lived, in memory on purpose (a restart ends an unfinished connection attempt)


def pending_attempt(state: str) -> Pending | None:
    """The unfinished connection attempt of a state (the one redirect address serves every channel): its language and where the owner came from."""
    return _PENDING.get(state)


def forget_attempt(state: str) -> None:
    _PENDING.pop(state, None)


class OAuthError(RuntimeError):
    """`reason` is a short code the interface shows: not_configured, state_invalid, token_rejected, channel_mismatch, scopes_missing, reconnect_required, channel_not_registered, google_error."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class YouTubeOAuth:
    def __init__(self, language: str, channel_id: str | None, cfg: YouTubeConfig | None = None, *, http: HttpFactory | None = None, tokens: TokenStore | None = None,
                 client_file: ClientFile | None = None, now=utc_now):
        self.language, self.channel_id = language, channel_id
        self.cfg = cfg or youtube_config()
        self.tokens = tokens or TokenStore(language, self.cfg.oauth)
        self.client_file = client_file or ClientFile(self.cfg.oauth)
        self.http = http or (lambda: httpx.AsyncClient(timeout=self.cfg.api.timeout_seconds))
        self.now = now

    def expected_channel(self) -> str:
        """The channel this language publishes to; a language whose channel is not registered yet has none, and nothing is connected or sent for it."""
        if not self.channel_id:
            raise OAuthError("channel_not_registered", f"The {self.language} YouTube channel id is not registered yet: enter it first (YouTube Studio > Settings > Channel > Advanced settings).")
        return self.channel_id

    # -- the connection page -------------------------------------------------------------------------
    def authorization_url(self, return_to: str = "/") -> str:
        self.expected_channel()
        try:
            client = self.client_file.load()
        except CredentialsError as exc:
            raise OAuthError("not_configured", str(exc)) from exc
        if client.redirect_uris and self.cfg.redirect_uri not in client.redirect_uris:
            raise OAuthError("not_configured", f"The OAuth client does not list {self.cfg.redirect_uri} as an authorized redirect URI.")
        for old in [s for s, old_attempt in _PENDING.items() if time.monotonic() - old_attempt.made > self.cfg.oauth.state_ttl_seconds]:
            del _PENDING[old]
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        _PENDING[state] = Pending(verifier, time.monotonic(), return_to, self.language)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        query = {"client_id": client.client_id, "redirect_uri": self.cfg.redirect_uri, "response_type": "code", "scope": " ".join(self.cfg.oauth.scopes), "access_type": "offline",
                 "prompt": "select_account consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        return f"{self.cfg.oauth.authorize_url}?{urlencode(query)}"

    async def complete(self, code: str, state: str) -> dict:
        """Exchange the code, check the scopes, check the channel, store the tokens. Returns the status."""
        pending = _PENDING.pop(state, None)
        if pending is None or pending.language != self.language or time.monotonic() - pending.made > self.cfg.oauth.state_ttl_seconds:
            raise OAuthError("state_invalid", "The connection attempt expired or did not start here. Press Connect YouTube again.")
        expected = self.expected_channel()
        client = self.client_file.load()
        body = await self._token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": self.cfg.redirect_uri, "code_verifier": pending.verifier,
                                          "client_id": client.client_id, "client_secret": client.client_secret})
        granted = str(body.get("scope", "")).split()
        missing = [s for s in self.cfg.oauth.scopes if s not in granted]
        refresh = body.get("refresh_token")
        if missing or not refresh:
            raise OAuthError("scopes_missing", "Google did not grant everything needed: " + (", ".join(missing) if missing else "no refresh token was issued (revoke the app's access in the Google account and connect again)."))
        channel = await self.channel(body["access_token"])
        if channel["id"] != expected:
            raise OAuthError("channel_mismatch", f"The authorized channel is '{channel['title']}' ({channel['id']}), not the {self.language} channel {expected}. Nothing was stored (and nothing was revoked: Google ties every channel of one account to one grant); "
                                                  "connect again and choose the right channel in Google's account chooser.")
        self.tokens.save(Tokens(refresh, body["access_token"], self.now() + timedelta(seconds=int(body.get("expires_in", 3600))), granted, channel["id"], channel["title"], self.now()))
        return {**self.status(), "return_to": pending.return_to}

    # -- using the connection ------------------------------------------------------------------------
    async def access_token(self) -> str:
        tokens = self.tokens.load()
        if tokens is None:
            raise OAuthError("reconnect_required", f"YouTube ({self.language}) is not connected.")
        self._belongs_to_expected_channel(tokens)
        if tokens.access_token and tokens.expires_at and tokens.expires_at - self.now() > timedelta(seconds=self.cfg.oauth.refresh_margin_seconds):
            return tokens.access_token
        return await self._refresh(tokens)

    async def _refresh(self, tokens: Tokens) -> str:
        try:
            client = self.client_file.load()
        except CredentialsError as exc:
            raise OAuthError("not_configured", str(exc)) from exc
        body = await self._token_request({"grant_type": "refresh_token", "refresh_token": tokens.refresh_token, "client_id": client.client_id, "client_secret": client.client_secret},
                                         refreshing=True)
        tokens.access_token = body["access_token"]
        tokens.expires_at = self.now() + timedelta(seconds=int(body.get("expires_in", 3600)))
        self.tokens.save(tokens)
        return tokens.access_token

    def _belongs_to_expected_channel(self, tokens: Tokens) -> None:
        """No request is ever made with a token of another channel: a token file that does not belong to this language's channel is refused."""
        expected = self.expected_channel()
        if tokens.channel_id != expected:
            raise OAuthError("channel_mismatch", f"The stored {self.language} credentials belong to channel {tokens.channel_id}, not {expected}: disconnect and connect again.")

    async def channel(self, access_token: str) -> dict:
        async with self.http() as http:
            response = await http.get(f"{self.cfg.api.base_url}/channels", params={"part": "id,snippet", "mine": "true"}, headers={"Authorization": f"Bearer {access_token}"})
        if response.status_code == 401:
            raise OAuthError("reconnect_required", f"Google no longer accepts the {self.language} credentials (access was revoked). Disconnect and connect the {self.language} channel again.")
        if response.status_code != 200:
            raise OAuthError("google_error", f"channels.list answered {response.status_code}: {self.tokens.redact(response.text[:300])}")
        items = response.json().get("items") or []
        if not items:
            raise OAuthError("channel_mismatch", "The authorized Google account has no YouTube channel.")
        return {"id": items[0]["id"], "title": (items[0].get("snippet") or {}).get("title", "")}

    async def test(self) -> dict:
        """A real, read-only check (one channels.list call): the stored grant has every required scope, the token works (refreshed if needed) and the channel is the configured one."""
        tokens = self.tokens.load()
        if tokens is None:
            raise OAuthError("reconnect_required", f"YouTube ({self.language}) is not connected.")
        missing = [s for s in self.cfg.oauth.scopes if s not in tokens.scopes]
        if missing:
            raise OAuthError("scopes_missing", "Google did not grant: " + ", ".join(m.rsplit("/", 1)[-1] for m in missing) + ". Disconnect and connect again, and allow every permission.")
        self._belongs_to_expected_channel(tokens)
        channel = await self.channel(await self._refresh(tokens))          # always a fresh token: the test proves the stored refresh token still works, not a cached access token
        if channel["id"] != self.expected_channel():
            raise OAuthError("channel_mismatch", f"The connected channel is {channel['id']}, not {self.expected_channel()}.")
        return {"channel_id": channel["id"], "channel_title": channel["title"], "scopes": [s.rsplit("/", 1)[-1] for s in tokens.scopes], "checked_at": self.now().isoformat()}

    async def disconnect(self, *, revoke: bool) -> bool:
        """Remove this channel's stored credentials. Google ties all tokens of one account to one grant, so revoking is only right when no other channel still uses it."""
        tokens = self.tokens.load()
        if tokens is not None and revoke:
            await self._revoke(tokens.refresh_token)
        return self.tokens.clear()

    def status(self) -> dict:
        """What the interface may show: never a token."""
        configured = self.client_file.configured()
        try:
            tokens = self.tokens.load()
        except CredentialsError:
            tokens = None
        connected = tokens is not None
        return {"live_enabled": self.cfg.live_enabled, "client_configured": configured, "connected": connected, "language": self.language, "expected_channel_id": self.channel_id,
                "channel_id": tokens.channel_id if tokens else None, "channel_title": tokens.channel_title if tokens else None,
                "channel_matches": bool(tokens and self.channel_id and tokens.channel_id == self.channel_id),
                "scopes_ok": bool(tokens and all(s in tokens.scopes for s in self.cfg.oauth.scopes)),
                "connected_at": tokens.connected_at.isoformat() if tokens and tokens.connected_at else None, "redirect_uri": self.cfg.redirect_uri,
                "privacy": self.cfg.upload.initial_privacy}

    async def _token_request(self, form: dict, *, refreshing: bool = False) -> dict:
        async with self.http() as http:
            response = await http.post(self.cfg.oauth.token_url, data=form)
        if response.status_code == 200:
            return response.json()
        problem = response.json().get("error", "") if response.headers.get("content-type", "").startswith("application/json") else ""
        if refreshing and problem == "invalid_grant":
            raise OAuthError("reconnect_required", "Google rejected the stored refresh token (revoked or expired). Disconnect and connect YouTube again.")
        raise OAuthError("token_rejected", f"Google refused the token request ({response.status_code} {problem}).")

    async def _revoke(self, token: str | None) -> None:
        if not token:
            return
        async with self.http() as http:
            await http.post(self.cfg.oauth.revoke_url, data={"token": token})
