from app.core.ai_config import ai_config
from app.providers.voice.base import (
    VoiceProvider,
    VoiceProviderError,
    VoiceRenderResult,
    VoiceRequest,
)


def get_voice_provider() -> VoiceProvider:
    name = ai_config.voice.provider
    if name == "elevenlabs":
        from app.providers.voice.elevenlabs import ElevenLabsVoiceProvider

        return ElevenLabsVoiceProvider()
    raise ValueError(f"Unknown voice provider: {name!r}")


__all__ = [
    "VoiceProvider",
    "VoiceProviderError",
    "VoiceRenderResult",
    "VoiceRequest",
    "get_voice_provider",
]
