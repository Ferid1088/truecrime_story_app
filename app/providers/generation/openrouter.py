"""OpenRouter generation provider — retained as a valid implementation.

Since the provider split, OpenRouter's active role is RESEARCH ONLY;
this class remains so that `provider="openrouter"` historical rows stay
renderable and the provider can be re-selected by config. It is not
instantiated while providers.generation == "apimaster".
"""

import logging

import httpx

from app.core.ai_config import ai_config
from app.providers.generation.openai_compat import (
    OpenAICompatibleGenerationProvider,
)

log = logging.getLogger(__name__)


class OpenRouterGenerationProvider(OpenAICompatibleGenerationProvider):
    name = "openrouter"
    label = "OpenRouter"

    async def check_health(self) -> bool:
        """OpenRouter key metadata endpoint (no key value returned)."""
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    f"{self.base_url}/auth/key", headers=self._headers()
                )
            return r.status_code == 200
        except (httpx.HTTPError, Exception):
            return False

    async def provider_status(self) -> dict:
        """OpenRouter-specific: /models is a public endpoint, so
        authorization is probed via /auth/key + a 1-token chat call."""
        base = {"provider": self.name, "configured": False,
                "reachable": False, "authorized": False,
                "models_available": [], "models_missing": [],
                "status": "not_configured"}
        if not self.api_key:
            return {**base, "status": "missing_key"}
        base["configured"] = True
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    f"{self.base_url}/auth/key", headers=self._headers()
                )
        except httpx.HTTPError:
            return {**base, "status": "unreachable"}
        base["reachable"] = True
        if r.status_code == 401:
            return {**base, "status": "unauthorized"}
        if r.status_code in (402, 403):
            return {**base, "status": "quota_or_permission_error"}
        if r.status_code != 200:
            return {**base, "status": "provider_error",
                    "detail": f"key check HTTP {r.status_code}"}

        try:
            model = ai_config.model_for("fact_extractor")
        except Exception:
            model = next(iter(ai_config.generation_provider().models.values()))
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json={
                        "model": model,
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
        except httpx.HTTPError:
            return {**base, "status": "provider_error", "detail": "probe failed"}
        if r.status_code == 200:
            return {**base, "authorized": True, "status": "authorized"}
        if r.status_code == 401:
            return {**base, "status": "unauthorized"}
        if r.status_code in (402, 403):
            return {**base, "status": "quota_or_permission_error"}
        if r.status_code == 429:
            return {**base, "authorized": True, "status": "rate_limited"}
        return {**base, "status": "provider_error",
                "detail": f"probe HTTP {r.status_code}"}
