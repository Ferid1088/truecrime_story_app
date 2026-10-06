from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    """Raised when the research provider cannot fulfil a request.

    Carries a machine-readable `kind` so callers can distinguish
    missing credentials, auth failures, rate limits, timeouts and
    malformed output without parsing message text.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class ProviderJob:
    """Normalized view of an external, possibly long-running research job."""

    external_id: str
    status: str  # queued | running | completed | failed
    result: dict[str, Any] | None = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class ResearchProvider:
    """Provider-agnostic interface for external research/internet access.

    Implementations submit long-running research tasks (start_*),
    expose progress (poll), and return structured results. They never
    write stories or touch the fact layer directly.
    """

    name: str = "base"

    def is_configured(self) -> bool:
        raise NotImplementedError

    async def check_health(self) -> bool:
        """Minimal safe request proving credentials + reachability."""
        raise NotImplementedError

    async def start_discovery(
        self,
        count: int,
        languages: list[str],
        theme: str,
        prefer_undercovered: bool,
        require_multiple_sources: bool,
        existing_titles: list[str],
    ) -> str:
        """Submit a case-discovery task; returns the external job id."""
        raise NotImplementedError

    async def start_case_research(
        self,
        case_title: str,
        language: str,
        objective: str | None = None,
        research_languages: list[str] | None = None,
    ) -> str:
        """Submit a deep-research task for one case; returns external job id."""
        raise NotImplementedError

    async def start_video_discovery(
        self, case_title: str, languages: list[str]
    ) -> str:
        """Submit a multilingual video-discovery task; returns external job id.

        The structured result must contain a "videos" array (title, url,
        video_id, channel_name, language, duration_seconds, published_at,
        description, classification, relevance)."""
        raise NotImplementedError

    async def poll(self, external_id: str) -> ProviderJob:
        """Fetch current state of an external job."""
        raise NotImplementedError
