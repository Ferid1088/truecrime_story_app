"""Eleven Music: POST /v1/music, returns the generated audio file directly (no JSON body with a
cost or duration figure — the provider's docs do not report usage per call). The caller supplies
``music_length_ms``; the real duration is measured from the returned audio with ffprobe, never
assumed from the request.

Only the secret lives in .env (``TRUECRIME_ELEVENLABS_API_KEY``); the endpoint, model id and
licence-gate flag live in config (``shorts_host_led.music_provider``)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.ai_config import ai_config
from app.core.env import env_value
from app.core.provider_guard import guard_real_provider


class MusicProviderError(RuntimeError):
    pass


class MusicLicenceNotCleared(RuntimeError):
    """``shorts_host_led.music_provider.licence_cleared`` is false: no generation call may be
    made until an explicit owner decision sets it true."""


@dataclass(frozen=True)
class MusicGenerationResult:
    audio_bytes: bytes
    provider_generation_id: str | None
    request_id: str | None


class ElevenLabsMusicProvider:
    name = "elevenlabs_music"

    def __init__(self):
        guard_real_provider(self.name)
        self.cfg = ai_config.shorts_host_led.music_provider
        self.api_key = env_value("TRUECRIME_ELEVENLABS_API_KEY") or ""

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, *, length_ms: int, seed: int | None = None) -> MusicGenerationResult:
        if not self.cfg.licence_cleared:
            raise MusicLicenceNotCleared(
                "shorts_host_led.music_provider.licence_cleared is false; no music may be "
                "generated until the owner clears the licence in config")
        if not self.is_configured():
            raise MusicProviderError("TRUECRIME_ELEVENLABS_API_KEY is not set")
        body: dict = {
            "prompt": prompt,
            "music_length_ms": int(length_ms),
            "model_id": self.cfg.model_id,
            "force_instrumental": True,
        }
        if seed is not None:
            body["seed"] = int(seed)
        url = f"{self.cfg.api_base}{self.cfg.endpoint}"
        resp = httpx.post(url, json=body, headers={"xi-api-key": self.api_key}, timeout=180.0)
        if resp.status_code != 200:
            raise MusicProviderError(f"Eleven Music {resp.status_code}: {resp.text[:500]}")
        return MusicGenerationResult(
            audio_bytes=resp.content,
            provider_generation_id=resp.headers.get("request-id") or resp.headers.get("x-request-id"),
            request_id=resp.headers.get("request-id"),
        )
