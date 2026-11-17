"""The preflight checks of one video against one platform's requirements, and the verdict. Nothing here touches a file or the network: it judges MediaFacts."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from app.meta_publishing.config import PlatformSpec
from app.meta_publishing.probe import MediaFacts

_ASPECT_TOLERANCE = 0.01
_REENCODE = "Re-encode (a new copy; the original stays untouched)"


class Verdict(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Check:
    name: str
    level: str                 # confirmed | recommended | unverified (the evidence behind the rule)
    verdict: Verdict
    actual: str
    expected: str
    action: str | None = None


@dataclass
class PlatformReport:
    platform: str
    label: str
    checks: list[Check] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        if any(c.verdict is Verdict.FAIL for c in self.checks):
            return Verdict.FAIL
        return Verdict.WARNING if any(c.verdict is Verdict.WARNING for c in self.checks) else Verdict.PASS

    def by(self, verdict: Verdict) -> list[Check]:
        return [c for c in self.checks if c.verdict is verdict]

    def actions(self) -> list[str]:
        return list(dict.fromkeys(c.action for c in self.checks if c.action and c.verdict is not Verdict.PASS))


def _shown(value) -> str:
    return "none" if value is None else f"{value:.3f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


class PlatformValidator:
    """A rule that fails is a FAIL when Meta states it as a requirement (`confirmed`); a failed recommendation or a rule Meta's page does not state (`unverified`) is a WARNING.
    A fact the file does not let us read is a WARNING, never a PASS."""

    def __init__(self, platform: str, spec: PlatformSpec, gop_tolerance: float):
        self.platform, self.spec, self.gop_tolerance = platform, spec, gop_tolerance
        self.checks: list[Check] = []

    def _add(self, key: str, name: str, ok: bool | None, actual, expected: str, action: str, *, unreadable: str | None = None) -> None:
        level = self.spec.levels[key]
        if ok is None:
            self.checks.append(Check(name, level, Verdict.WARNING, unreadable or "not readable from the file", expected, action))
        elif ok:
            self.checks.append(Check(name, level, Verdict.PASS, _shown(actual), expected))
        else:
            verdict = Verdict.FAIL if level == "confirmed" else Verdict.WARNING
            self.checks.append(Check(name, level, verdict, _shown(actual), expected, action))

    def validate(self, facts: MediaFacts) -> PlatformReport:
        self.checks = []
        s = self.spec
        self._container(facts)
        self._video(facts)
        self._audio(facts)
        self._gop(facts)
        self._file(facts)
        return PlatformReport(self.platform, s.label, self.checks)

    def _container(self, f: MediaFacts) -> None:
        s = self.spec
        kinds = {"mov": "mov" in f.format_names, "mp4": "mp4" in f.format_names}
        found = [k for k in s.containers if kinds.get(k)]
        self._add("containers", "container", bool(found), "/".join(f.format_names), " or ".join(s.containers).upper(), f"{_REENCODE} into {'/'.join(s.containers).upper()}")
        c = f.container
        self._add("faststart", "moov atom at the front", c.moov_before_mdat, "moov first" if c.moov_before_mdat else "moov last", "moov before mdat",
                  "Write a new copy with -movflags +faststart (container-only change)", unreadable="not an MP4/MOV box layout")
        self._add("edit_list", "no edit list", None if c.has_edit_list is None else not c.has_edit_list, "edit list present" if c.has_edit_list else "none", "no edit lists",
                  "Write a new copy with -use_editlist 0 (container-only change if streams are copied; unverified that this keeps audio/video sync, so re-check the copy)", unreadable="moov atom not readable")

    def _video(self, f: MediaFacts) -> None:
        s = self.spec
        if f.video_codec is None:
            self._add("video_codecs", "video stream", False, "no video stream", "one video stream", "The file has no video: it cannot be a Reel")
            return
        self._add("video_codecs", "video codec", f.video_codec in s.video_codecs, f.video_codec, ", ".join(s.video_codecs), f"{_REENCODE} to H.264 (libx264)")
        self._add("pix_fmts", "pixel format / chroma", f.pix_fmt in s.pix_fmts, f.pix_fmt, "4:2:0 (" + ", ".join(s.pix_fmts) + ")", f"{_REENCODE} with -pix_fmt yuv420p")
        self._add("pix_fmts", "progressive scan", f.field_order in (None, "progressive", "unknown"), f.field_order or "progressive", "progressive", f"{_REENCODE} deinterlaced")
        self._add("fps", "frame rate", None if f.fps is None else s.min_fps <= f.fps <= s.max_fps, f.fps, f"{s.min_fps:g} to {s.max_fps:g} fps",
                  f"{_REENCODE} at a frame rate of {s.min_fps:g} to {s.max_fps:g}", unreadable="no frame rate")
        if f.fps_constant is False:
            self.checks.append(Check("constant frame rate", s.levels["fps"], Verdict.WARNING if s.levels["fps"] != "confirmed" else Verdict.FAIL, "variable", "fixed frame rate",
                                     f"{_REENCODE} with a constant frame rate (-vsync cfr)"))
        if s.max_width is not None:
            self._add("max_width", "width", None if f.width is None else f.width <= s.max_width, f.width, f"at most {s.max_width} px", f"{_REENCODE} scaled to at most {s.max_width} px wide")
        a = f.aspect
        self._add("aspect_range", "aspect ratio (width/height)", None if a is None else s.min_aspect - _ASPECT_TOLERANCE <= a <= s.max_aspect + _ASPECT_TOLERANCE, a,
                  f"{s.min_aspect:g} to {s.max_aspect:g}", f"{_REENCODE} to a 9:16 frame (crop or pad)")
        w, h = s.recommended_aspect
        self._add("recommended_aspect", "aspect ratio 9:16", None if a is None else abs(a - w / h) <= _ASPECT_TOLERANCE, f"{f.width}x{f.height}", f"{w}:{h}",
                  f"{_REENCODE} to 9:16 so that nothing is cropped or padded by the platform")
        if s.recommended_min_width and s.recommended_min_height:
            self._add("resolution_floor", "resolution", None if f.width is None or f.height is None else f.width >= s.recommended_min_width and f.height >= s.recommended_min_height,
                      f"{f.width}x{f.height}", f"at least {s.recommended_min_width}x{s.recommended_min_height}", f"{_REENCODE} at {s.recommended_min_width}x{s.recommended_min_height} or larger")
        if s.max_video_bitrate_bps is not None:
            self._add("video_bitrate", "video bitrate", None if f.video_bitrate is None else f.video_bitrate <= s.max_video_bitrate_bps, f.video_bitrate,
                      f"at most {s.max_video_bitrate_bps} bit/s", f"{_REENCODE} with a lower bitrate (-maxrate/-bufsize)")

    def _audio(self, f: MediaFacts) -> None:
        s = self.spec
        if f.audio_codec is None:
            self._add("audio_codecs", "audio stream", False, "no audio stream", "one audio stream", f"{_REENCODE} with an audio track (AAC, {s.max_audio_sample_rate} Hz, stereo); the clip has no sound")
            return
        self._add("audio_codecs", "audio codec", f.audio_codec in s.audio_codecs, f.audio_codec, ", ".join(s.audio_codecs), f"{_REENCODE} with AAC audio")
        self._add("audio_sample_rate", "audio sample rate", None if f.audio_sample_rate is None else f.audio_sample_rate <= s.max_audio_sample_rate, f.audio_sample_rate,
                  f"at most {s.max_audio_sample_rate} Hz", f"{_REENCODE} with -ar {s.max_audio_sample_rate}")
        self._add("audio_channels", "audio channels", f.audio_channels in s.audio_channels, f.audio_channels, " or ".join(map(str, s.audio_channels)), f"{_REENCODE} with -ac {s.audio_channels[-1]}")
        self._add("audio_bitrate", "audio bitrate", None if f.audio_bitrate is None else f.audio_bitrate >= s.min_audio_bitrate_bps * 0.95, f.audio_bitrate,
                  f"{s.min_audio_bitrate_bps} bit/s", f"{_REENCODE} with -b:a {s.min_audio_bitrate_bps // 1000}k")

    def _gop(self, f: MediaFacts) -> None:
        s = self.spec
        if f.video_codec is None:
            return
        gap = f.max_keyframe_gap
        if s.max_gop_seconds is not None:
            self._add("gop", "keyframe interval (GOP)", None if gap is None else gap <= s.max_gop_seconds + self.gop_tolerance, gap, f"every {s.max_gop_seconds:g} s at most",
                      f"{_REENCODE} with a keyframe at least every {s.max_gop_seconds:g} s (for example libx264 -g {math.floor(s.max_gop_seconds * (f.fps or 30) * 0.9)} -sc_threshold 0 "
                      "or -force_key_frames 'expr:gte(t,n_forced*2)'). A remux (-c copy) cannot move keyframes",
                      unreadable="no keyframe list")
        elif gap is not None:
            self.checks.append(Check("keyframe interval (GOP)", s.levels["gop"], Verdict.PASS, f"{gap:.2f} s longest (Meta states no limit)", "no stated limit"))
        o = f.container.open_gop
        self._add("closed_gop", "closed GOP", None if o is None else not o, "open GOP" if o else "closed", "closed GOP",
                  f"{_REENCODE} as closed GOP (libx264 default; no -x264-params open-gop=1)",
                  unreadable="the encoder did not record its GOP mode; ffprobe cannot tell")

    def _file(self, f: MediaFacts) -> None:
        s = self.spec
        self._add("duration", "duration", s.min_seconds <= f.duration <= s.max_seconds, f.duration, f"{s.min_seconds:g} to {s.max_seconds:g} s",
                  "Make a clip of the allowed length (a new script/render); a re-encode cannot fix the length")
        if s.max_bytes is not None:
            self._add("max_bytes", "file size", f.size_bytes <= s.max_bytes, f.size_bytes, f"at most {s.max_bytes} bytes", f"{_REENCODE} with a lower bitrate")
