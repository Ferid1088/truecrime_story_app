"""Upload contracts and deterministic adapters used before live YouTube access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UploadReceipt:
    upload_id: str
    visibility: str
    scheduled_at: str | None = None
    resume_token: str | None = None


@dataclass(frozen=True)
class ChannelConnection:
    language: str
    channel_id: str
    connected: bool = True


class UploadAdapterError(RuntimeError):
    retryable = True
    reconnect_required = False


class QuotaExceeded(UploadAdapterError):
    pass


class TokenExpired(UploadAdapterError):
    reconnect_required = True


class PartialUpload(UploadAdapterError):
    def __init__(self, upload_id: str, resume_token: str):
        super().__init__(f"upload {upload_id} is partial; resume with its token")
        self.upload_id, self.resume_token = upload_id, resume_token


class YouTubeUploadAdapter(Protocol):
    """The live adapter contract: OAuth, private/draft, schedule and resume."""

    def connect(self, *, language: str, authorization_code: str) -> ChannelConnection: ...
    def upload(self, *, bundle_id: int, bundle_hash: str, visibility: str,
               schedule_at: str | None = None, resume_token: str | None = None) -> UploadReceipt: ...
    def retry(self, *, upload_id: str, resume_token: str) -> UploadReceipt: ...


class RecordedYouTubeAdapter:
    """No-network fake with recorded responses for contract and UI tests."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or ["success"])
        self.channels: dict[str, ChannelConnection] = {}
        self.calls: list[dict] = []

    def connect(self, *, language: str, authorization_code: str) -> ChannelConnection:
        if not authorization_code:
            raise TokenExpired("OAuth reconnect is required for this language channel")
        row = ChannelConnection(language, f"recorded-{language}")
        self.channels[language] = row
        self.calls.append({"method": "connect", "language": language})
        return row

    def _response(self) -> str:
        return self.responses.pop(0) if self.responses else "success"

    def upload(self, *, bundle_id: int, bundle_hash: str, visibility: str,
               schedule_at: str | None = None, resume_token: str | None = None) -> UploadReceipt:
        self.calls.append({"method": "upload", "bundle_id": bundle_id,
                           "visibility": visibility, "schedule_at": schedule_at,
                           "resume_token": resume_token})
        if visibility not in {"private", "draft"}:
            raise ValueError("Uploads must be private or draft")
        response = self._response()
        upload_id = f"recorded-{bundle_id}-{bundle_hash[:12]}"
        if response == "quota_exceeded":
            raise QuotaExceeded("YouTube quota exceeded; retry later")
        if response == "token_expired":
            raise TokenExpired("YouTube token expired; reconnect this language channel")
        if response == "partial":
            raise PartialUpload(upload_id, f"resume-{bundle_id}")
        if response != "success":
            raise UploadAdapterError(f"recorded upload response: {response}")
        return UploadReceipt(upload_id, visibility, schedule_at)

    def retry(self, *, upload_id: str, resume_token: str) -> UploadReceipt:
        return self.upload(bundle_id=0, bundle_hash=upload_id, visibility="private",
                           resume_token=resume_token)


class SandboxUploadAdapter:
    """Record a deterministic private/draft upload without network access."""

    def upload(self, *, bundle_id: int, bundle_hash: str, visibility: str,
               schedule_at: str | None = None, resume_token: str | None = None) -> UploadReceipt:
        return UploadReceipt(
            upload_id=f"sandbox-bundle-{bundle_id}-{bundle_hash[:16]}",
            visibility=visibility,
        )
