import os

from app.core.ai_config import ai_config
from app.providers.generation.apimaster import APIMasterGenerationProvider
from app.providers.generation.base import (
    GenerationError,
    GenerationProvider,
    GenerationResult,
)
from app.providers.generation.recorded import RecordedE2EGenerationProvider

# Central generation-provider registry (Part 3): the active provider is
# selected by providers.generation in config/ (connections.json, models.json, parameters/) — agents ask
# for a ROLE, never a provider or model.
_PROVIDERS: dict[str, type[GenerationProvider]] = {
    "apimaster": APIMasterGenerationProvider,
}


def get_generation_provider() -> GenerationProvider:
    """Return the configured generation/reasoning provider.

    APIMaster is the active provider. Historical provider strings remain
    readable in stored rows; they are not active provider registrations.
    The instance reports configured=False when its credentials are missing.
    """
    if (os.environ.get("TC_TEST_NO_NETWORK") == "1"
            or os.environ.get("TRUECRIME_TEST_NO_REAL_PROVIDERS") == "1"):
        return RecordedE2EGenerationProvider()
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
    "get_generation_provider",
]
