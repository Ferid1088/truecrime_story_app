"""The media tools (ffmpeg, ffprobe) the production exporters run: the environment's choice, else the first
configured build that exists, else the tool on PATH."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.core.ai_config import ai_config

_ENV = {"ffmpeg": "FFMPEG_BIN", "ffprobe": "FFPROBE_BIN"}


def _resolved(tool: str) -> str | None:
    chosen = os.environ.get(_ENV[tool])
    if chosen:      # an explicit choice of the environment is never second-guessed
        return str(Path(chosen).expanduser())
    for candidate in getattr(ai_config.short_form.production, f"{tool}_candidates"):
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
    return shutil.which(tool)


def ffmpeg_bin() -> str | None:
    return _resolved("ffmpeg")


def ffprobe_bin() -> str | None:
    return _resolved("ffprobe")
