"""The Meta Reels requirements (config/meta_publishing.json). Every rule carries its evidence level: `confirmed` (stated as a requirement by Meta's own page),
`recommended` (Meta words it as a recommendation) or `unverified` (not stated on the page we read, or only inferred)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    connection_permissions: list[str]    # what the connection test needs: the token reaches the account and may publish to it
    required_permissions: list[str]      # what Meta documents for publishing (the Reels publisher refuses without all of them)
    token_env: str
    account_id_env: str                  # optional: when set, it must name the configured account
    token_file: str
    prepared_copy_dir: str
    thumb_offset_ms: int = Field(ge=0)   # Meta's `thumb_offset`: the frame (in milliseconds) that is the Reel's cover; 0 is the first frame, where a delivery copy carries its cover
    request_timeout_s: float
    upload_timeout_s: float
    retry_attempts: int
    retry_backoff_s: float
    poll_interval_s: float
    poll_max_s: float
    connection: ConnectionSettings


class FacebookPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str
    brand: str
    linked_instagram_id: str


class FacebookReels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_interval_s: float
    poll_max_s: float
    upload_timeout_s: float
    max_posts_per_24h: int


class FacebookFilm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_bytes: int
    poll_interval_s: float
    poll_max_s: float
    chunk_timeout_s: float


class FacebookCover(BaseModel):
    """Setting a custom cover (a thumbnail) on an uploaded Reel: a separate Graph call that needs more permissions than the upload does (`required_permissions`), so the upload never waits for it."""

    model_config = ConfigDict(extra="forbid")

    required_permissions: list[str]
    max_mb: int
    source: str


class FacebookSettings(BaseModel):
    """The Farsi Facebook Page. It uses the Instagram section's token and Graph settings (one Meta system-user token, one Meta app): nothing about credentials is repeated here.
    `live_enabled` allows uploads (the read-only connection test never needs it); `allow_public_publish` is the separate switch that PUBLISH needs."""

    model_config = ConfigDict(extra="forbid")

    live_enabled: bool
    allow_public_publish: bool
    pages: dict[str, FacebookPage]       # one Page per language; all of them are reached with the one system-user token
    language: str = ""                   # the language this view serves (`for_language`); the first configured one when not chosen
    app_id: str
    video_host: str
    required_permissions: list[str]
    required_task: str
    state_file: str
    reels: FacebookReels
    film: FacebookFilm
    cover: FacebookCover
    evidence: dict[str, str]

    @model_validator(mode="after")
    def _chosen_language(self):
        if not self.language:
            self.language = next(iter(self.pages))
        if self.language not in self.pages:
            raise ValueError(f"no Facebook Page is configured for '{self.language}'")
        return self

    @property
    def page(self) -> FacebookPage:
        return self.pages[self.language]

    def for_language(self, language: str) -> FacebookSettings:
        """The same settings for the Page of one language. Raises ValueError for a language that has no Page."""
        if language not in self.pages:
            raise ValueError(f"no Facebook Page is configured for '{language}'")
        return self.model_copy(update={"language": language})


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
    facebook: FacebookSettings
    prepare: PrepareSettings
    platforms: dict[str, PlatformSpec]


@lru_cache(maxsize=1)
def meta_config() -> MetaPublishingConfig:
    return MetaPublishingConfig.model_validate(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
