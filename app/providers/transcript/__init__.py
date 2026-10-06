from app.core.ai_config import ai_config
from app.core.config import settings
from app.providers.transcript.base import TranscriptProvider, TranscriptResult


def get_transcript_provider() -> TranscriptProvider:
    """Configured transcript provider. The rest of the code depends only on
    the TranscriptProvider interface, never on a concrete service."""
    from app.providers.transcript.youtube import YouTubeTranscriptProvider

    return YouTubeTranscriptProvider()


__all__ = ["TranscriptProvider", "TranscriptResult", "get_transcript_provider"]
