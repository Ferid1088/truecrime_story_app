from app.providers.base import ProviderJob, ProviderError, ResearchProvider
from app.research_engine.provider import TrueCrimeSearchProvider


def get_research_provider() -> ResearchProvider:
    """Return the active research provider — the TrueCrime Search
    Engine (Brave Search + fetcher + extractor + index), with APIMaster
    supplying bounded intelligence only.

    Search is not an LLM task: no AI vendor performs web discovery.
    Historical provider strings remain readable data."""
    return TrueCrimeSearchProvider()


__all__ = [
    "ProviderJob",
    "ProviderError",
    "ResearchProvider",
    "TrueCrimeSearchProvider",
    "get_research_provider",
]
