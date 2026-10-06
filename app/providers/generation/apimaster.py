"""APIMaster — the sole generation/analysis provider.

All non-search LLM work (extraction, normalization, critique, story
generation, localization) routes here through logical roles. Model IDs
are APIMaster-native (no vendor prefixes) and come only from
config/ai_config.json — agents never name them.

Scope boundary: APIMaster is never used for web search/fetch; that is
OpenRouter's sole responsibility.
"""

from app.providers.generation.openai_compat import (
    OpenAICompatibleGenerationProvider,
)


class APIMasterGenerationProvider(OpenAICompatibleGenerationProvider):
    name = "apimaster"
    label = "APIMaster"
