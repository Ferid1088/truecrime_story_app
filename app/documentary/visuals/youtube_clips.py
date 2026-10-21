"""The YouTube clip route: a downloader for videos of the allowed source classes only.

Behind an owner switch in Settings ("YouTube clip downloader"), off by default; nothing in the code
turns it on. Guardrails: only a film candidate with a source class and its stored evidence; never
another creator's edited film; a source longer than youtube_clips.max_source_seconds is refused; the
download is bounded in height and size; a source record (URL, channel, title, video date, class,
evidence, hash, download time) is stored with every clip and exported; the clip carries an on-screen
credit; the same bytes are stored once. yt-dlp (The Unlicense, docs/youtube_clip_downloader.md) does
the download; the cut to the sentence is the existing segmenter and auditor."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import AppSetting
from app.services.video_rights import RightsRefused, VideoRightsGate
from app.utils import utc_now

SETTING_KEY = "youtube_clip_downloader"
YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")
MB = 1024 * 1024


def is_youtube(url: str | None) -> bool:
    try:
        return (urlparse(url or "").hostname or "").lower() in YOUTUBE_HOSTS
    except ValueError:
        return False


def enabled(db: Session) -> bool:
    """The owner's switch; the config default (off) until one is made."""
    row = db.get(AppSetting, SETTING_KEY)
    if row is not None:
        try:
            value = json.loads(row.value_json).get("enabled")
        except (ValueError, AttributeError):
            value = None
        if isinstance(value, bool):
            return value
    return ai_config.youtube_clips.default_enabled


def set_enabled(db: Session, value: bool) -> bool:
    row = db.get(AppSetting, SETTING_KEY)
    if row is None:
        row = AppSetting(key=SETTING_KEY)
        db.add(row)
    row.value_json = json.dumps({"enabled": bool(value)})
    row.updated_at = utc_now()
    db.commit()
    return bool(value)


def check_allowed(db: Session, video, profile: str | None = None) -> None:
    """Raise RightsRefused with the reason unless this video may be downloaded from YouTube."""
    if not enabled(db):
        raise RightsRefused("the YouTube clip downloader switch is off (Settings)")
    VideoRightsGate().require(video, profile)
    if getattr(video, "classification", None) in ai_config.video_rights.research_only_classifications:
        raise RightsRefused("another creator's edited film is research only")
    seconds = getattr(video, "duration_seconds", None)
    if seconds and seconds > ai_config.youtube_clips.max_source_seconds:
        raise RightsRefused(f"the video is longer than {ai_config.youtube_clips.max_source_seconds:g} s")


def build_options(outtmpl: Path | str) -> dict:
    cfg = ai_config.youtube_clips
    return {"format": f"bv*[height<={cfg.max_height}]+ba/b[height<={cfg.max_height}]", "noplaylist": True,
            "max_filesize": int(cfg.max_download_mb * MB), "outtmpl": str(outtmpl),
            "merge_output_format": "mp4", "quiet": True, "no_warnings": True, "retries": 2}


class YtDlpDownloader:
    """Downloads one video with yt-dlp (in a thread: the library is blocking)."""

    async def fetch(self, url: str, dest: Path) -> Path:
        def run() -> None:
            import yt_dlp

            with yt_dlp.YoutubeDL(build_options(dest.with_suffix(".%(ext)s"))) as ydl:
                ydl.download([url])

        await asyncio.to_thread(run)
        found = sorted(dest.parent.glob(dest.stem + ".*"))
        if not found:
            raise RightsRefused("the download produced no file")
        return found[0]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(MB), b""):
            h.update(chunk)
    return h.hexdigest()


def source_record(video, sha256: str) -> dict:
    """What is stored and exported for every clip made from a YouTube video."""
    return {"url": video.url, "channel": video.channel_name, "channel_url": video.channel_url,
            "title": video.title, "video_date": video.published_at, "source_class": video.source_class,
            "class_evidence": video.class_evidence, "sha256": sha256,
            "downloaded_at": utc_now().isoformat()}
