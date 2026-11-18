"""The Graph API client of the Instagram Reels flow (Facebook Login for Business / Unified Login: graph.facebook.com, resumable upload on rupload.facebook.com).

Endpoints, from Meta's content-publishing page (read 2026-11-15):
  POST   {graph}/{version}/{ig_id}/media              media_type=REELS, upload_type=resumable        -> {"id": container}
  POST   {upload}/ig-api-upload/{version}/{container} headers Authorization: OAuth, offset, file_size -> {"success": true}
  GET    {graph}/{version}/{container}?fields=status_code   IN_PROGRESS | FINISHED | ERROR | EXPIRED | PUBLISHED
  POST   {graph}/{version}/{ig_id}/media_publish      creation_id                                    -> {"id": media}
  GET    {graph}/{version}/{ig_id}/content_publishing_limit
Reading calls and the container upload are retried (bounded, with a backoff); `media_publish` is never retried: a lost answer is `PublishOutcomeUnknown`."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from app.meta_publishing.config import InstagramSettings
from app.meta_publishing.token import TokenStore

log = logging.getLogger(__name__)
_TOKEN_CODES = {190, 102}
_PERMISSION_CODES = {10, 200, 299, 3}
_RATE_CODES = {4, 17, 32, 613, 9007}


class MetaError(RuntimeError):
    """A failed Meta call. `kind`: token_invalid | token_expired | permission_missing | rate_limited | invalid_request | upload_failed | server | timeout | network."""

    def __init__(self, kind: str, message: str, *, code: int | None = None, retriable: bool = False):
        super().__init__(message)
        self.kind, self.code, self.retriable = kind, code, retriable


class PublishOutcomeUnknown(MetaError):
    """`media_publish` got no answer: the Reel may or may not be public. It must be looked at on Instagram, never retried."""


def classify(status: int, body: Any, message: str) -> MetaError:
    error = (body or {}).get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    subcode = error.get("error_subcode") if isinstance(error, dict) else None
    text = (error.get("message") if isinstance(error, dict) else None) or message
    if code in _TOKEN_CODES or status == 401:
        expired = subcode in {463, 467} or "expired" in text.lower()
        return MetaError("token_expired" if expired else "token_invalid", text, code=code)
    if code in _PERMISSION_CODES or status == 403 or "permission" in text.lower():
        return MetaError("permission_missing", text, code=code)
    if code in _RATE_CODES or status == 429:
        return MetaError("rate_limited", text, code=code, retriable=True)
    if status >= 500:
        return MetaError("server", text, code=code, retriable=True)
    return MetaError("invalid_request", text, code=code)


class MetaGraphClient:
    """`transport` is injectable: the tests give it an httpx.MockTransport, so that no test can reach Meta."""

    def __init__(self, settings: InstagramSettings, tokens: TokenStore, *, transport: httpx.AsyncBaseTransport | None = None, sleep=asyncio.sleep):
        self.s, self.tokens, self.transport, self.sleep = settings, tokens, transport, sleep

    def _url(self, *parts: str) -> str:
        return "/".join([self.s.graph_host.rstrip("/"), self.s.api_version, *parts])

    async def _send(self, method: str, url: str, *, headers: dict[str, str], timeout: float, retry: bool, json: dict | None = None, params: dict | None = None,
                    content: bytes | None = None) -> dict:
        attempts = self.s.retry_attempts if retry else 1
        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=timeout) as http:
                    response = await http.request(method, url, headers=headers, json=json, params=params, content=content)
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if response.status_code < 400:
                    return body if isinstance(body, dict) else {}
                debug = (body or {}).get("debug_info") if isinstance(body, dict) else None
                error = classify(response.status_code, body, self.tokens.redact(f"HTTP {response.status_code}"))
                if isinstance(debug, dict):
                    error = MetaError("upload_failed", self.tokens.redact(str(debug.get("message") or "upload failed")), retriable=bool(debug.get("retriable")))
            except httpx.TimeoutException as exc:
                error = MetaError("timeout", f"The request timed out after {timeout:g} s", retriable=True)
                error.__cause__ = exc
            except httpx.HTTPError as exc:
                error = MetaError("network", self.tokens.redact(f"Network error: {type(exc).__name__}"), retriable=True)
            error = MetaError(error.kind, self.tokens.redact(str(error)), code=error.code, retriable=error.retriable)
            if not (retry and error.retriable) or attempt == attempts:
                raise error
            log.warning("meta call %s failed (%s), attempt %s of %s", method, error.kind, attempt, attempts)
            await self.sleep(self.s.retry_backoff_s * attempt)
        raise AssertionError("unreachable")

    def _bearer(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens.get()}", "Content-Type": "application/json"}

    async def account(self) -> dict:
        return await self._send("GET", self._url(self.s.account.ig_account_id), headers=self._bearer(), params={"fields": "id,username"}, timeout=self.s.request_timeout_s, retry=True)

    async def granted_permissions(self) -> set[str]:
        """The permissions the token has been granted (GET /me/permissions: read-only)."""
        body = await self._send("GET", self._url("me", "permissions"), headers=self._bearer(), timeout=self.s.request_timeout_s, retry=True)
        return {row["permission"] for row in body.get("data") or [] if row.get("status") == "granted"}

    async def publishing_limit(self) -> dict:
        return await self._send("GET", self._url(self.s.account.ig_account_id, "content_publishing_limit"), headers=self._bearer(), params={"fields": "quota_usage,config"},
                                timeout=self.s.request_timeout_s, retry=True)

    async def create_container(self, caption: str) -> str:
        body = await self._send("POST", self._url(self.s.account.ig_account_id, "media"), headers=self._bearer(), timeout=self.s.request_timeout_s, retry=False,
                                json={"media_type": "REELS", "upload_type": "resumable", "caption": caption, "thumb_offset": self.s.thumb_offset_ms})
        if not body.get("id"):
            raise MetaError("invalid_request", "Meta did not return a container id")
        return str(body["id"])

    async def upload(self, container_id: str, path: Path) -> None:
        data = path.read_bytes()
        headers = {"Authorization": f"OAuth {self.tokens.get()}", "offset": "0", "file_size": str(len(data))}
        url = f"{self.s.upload_host.rstrip('/')}/ig-api-upload/{self.s.api_version}/{container_id}"
        body = await self._send("POST", url, headers=headers, timeout=self.s.upload_timeout_s, retry=True, content=data)
        if body.get("success") is not True:
            raise MetaError("upload_failed", "Meta did not confirm the upload")

    async def container_status(self, container_id: str) -> str:
        body = await self._send("GET", self._url(container_id), headers=self._bearer(), params={"fields": "status_code"}, timeout=self.s.request_timeout_s, retry=True)
        return str(body.get("status_code") or "UNKNOWN")

    async def publish(self, container_id: str) -> str:
        try:
            body = await self._send("POST", self._url(self.s.account.ig_account_id, "media_publish"), headers=self._bearer(), timeout=self.s.request_timeout_s, retry=False,
                                    json={"creation_id": container_id})
        except MetaError as exc:
            if exc.kind in {"timeout", "network", "server"}:
                raise PublishOutcomeUnknown(exc.kind, str(exc), code=exc.code) from exc
            raise
        if not body.get("id"):
            raise PublishOutcomeUnknown("invalid_request", "media_publish returned no media id")
        return str(body["id"])

    async def media(self, media_id: str) -> dict:
        return await self._send("GET", self._url(media_id), headers=self._bearer(), params={"fields": "permalink,media_product_type"}, timeout=self.s.request_timeout_s, retry=True)
