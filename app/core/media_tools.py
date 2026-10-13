"""Configured media-tool paths shared by production exporters."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.core.ai_config import ai_config

DEFAULT_FFMPEG_BIN = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
DEFAULT_FFPROBE_BIN = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"


def _configured(tool: str) -> str | None:
    env_name = "FFMPEG_BIN" if tool == "ffmpeg" else "FFPROBE_BIN"
    config_name = "ffmpeg_bin" if tool == "ffmpeg" else "ffprobe_bin"
    fallback = DEFAULT_FFMPEG_BIN if tool == "ffmpeg" else DEFAULT_FFPROBE_BIN
    configured = os.environ.get(env_name) or getattr(ai_config.short_form.production, config_name, fallback)
    if configured:
        return str(Path(configured).expanduser())
    return shutil.which(tool)


def ffmpeg_bin() -> str | None:
    return _configured("ffmpeg")


def ffprobe_bin() -> str | None:
    return _configured("ffprobe")
