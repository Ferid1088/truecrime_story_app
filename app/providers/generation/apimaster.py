"""APIMaster — the sole generation/analysis provider.

All non-search LLM work (extraction, normalization, critique, story
generation, the language pipeline) routes here through logical roles. Model IDs
are APIMaster-native (no vendor prefixes) and come only from
config/ (connections.json, models.json, parameters/) — agents never name them.

Scope boundary: APIMaster is never used for web search/fetch; that is the
first-party TrueCrime Search Engine's responsibility.
"""

from app.providers.generation.openai_compat import (
    OpenAICompatibleGenerationProvider,
)


class APIMasterGenerationProvider(OpenAICompatibleGenerationProvider):
    name = "apimaster"
    label = "APIMaster"
