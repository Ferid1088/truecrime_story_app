"""Provider-agnostic talking-avatar interface: our audio in, a video of
the avatar speaking it out (transparent when the provider can)."""

from dataclasses import dataclass, field
from pathlib import Path


class AvatarProviderError(RuntimeError):
    """`kind`: missing_credentials | unauthorized | forbidden | not_found |
    rate_limited | invalid_request | provider_error | timeout | unreachable |
    job_failed | invalid_output."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class AvatarJob:
    job_id: str
    status: str                      # waiting | processing | completed | failed
    video_url: str | None = None
    failure_message: str | None = None
    duration: float | None = None
    extra: dict = field(default_factory=dict)


class AvatarProvider:
    name = "base"

    def is_configured(self) -> bool:
        raise NotImplementedError

    async def resolve_look(self, avatar_id: str) -> str:
        raise NotImplementedError

    async def upload_asset(self, path: Path, mime: str) -> str:
        raise NotImplementedError

    async def create_video(self, look_id: str, audio_asset_id: str, idempotency_key: str,
                           output_format: str, resolution: str,
                           background_asset_id: str | None = None,
                           title: str | None = None) -> AvatarJob:
        raise NotImplementedError

    async def get_video(self, job_id: str) -> AvatarJob:
        raise NotImplementedError

    async def download(self, url: str, dest: Path) -> int:
        raise NotImplementedError
