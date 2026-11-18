"""The YouTube Data API calls the uploader needs, over the resumable upload protocol (developers.google.com/youtube/v3/guides/using_resumable_upload_protocol).

  start_session   POST /upload/youtube/v3/videos?uploadType=resumable   -> the session address (Location header)
  upload          PUT <session> with Content-Range chunks (a multiple of 256 KiB, except the last); 308 = more wanted, 200/201 = the video resource
  offset          PUT <session> with `Content-Range: bytes */total`      -> how many bytes Google has (or the finished resource)
  video           GET videos?part=status,processingDetails               -> upload and processing state
  set_thumbnail / insert_caption

Every video is created with an explicit privacyStatus; this phase only ever sends "private" and the client refuses anything else. 5xx answers and dropped connections are
retried with exponential backoff, a 404 on the session means it has expired (a new session starts from byte 0)."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable
from pathlib import Path

import httpx

from app.youtube_publishing.config import YouTubeConfig, youtube_config
from app.youtube_publishing.oauth import HttpFactory, YouTubeOAuth

CHUNK_STEP = 256 * 1024
RETRYABLE = {500, 502, 503, 504}
Progress = Callable[[int], None]


class YouTubeError(RuntimeError):
    """`reason`: quota, forbidden, session_expired, google_error, privacy_refused, unavailable."""

    def __init__(self, reason: str, message: str, status: int | None = None):
        super().__init__(message)
        self.reason, self.status = reason, status


class SessionExpired(YouTubeError):
    def __init__(self):
        super().__init__("session_expired", "Google no longer knows this upload session (it expired): the upload must start again from the beginning.", 404)


def _problem(response: httpx.Response) -> YouTubeError:
    try:
        error = (response.json().get("error") or {})
        reasons = [e.get("reason", "") for e in error.get("errors", [])]
        message = error.get("message", "")
    except ValueError:
        reasons, message = [], response.text[:200]
    if {"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"} & set(reasons):
        return YouTubeError("quota", f"YouTube's quota or upload limit is used up ({', '.join(reasons)}). Try again after it resets.", response.status_code)
    if response.status_code in (401, 403):
        return YouTubeError("forbidden", f"YouTube refused the request ({response.status_code} {', '.join(reasons) or message}).", response.status_code)
    return YouTubeError("google_error", f"YouTube answered {response.status_code} {', '.join(reasons) or message}".strip(), response.status_code)


class YouTubeClient:
    def __init__(self, oauth: YouTubeOAuth, cfg: YouTubeConfig | None = None, *, http: HttpFactory | None = None, sleep=asyncio.sleep):
        """One client per channel: every request carries the token of `oauth`'s channel and of no other."""
        self.cfg = cfg or youtube_config()
        self.http = http or (lambda: httpx.AsyncClient(timeout=self.cfg.api.upload_timeout_seconds))
        self.oauth = oauth
        self.sleep = sleep

    async def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        """One request with the backoff the protocol asks for. The Authorization header is built again on every attempt (a refresh may happen in between)."""
        extra = kwargs.pop("headers", None) or {}
        attempt = 0
        while True:
            headers = {**extra, "Authorization": f"Bearer {await self.oauth.access_token()}"}
            try:
                async with self.http() as http:
                    response = await http.request(method, url, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                if attempt >= self.cfg.upload.max_retries:
                    raise YouTubeError("unavailable", f"The connection to YouTube failed ({type(exc).__name__}).") from exc
            else:
                if response.status_code not in RETRYABLE or attempt >= self.cfg.upload.max_retries:
                    return response
            await self.sleep(min(self.cfg.upload.retry_max_seconds, self.cfg.upload.retry_base_seconds * 2 ** attempt))
            attempt += 1

    # -- the upload ----------------------------------------------------------------------------------
    @staticmethod
    def video_resource(*, title: str, description: str, tags: list[str], category_id: str, language: str, privacy: str, made_for_kids: bool, synthetic: bool) -> dict:
        return {"snippet": {"title": title, "description": description, "tags": tags, "categoryId": category_id, "defaultLanguage": language},
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids, "containsSyntheticMedia": synthetic}}

    async def start_session(self, resource: dict, size: int, mime: str = "video/mp4") -> str:
        if resource.get("status", {}).get("privacyStatus") != "private":
            raise YouTubeError("privacy_refused", "This phase only uploads private videos: the privacyStatus must be explicitly 'private'.")
        response = await self._send("POST", f"{self.cfg.api.upload_url}/videos", params={"uploadType": "resumable", "part": "snippet,status"},
                                    headers={"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": mime},
                                    content=json.dumps(resource, ensure_ascii=False).encode("utf-8"))
        if response.status_code != 200 or not response.headers.get("location"):
            raise _problem(response)
        return response.headers["location"]

    @staticmethod
    def _received(response: httpx.Response) -> int:
        header = response.headers.get("range", "")
        return int(header.rsplit("-", 1)[1]) + 1 if header.startswith("bytes=") else 0

    async def offset(self, session_uri: str, total: int) -> tuple[int, dict | None]:
        """(bytes Google has, the video resource when the upload had already finished)."""
        response = await self._send("PUT", session_uri, headers={"Content-Length": "0", "Content-Range": f"bytes */{total}"})
        if response.status_code in (200, 201):
            return total, response.json()
        if response.status_code == 404:
            raise SessionExpired
        if response.status_code == 308:
            return self._received(response), None
        raise _problem(response)

    async def upload(self, session_uri: str, path: Path, total: int, start: int, on_progress: Progress | None = None, mime: str = "video/mp4") -> dict:
        """Send the file from `start` in chunks; returns the video resource. A chunk that still fails after the retries leaves the offset to be asked again with `offset`."""
        chunk = max(CHUNK_STEP, self.cfg.upload.chunk_bytes // CHUNK_STEP * CHUNK_STEP)
        position = start
        with path.open("rb") as f:
            while position < total:
                f.seek(position)
                data = f.read(min(chunk, total - position))
                response = await self._send("PUT", session_uri, content=data, headers={"Content-Length": str(len(data)), "Content-Type": mime,
                                                                                         "Content-Range": f"bytes {position}-{position + len(data) - 1}/{total}"})
                if response.status_code in (200, 201):
                    if on_progress:
                        on_progress(total)
                    return response.json()
                if response.status_code == 404:
                    raise SessionExpired
                if response.status_code != 308:
                    raise _problem(response)
                position = self._received(response)
                if on_progress:
                    on_progress(position)
        raise YouTubeError("google_error", "All bytes were sent but YouTube did not answer with the video.")

    # -- after the upload ----------------------------------------------------------------------------
    async def video(self, video_id: str) -> dict:
        response = await self._send("GET", f"{self.cfg.api.base_url}/videos", params={"part": "status,processingDetails,snippet", "id": video_id})
        if response.status_code != 200:
            raise _problem(response)
        items = response.json().get("items") or []
        if not items:
            raise YouTubeError("google_error", f"YouTube has no video {video_id} for this channel.")
        return items[0]

    async def set_thumbnail(self, video_id: str, path: Path) -> None:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        response = await self._send("POST", f"{self.cfg.api.upload_url}/thumbnails/set", params={"videoId": video_id, "uploadType": "media"}, headers={"Content-Type": mime},
                                    content=path.read_bytes())
        if response.status_code != 200:
            raise _problem(response)

    async def set_category(self, video: dict, category_id: str) -> None:
        """videos.update of the snippet part with the video's own current title, description, tags and language and the new category: an update that leaves a field out would delete it."""
        snippet = video["snippet"]
        body = {"id": video["id"], "snippet": {key: snippet[key] for key in ("title", "description", "tags", "defaultLanguage") if key in snippet} | {"categoryId": category_id}}
        response = await self._send("PUT", f"{self.cfg.api.base_url}/videos", params={"part": "snippet"}, headers={"Content-Type": "application/json; charset=UTF-8"},
                                    content=json.dumps(body, ensure_ascii=False).encode("utf-8"))
        if response.status_code != 200:
            raise _problem(response)

    async def set_privacy(self, video_id: str, privacy: str, *, made_for_kids: bool, synthetic: bool) -> None:
        """videos.update of the status part (about 50 quota units); the video, its title and its description are not touched."""
        body = {"id": video_id, "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids, "containsSyntheticMedia": synthetic}}
        response = await self._send("PUT", f"{self.cfg.api.base_url}/videos", params={"part": "status"}, headers={"Content-Type": "application/json; charset=UTF-8"},
                                    content=json.dumps(body).encode("utf-8"))
        if response.status_code != 200:
            raise _problem(response)

    async def insert_caption(self, video_id: str, language: str, name: str, path: Path) -> None:
        boundary = secrets.token_hex(12)
        meta = json.dumps({"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}}, ensure_ascii=False)
        head = f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n"
        body = head.encode("utf-8") + path.read_bytes() + f"\r\n--{boundary}--".encode()
        response = await self._send("POST", f"{self.cfg.api.upload_url}/captions", params={"uploadType": "multipart", "part": "snippet"},
                                    headers={"Content-Type": f"multipart/related; boundary={boundary}"}, content=body)
        if response.status_code != 200:
            raise _problem(response)
