"""The account connections of every language and platform in one table: which account each destination is, whether a real authenticated check has confirmed it, and what the owner
must still do. Reads only the registry, the configuration and the stored facts of earlier connection tests: it calls neither Google nor Meta (the per-platform Test connection
endpoints do, read-only). Nothing here uploads, publishes or changes a credential."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.orm import Session

from app.meta_publishing.config import meta_config
from app.meta_publishing.connection import InstagramConnection
from app.meta_publishing.facebook_connection import FacebookConnection
from app.youtube_publishing.channels import YouTubeChannels
from app.youtube_publishing.config import youtube_config


class ConnectionState(StrEnum):
    VERIFIED = "verified"                                        # a real authenticated read-only request confirmed the account
    KNOWN_NOT_AUTHENTICATED = "known_not_authenticated"          # the account is known, no credentials have confirmed it yet
    NOT_CREATED = "not_created"                                  # the account does not exist yet
    AWAITING_CREATION = "awaiting_creation"                      # creation was attempted or is pending
    AUTH_FAILED = "auth_failed"                                  # the last authenticated check failed
    NEEDS_OWNER_AUTHORIZATION = "needs_owner_authorization"      # the owner must authorize before the account id can be known


def _row(language: str, platform: str, brand: str, state: ConnectionState, *, account_id: str | None = None, identity_verified: bool = False, credentials_valid: bool | None = None,
         last_verified: str | None = None, last_error: str | None = None, action: str | None = None, missing_permissions: list[str] | None = None, testable: bool = False) -> dict:
    return {"language": language, "platform": platform, "brand": brand, "account_id": account_id, "identity_verified": identity_verified, "credentials_valid": credentials_valid,
            "state": state.value, "last_verified": last_verified, "last_error": last_error, "owner_action": action, "missing_permissions": missing_permissions or [], "testable": testable}


def _youtube(channels: YouTubeChannels, language: str) -> dict:
    status = channels.status(language)
    brand = status["name"] or status["label"]
    if status["awaiting"]:
        return _row(language, "youtube", brand, ConnectionState.AWAITING_CREATION, action="Create the channel, then register its channel id.")
    if not status["registered"]:
        return _row(language, "youtube", brand, ConnectionState.NEEDS_OWNER_AUTHORIZATION,
                    action="Authorize the Google account of this channel with Discover channel; the channel id Google returns is checked against the intended channel before it is registered.")
    common = {"account_id": status["expected_channel_id"]}
    if not status["connected"]:
        return _row(language, "youtube", brand, ConnectionState.KNOWN_NOT_AUTHENTICATED, **common, action="Press Connect YouTube and choose this channel in Google's account chooser.", testable=False)
    if not status["channel_matches"] or not status["scopes_ok"]:
        return _row(language, "youtube", brand, ConnectionState.AUTH_FAILED, **common, credentials_valid=False, last_error="The stored credentials do not belong to the registered channel or lack a scope.",
                    action="Disconnect and connect again.", testable=True)
    return _row(language, "youtube", brand, ConnectionState.VERIFIED, **common, identity_verified=True, credentials_valid=True, last_verified=status["connected_at"], testable=True)


def _meta(language: str, platform: str, brand: str, status: dict, account_id: str, permissions: list[str]) -> dict:
    last = status["last_test"]
    common = {"account_id": account_id, "missing_permissions": permissions}
    if not status["connected"]:
        return _row(language, platform, brand, ConnectionState.KNOWN_NOT_AUTHENTICATED, **common, action="Store the Meta system-user token (python -m app.meta_publishing.token set), then press Test connection.", testable=True)
    if last is None:
        return _row(language, platform, brand, ConnectionState.KNOWN_NOT_AUTHENTICATED, **common, action="Press Test connection.", testable=True)
    if not last["ok"]:
        return _row(language, platform, brand, ConnectionState.AUTH_FAILED, **common, credentials_valid=False, last_error=last["error"], action="Fix what the error names (assign the asset to the system user), then test again.", testable=True)
    return _row(language, platform, brand, ConnectionState.VERIFIED, **common, identity_verified=bool(status["verified"]), credentials_valid=not status.get("expired", False), last_verified=last["at"], testable=True)


def connection_table(db: Session) -> list[dict]:
    """One row per language and platform, in the order of the configured YouTube channels (the full language list)."""
    youtube, meta = YouTubeChannels(db), meta_config()
    rows: list[dict] = []
    for language, label in youtube_config().channels.items():
        rows.append(_youtube(youtube, language))
        intended = label.name or label.label
        if language in meta.instagram.accounts:
            conn = InstagramConnection(meta.instagram.for_language(language))
            status = conn.status()
            rows.append(_meta(language, "instagram", meta.instagram.accounts[language].brand, status, meta.instagram.accounts[language].ig_account_id, status["missing_for_publishing"]))
        else:
            rows.append(_row(language, "instagram", intended, ConnectionState.NOT_CREATED, action="Create the Instagram professional account and link it to the Facebook Page."))
        if language in meta.facebook.pages:
            status = FacebookConnection(language=language).status()
            rows.append(_meta(language, "facebook", meta.facebook.pages[language].brand, status, meta.facebook.pages[language].page_id,
                              [p for p in status["required_permissions"] if p not in status["granted_permissions"]] if status["last_test"] else []))
        else:
            rows.append(_row(language, "facebook", intended, ConnectionState.NOT_CREATED, action="Create the Facebook Page."))
    return rows
