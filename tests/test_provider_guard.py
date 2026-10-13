"""The test process must never construct or call a live provider client."""

import pytest

from app.core.provider_guard import RealProviderUseError


@pytest.mark.parametrize(
    ("label", "builder"),
    [
        ("LLM", lambda: __import__(
            "app.providers.generation.apimaster", fromlist=["APIMasterGenerationProvider"]
        ).APIMasterGenerationProvider()),
        ("ElevenLabs", lambda: __import__(
            "app.providers.voice.elevenlabs", fromlist=["ElevenLabsVoiceProvider"]
        ).ElevenLabsVoiceProvider()),
        ("sound effects", lambda: __import__(
            "app.providers.voice.elevenlabs", fromlist=["ElevenLabsSoundProvider"]
        ).ElevenLabsSoundProvider()),
        ("HeyGen", lambda: __import__(
            "app.providers.avatar.heygen", fromlist=["HeyGenAvatarProvider"]
        ).HeyGenAvatarProvider()),
        ("YouTube transcripts", lambda: __import__(
            "app.providers.transcript.youtube", fromlist=["YouTubeTranscriptProvider"]
        ).YouTubeTranscriptProvider()),
        ("YouTube Data API", lambda: __import__(
            "app.services.youtube", fromlist=["YouTubeSearchService"]
        ).YouTubeSearchService()),
        ("ASR", lambda: __import__(
            "app.documentary.asr", fromlist=["FasterWhisperASR"]
        ).FasterWhisperASR()),
    ],
)
def test_real_provider_construction_is_blocked(label, builder):
    with pytest.raises(RealProviderUseError, match="Tests forbid real provider"):
        builder()


def test_real_image_http_client_is_blocked():
    from app.agents.visual_research import VisualResearchAgent

    with pytest.raises(RealProviderUseError, match="Tests forbid real provider"):
        VisualResearchAgent()._new_client()

