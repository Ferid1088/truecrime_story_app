from dataclasses import dataclass, field
from typing import Any


@dataclass
class TranscriptSegmentData:
    """One timed caption segment as returned by a provider."""

    start_seconds: float
    end_seconds: float
    text: str


@dataclass
class TranscriptResult:
    """Normalized provider response. `available=False` means the transcript
    could not legitimately be obtained — callers store metadata only."""

    available: bool
    language: str = "unknown"
    transcript_type: str = "unavailable"
    # manual|auto_caption|creator_supplied|external_authorized|user_supplied|unavailable
    segments: list[TranscriptSegmentData] = field(default_factory=list)
    retrieval_method: str = ""
    reason: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class TranscriptProvider:
    """Provider-agnostic interface for legitimately-available transcripts.

    Implementations fetch publicly exposed captions/transcripts only —
    no authentication bypass, no access-control circumvention.
    """

    name: str = "base"

    async def get_transcript(
        self, video_id: str, preferred_languages: list[str] | None = None
    ) -> TranscriptResult:
        raise NotImplementedError
