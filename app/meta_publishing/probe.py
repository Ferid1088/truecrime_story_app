"""What a video file is, read with ffprobe (read-only) and by walking the MP4 box headers (read-only)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from app.core.media_tools import ffprobe_bin

Runner = Callable[[list[str], int], str]
_SCAN_BYTES = 4 * 1024 * 1024
_MAX_MOOV = 64 * 1024 * 1024


class ProbeError(RuntimeError):
    """ffprobe is missing, failed, timed out or returned something that is not a media description."""


def run_ffprobe(args: list[str], timeout: int) -> str:
    binary = ffprobe_bin()
    if not binary:
        raise ProbeError("ffprobe is not available")
    try:
        result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out after {timeout} s") from exc
    except OSError as exc:
        raise ProbeError(f"ffprobe could not be started: {exc}") from exc
    if result.returncode != 0:
        raise ProbeError(f"ffprobe failed: {result.stderr.strip()[-300:] or 'no message'}")
    return result.stdout


@dataclass(frozen=True)
class ContainerFacts:
    """From the box headers: where the moov atom is, whether it holds an edit list, and what the encoder wrote into the first frames."""

    moov_before_mdat: bool | None
    has_edit_list: bool | None
    open_gop: bool | None


@dataclass(frozen=True)
class MediaFacts:
    path: Path
    size_bytes: int
    format_names: list[str]
    duration: float
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    pix_fmt: str | None = None
    field_order: str | None = None
    fps: float | None = None
    fps_constant: bool | None = None
    video_bitrate: int | None = None
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    audio_bitrate: int | None = None
    audio_initial_padding: int | None = None
    color: dict[str, str] = field(default_factory=dict)
    keyframes: list[float] = field(default_factory=list)
    container: ContainerFacts = ContainerFacts(None, None, None)

    @property
    def aspect(self) -> float | None:
        return self.width / self.height if self.width and self.height else None

    @property
    def max_keyframe_gap(self) -> float | None:
        if not self.keyframes:
            return None
        points = [*self.keyframes, self.duration]
        return max(b - a for a, b in zip(points, points[1:], strict=False))


def _fraction(text: str | None) -> float | None:
    try:
        value = Fraction(text or "0")
    except (ValueError, ZeroDivisionError):
        return None
    return float(value) if value > 0 else None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def container_facts(path: Path) -> ContainerFacts:
    """Walk the top-level boxes: is `moov` before `mdat`, does `moov` hold an `elst` (edit list), does the encoder's SEI say open_gop. Any file that is not an ISO-BMFF file gives unknowns."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            offset, order, moov = 0, [], b""
            while offset + 8 <= size:
                f.seek(offset)
                head = f.read(16)
                box_size, kind = int.from_bytes(head[:4], "big"), head[4:8]
                if box_size == 1:
                    box_size = int.from_bytes(head[8:16], "big")
                elif box_size == 0:
                    box_size = size - offset
                if box_size < 8 or not kind.isalpha():
                    return ContainerFacts(None, None, None)
                order.append(kind)
                if kind == b"moov" and box_size <= _MAX_MOOV:
                    f.seek(offset)
                    moov = f.read(box_size)
                offset += box_size
    except OSError:
        return ContainerFacts(None, None, None)
    if b"ftyp" not in order:
        return ContainerFacts(None, None, None)
    before = (order.index(b"moov") < order.index(b"mdat")) if b"moov" in order and b"mdat" in order else None
    try:
        window = _first_frames(path)
    except OSError:
        window = b""
    open_gop = True if b"open_gop=1" in window else False if b"open_gop=0" in window else None
    return ContainerFacts(before, (b"elst" in moov) if moov else None, open_gop)


def _first_frames(path: Path) -> bytes:
    """The bytes where the first video frame (and the x264 SEI with the encoder's settings) lies: the start of the file's first `mdat`."""
    with path.open("rb") as f:
        size, offset = path.stat().st_size, 0
        while offset + 8 <= size:
            f.seek(offset)
            head = f.read(16)
            box_size, kind = int.from_bytes(head[:4], "big"), head[4:8]
            if box_size == 1:
                box_size = int.from_bytes(head[8:16], "big")
            elif box_size == 0:
                box_size = size - offset
            if box_size < 8:
                return b""
            if kind == b"mdat":
                f.seek(offset)
                return f.read(min(_SCAN_BYTES, box_size))
            offset += box_size
    return b""


class Prober:
    """Reads a file's media facts. `runner` is injectable so that ffprobe failures can be tested."""

    def __init__(self, timeout: int, runner: Runner = run_ffprobe):
        self.timeout, self.runner = timeout, runner

    def _json(self, args: list[str]) -> dict:
        try:
            data = json.loads(self.runner([*args, "-of", "json"], self.timeout))
        except json.JSONDecodeError as exc:
            raise ProbeError("ffprobe returned output that is not JSON") from exc
        if not isinstance(data, dict):
            raise ProbeError("ffprobe returned an unexpected document")
        return data

    def probe(self, path: Path) -> MediaFacts:
        if not path.is_file():
            raise ProbeError(f"file not found: {path}")
        info = self._json(["-v", "error", "-show_format", "-show_streams", str(path)])
        fmt, streams = info.get("format") or {}, info.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        try:
            duration = float(fmt.get("duration"))
        except (TypeError, ValueError) as exc:
            raise ProbeError("ffprobe reported no duration: not a readable media file") from exc
        facts = dict(path=path, size_bytes=path.stat().st_size, format_names=str(fmt.get("format_name") or "").split(","), duration=duration,
                     container=container_facts(path))
        if video is not None:
            real, average = _fraction(video.get("r_frame_rate")), _fraction(video.get("avg_frame_rate"))
            video_bitrate = _int(video.get("bit_rate")) or ((_int(fmt.get("bit_rate")) or 0) - (_int(audio.get("bit_rate")) if audio else 0) or None)
            facts.update(width=_int(video.get("width")), height=_int(video.get("height")), video_codec=video.get("codec_name"), pix_fmt=video.get("pix_fmt"),
                         field_order=video.get("field_order"), fps=average or real, video_bitrate=video_bitrate,
                         fps_constant=(abs(real - average) < 0.01 * real) if real and average else None,
                         color={k: video[k] for k in ("color_range", "color_space", "color_transfer", "color_primaries") if video.get(k) not in (None, "unknown")},
                         keyframes=self._keyframes(path))
        if audio is not None:
            facts.update(audio_codec=audio.get("codec_name"), audio_sample_rate=_int(audio.get("sample_rate")), audio_channels=_int(audio.get("channels")),
                         audio_bitrate=_int(audio.get("bit_rate")), audio_initial_padding=_int(audio.get("initial_padding")))
        return MediaFacts(**facts)

    def _keyframes(self, path: Path) -> list[float]:
        data = self._json(["-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries", "frame=pts_time", str(path)])
        times = [float(f["pts_time"]) for f in data.get("frames") or [] if f.get("pts_time") not in (None, "N/A")]
        return sorted(times)
