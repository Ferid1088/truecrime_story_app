"""The YouTube publishing settings (config/youtube_publishing.json). Uploads are private in this phase: the initial privacy is a fixed value the uploader refuses to leave."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "youtube_publishing.json"


class ChannelLabel(BaseModel):
    """How the owner knows a channel. The channel id itself lives in `publication_accounts` (the registry), never here."""

    model_config = ConfigDict(extra="forbid")

    label: str
    name: str = ""          # the channel's name once the owner has given it


class OAuthSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorize_url: str
    token_url: str
    revoke_url: str
    scopes: list[str]
    redirect_path: str
    state_ttl_seconds: int = Field(gt=0)
    refresh_margin_seconds: int = Field(ge=0)
    client_secret_env: str
    client_secret_file: str
    token_file: str            # one file per channel: `{language}` is replaced


class ApiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    upload_url: str
    timeout_seconds: float = Field(gt=0)
    upload_timeout_seconds: float = Field(gt=0)


class UploadSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_privacy: Literal["private"]
    public_after_processing: bool                    # owner decision 2026-11-18: a video that was uploaded private, processed and given its thumbnail and subtitles is made public at once
    category_id: str                                  # 1 is "Film & Animation" (videoCategories)
    studio_url: str                                   # where a video is edited by hand in YouTube Studio ({video_id}); the related video of a Short is set there
    self_declared_made_for_kids: bool
    contains_synthetic_media: bool
    chunk_bytes: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    retry_base_seconds: float = Field(ge=0)
    retry_max_seconds: float = Field(ge=0)
    processing_poll_seconds: float = Field(ge=0)
    processing_max_wait_seconds: float = Field(gt=0)
    session_max_age_hours: int = Field(gt=0)
    caption_languages: list[str]
    evidence: str


class YouTubeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    live_enabled: bool
    channels: dict[str, ChannelLabel]
    ui_base_url: str
    api_public_url: str
    oauth: OAuthSettings
    api: ApiSettings
    upload: UploadSettings

    @model_validator(mode="after")
    def _token_files_are_per_language(self) -> YouTubeConfig:
        if "{language}" not in self.oauth.token_file:
            raise ValueError("oauth.token_file must contain {language}: every channel keeps its own credentials")
        return self

    @property
    def redirect_uri(self) -> str:
        return self.api_public_url.rstrip("/") + self.oauth.redirect_path


@lru_cache(maxsize=1)
def youtube_config() -> YouTubeConfig:
    return YouTubeConfig.model_validate(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
