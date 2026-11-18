"""The platforms' file rules for reels, read from config/platform_specs.json, and a check of a real video file against them.

The numbers come with their source and how well each is known (docs/platform_specs.md); this module enforces only what the config marks as official or measured for the video. Meta's own preflight
(app/meta_publishing/validator.py) stays the gate for Facebook and Instagram uploads; this check covers every platform in one place and adds the sound level our clips are made to."""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin, ffprobe_bin

CONFIG = Path(__file__).resolve().parents[2] / "config" / "platform_specs.json"
CODEC_NAMES = {"hevc": "hevc", "h265": "hevc", "h264": "h264", "avc": "h264"}


class SpecError(ValueError):
    pass


class VideoLimits(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    aspect: str | None = None
    width: int | None = None
    height: int | None = None
    min_width: int | None = None
    min_height: int | None = None
    min_pixels: int | None = None
    max_pixels: int | None = None
    max_short_side: int | None = None
    fps_min: float | None = None
    fps_max: float | None = None
    fixed_fps: bool = False
    seconds_min: float | None = None
    seconds_max: float | None = None
    codecs: list[str] = []
    pix_fmt: str | None = None
    gop_seconds_min: float | None = None
    gop_seconds_max: float | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    audio_rate: int | None = None
    audio_kbps_min: int | None = None


class Platform(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    video: VideoLimits
    cover: dict
    sources: list[str]


class PlatformSpecs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int
    verified_on: str
    platforms: dict[str, Platform]
    cover_use: dict[str, str]


@lru_cache(maxsize=1)
def specs() -> PlatformSpecs:
    try:
        return PlatformSpecs.model_validate(json.loads(CONFIG.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise SpecError(f"{CONFIG.name} cannot be read: {exc}") from exc


def platform(key: str) -> Platform:
    try:
        return specs().platforms[key]
    except KeyError:
        raise SpecError(f"unknown platform {key!r}; known: {', '.join(specs().platforms)}") from None


def probe(path: Path) -> dict:
    """What the file is, measured: size, frame rates, length, codecs, the longest gap between key frames, the sound's true peak."""
    ffprobe, ffmpeg = ffprobe_bin(), ffmpeg_bin()
    if not ffprobe or not ffmpeg:
        raise SpecError("ffmpeg and ffprobe are needed to check a video")
    shown = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout)
    video = next((s for s in shown["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in shown["streams"] if s["codec_type"] == "audio"), None)
    if video is None:
        raise SpecError(f"{path.name} has no video stream")
    keys = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.split()
    times = [float(t.rstrip(",")) for t in keys if t.rstrip(",")]
    out = {"width": int(video["width"]), "height": int(video["height"]), "codec": CODEC_NAMES.get(video["codec_name"], video["codec_name"]), "pix_fmt": video.get("pix_fmt"),
           "fps": _rate(video.get("avg_frame_rate")), "fixed_fps": video.get("avg_frame_rate") == video.get("r_frame_rate"), "seconds": float(shown["format"]["duration"]),
           "gop_seconds": max((b - a for a, b in zip(times, times[1:], strict=False)), default=float(shown["format"]["duration"])), "audio": None}
    if audio is not None:
        meter = subprocess.run([ffmpeg, "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
        peak = meter.rsplit("Peak:", 1)[1].split("dBFS")[0].strip() if "Peak:" in meter else None
        out["audio"] = {"codec": audio["codec_name"], "channels": int(audio["channels"]), "rate": int(audio["sample_rate"]), "kbps": int(audio.get("bit_rate", 0)) // 1000, "true_peak_db": float(peak) if peak else None}
    return out


def _rate(text: str | None) -> float:
    if not text or "/" not in text:
        return 0.0
    top, bottom = text.split("/")
    return float(top) / float(bottom) if float(bottom) else 0.0


def check_all(path: Path) -> dict[str, list[str]]:
    """The check for every platform at once ({platform: what is wrong}); the file is measured only once."""
    found = probe(path)
    return {key: check_video(path, key, found) for key in specs().platforms}


def check_video(path: Path, platform_key: str, found: dict | None = None) -> list[str]:
    """What is wrong with this file for this platform, in words (empty when nothing is). A closed GOP cannot be seen from outside; it is made by the encoder (`clip_video.encode_args`)."""
    limits, found = platform(platform_key).video, found or probe(path)
    problems: list[str] = []
    w, h = found["width"], found["height"]
    if limits.aspect == "9:16" and abs(w / h - 9 / 16) > 0.01:
        problems.append(f"the picture is {w}x{h}, not 9:16")
    for need, have, name in ((limits.min_width, w, "width"), (limits.min_height, h, "height"), (limits.min_pixels, min(w, h), "shorter side")):
        if need and have < need:
            problems.append(f"the {name} is {have} px, at least {need} are needed")
    if limits.max_pixels and max(w, h) > limits.max_pixels:
        problems.append(f"a side is {max(w, h)} px, at most {limits.max_pixels} are allowed")
    if limits.max_short_side and min(w, h) > limits.max_short_side:
        problems.append(f"the shorter side is {min(w, h)} px, at most {limits.max_short_side} are allowed")
    if limits.fps_min and found["fps"] < limits.fps_min - 0.01 or limits.fps_max and found["fps"] > limits.fps_max + 0.01:
        problems.append(f"{found['fps']:.2f} fps is outside {limits.fps_min}-{limits.fps_max}")
    if limits.fixed_fps and not found["fixed_fps"]:
        problems.append("the frame rate is not fixed")
    if limits.seconds_min and found["seconds"] < limits.seconds_min or limits.seconds_max and found["seconds"] > limits.seconds_max:
        problems.append(f"{found['seconds']:.1f} s is outside {limits.seconds_min or 0}-{limits.seconds_max} s")
    if limits.codecs and found["codec"] not in limits.codecs:
        problems.append(f"the video codec is {found['codec']}, one of {', '.join(limits.codecs)} is needed")
    if limits.pix_fmt and found["pix_fmt"] != limits.pix_fmt:
        problems.append(f"the pixel format is {found['pix_fmt']}, {limits.pix_fmt} is needed")
    if limits.gop_seconds_max and found["gop_seconds"] > limits.gop_seconds_max + 0.05:
        problems.append(f"the key frames are up to {found['gop_seconds']:.1f} s apart, at most {limits.gop_seconds_max:g} s are allowed")
    sound = found["audio"]
    if sound is None:
        problems.append("the video has no sound")
    else:
        if limits.audio_codec and sound["codec"] != limits.audio_codec:
            problems.append(f"the sound is {sound['codec']}, {limits.audio_codec} is needed")
        if limits.audio_channels and sound["channels"] != limits.audio_channels:
            problems.append(f"the sound has {sound['channels']} channels, {limits.audio_channels} are needed")
        if limits.audio_rate and sound["rate"] != limits.audio_rate:
            problems.append(f"the sound is {sound['rate']} Hz, {limits.audio_rate} Hz are needed")
        if limits.audio_kbps_min and sound["kbps"] < limits.audio_kbps_min:
            problems.append(f"the sound is {sound['kbps']} kbps, at least {limits.audio_kbps_min} are needed")
        ceiling = ai_config.short_clips_settings().video.delivery.peak_ceiling
        if sound["true_peak_db"] is not None and sound["true_peak_db"] > ceiling + 0.05:
            problems.append(f"the sound peaks at {sound['true_peak_db']:.1f} dB, above the {ceiling} dB ceiling (it can clip when the platform re-encodes it)")
    return problems
