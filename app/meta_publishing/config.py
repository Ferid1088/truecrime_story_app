"""The Meta Reels requirements (config/meta_publishing.json). Every rule carries its evidence level: `confirmed` (stated as a requirement by Meta's own page),
`recommended` (Meta words it as a recommendation) or `unverified` (not stated on the page we read, or only inferred)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "meta_publishing.json"
Level = Literal["confirmed", "recommended", "unverified"]


class PlatformSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    containers: list[str]
    video_codecs: list[str]
    pix_fmts: list[str]
    audio_codecs: list[str]
    audio_channels: list[int]
    min_seconds: float
    max_seconds: float
    max_bytes: int | None
    min_fps: float
    max_fps: float
    max_width: int | None
    min_aspect: float
    max_aspect: float
    max_audio_sample_rate: int
    max_video_bitrate_bps: int | None
    recommended_aspect: tuple[int, int]
    recommended_min_width: int | None
    recommended_min_height: int | None
    min_audio_bitrate_bps: int
    max_gop_seconds: float | None
    levels: dict[str, Level]


class PreflightSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: int
    language: str
    ffprobe_timeout_s: int
    gop_tolerance_s: float


class InstagramAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ig_account_id: str
    username: str
    brand: str


class ConnectionSettings(BaseModel):
    """The owner's connection of the Instagram account (Facebook Login web flow). `live_enabled` allows only the read-only connection calls; publishing has its own switch."""

    model_config = ConfigDict(extra="forbid")

    live_enabled: bool
    app_env: str
    app_file: str
    state_file: str                      # `{language}` is replaced
    authorize_url: str                   # `{version}` is replaced
    redirect_path: str
    api_public_url: str
    ui_base_url: str
    state_ttl_seconds: int
    expiry_warning_days: int
    evidence: str

    @property
    def redirect_uri(self) -> str:
        return self.api_public_url.rstrip("/") + self.redirect_path


class InstagramSettings(BaseModel):
    """The one Instagram destination (Facebook Login for Business / Unified Login: graph.facebook.com, resumable upload on rupload.facebook.com)."""

    model_config = ConfigDict(extra="forbid")

    default_mode: Literal["DRY_RUN", "UPLOAD_ONLY", "PUBLISH"]
    live_enabled: bool
    graph_host: str
    upload_host: str
    api_version: str
    language: str
    account: InstagramAccount
    required_permissions: list[str]
    token_env: str
    token_file: str
    prepared_copy_dir: str
    request_timeout_s: float
    upload_timeout_s: float
    retry_attempts: int
    retry_backoff_s: float
    poll_interval_s: float
    poll_max_s: float
    connection: ConnectionSettings


class PrepareSettings(BaseModel):
    """How a Reel copy is made when the original does not meet a platform's media rules: the video is re-encoded only when its keyframes are too far apart (a remux cannot move
    them) with these settings, the audio is always copied bit for bit, and the copy must stay within `min_psnr_db` of the original."""

    model_config = ConfigDict(extra="forbid")

    video_crf: int
    video_preset: str
    video_profile: str
    keyframe_seconds: float
    min_psnr_db: float
    timeout_s: int


class MetaPublishingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    sources: dict[str, str]
    preflight: PreflightSettings
    instagram: InstagramSettings
    prepare: PrepareSettings
    platforms: dict[str, PlatformSpec]


@lru_cache(maxsize=1)
def meta_config() -> MetaPublishingConfig:
    return MetaPublishingConfig.model_validate(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
