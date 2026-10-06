from dataclasses import dataclass
from typing import Any


class GenerationError(RuntimeError):
    """Raised when a generation provider cannot fulfil a request.

    `kind` distinguishes missing credentials, auth failures, rate
    limits, timeouts, unavailable models and invalid model output so
    callers can decide when model fallback is legitimate.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


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
        self, role: str, system: str, user: str
    ) -> GenerationResult:
        raise NotImplementedError

    async def generate_structured(
        self, role: str, system: str, user: str
    ) -> tuple[dict[str, Any], GenerationResult]:
        raise NotImplementedError
