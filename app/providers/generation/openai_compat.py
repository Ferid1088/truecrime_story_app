"""Shared OpenAI-compatible chat-completions generation provider.

OpenAI-compatible generation providers speak the same wire format; the
concrete subclasses only differ in name, credential env var and base URL
(all from central config). Error kinds are normalized to the
application-level taxonomy so agents and fallback logic never see raw
HTTP details.
"""

import json
import logging
from typing import Any

import httpx

from app.core.ai_config import ai_config
from app.core.concurrency import slot
from app.core.env import env_value
from app.providers.generation.base import (
    GenerationError,
    GenerationProvider,
    GenerationResult,
)

log = logging.getLogger(__name__)


def _sum_optional(a: float | None, b: float | None) -> float | None:
    """Sum two provider-reported usage values, None meaning "not reported"
    rather than zero; None only when neither side reported anything."""
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)

# Failure kinds where falling back to a configured fallback model is
# legitimate. Credential failures (unauthorized/forbidden) and invalid
# model output never trigger fallback — a bad key is not a bad model,
# and quality failures must stay visible.
_FALLABLE_KINDS = {
    "model_unavailable", "rate_limited", "provider_error", "timeout"
}


class OpenAICompatibleGenerationProvider(GenerationProvider):
    """Chat-completions client for any OpenAI-compatible API."""

    name = "openai-compatible"
    # Human-readable provider label used in error messages.
    label = "Provider"
    # Extra per-request body fields a gateway requires (none by default).
    extra_body: dict[str, Any] = {}
    # Endpoint paths (OpenAI-compatible defaults; subclasses may override).
    models_path = "/models"
    chat_path = "/chat/completions"

    def __init__(self):
        from app.core.provider_guard import guard_real_provider
        guard_real_provider(self.name)
        provider_cfg = ai_config.generation_provider()
        self.base_url = provider_cfg.base_url.rstrip("/")
        self.api_key = env_value(provider_cfg.secret_env)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise GenerationError(
                "missing_key",
                f"{ai_config.generation_provider().secret_env} is not configured.",
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _map_http_error(self, e: httpx.HTTPStatusError) -> GenerationError:
        code = e.response.status_code
        if code == 401:
            return GenerationError(
                "unauthorized", f"{self.label} API key rejected (401)."
            )
        if code in (402, 403):
            return GenerationError(
                "forbidden", f"{self.label} permission/quota failure ({code})."
            )
        if code == 404:
            return GenerationError(
                "model_unavailable", f"{self.label} model unavailable (404)."
            )
        if code == 429:
            return GenerationError(
                "rate_limited", f"{self.label} rate limit exceeded (429)."
            )
        return GenerationError(
            "provider_error", f"{self.label} error (HTTP {code}){self._provider_message(e)}."
        )

    @staticmethod
    def _provider_message(e: httpx.HTTPStatusError) -> str:
        """The gateway's own explanation (JSON error.message), short; a body that
        is not JSON carries nothing worth showing."""
        try:
            message = (e.response.json().get("error") or {}).get("message")
        except (ValueError, AttributeError):
            return ""
        return f": {message[:300]}" if isinstance(message, str) and message else ""

    async def check_health(self) -> bool:
        """Minimal safe request: GET /models (no key value exposed)."""
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    f"{self.base_url}{self.models_path}",
                    headers=self._headers(),
                )
            return r.status_code == 200
        except (httpx.HTTPError, GenerationError):
            return False

    async def _models_available(self) -> tuple[set[str], set[str]] | None:
        """(available, missing) among configured model IDs, via GET /models.
        None when the listing itself fails."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    f"{self.base_url}{self.models_path}",
                    headers=self._headers(),
                )
            if r.status_code != 200:
                return None
            data = r.json()
            raw = data.get("data", data if isinstance(data, list) else [])
            listed = {m.get("id") for m in raw if isinstance(m, dict)}
            configured = set(ai_config.generation_provider().models.values())
            return configured & listed, configured - listed
        except (httpx.HTTPError, ValueError, GenerationError):
            return None

    async def provider_status(self) -> dict:
        """Classified provider health: configured / reachable / authorized
        are separate facts; model availability is verified against
        GET /models (Part 7-8). Never exposes the key."""
        base = {
            "provider": self.name,
            "role": "generation",
            "configured": False,
            "reachable": False,
            "authorized": None,
            "models_available": [],
            "models_missing": [],
            "status": "not_configured",
        }
        if not self.api_key:
            return {**base, "status": "missing_key"}
        base["configured"] = True
        availability = await self._models_available()
        if availability is None:
            # Distinguish unreachable from unauthorized.
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.get(
                        f"{self.base_url}{self.models_path}",
                        headers=self._headers(),
                    )
                if r.status_code == 401:
                    return {**base, "reachable": True, "authorized": False,
                            "status": "unauthorized"}
                if r.status_code == 403:
                    return {**base, "reachable": True, "authorized": False,
                            "status": "forbidden"}
                return {**base, "reachable": True, "status": "provider_error",
                        "detail": f"models HTTP {r.status_code}"}
            except (httpx.HTTPError, GenerationError):
                return {**base, "status": "unreachable"}
        available, missing = availability
        base["reachable"] = True
        base["authorized"] = True
        base["models_available"] = sorted(available)
        base["models_missing"] = sorted(missing)
        # Missing configured models is a defect — surfaced, not
        # silently substituted (Part 7).
        base["status"] = "models_missing" if missing else "ok"
        return base

    async def _chat(self, model: str, system: str, user: str, gen,
                    images: list[str] | None = None) -> str:
        content: Any = user
        if images:
            # OpenAI-compatible multimodal message: text + image parts
            # (data: URLs or https URLs).
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": url}} for url in images
            ]
        body: dict[str, Any] = {
            "model": model,
            "temperature": gen.temperature,
            "max_tokens": gen.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            **self.extra_body,
        }
        if gen.seed is not None:
            body["seed"] = gen.seed
        try:
            cfg = ai_config.generation_provider()
            # The request below does not set "stream": true, so this is NOT
            # a true per-token inactivity timeout — the provider can legally
            # hold the connection open with no bytes at all until the whole
            # completion is ready, then deliver it in one write. httpx's
            # `read` timeout only resets on bytes actually received, so for
            # a non-streaming call it is effectively a hard ceiling on total
            # generation time. Measured master writer/rewriter calls take
            # 330-400s end to end (see tests/test_pilot_master_regressions.py),
            # so this must stay comfortably above that — not a short "idle"
            # window — until real token streaming is implemented here.
            timeout = httpx.Timeout(
                connect=30.0,
                read=cfg.inactivity_timeout_s,
                write=30.0,
                pool=30.0,
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"{self.base_url}{self.chat_path}",
                    headers=self._headers(),
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as e:
            raise self._map_http_error(e) from e
        except httpx.TimeoutException as e:
            raise GenerationError(
                "timeout", f"{self.label} request timed out."
            ) from e
        except httpx.HTTPError as e:
            raise GenerationError(
                "unreachable", f"Cannot reach {self.label}: {type(e).__name__}"
            ) from e
        # Extracted before the validity checks below: a response that gets
        # rejected for an empty/truncated completion was still a real,
        # billed call — its usage must be attached to any GenerationError
        # raised from here, or the cost is lost rather than booked.
        usage = data.get("usage") or {}
        billed = {
            "provider": self.name,
            "model": model,
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cost_usd": usage.get("cost"),
        }
        choice = (data.get("choices") or [{}])[0]
        content = choice.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise GenerationError(
                "invalid_output", f"{self.label} returned an empty completion.",
                **billed,
            )
        if choice.get("finish_reason") in ("length", "max_tokens"):
            raise GenerationError(
                "invalid_output",
                f"{self.label} completion truncated at max_tokens for "
                f"model {model}.",
                **billed,
            )
        meta = {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cost_usd": usage.get("cost"),
            "generation_id": data.get("id"),
        }
        reported_model = data.get("model")
        if reported_model and reported_model != model:
            # The resolved model we requested is the one config/parameters/
            # pricing.json has a price for; trust it for billing even when
            # the gateway echoes a different string back (e.g. a dated or
            # internally-aliased model id), and say so loudly rather than
            # silently mis-pricing the call.
            log.warning(
                "%s gateway echoed model=%s for requested model=%s; "
                "billing against the requested model.",
                self.label, reported_model, model,
            )
        return content, model, meta

    async def generate_text(
        self, role: str, system: str, user: str, images: list[str] | None = None
    ) -> GenerationResult:
        gen = ai_config.generation_for(role)
        attempted = ai_config.model_for(role)
        candidates = [attempted] + ai_config.fallback_models_for(role)
        last_error: GenerationError | None = None
        for model in candidates:
            # Provider hiccups (empty body, truncation, transport errors)
            # deserve one retry on the same model before giving up or
            # falling back — they are not model-quality failures.
            for _attempt in range(2):
                try:
                    # Process-wide limit on parallel model calls (agents
                    # run in parallel inside and across documentaries).
                    async with slot("vision" if images else "llm"):
                        if images:
                            text, used, meta = await self._chat(
                                model, system, user, gen, images)
                        else:
                            text, used, meta = await self._chat(model, system, user, gen)
                    fallback_used = model != attempted
                    if fallback_used:
                        log.warning(
                            "%s model fallback for role=%s: %s -> %s "
                            "(reason: %s)",
                            self.label, role, attempted, model,
                            last_error.kind if last_error else "unknown",
                        )
                    return GenerationResult(
                        text=text,
                        model=used,
                        provider=self.name,
                        fallback_used=fallback_used,
                        **meta,
                    )
                except GenerationError as e:
                    last_error = e
                    if e.kind not in _FALLABLE_KINDS | {
                        "invalid_output", "unreachable"
                    }:
                        raise
                    if e.kind in _FALLABLE_KINDS:
                        break  # straight to the fallback candidate
        raise last_error or GenerationError(
            "provider_error",
            f"All configured models failed for role {role}.",
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Parse JSON from model output, tolerating markdown fences and
        preamble/suffix prose around the object."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise json.JSONDecodeError("no JSON object found", cleaned, 0)

    async def generate_structured(
        self, role: str, system: str, user: str, images: list[str] | None = None
    ) -> tuple[dict[str, Any], GenerationResult]:
        first = await self.generate_text(role, system, user, images)
        try:
            return self._extract_json(first.text), first
        except json.JSONDecodeError:
            pass
        # One strict-JSON repair attempt (Part 16) — non-JSON output is
        # a transient hiccup, not a stage failure; malformed output is
        # never silently accepted.
        log.warning(
            "%s non-JSON output for role=%s; retrying with strict JSON",
            self.label, role,
        )
        strict = (
            system
            + "\n\nCRITICAL: return ONLY one valid JSON object — no prose, "
              "no markdown fences, no trailing text."
        )
        repaired = await self.generate_text(role, strict, user, images)
        try:
            return self._extract_json(repaired.text), repaired
        except json.JSONDecodeError as e:
            log.error(
                "%s invalid JSON output for role=%s model=%s: %.200s",
                self.label, role, repaired.model, repaired.text,
            )
            # Both the first and the repair call were real, billed
            # generations (only their JSON parsing failed) — book the sum
            # of both rather than losing either.
            raise GenerationError(
                "invalid_output",
                f"Model {repaired.model} returned non-JSON output for "
                f"role {role} after one repair attempt.",
                provider=repaired.provider, model=repaired.model,
                input_tokens=_sum_optional(first.input_tokens, repaired.input_tokens),
                output_tokens=_sum_optional(first.output_tokens, repaired.output_tokens),
                total_tokens=_sum_optional(first.total_tokens, repaired.total_tokens),
                cost_usd=_sum_optional(first.cost_usd, repaired.cost_usd),
            ) from e
