"""The owner's Google OAuth client and the channel's tokens, kept outside the repository (owner-only files) and out of every response and log.

The client file is the JSON Google gives for an OAuth client ("web" or "installed"). The token file holds the refresh token, the current access token and its expiry, the scopes
that were granted and the channel they were verified for. Nothing here returns a secret to a caller other than the OAuth and API clients."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.env import env_value
from app.youtube_publishing.config import OAuthSettings, youtube_config


class CredentialsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClientSecret:
    client_id: str
    client_secret: str
    redirect_uris: tuple[str, ...]


class ClientFile:
    def __init__(self, settings: OAuthSettings | None = None):
        self.settings = settings or youtube_config().oauth

    @property
    def path(self) -> Path:
        return Path(env_value(self.settings.client_secret_env) or self.settings.client_secret_file).expanduser()

    def configured(self) -> bool:
        return self.path.is_file()

    def load(self) -> ClientSecret:
        if not self.path.is_file():
            raise CredentialsError(f"No Google OAuth client file at {self.path}.")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CredentialsError("The Google OAuth client file is not valid JSON.") from exc
        body = data.get("web") or data.get("installed") or {}
        if not body.get("client_id") or not body.get("client_secret"):
            raise CredentialsError("The Google OAuth client file has no client_id and client_secret (download the OAuth client JSON from Google Cloud).")
        return ClientSecret(body["client_id"], body["client_secret"], tuple(body.get("redirect_uris") or ()))


@dataclass
class Tokens:
    refresh_token: str
    access_token: str | None
    expires_at: datetime | None
    scopes: list[str]
    channel_id: str | None = None
    channel_title: str | None = None
    connected_at: datetime | None = None


class TokenStore:
    def __init__(self, language: str, settings: OAuthSettings | None = None):
        self.language = language
        self.settings = settings or youtube_config().oauth

    @property
    def path(self) -> Path:
        return Path(self.settings.token_file.format(language=self.language)).expanduser()

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> Tokens | None:
        if not self.path.is_file():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return Tokens(raw["refresh_token"], raw.get("access_token"), _parse(raw.get("expires_at")), list(raw.get("scopes") or []), raw.get("channel_id"), raw.get("channel_title"),
                          _parse(raw.get("connected_at")))
        except (json.JSONDecodeError, KeyError) as exc:
            raise CredentialsError("The stored YouTube token file is unreadable: disconnect and connect again.") from exc

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {"refresh_token": tokens.refresh_token, "access_token": tokens.access_token, "expires_at": _iso(tokens.expires_at), "scopes": tokens.scopes,
                "channel_id": tokens.channel_id, "channel_title": tokens.channel_title, "connected_at": _iso(tokens.connected_at)}
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            json.dump(body, f)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def clear(self) -> bool:
        if self.path.is_file():
            self.path.unlink()
            return True
        return False

    def redact(self, text: str) -> str:
        """The text with every stored secret replaced: an error from Google can never carry a token into a log or a response."""
        try:
            tokens, client = self.load(), ClientFile(self.settings)
            secrets = [tokens.refresh_token, tokens.access_token] if tokens else []
            if client.configured():
                secrets.append(client.load().client_secret)
        except CredentialsError:
            return text
        for secret in filter(None, secrets):
            text = text.replace(secret, "[secret]")
        return text


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
