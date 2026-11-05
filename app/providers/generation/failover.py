"""The generation provider with a second gateway behind it (config generation_failover): a call goes to the active provider; when that provider
is not available (it cannot be reached, times out, answers a server error, has no such model, rate-limits, refuses the key or has no quota left,
as configured), the same call goes to the second gateway with the same role and model, and every call goes there for the cooldown. A content
refusal, an invalid request or invalid output is never moved: quality failures stay visible. Each result says which provider made it (the run
ledger books it under that provider), and usage a failed first attempt was billed is added to the result."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace

from app.core.ai_config import GenerationFailoverConfig
from app.providers.generation.base import GenerationError, GenerationProvider, GenerationResult

log = logging.getLogger(__name__)


def failure_of(error: GenerationError) -> str:
    """The configured name of a failure ("quota" is a 402; another 403 is a permission or content refusal, never moved)."""
    if error.kind == "forbidden":
        return "quota" if "(402)" in str(error) else "forbidden"
    return error.kind


def with_discarded(result: GenerationResult, error: GenerationError) -> GenerationResult:
    def add(a, b):
        return a if b is None else (b if a is None else a + b)

    return replace(result, input_tokens=add(result.input_tokens, error.input_tokens), output_tokens=add(result.output_tokens, error.output_tokens),
                   total_tokens=add(result.total_tokens, error.total_tokens), cost_usd=add(result.cost_usd, error.cost_usd), fallback_used=True)


class FailoverGenerationProvider(GenerationProvider):
    _lock = threading.Lock()
    _until: dict[str, float] = {}           # per primary: until when calls go to the second gateway (process-wide)

    def __init__(self, primary: GenerationProvider, secondary: GenerationProvider, cfg: GenerationFailoverConfig):
        self.primary, self.secondary, self.cfg = primary, secondary, cfg
        self.name, self.label = primary.name, primary.label

    def __getattr__(self, name: str):
        return getattr(self.primary, name)

    def is_configured(self) -> bool:
        return self.primary.is_configured() or self.secondary.is_configured()

    async def check_health(self) -> bool:
        return await self.primary.check_health() or await self.secondary.check_health()

    def cooling(self) -> bool:
        with self._lock:
            return self._until.get(self.primary.name, 0.0) > time.monotonic()

    def moved(self, error: GenerationError) -> bool:
        if failure_of(error) not in self.cfg.on or not self.secondary.is_configured():
            return False
        with self._lock:
            self._until[self.primary.name] = time.monotonic() + self.cfg.cooldown_s
        log.warning("%s not available (%s: %s): the call goes to %s, and every call for %ss", self.primary.label, error.kind, str(error)[:200],
                    self.secondary.label, self.cfg.cooldown_s)
        return True

    async def generate_text(self, role, system, user, images=None, *, prefix=None, cache_key=None) -> GenerationResult:
        if self.cooling():
            return replace(await self.secondary.generate_text(role, system, user, images, prefix=prefix, cache_key=cache_key), fallback_used=True)
        try:
            return await self.primary.generate_text(role, system, user, images, prefix=prefix, cache_key=cache_key)
        except GenerationError as e:
            if not self.moved(e):
                raise
            result = await self.secondary.generate_text(role, system, user, images, prefix=prefix, cache_key=cache_key)
            return with_discarded(result, e)

    async def generate_structured(self, role, system, user, images=None, *, prefix=None, cache_key=None):
        if self.cooling():
            data, result = await self.secondary.generate_structured(role, system, user, images, prefix=prefix, cache_key=cache_key)
            return data, replace(result, fallback_used=True)
        try:
            return await self.primary.generate_structured(role, system, user, images, prefix=prefix, cache_key=cache_key)
        except GenerationError as e:
            if not self.moved(e):
                raise
            data, result = await self.secondary.generate_structured(role, system, user, images, prefix=prefix, cache_key=cache_key)
            return data, with_discarded(result, e)
