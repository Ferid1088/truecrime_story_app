"""HeyGen v3: our ElevenLabs audio → the avatar speaking it.

  POST /v3/assets            upload the audio (or a studio image) → asset id
  POST /v3/videos            type=avatar, avatar_id=<look>, audio_asset_id,
                             output_format=webm (transparent; the look must be
                             trained with matting) or mp4 with a background
                             image (provider-composited fallback);
                             Idempotency-Key makes a retried POST safe
  GET  /v3/videos/{id}       status, video_url (expires), failure_message
  GET  /v3/avatars/looks/{id} / /v3/avatars/{id}   look or avatar group

The key is read from the env var named in config (never logged)."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from app.core.ai_config import ai_config
from app.providers.avatar.base import AvatarJob, AvatarProvider, AvatarProviderError

_KINDS = {400: "invalid_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
          409: "invalid_request", 422: "invalid_request", 429: "rate_limited"}


class HeyGenAvatarProvider(AvatarProvider):
    name = "heygen"

    def __init__(self, key_env: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = ai_config.avatar
        self.key_env = key_env or self.cfg.secret_env
        self.api_key = os.getenv(self.key_env) or ""
        self.transport = transport  # tests replace the network

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.cfg.request_timeout_s, transport=self.transport,
                                 base_url=self.cfg.base_url.rstrip("/"))

    async def _call(self, method: str, path: str, **kw) -> dict:
        if not self.is_configured():
            raise AvatarProviderError("missing_credentials", f"{self.key_env} is not set (.env).")
        headers = {"X-Api-Key": self.api_key, "Accept": "application/json",
                   **kw.pop("headers", {})}
        try:
            async with self._client() as c:
                r = await c.request(method, path, headers=headers, **kw)
        except httpx.TimeoutException as e:
            raise AvatarProviderError("timeout", f"HeyGen timeout: {e}") from e
        except httpx.HTTPError as e:
            raise AvatarProviderError("unreachable", f"HeyGen unreachable: {e}") from e
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:300]}
        if r.status_code >= 400:
            err = data.get("error") if isinstance(data, dict) else None
            msg = (err.get("message") if isinstance(err, dict) else err) or str(data)[:300]
            kind = _KINDS.get(r.status_code, "provider_error" if r.status_code >= 500
                              else "invalid_request")
            raise AvatarProviderError(kind, f"HeyGen {r.status_code}: {msg}")
        return data if isinstance(data, dict) else {"data": data}

    async def resolve_look(self, avatar_id: str) -> str:
        """A look id as is; an avatar (group) id → its first finished look."""
        try:
            d = await self._call("GET", f"/v3/avatars/looks/{avatar_id}")
            if (d.get("data") or {}).get("id"):
                return d["data"]["id"]
        except AvatarProviderError as e:
            if e.kind != "not_found":
                raise
        token = None
        for _ in range(20):
            params = {"limit": 50, **({"token": token} if token else {})}
            d = await self._call("GET", "/v3/avatars/looks", params=params)
            for look in d.get("data") or []:
                if look.get("group_id") == avatar_id and look.get("status") in (None, "completed"):
                    return look["id"]
            token = d.get("next_token")
            if not d.get("has_more") or not token:
                break
        raise AvatarProviderError("not_found", "no finished look for the configured avatar")

    async def upload_asset(self, path: Path, mime: str) -> str:
        d = await self._call("POST", "/v3/assets",
                             files={"file": (path.name, path.read_bytes(), mime)})
        data = d.get("data") or {}
        aid = data.get("asset_id") or data.get("id")
        if not aid:
            raise AvatarProviderError("invalid_output", "HeyGen upload returned no asset id")
        return aid

    async def create_video(self, look_id: str, audio_asset_id: str, idempotency_key: str,
                           output_format: str, resolution: str,
                           background_asset_id: str | None = None,
                           title: str | None = None) -> AvatarJob:
        body: dict = {"type": "avatar", "avatar_id": look_id, "audio_asset_id": audio_asset_id,
                      "output_format": output_format, "resolution": resolution,
                      "aspect_ratio": "16:9"}
        if title:
            body["title"] = title[:100]
        if background_asset_id:
            if output_format == "webm":
                raise AvatarProviderError("invalid_request",
                                          "webm (transparent) cannot take a background")
            body["background"] = {"type": "image", "asset_id": background_asset_id}
        d = await self._call("POST", "/v3/videos", json=body,
                             headers={"Idempotency-Key": idempotency_key})
        data = d.get("data") or {}
        if not data.get("video_id"):
            raise AvatarProviderError("invalid_output", "HeyGen returned no video_id")
        return AvatarJob(job_id=data["video_id"], status=data.get("status") or "waiting")

    async def get_video(self, job_id: str) -> AvatarJob:
        d = await self._call("GET", f"/v3/videos/{job_id}")
        data = d.get("data") or {}
        return AvatarJob(job_id=job_id, status=data.get("status") or "unknown",
                         video_url=data.get("video_url"),
                         failure_message=data.get("failure_message") or data.get("error"),
                         duration=data.get("duration"),
                         extra={k: data.get(k) for k in ("created_at", "completed_at",
                                                         "video_page_url") if data.get(k)})

    async def download(self, url: str, dest: Path) -> int:
        """Into dest via a .part file — a broken download never looks finished."""
        tmp = dest.with_name(dest.name + ".part")
        try:
            async with httpx.AsyncClient(timeout=self.cfg.request_timeout_s,
                                         transport=self.transport) as c:
                async with c.stream("GET", url) as r:
                    if r.status_code != 200:
                        raise AvatarProviderError("provider_error",
                                                  f"download failed: HTTP {r.status_code}")
                    with open(tmp, "wb") as f:
                        async for chunk in r.aiter_bytes():
                            f.write(chunk)
        except httpx.HTTPError as e:
            raise AvatarProviderError("unreachable", f"download failed: {e}") from e
        size = tmp.stat().st_size
        if size == 0:
            tmp.unlink(missing_ok=True)
            raise AvatarProviderError("invalid_output", "downloaded video is empty")
        os.replace(tmp, dest)
        return size
