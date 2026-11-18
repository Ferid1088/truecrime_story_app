"""The Graph API client of the Farsi Facebook Page: connection reads, Reels (start, binary upload, status, finish) and long films (Resumable Upload API, then the Page video).

Endpoints, from Meta's own pages (read 2026-11-17):
  Reels   POST {graph}/{page}/video_reels            upload_phase=start                       -> {video_id, upload_url}
          POST {upload_url}                           headers Authorization: OAuth, offset, file_size, body = the file -> {"success": true}
          GET  {graph}/{video}?fields=status          video_status uploading | upload_complete | processing | ready | error | expired ...
          POST {graph}/{page}/video_reels            video_id, upload_phase=finish, video_state=DRAFT | SCHEDULED | PUBLISHED
  Film    POST {graph}/{app}/uploads                  file_name, file_length, file_type        -> {id: "upload:<session>"}
          POST {graph}/upload:<session>               headers Authorization: OAuth, file_offset, body = one chunk -> {h: <handle>} when the file is complete
          GET  {graph}/upload:<session>               -> {file_offset}  (where to resume)
          POST {video_host}/{page}/videos             fbuploader_video_file_chunk=<handle>, title, description, published, unpublished_content_type
The Reels calls and the Page videos call need a Page access token (from the system-user token: GET {page}?fields=access_token); the connection reads use the system-user token.
Reading calls and the byte uploads are retried (bounded); `finish` and the film's `create` are never retried: a lost answer is `PublishOutcomeUnknown`. No token ever leaves this module:
every message passes through the redaction of both tokens."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx

from app.meta_publishing.config import FacebookSettings, InstagramSettings
from app.meta_publishing.graph import MetaError, MetaGraphClient, PublishOutcomeUnknown
from app.meta_publishing.token import TokenStore


class _Redacting:
    """The token store of the Graph client, extended: the Page token (which is fetched, never stored) is redacted from every message as well."""

    def __init__(self, tokens: TokenStore):
        self.tokens, self.extra = tokens, set()

    def get(self) -> str:
        return self.tokens.get()

    def redact(self, text: str) -> str:
        text = self.tokens.redact(text)
        for secret in self.extra:
            text = text.replace(secret, "[token]")
        return text


class FacebookClient(MetaGraphClient):
    def __init__(self, settings: InstagramSettings, facebook: FacebookSettings, tokens: TokenStore, *, transport: httpx.AsyncBaseTransport | None = None, sleep=None):
        import asyncio

        self.redacting = _Redacting(tokens)
        super().__init__(settings, self.redacting, transport=transport, sleep=sleep or asyncio.sleep)       # type: ignore[arg-type]
        self.f = facebook
        self._page_token: str | None = None

    @staticmethod
    def _oauth(token: str, **extra: str) -> dict[str, str]:
        return {"Authorization": f"OAuth {token}", **extra}

    @staticmethod
    def _bearer_of(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # -- connection reads (system-user token) ----------------------------------------------------------
    async def page(self) -> dict:
        return await self._send("GET", self._url(self.f.page.page_id), headers=self._bearer(), params={"fields": "id,name,is_published,instagram_business_account{id,username}"},
                                timeout=self.s.request_timeout_s, retry=True)

    async def managed_page(self) -> dict | None:
        """The Page as the token's own list shows it, with the tasks the token may do on it (GET /me/accounts), None when the token does not list it."""
        body = await self._send("GET", self._url("me", "accounts"), headers=self._bearer(), params={"fields": "id,name,tasks", "limit": "100"}, timeout=self.s.request_timeout_s, retry=True)
        return next((row for row in body.get("data") or [] if str(row.get("id")) == self.f.page.page_id), None)

    async def page_token(self) -> str:
        """The Page access token for the configured Page. Kept in memory for this client only; never stored, never returned."""
        if self._page_token is None:
            body = await self._send("GET", self._url(self.f.page.page_id), headers=self._bearer(), params={"fields": "access_token"}, timeout=self.s.request_timeout_s, retry=True)
            token = body.get("access_token")
            if not token:
                raise MetaError("permission_missing", "Meta gave no Page access token for this Page: the token needs a role with the CREATE_CONTENT task on it.")
            self._page_token = str(token)
            self.redacting.extra.add(self._page_token)
        return self._page_token

    async def page_token_identity(self) -> dict:
        return await self._send("GET", self._url("me"), headers=self._bearer_of(await self.page_token()), params={"fields": "id,name"}, timeout=self.s.request_timeout_s, retry=True)

    async def upload_limits(self) -> dict:
        """Meta's own limit for a video upload by this token: {"length": seconds, "size": bytes}."""
        body = await self._send("GET", self._url("me"), headers=self._bearer(), params={"fields": "video_upload_limits"}, timeout=self.s.request_timeout_s, retry=True)
        limits = body.get("video_upload_limits")
        if not isinstance(limits, dict) or not limits.get("size") or not limits.get("length"):
            raise MetaError("invalid_request", "Meta did not report the video upload limits.")
        return {"length": int(limits["length"]), "size": int(limits["size"])}

    # -- Reels (Page token) -------------------------------------------------------------------------
    async def reel_start(self) -> tuple[str, str]:
        token = await self.page_token()
        body = await self._send("POST", self._url(self.f.page.page_id, "video_reels"), headers=self._bearer_of(token), json={"upload_phase": "start"}, timeout=self.s.request_timeout_s, retry=False)
        if not body.get("video_id") or not body.get("upload_url"):
            raise MetaError("invalid_request", "Meta did not return a video id and an upload address.")
        return str(body["video_id"]), str(body["upload_url"])

    async def reel_upload(self, upload_url: str, path: Path, offset: int = 0) -> None:
        data = path.read_bytes()
        headers = self._oauth(await self.page_token(), offset=str(offset), file_size=str(len(data)))
        body = await self._send("POST", upload_url, headers=headers, timeout=self.f.reels.upload_timeout_s, retry=True, content=data[offset:])
        if body.get("success") is not True:
            raise MetaError("upload_failed", "Meta did not confirm the upload")

    async def video_status(self, video_id: str) -> dict:
        """The video's processing state as Meta reports it: {video_status, uploading_phase, processing_phase, publishing_phase}."""
        body = await self._send("GET", self._url(video_id), headers=self._bearer_of(await self.page_token()), params={"fields": "status"}, timeout=self.s.request_timeout_s, retry=True)
        return body.get("status") or {}

    async def reel_finish(self, video_id: str, *, state: str, description: str, title: str) -> None:
        token = await self.page_token()
        payload = {"video_id": video_id, "upload_phase": "finish", "video_state": state, "description": description}
        if title:
            payload["title"] = title
        try:
            body = await self._send("POST", self._url(self.f.page.page_id, "video_reels"), headers=self._bearer_of(token), json=payload, timeout=self.s.request_timeout_s, retry=False)
        except MetaError as exc:
            if exc.kind in ("timeout", "network", "server"):
                raise PublishOutcomeUnknown(exc.kind, str(exc), code=exc.code) from exc
            raise
        if body.get("success") is not True:
            raise MetaError("invalid_request", "Meta did not confirm the finish step.")

    async def permalink(self, video_id: str) -> str | None:
        body = await self._send("GET", self._url(video_id), headers=self._bearer_of(await self.page_token()), params={"fields": "permalink_url"}, timeout=self.s.request_timeout_s, retry=True)
        link = body.get("permalink_url")
        return f"https://www.facebook.com{link}" if link and str(link).startswith("/") else link

    # -- the thumbnails of a Reel (Page token, read only) -----------------------------------------------------------
    async def thumbnails(self, video_id: str) -> list[dict]:
        """The thumbnails Meta holds for a video: [{id, uri, width, height, is_preferred}] (a read: pages_read_engagement). `uri` is a picture address on Meta's own server."""
        body = await self._send("GET", self._url(video_id, "thumbnails"), headers=self._bearer_of(await self.page_token()), params={"fields": "id,uri,width,height,is_preferred", "limit": "100"},
                                timeout=self.s.request_timeout_s, retry=True)
        return [{"id": str(t["id"]), "uri": t.get("uri"), "width": t.get("width"), "height": t.get("height"), "is_preferred": bool(t.get("is_preferred"))} for t in body.get("data") or [] if t.get("id")]

    async def set_thumbnail(self, video_id: str, picture: Path) -> None:
        """Posts `picture` as the preferred thumbnail (the cover) of an uploaded video (POST /{video}/thumbnails; pages_read_user_content and pages_manage_engagement). A thumbnail posted this way
        cannot be changed or deleted through the API, so the caller shows it to the owner before it is sent."""
        data = picture.read_bytes()
        if len(data) > self.f.cover.max_mb * 1024 * 1024:
            raise MetaError("invalid_request", f"The cover is {len(data) // 1024} KB; Meta takes at most {self.f.cover.max_mb} MB.")
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="is_preferred"\r\n\r\ntrue\r\n'.encode(),
                 f'--{boundary}\r\nContent-Disposition: form-data; name="source"; filename="{picture.name}"\r\nContent-Type: image/jpeg\r\n\r\n'.encode(), data, f"\r\n--{boundary}--\r\n".encode()]
        headers = {**self._bearer_of(await self.page_token()), "Content-Type": f"multipart/form-data; boundary={boundary}"}
        body = await self._send("POST", self._url(video_id, "thumbnails"), headers=headers, content=b"".join(parts), timeout=self.s.request_timeout_s, retry=False)
        if body.get("success") is not True:
            raise MetaError("invalid_request", "Meta did not confirm the cover.")

    # -- films (system-user token for the upload, Page token for the video) ---------------------------------
    async def film_session(self, file_name: str, size: int) -> str:
        body = await self._send("POST", self._url(self.f.app_id, "uploads"), headers=self._bearer(), params={"file_name": file_name, "file_length": str(size), "file_type": "video/mp4"},
                                timeout=self.s.request_timeout_s, retry=False)
        if not body.get("id"):
            raise MetaError("invalid_request", "Meta did not return an upload session id.")
        return str(body["id"])

    async def film_offset(self, session_id: str) -> int:
        body = await self._send("GET", self._url(session_id), headers=self._oauth(self.tokens.get()), timeout=self.s.request_timeout_s, retry=True)
        return int(body.get("file_offset", 0))

    async def film_chunk(self, session_id: str, offset: int, chunk: bytes) -> str | None:
        """One chunk at `offset`. Returns the file handle when Meta answers with it (the file is complete), else None."""
        body = await self._send("POST", self._url(session_id), headers=self._oauth(self.tokens.get(), file_offset=str(offset)), timeout=self.f.film.chunk_timeout_s, retry=True, content=chunk)
        return str(body["h"]) if body.get("h") else None

    async def film_create(self, handle: str, *, title: str, description: str, draft: bool, public: bool) -> str:
        """The Page video from an uploaded file: a hidden DRAFT, or public. Never retried."""
        params = {"title": title, "description": description, "fbuploader_video_file_chunk": handle}
        if draft:
            params |= {"published": "false", "unpublished_content_type": "DRAFT"}
        elif public:
            params |= {"published": "true"}
        else:
            raise MetaError("invalid_request", "A film is created either as a draft or public.")
        url = f"{self.f.video_host.rstrip('/')}/{self.s.api_version}/{self.f.page.page_id}/videos"
        try:
            body = await self._send("POST", url, headers=self._bearer_of(await self.page_token()), params=params, timeout=self.s.request_timeout_s, retry=False)
        except MetaError as exc:
            if exc.kind in ("timeout", "network", "server"):
                raise PublishOutcomeUnknown(exc.kind, str(exc), code=exc.code) from exc
            raise
        if not body.get("id"):
            raise MetaError("invalid_request", "Meta did not return a video id.")
        return str(body["id"])
