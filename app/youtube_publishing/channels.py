"""The YouTube channels of the application: one per language, registered in `publication_accounts` (platform "youtube"). The registry holds the channel id the language must publish to;
the credentials of each channel live in their own token file outside the repository, and one Google OAuth client serves all of them."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.db.models import PublicationAccount
from app.publishing.registry import PublicationRegistry
from app.youtube_publishing.config import YouTubeConfig, youtube_config
from app.youtube_publishing.oauth import HttpFactory, YouTubeOAuth

CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
PLATFORM = "youtube"


class ChannelError(RuntimeError):
    """`reason`: unknown_language, invalid_channel_id, channel_in_use, disconnect_first."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class YouTubeChannels:
    def __init__(self, db: Session, cfg: YouTubeConfig | None = None, *, http: HttpFactory | None = None):
        self.db, self.cfg, self.http = db, cfg or youtube_config(), http

    def _account(self, language: str) -> PublicationAccount:
        if language not in self.cfg.channels:
            raise ChannelError("unknown_language", f"There is no YouTube channel for '{language}' (configured: {', '.join(self.cfg.channels)}).")
        return PublicationRegistry(self.db).ensure_accounts(language)[PLATFORM]

    def channel_id(self, language: str) -> str | None:
        return self._account(language).external_id or None

    def oauth(self, language: str) -> YouTubeOAuth:
        return YouTubeOAuth(language, self.channel_id(language), self.cfg, http=self.http)

    def register(self, language: str, channel_id: str) -> PublicationAccount:
        """Record the channel id a language publishes to. It is never changed while credentials are stored, and one channel never serves two languages."""
        channel_id = channel_id.strip()
        account = self._account(language)
        if not CHANNEL_ID.match(channel_id):
            raise ChannelError("invalid_channel_id", "A YouTube channel id starts with UC and has 24 characters (YouTube Studio > Settings > Channel > Advanced settings).")
        if account.external_id == channel_id:
            return account
        other = self.db.query(PublicationAccount).filter(PublicationAccount.platform == PLATFORM, PublicationAccount.external_id == channel_id,
                                                         PublicationAccount.language != language).first()
        if other is not None:
            raise ChannelError("channel_in_use", f"This channel is already the {other.language} channel.")
        if self.oauth(language).tokens.exists():
            raise ChannelError("disconnect_first", f"The {language} channel is connected: disconnect it before its channel id is changed.")
        account.external_id, account.display_name, account.status = channel_id, f"https://www.youtube.com/channel/{channel_id}", "not_connected"
        self.db.commit()
        return account

    def sync(self, language: str) -> None:
        """The account row mirrors the real credentials: connected only while the stored token belongs to the registered channel."""
        account, oauth = self._account(language), self.oauth(language)
        status = oauth.status()
        account.status = "connected" if status["connected"] and status["channel_matches"] else "not_connected"
        account.secret_ref = f"youtube/token_{language}" if status["connected"] else ""
        self.db.commit()

    async def disconnect(self, language: str) -> dict:
        """Disconnect one channel. Its credentials are removed; Google's grant is revoked only when it was the last connected channel (one account's channels share one grant, and revoking
        it would silently disconnect the others)."""
        others = [other for other in self.cfg.channels if other != language and self.oauth(other).tokens.exists()]
        removed = await self.oauth(language).disconnect(revoke=not others)
        self.sync(language)
        return {"disconnected": removed, "language": language, "revoked": removed and not others, "kept_connected": others}

    def status(self, language: str) -> dict:
        label = self.cfg.channels[language]
        return {**self.oauth(language).status(), "label": label.label, "name": label.name, "registered": self.channel_id(language) is not None}

    def overview(self) -> list[dict]:
        return [self.status(language) for language in self.cfg.channels]
