"""Deterministic local short-form export used by the pilot and review UI.

The real voice provider can replace the synthetic tone later; the package
contract stays the same, which keeps review and gate behavior testable offline.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin, ffprobe_bin

ASS_CANVAS_WIDTH, ASS_CANVAS_HEIGHT = 384, 288
# libass style Alignment (the SSA numbering): 2 = bottom centre (captions), 6 = top centre (the label)
CAPTION_ALIGNMENT, LABEL_ALIGNMENT = 2, 6
WIDTH = ai_config.short_form.production.width
HEIGHT = ai_config.short_form.production.height
SAFE_X = 96
SAFE_TOP = 180
SAFE_BOTTOM = 330
ROOT = Path(__file__).resolve().parents[2]
FFMPEG_CAPTION_HINT = (
    "FFmpeg lacks libass/subtitles or its RTL shaping: install an ffmpeg built with libass, fribidi and harfbuzz "
    "(Homebrew: ffmpeg-full; Debian/Ubuntu: the ffmpeg package) and, if it is not the one on PATH, set FFMPEG_BIN and "
    "FFPROBE_BIN or short_form.production.ffmpeg_candidates / ffprobe_candidates"
)
# libass logs, when it starts (ffmpeg -v verbose): "Shaper: FriBidi 1.0.16 (SIMPLE) HarfBuzz-ng 14.6.0 (COMPLEX)"
_SHAPER = re.compile(r"Shaper: FriBidi (\S+).*HarfBuzz-ng (\S+)")


class FFmpegCapabilityError(RuntimeError):
    """Raised when a production render cannot burn its required captions."""


def ffmpeg_caption_capability() -> dict:
    """Inspect the executable that production rendering will invoke."""
    executable = ffmpeg_bin()
    probe = ffprobe_bin()
    if executable is None:
        return {"available": False, "executable": None, "ffprobe": probe,
                "subtitles_filter": False, "ass_filter": False,
                "libass": False, "reason": "ffmpeg is not installed",
                "fix": FFMPEG_CAPTION_HINT}
    try:
        filters = subprocess.run([executable, "-filters"], capture_output=True, text=True, check=True).stdout
        version = subprocess.run(
            [executable, "-version"], capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"available": False, "executable": executable, "ffprobe": probe,
                "subtitles_filter": False, "ass_filter": False,
                "libass": False, "reason": f"ffmpeg capability probe failed: {exc}",
                "fix": FFMPEG_CAPTION_HINT}
    has_subtitles = any(" subtitles " in f" {line} " for line in filters.splitlines())
    ass_filter = any(" ass " in f" {line} " for line in filters.splitlines())
    libass = "--enable-libass" in version
    shaper = _libass_shaper(executable) if has_subtitles and libass else None
    harfbuzz = bool(shaper and shaper["harfbuzz"])
    fribidi = bool(shaper and shaper["fribidi"])
    available = has_subtitles and ass_filter and libass and harfbuzz and fribidi and bool(probe)
    return {
        "available": available,
        "executable": executable,
        "ffprobe": probe,
        "subtitles_filter": has_subtitles,
        "ass_filter": ass_filter,
        "libass": libass,
        "harfbuzz": harfbuzz,
        "fribidi": fribidi,
        "reason": None if available else "FFmpeg caption capability or RTL shaping dependency is unavailable",
        "fix": None if available else FFMPEG_CAPTION_HINT,
    }


def _libass_shaper(executable: str) -> dict | None:
    """The text shaper libass runs with in this ffmpeg, read from what libass logs when it starts:
    the same on every platform (no looking at library files). None: libass did not start."""
    with tempfile.TemporaryDirectory() as folder:
        srt = Path(folder) / "probe.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nشبکه\n", encoding="utf-8")
        run = subprocess.run(
            [executable, "-hide_banner", "-v", "verbose", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.2",
             "-vf", f"subtitles=filename='{_filter_path(srt)}'", "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, check=False)
    found = _SHAPER.search(run.stderr)
    return {"fribidi": found.group(1), "harfbuzz": found.group(2)} if found else None


def require_caption_capability() -> dict:
    capability = ffmpeg_caption_capability()
    if not capability["available"]:
        raise FFmpegCapabilityError(capability["reason"] + ". " + capability["fix"])
    return capability


@dataclass(frozen=True)
class SubtitleCue:
    start: float
    end: float
    text: str


def subtitle_layout(language: str, cues: list[SubtitleCue]) -> dict:
    """Return layout evidence and reject cues outside the platform safe zone."""
    rtl = language in {"fa", "ar"}
    lines = [cue.text for cue in cues]
    if any(len(line) > 42 for line in lines):
        raise ValueError("subtitle line exceeds 42 characters")
    return {
        "language": language,
        "direction": "rtl" if rtl else "ltr",
        "font": caption_font(language)["file"],
        "safe_area": {"left": SAFE_X, "right": WIDTH - SAFE_X,
                      "top": SAFE_TOP, "bottom": HEIGHT - SAFE_BOTTOM},
        "cues": [cue.__dict__ for cue in cues],
    }


def _font(language: str, size: int):
    path = Path(caption_font(language)["file"])
    try:
        return ImageFont.truetype(str(path), size, index=0)
    except OSError:
        return ImageFont.load_default()


def caption_font(language: str) -> dict:
    """The font of a language's captions: the first of short_form.production.caption_fonts that exists,
    {"file": absolute path, "family": the name libass asks for, "bundled": it lies inside the repository}."""
    entries = ai_config.short_form.production.caption_fonts.get(language)
    for entry in entries or []:
        path = Path(entry.file).expanduser()
        path = path if path.is_absolute() else ROOT / path
        if path.is_file():
            return {"file": str(path), "family": entry.family, "bundled": ROOT in path.parents}
    raise FFmpegCapabilityError(
        f"No caption font with {language} coverage exists: none of short_form.production.caption_fonts[{language!r}] "
        "is a file on this machine; install one of them or add a font to the list"
    )


def _caption_font_family(language: str) -> str:
    return caption_font(language)["family"]  # fails closed before FFmpeg is invoked


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def subtitles_filter(srt: Path, language: str, force_style: str) -> str:
    """The libass filter that burns `srt` in. A font that ships with the repository is found by its own
    folder (fontsdir): libass otherwise looks only in the system fonts."""
    font = caption_font(language)
    fonts_dir = f":fontsdir='{_filter_path(Path(font['file']).parent)}'" if font["bundled"] else ""
    return f"subtitles=filename='{_filter_path(srt)}'{fonts_dir}:force_style='{force_style}'"


def _write_cover(path: Path, title: str, language: str,
                 subtitle_preview: str = "") -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), (14, 19, 27))
    draw = ImageDraw.Draw(image)
    draw.rectangle((SAFE_X, SAFE_TOP, WIDTH - SAFE_X, HEIGHT - SAFE_BOTTOM),
                   outline=(211, 164, 78), width=4)
    draw.text((SAFE_X + 48, SAFE_TOP + 60), "TRUECRIME / SHORT",
              fill=(211, 164, 78), font=_font("en", 38))
    _multiline(draw, (SAFE_X + 48, 620), title, fill=(245, 241, 231),
               font=_font(language, 78), spacing=16, language=language)
    draw.text((SAFE_X + 48, HEIGHT - SAFE_BOTTOM - 80), "AI-assisted narration",
              fill=(180, 186, 198), font=_font("en", 30))
    if subtitle_preview:
        _multiline(draw, (SAFE_X + 48, 1260), subtitle_preview,
                   fill=(245, 241, 231), font=_font(language, 42),
                   spacing=12, language=language)
    image.save(path)


def _multiline(draw: ImageDraw.ImageDraw, xy, text: str, *, fill, font,
               spacing: int, language: str) -> None:
    """Draw RTL cover text when Pillow has libraqm, with a safe local fallback."""
    kwargs = {"fill": fill, "font": font, "spacing": spacing}
    if language in {"fa", "ar"}:
        kwargs["direction"] = "rtl"
    try:
        draw.multiline_text(xy, text, **kwargs)
    except (KeyError, ValueError):
        # The video captions still go through libass in FFmpeg; this fallback
        # keeps package generation working on minimal Pillow installations.
        kwargs.pop("direction", None)
        draw.multiline_text(xy, text, **kwargs)


def _write_srt(path: Path, cues: list[SubtitleCue]) -> None:
    def stamp(seconds: float) -> str:
        ms = round(seconds * 1000)
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    path.write_text("\n\n".join(
        f"{i}\n{stamp(c.start)} --> {stamp(c.end)}\n{c.text}"
        for i, c in enumerate(cues, 1)
    ) + "\n", encoding="utf-8")


def build_export_package(
    output_dir: Path,
    *,
    language: str = "en",
    title: str = "The night the signal stopped",
    narration: list[str] | None = None,
    duration_seconds: float = 8,
    rights_status: str = "cleared",
    rights_source: str = "synthetic pilot background",
    rights_human_signoff: bool = False,
    source_video: str | Path | None = None,
    audio_source: str | Path | None = None,
    source_start_seconds: float = 0.0,
    captions_burned_in: bool | None = None,
    cues: list[dict] | None = None,
    labels: list[dict] | None = None,
) -> dict:
    """Build a small, inspectable, watchable pilot package offline."""
    executable = ffmpeg_bin()
    if executable is None:
        raise RuntimeError("ffmpeg is required for short-form export")
    if duration_seconds <= 0 or duration_seconds > 60:
        raise ValueError("duration_seconds must be between 0 and 60")
    output_dir.mkdir(parents=True, exist_ok=True)
    default_narration = [
        "The last signal came after midnight.",
        "What happened next is still disputed.",
        "Watch the full evidence-led story.",
    ]
    words = narration or default_narration
    if not words or any(not str(text).strip() for text in words):
        raise ValueError("narration must contain non-empty text")
    words = [str(text).strip()[:42] for text in words]
    step = duration_seconds / len(words)
    # the film's own subtitle cues of the window (real times) when given, else the words spread evenly
    subtitle_cues = [SubtitleCue(float(c["start"]), float(c["end"]), str(c["text"]).strip())
                     for c in cues or [] if str(c.get("text") or "").strip()] or [
        SubtitleCue(
            max(0.1, index * step + 0.1),
            min(duration_seconds - 0.1, (index + 1) * step - 0.1),
            str(text).strip(),
        )
        for index, text in enumerate(words)
    ]
    layout = subtitle_layout(language, subtitle_cues)
    cover = output_dir / "cover.png"
    subtitles = output_dir / "subtitles.srt"
    video = output_dir / "short.mp4"
    _write_cover(cover, title, language, "\n".join(cue.text for cue in subtitle_cues[:3]))
    _write_srt(subtitles, subtitle_cues)
    source = Path(source_video) if source_video else None
    audio = Path(audio_source) if audio_source else None
    burned = bool(ai_config.short_form.production.captions_burned_in
                  if captions_burned_in is None else captions_burned_in)
    if source is not None and not source.is_file():
        raise FileNotFoundError(f"long-film source is missing: {source}")
    if source and source.is_file() and burned:
        require_caption_capability()
    inputs = [executable, "-y", "-v", "error"]
    if source and source.is_file():
        if source_start_seconds > 0:
            inputs += ["-ss", str(source_start_seconds)]
        inputs += ["-i", str(source)]
        separate_audio = bool(audio and audio.is_file() and audio.resolve() != source.resolve())
        if separate_audio:
            inputs += ["-i", str(audio)]
        else:
            audio = source
        video_filter = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}"
        if burned:
            # libass handles Arabic/Persian shaping and bidi ordering. The
            # capability check above makes this a fail-closed requirement.
            prod = ai_config.short_form.production
            # libass sets an SRT to a 384x288 canvas: pixel sizes of the 1080x1920 frame are
            # converted to that canvas (a size or margin written in frame pixels would be
            # several times too large there)
            across, down = ASS_CANVAS_WIDTH / WIDTH, ASS_CANVAS_HEIGHT / HEIGHT
            force_style = (f"FontName={_caption_font_family(language)},Alignment={CAPTION_ALIGNMENT},"
                           f"FontSize={prod.caption_font_px * down:.2f},"
                           f"MarginL={round(prod.caption_side_margin_px * across)},"
                           f"MarginR={round(prod.caption_side_margin_px * across)},"
                           f"MarginV={round(prod.caption_bottom_margin_px * down)},"
                           f"Outline={3 * down:.2f},Shadow=0")
            video_filter += "," + subtitles_filter(subtitles, language, force_style)
            if labels:
                # the "Illustration" label of the film sits in a corner the vertical crop cuts off:
                # it is burned in again, top centre, for as long as an illustration is on screen
                label_srt = output_dir / "labels.srt"
                _write_srt(label_srt, [SubtitleCue(float(x["start"]), float(x["end"]), str(x["text"]))
                                       for x in labels])
                label_style = (f"FontName={_caption_font_family(language)},Alignment={LABEL_ALIGNMENT},"
                               f"FontSize={prod.caption_font_px * down * 0.6:.2f},"
                               f"MarginV={round(SAFE_TOP * 1.1 * down)},Outline={3 * down:.2f},Shadow=0")
                video_filter += "," + subtitles_filter(label_srt, language, label_style)
        command = inputs + ["-t", str(duration_seconds), "-vf", video_filter,
                            "-map", "0:v:0", "-map", "1:a:0" if separate_audio else "0:a:0",
                            "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-shortest", str(video)]
        subprocess.run(command, check=True)
    else:
        # This is a package-level preview with no film source, not a production
        # Shorts render. It has no claim of burned-in captions.
        burned = False
        subprocess.run([
            executable, "-y", "-v", "error", "-loop", "1", "-i", str(cover),
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", str(duration_seconds), "-r", "30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
        ], check=True)
    manifest = {
        "schema": "short-form-export-v1",
        "video": video.name,
        "cover": cover.name,
        "subtitles": subtitles.name,
        "width": WIDTH,
        "height": HEIGHT,
        "duration_seconds": duration_seconds,
        "language": language,
        "subtitle_layout": layout,
        "captions_burned_in": burned,
        "subtitle_render_mode": "burned_in_plus_timed_srt" if burned else "static_preview_plus_timed_srt",
        "disclosure": {"required": True, "label": "AI-assisted narration",
                        "platforms": ["youtube", "tiktok", "meta"]},
        "rights": {"status": rights_status, "source": rights_source,
                    "human_signoff": rights_human_signoff},
        "audio": {"status": "language_source" if source and source.is_file() else "synthetic_silence",
                   "original_media_segments": 1 if source and source.is_file() else 0,
                   "narrator_audio": str(audio) if audio and audio.is_file() else "unavailable",
                   "language": language},
        "review": {"status": "pending", "caption": title,
                   "cta": "Watch the full evidence-led story.",
                   "destination": "episode://pilot"},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
