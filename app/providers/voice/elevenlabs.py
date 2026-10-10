"""ElevenLabs text-to-speech with character-level timestamps.

Uses POST /v1/text-to-speech/{voice_id}/with-timestamps, which returns
the audio plus the start/end time of every input character. Continuity
between blocks uses previous_text / next_text (the neighbouring
sentences), so separately rendered blocks sound like one performance.

Only the secret lives in .env (config: voice.elevenlabs.secret_env);
voice ids, model ids and settings live in config/ (connections.json, models.json, parameters/).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os

import httpx

from app.core.ai_config import ai_config
from app.core.concurrency import slot
from app.providers.voice.base import (
    VoiceProvider,
    VoiceProviderError,
    VoiceRenderResult,
    VoiceRequest,
)

_RETRYABLE = {"rate_limited", "provider_error", "timeout", "unreachable"}


class ElevenLabsVoiceProvider(VoiceProvider):
    name = "elevenlabs"

    def __init__(self):
        self.cfg = ai_config.voice.elevenlabs
        self.api_key = os.getenv(self.cfg.secret_env) or ""

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _body(self, req: VoiceRequest) -> dict:
        body: dict = {
            "text": req.text,
            "model_id": req.model_id,
            "voice_settings": dict(req.settings),
        }
        context_ok = req.model_id not in self.cfg.no_context_models
        if req.previous_text and context_ok:
            body["previous_text"] = req.previous_text
        if req.next_text and context_ok:
            body["next_text"] = req.next_text
        if req.seed is not None:
            body["seed"] = int(req.seed)
        if req.language_code and req.model_id in self.cfg.language_code_models:
            body["language_code"] = req.language_code
        return body

    async def _post(self, url: str, body: dict) -> tuple[int, dict, dict]:
        """One HTTP call. Returns (status, json, headers). Isolated so
        tests can replace the network."""
        async with httpx.AsyncClient(timeout=self.cfg.request_timeout_s) as client:
            r = await client.post(
                url,
                params={"output_format": self.cfg.output_format},
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=body,
            )
            try:
                data = r.json()
            except ValueError:
                data = {"raw": r.text[:300]}
            return r.status_code, data, dict(r.headers)

    @staticmethod
    def _error(status: int, data: dict) -> VoiceProviderError:
        detail = data.get("detail") if isinstance(data, dict) else None
        msg = (
            detail.get("message") if isinstance(detail, dict) else detail
        ) or str(data)[:300]
        kind = {
            401: "unauthorized", 403: "forbidden", 422: "invalid_request",
            400: "invalid_request", 429: "rate_limited",
        }.get(status, "provider_error" if status >= 500 else "invalid_request")
        return VoiceProviderError(kind, f"ElevenLabs {status}: {msg}")

    async def synthesize(self, req: VoiceRequest) -> VoiceRenderResult:
        if not self.is_configured():
            raise VoiceProviderError(
                "missing_credentials",
                f"{self.cfg.secret_env} is not set (.env).",
            )
        url = (
            f"{self.cfg.base_url.rstrip('/')}/v1/text-to-speech/"
            f"{req.voice_id}/with-timestamps"
        )
        body = self._body(req)
        last: VoiceProviderError | None = None
        # Too many parallel requests (429 concurrent_limit_exceeded) is a
        # queueing problem, not an error: wait and retry more often.
        attempts = self.cfg.max_retries + 1 + 4
        for attempt in range(attempts):
            try:
                async with slot("elevenlabs_tts"):
                    status, data, headers = await self._post(url, body)
            except httpx.TimeoutException as e:
                last = VoiceProviderError("timeout", f"ElevenLabs timeout: {e}")
            except httpx.HTTPError as e:
                last = VoiceProviderError("unreachable", f"ElevenLabs unreachable: {e}")
            else:
                if status == 200:
                    return self._parse(req, data, headers)
                last = self._error(status, data)
            busy = last.kind == "rate_limited" and "concurren" in str(last).lower()
            limit = attempts - 1 if busy else self.cfg.max_retries
            if last.kind not in _RETRYABLE or attempt >= limit:
                break
            await asyncio.sleep(min(2 ** attempt, 8) * (1.5 if busy else 1))
        raise last  # type: ignore[misc]

    def _parse(self, req: VoiceRequest, data: dict, headers: dict) -> VoiceRenderResult:
        try:
            audio = base64.b64decode(data["audio_base64"])
            al = data["alignment"]
            chars = list(al["characters"])
            starts = [float(x) for x in al["character_start_times_seconds"]]
            ends = [float(x) for x in al["character_end_times_seconds"]]
        except (KeyError, TypeError, ValueError) as e:
            raise VoiceProviderError(
                "invalid_output", f"ElevenLabs response missing audio/alignment: {e}"
            ) from e
        if not audio or not (len(chars) == len(starts) == len(ends)):
            raise VoiceProviderError("invalid_output", "Empty audio or broken alignment.")
        lower = {k.lower(): v for k, v in headers.items()}
        cost = lower.get("character-cost")
        return VoiceRenderResult(
            audio=audio,
            audio_format=self.cfg.output_format,
            characters=chars,
            char_starts=starts,
            char_ends=ends,
            provider=self.name,
            model_id=req.model_id,
            voice_id=req.voice_id,
            request_id=lower.get("request-id"),
            character_cost=int(cost) if cost and str(cost).isdigit() else None,
        )


class ElevenLabsSoundProvider:
    """Short music/atmosphere cues from text (POST /v1/sound-generation).

    Used for the documentary's music beds, bridges, stings and room tone
    (up to ~22 s each; beds as seamless loops). Same key and settings as
    the voice provider; needs the key's sound-generation permission."""

    name = "elevenlabs_sound"

    def __init__(self):
        self.cfg = ai_config.voice.elevenlabs
        self.api_key = os.getenv(self.cfg.secret_env) or ""

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def _post(self, url: str, body: dict) -> tuple[int, bytes, dict]:
        async with httpx.AsyncClient(timeout=self.cfg.request_timeout_s) as client:
            r = await client.post(
                url, params={"output_format": self.cfg.output_format},
                headers={"xi-api-key": self.api_key,
                         "Content-Type": "application/json"},
                json=body,
            )
            return r.status_code, r.content, dict(r.headers)

    async def generate(self, prompt: str, seconds: float, loop: bool,
                       prompt_influence: float) -> tuple[bytes, int | None]:
        if not self.is_configured():
            raise VoiceProviderError(
                "missing_credentials", f"{self.cfg.secret_env} is not set (.env).")
        body = {"text": prompt, "duration_seconds": float(seconds),
                "prompt_influence": float(prompt_influence)}
        if loop:
            body["loop"] = True
            body["model_id"] = "eleven_text_to_sound_v2"
        url = f"{self.cfg.base_url.rstrip('/')}/v1/sound-generation"
        last: VoiceProviderError | None = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                async with slot("elevenlabs_sound"):
                    status, content, headers = await self._post(url, body)
            except httpx.TimeoutException as e:
                last = VoiceProviderError("timeout", f"ElevenLabs timeout: {e}")
            except httpx.HTTPError as e:
                last = VoiceProviderError("unreachable", f"ElevenLabs unreachable: {e}")
            else:
                if status == 200 and content:
                    cost = {k.lower(): v for k, v in headers.items()}.get("character-cost")
                    return content, int(cost) if cost and str(cost).isdigit() else None
                try:
                    data = json.loads(content.decode("utf-8", "replace"))
                except ValueError:
                    data = {"raw": content[:200].decode("utf-8", "replace")}
                last = ElevenLabsVoiceProvider._error(status, data)
            if last.kind not in _RETRYABLE or attempt == self.cfg.max_retries:
                break
            await asyncio.sleep(min(2 ** attempt, 8))
        raise last  # type: ignore[misc]
