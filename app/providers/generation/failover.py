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


def failure_of(error: GenerationError, cfg: GenerationFailoverConfig) -> str:
    """The configured name of a failure ("quota": a status of cfg.quota_http_status or a message of cfg.quota_messages; another refusal is a permission or content refusal, never
    moved)."""
    if error.kind == "forbidden":
        text = str(error).lower()
        empty = any(f"({code})" in text for code in cfg.quota_http_status) or any(m in text for m in cfg.quota_messages)
        return "quota" if empty else "forbidden"
    return error.kind


def with_discarded(result: GenerationResult, error: GenerationError) -> GenerationResult:
    def add(a, b):
        return a if b is None else (b if a is None else a + b)

    return replace(result, input_tokens=add(result.input_tokens, error.input_tokens), output_tokens=add(result.output_tokens, error.output_tokens),
                   total_tokens=add(result.total_tokens, error.total_tokens), cost_usd=add(result.cost_usd, error.cost_usd), fallback_used=True)


class FailoverGenerationProvider(GenerationProvider):
    """Two gateways. While both are available the calls are shared by `cfg.secondary_share` (the gateway that is less loaded for its share gets the
    next call, so a slow one is not waited for); a gateway that is not available (as configured) is skipped for the cooldown and its calls go to the
    other one."""

    _lock = threading.Lock()
    _until: dict[str, float] = {}           # per gateway: until when no call goes to it (process-wide)
    _inflight: dict[str, int] = {}          # per gateway: calls running now (process-wide)

    def __init__(self, primary: GenerationProvider, secondary: GenerationProvider, cfg: GenerationFailoverConfig):
        self.primary, self.secondary, self.cfg = primary, secondary, cfg
        self.name, self.label = primary.name, primary.label

    def __getattr__(self, name: str):
        return getattr(self.primary, name)

    def is_configured(self) -> bool:
        return self.primary.is_configured() or self.secondary.is_configured()

    async def check_health(self) -> bool:
        return await self.primary.check_health() or await self.secondary.check_health()

    def cooling(self, gateway: GenerationProvider | None = None) -> bool:
        gateway = gateway or self.primary
        with self._lock:
            return self._until.get(gateway.name, 0.0) > time.monotonic()

    def _prefer_secondary(self) -> bool:
        share = self.cfg.secondary_share
        if share <= 0.0:
            return False
        if share >= 1.0:
            return True
        with self._lock:
            primary, secondary = self._inflight.get(self.primary.name, 0), self._inflight.get(self.secondary.name, 0)
        return (secondary + 1) / share < (primary + 1) / (1.0 - share)

    def _order(self, cached: bool = False) -> list[GenerationProvider]:
        """The gateways in the order a call tries them. A call with a cacheable prefix (or a cache key) always tries the first gateway first while it is
        available: the cache lives on one gateway, so sharing such calls between two gateways makes half of them miss it and pay for the writes."""
        if not self.secondary.is_configured():
            return [self.primary]
        primary_cooling, secondary_cooling = self.cooling(self.primary), self.cooling(self.secondary)
        if cached and not primary_cooling:
            return [self.primary, self.secondary]
        if primary_cooling and not secondary_cooling:
            return [self.secondary, self.primary]
        if secondary_cooling or primary_cooling:
            return [self.primary, self.secondary]
        return [self.secondary, self.primary] if self._prefer_secondary() else [self.primary, self.secondary]

    def moved(self, gateway: GenerationProvider, error: GenerationError) -> bool:
        if failure_of(error, self.cfg) not in self.cfg.on:
            return False
        with self._lock:
            self._until[gateway.name] = time.monotonic() + self.cfg.cooldown_s
        other = self.secondary if gateway is self.primary else self.primary
        log.warning("%s not available (%s: %s): the call goes to %s, and no call goes to %s for %ss", gateway.label, error.kind, str(error)[:200],
                    other.label, gateway.label, self.cfg.cooldown_s)
        return True

    async def _call(self, method: str, role, system, user, images, prefix, cache_key):
        order = self._order(cached=bool(prefix or cache_key))
        discarded: GenerationError | None = None
        for position, gateway in enumerate(order):
            with self._lock:
                self._inflight[gateway.name] = self._inflight.get(gateway.name, 0) + 1
            try:
                out = await getattr(gateway, method)(role, system, user, images, prefix=prefix, cache_key=cache_key)
            except GenerationError as e:
                if position + 1 < len(order) and self.moved(gateway, e):
                    discarded = e
                    continue
                raise
            finally:
                with self._lock:
                    self._inflight[gateway.name] -= 1
            moved = discarded is not None or (gateway is not self.primary and self.cooling(self.primary))       # a share of the calls is no fallback
            return out, discarded, moved
        raise AssertionError("unreachable: the last gateway either answers or raises")

    async def generate_text(self, role, system, user, images=None, *, prefix=None, cache_key=None) -> GenerationResult:
        result, discarded, moved = await self._call("generate_text", role, system, user, images, prefix, cache_key)
        if discarded is not None:
            return with_discarded(result, discarded)
        return replace(result, fallback_used=True) if moved else result

    async def generate_structured(self, role, system, user, images=None, *, prefix=None, cache_key=None):
        (data, result), discarded, moved = await self._call("generate_structured", role, system, user, images, prefix, cache_key)
        if discarded is not None:
            return data, with_discarded(result, discarded)
        return (data, replace(result, fallback_used=True)) if moved else (data, result)
