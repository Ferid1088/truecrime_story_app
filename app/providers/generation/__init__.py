from app.core.ai_config import ai_config
from app.providers.generation.apimaster import APIMasterGenerationProvider
from app.providers.generation.base import (
    GenerationError,
    GenerationProvider,
    GenerationResult,
)
from app.providers.generation.openrouter import OpenRouterGenerationProvider

# Central generation-provider registry (Part 3): the active provider is
# selected by providers.generation in config/ai_config.json — agents ask
# for a ROLE, never a provider or model.
_PROVIDERS: dict[str, type[GenerationProvider]] = {
    "apimaster": APIMasterGenerationProvider,
    "openrouter": OpenRouterGenerationProvider,
}


def get_generation_provider() -> GenerationProvider:
    """Return the configured generation/reasoning provider.

    APIMaster is the active provider; OpenRouter generation stays
    registered so provider='openrouter' historical runs and a config
    re-selection remain valid. The instance reports configured=False
    when its credentials are missing.
    """
    name = ai_config.providers.generation
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise GenerationError(
            "invalid_configuration",
            f"Unknown generation provider {name!r}. "
            f"Known: {sorted(_PROVIDERS)}",
        )
    return cls()


__all__ = [
    "APIMasterGenerationProvider",
    "GenerationError",
    "GenerationProvider",
    "GenerationResult",
    "OpenRouterGenerationProvider",
    "get_generation_provider",
]
