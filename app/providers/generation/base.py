from dataclasses import dataclass
from typing import Any


class GenerationError(RuntimeError):
    """Raised when a generation provider cannot fulfil a request.

    `kind` distinguishes missing credentials, auth failures, rate
    limits, timeouts, unavailable models and invalid model output so
    callers can decide when model fallback is legitimate.

    Some failures happen AFTER the provider already returned a billed
    response (e.g. output truncated at max_tokens, or a repair call whose
    JSON still failed to parse) — those carry the usage the provider
    already reported, so the cost ledger can book what was actually
    consumed instead of losing it. A failure with no response at all
    (timeout, unreachable, auth) leaves these None: nothing was billed.
    """

    def __init__(self, kind: str, message: str, *, provider: str | None = None,
                 model: str | None = None, input_tokens: int | None = None,
                 output_tokens: int | None = None, total_tokens: int | None = None,
                 cost_usd: float | None = None, cached_tokens: int | None = None,
                 reasoning_tokens: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens
        self.cost_usd = cost_usd
        self.cached_tokens = cached_tokens
        self.reasoning_tokens = reasoning_tokens


@dataclass
class GenerationResult:
    """One completed generation, including which model actually produced it.

    Usage/cost fields are populated only when the provider reports them —
    never invented; they stay None otherwise.
    """

    text: str
    model: str
    provider: str
    fallback_used: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    generation_id: str | None = None
    # Input tokens served from the provider's prompt cache (part of
    # input_tokens), tokens written to it, and the reasoning tokens inside
    # output_tokens; None when the provider reports nothing.
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    # What the request carried and what the gateway named it: the hash of the reusable start of the
    # prompt (the cache prefix, or the system prompt when there is none), the gateway's request id
    # and the usage source it reports. None when the provider gave nothing (never invented).
    prefix_sha256: str | None = None
    gateway_request_id: str | None = None
    usage_source: str | None = None
    # The reasoning effort that was sent with the request, None when none was.
    reasoning_effort: str | None = None


class GenerationProvider:
    """Provider-agnostic interface for LLM generation.

    Agents call generate_text / generate_structured with a logical
    `role` (fact_extractor, writer, ...) — the provider resolves the
    concrete model and parameters from central config. No agent ever
    names a model ID.
    """

    name: str = "base"

    def is_configured(self) -> bool:
        raise NotImplementedError

    async def check_health(self) -> bool:
        raise NotImplementedError

    async def generate_text(
        self, role: str, system: str, user: str, images: list[str] | None = None,
        *, prefix: str | None = None, cache_key: str | None = None,
    ) -> GenerationResult:
        """`prefix`: stable text sent before `system` so that calls sharing it hit
        the provider's prompt cache; `cache_key` groups such calls where the
        gateway asks for one."""
        raise NotImplementedError

    async def generate_structured(
        self, role: str, system: str, user: str, images: list[str] | None = None,
        *, prefix: str | None = None, cache_key: str | None = None,
    ) -> tuple[dict[str, Any], GenerationResult]:
        """`images`: optional data:/https URLs for vision-capable roles."""
        raise NotImplementedError
