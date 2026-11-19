"""The gateway's image endpoint (POST /v1/images/generations).

What the real response of `gpt-image-2.6-flare` holds (checked 2026-11-11): `data[0].url` (the finished PNG at the gateway), `width`, `height`, `revised_prompt`
and `usage` with `input_tokens`, `output_tokens` and their details (`text_tokens`, `image_tokens`). There is no price in it. A call that fails comes back as 200 without
`data`, so a missing `data` is an error, not an empty answer."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from app.core.ai_config import ai_config
from app.core.concurrency import report_throttle, retry_after_seconds, slot
from app.core.env import env_value
from app.core.provider_guard import guard_real_provider


class ImageProviderError(RuntimeError):
    pass


@dataclass
class ImageResult:
    ok: bool
    content: bytes = b""
    usage: dict = field(default_factory=dict)
    request_id: str | None = None
    duration_ms: int = 0
    error: str | None = None
    revised_prompt: str | None = None
    rate_limited: bool = False            # the gateway asked for fewer requests: the limit of "image" has been pulled down
    retry_after_s: float | None = None    # what its Retry-After header asked for
    uncertain: bool = False               # the request was sent and got no answer: it may have been billed, and the gateway documents no idempotency key


class APIMasterImageProvider:
    path = "/images/generations"

    def __init__(self):
        guard_real_provider("apimaster")
        cfg = ai_config.generation_provider()
        self.base_url = cfg.base_url.rstrip("/")
        self.secret_env = cfg.secret_env
        self.api_key = env_value(cfg.secret_env)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def generate(self, model: str, prompt: str, size: str, timeout_s: float) -> ImageResult:
        """One image, waiting for a free "image" slot first (at most `concurrency.image` requests at a time; the others queue). Never raises for a refused or empty answer:
        the result says what went wrong, so the attempt can be recorded."""
        if not self.api_key:
            raise ImageProviderError(f"{self.secret_env} is not configured")
        async with slot("image"):
            return await self._generate(model, prompt, size, timeout_s)

    async def _generate(self, model: str, prompt: str, size: str, timeout_s: float) -> ImageResult:
        started = time.monotonic()

        def elapsed() -> int:
            return round((time.monotonic() - started) * 1000)

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            try:
                response = await client.post(f"{self.base_url}{self.path}", headers={"Authorization": f"Bearer {self.api_key}"},
                                             json={"model": model, "prompt": prompt, "n": 1, "size": size})
                body = response.json()
            except (httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError) as e:
                return ImageResult(ok=False, error=f"{type(e).__name__}: {e} (the request was sent and not answered: it may have been billed)"[:300], duration_ms=elapsed(), uncertain=True)
            except (httpx.HTTPError, ValueError) as e:
                return ImageResult(ok=False, error=f"{type(e).__name__}: {e}"[:300], duration_ms=elapsed())
            data = body.get("data") if isinstance(body, dict) else None
            if response.status_code != 200 or not data or not data[0].get("url"):
                overloaded = response.status_code == 429 or (response.status_code == 503 and "cpu overloaded" in str(body).lower())
                if overloaded:
                    report_throttle("image")
                return ImageResult(ok=False, error=f"{response.status_code}: {str(body)[:250]}", duration_ms=elapsed(), rate_limited=overloaded,
                                   retry_after_s=retry_after_seconds(response.headers.get("retry-after")) if overloaded else None)
            try:
                picture = await client.get(data[0]["url"])
                picture.raise_for_status()
            except httpx.HTTPError as e:
                return ImageResult(ok=False, error=f"the finished image could not be fetched: {e}"[:300], usage=body.get("usage") or {}, duration_ms=elapsed())
        return ImageResult(ok=True, content=picture.content, usage=body.get("usage") or {}, revised_prompt=data[0].get("revised_prompt"),
                           request_id=str(body.get("created")) if body.get("created") else None, duration_ms=elapsed())
