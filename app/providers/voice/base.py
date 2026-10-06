"""Provider-agnostic text-to-speech interface.

The documentary engine asks for one voice block at a time and always
gets back the provider's own character timing, so actual audio — never
the planned duration — drives every later timing decision.
"""

from dataclasses import dataclass, field


class VoiceProviderError(RuntimeError):
    """`kind`: missing_credentials | unauthorized | forbidden |
    rate_limited | invalid_request | provider_error | timeout |
    unreachable | invalid_output."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class VoiceRequest:
    text: str
    voice_id: str
    model_id: str
    settings: dict
    previous_text: str = ""
    next_text: str = ""
    seed: int | None = None
    language: str | None = None


@dataclass
class VoiceRenderResult:
    audio: bytes
    audio_format: str            # e.g. "mp3_44100_128"
    characters: list[str]        # provider alignment of the input text
    char_starts: list[float]     # seconds
    char_ends: list[float]
    provider: str
    model_id: str
    voice_id: str
    request_id: str | None = None
    character_cost: int | None = None
    extra: dict = field(default_factory=dict)


class VoiceProvider:
    name = "base"

    def is_configured(self) -> bool:
        raise NotImplementedError

    async def synthesize(self, request: VoiceRequest) -> VoiceRenderResult:
        raise NotImplementedError
