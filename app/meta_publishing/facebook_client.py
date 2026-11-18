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

    async def update_description(self, video_id: str, description: str) -> None:
        """Changes the description of a video (or Reel) that is already on the Page (POST /{video}); the video itself is not touched."""
        body = await self._send("POST", self._url(video_id), headers=self._bearer_of(await self.page_token()), json={"description": description}, timeout=self.s.request_timeout_s, retry=False)
        if body.get("success") is not True:
            raise MetaError("invalid_request", "Meta did not confirm the new description.")

    async def comment(self, video_id: str, message: str) -> str:
        """Posts a comment as the Page on a video or Reel (POST /{video}/comments; pages_manage_engagement). Never retried, so a comment is never posted twice. Returns the comment id."""
        body = await self._send("POST", self._url(video_id, "comments"), headers=self._bearer_of(await self.page_token()), json={"message": message}, timeout=self.s.request_timeout_s, retry=False)
        if not body.get("id"):
            raise MetaError("invalid_request", "Meta did not return a comment id.")
        return str(body["id"])

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

    # -- films: Meta's chunked Page-video upload (start, transfer, finish), all with the Page token -----------------------
    @staticmethod
    def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]] | None = None) -> tuple[str, bytes]:
        """A multipart form: (content type with its boundary, body). Meta takes these fields in a form, not in the address."""
        boundary = uuid.uuid4().hex
        body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode() for name, value in fields.items())
        for name, (filename, data, mime) in (files or {}).items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode() + data + b"\r\n"
        return f"multipart/form-data; boundary={boundary}", body + f"--{boundary}--\r\n".encode()

    async def _film_post(self, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]] | None = None, *, timeout: float, retry: bool) -> dict:
        content_type, body = self._multipart(fields, files)
        headers = {**self._bearer_of(await self.page_token()), "Content-Type": content_type}
        url = f"{self.f.video_host.rstrip('/')}/{self.s.api_version}/{self.f.page.page_id}/videos"
        return await self._send("POST", url, headers=headers, content=body, timeout=timeout, retry=retry)

    async def film_start(self, size: int) -> dict:
        """Opens an upload for a file of `size` bytes: {video_id, upload_session_id, start_offset, end_offset} (the first chunk is start_offset..end_offset)."""
        body = await self._film_post({"upload_phase": "start", "file_size": str(size)}, timeout=self.s.request_timeout_s, retry=False)
        if not body.get("upload_session_id") or not body.get("video_id"):
            raise MetaError("invalid_request", "Meta did not return an upload session and a video id.")
        return {"video_id": str(body["video_id"]), "upload_session_id": str(body["upload_session_id"]), "start_offset": int(body["start_offset"]), "end_offset": int(body["end_offset"])}

    async def film_transfer(self, session_id: str, offset: int, chunk: bytes) -> tuple[int, int]:
        """One chunk at `offset`; Meta answers with where the next chunk starts and ends (equal when the file is complete)."""
        body = await self._film_post({"upload_phase": "transfer", "upload_session_id": session_id, "start_offset": str(offset)}, {"video_file_chunk": ("chunk", chunk, "application/octet-stream")},
                                     timeout=self.f.film.chunk_timeout_s, retry=True)
        if "start_offset" not in body or "end_offset" not in body:
            raise MetaError("upload_failed", "Meta did not say where the next chunk goes.")
        return int(body["start_offset"]), int(body["end_offset"])

    async def film_finish(self, session_id: str, *, title: str, description: str, draft: bool, public: bool, thumb: Path | None = None) -> None:
        """Turns the uploaded file into the Page video: a hidden DRAFT, or public. Never retried. With `thumb` the cover picture goes along as the video's thumbnail (the `thumb` field), which Meta
        then holds as the preferred one (measured 2026-11-18)."""
        fields = {"upload_phase": "finish", "upload_session_id": session_id, "title": title, "description": description}
        if draft:
            fields |= {"published": "false", "unpublished_content_type": "DRAFT"}
        elif public:
            fields |= {"published": "true"}
        else:
            raise MetaError("invalid_request", "A film is created either as a draft or public.")
        files = {"thumb": (thumb.name, thumb.read_bytes(), "image/jpeg")} if thumb is not None else None
        try:
            body = await self._film_post(fields, files, timeout=self.s.request_timeout_s, retry=False)
        except MetaError as exc:
            if exc.kind in ("timeout", "network", "server"):
                raise PublishOutcomeUnknown(exc.kind, str(exc), code=exc.code) from exc
            raise
        if body.get("success") is not True:
            raise MetaError("invalid_request", "Meta did not confirm the film.")
