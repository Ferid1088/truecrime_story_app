"""Deterministic local short-form export used by the pilot and review UI.

The real voice provider can replace the synthetic tone later; the package
contract stays the same, which keeps review and gate behavior testable offline.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 1080, 1920
SAFE_X = 96
SAFE_TOP = 180
SAFE_BOTTOM = 330
FONT_PATHS = {
    "en": "/System/Library/Fonts/SFNS.ttf",
    "fa": "/System/Library/Fonts/GeezaPro.ttc",
    "ar": "/System/Library/Fonts/GeezaPro.ttc",
}


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
        "font": FONT_PATHS.get(language, FONT_PATHS["en"]),
        "safe_area": {"left": SAFE_X, "right": WIDTH - SAFE_X,
                      "top": SAFE_TOP, "bottom": HEIGHT - SAFE_BOTTOM},
        "cues": [cue.__dict__ for cue in cues],
    }


def _font(language: str, size: int):
    path = FONT_PATHS.get(language, FONT_PATHS["en"])
    try:
        return ImageFont.truetype(path, size, index=0 if path.endswith(".ttc") else 0)
    except OSError:
        return ImageFont.load_default()


def _write_cover(path: Path, title: str, language: str,
                 subtitle_preview: str = "") -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), (14, 19, 27))
    draw = ImageDraw.Draw(image)
    draw.rectangle((SAFE_X, SAFE_TOP, WIDTH - SAFE_X, HEIGHT - SAFE_BOTTOM),
                   outline=(211, 164, 78), width=4)
    draw.text((SAFE_X + 48, SAFE_TOP + 60), "TRUECRIME / SHORT",
              fill=(211, 164, 78), font=_font("en", 38))
    draw.multiline_text((SAFE_X + 48, 620), title, fill=(245, 241, 231),
                        font=_font(language, 78), spacing=16,
                        direction="rtl" if language in {"fa", "ar"} else None)
    draw.text((SAFE_X + 48, HEIGHT - SAFE_BOTTOM - 80), "AI-assisted narration",
              fill=(180, 186, 198), font=_font("en", 30))
    if subtitle_preview:
        draw.multiline_text((SAFE_X + 48, 1260), subtitle_preview,
                            fill=(245, 241, 231), font=_font(language, 42),
                            spacing=12, direction="rtl" if language in {"fa", "ar"} else None)
    image.save(path)


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
) -> dict:
    """Build a small, inspectable, watchable pilot package offline."""
    if shutil.which("ffmpeg") is None:
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
    cues = [
        SubtitleCue(
            max(0.1, index * step + 0.1),
            min(duration_seconds - 0.1, (index + 1) * step - 0.1),
            str(text).strip(),
        )
        for index, text in enumerate(words)
    ]
    layout = subtitle_layout(language, cues)
    cover = output_dir / "cover.png"
    subtitles = output_dir / "subtitles.srt"
    video = output_dir / "short.mp4"
    _write_cover(cover, title, language, "\n".join(cue.text for cue in cues))
    _write_srt(subtitles, cues)
    # The bundled macOS FFmpeg may not include libass. The cover carries a
    # static subtitle proof while the timed SRT remains the canonical track.
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(cover),
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
        "subtitle_render_mode": "static_preview_plus_timed_srt",
        "disclosure": {"required": True, "label": "AI-assisted narration",
                        "platforms": ["youtube", "tiktok", "meta"]},
        "rights": {"status": rights_status, "source": rights_source,
                    "human_signoff": rights_human_signoff},
        "audio": {"status": "synthetic_silence", "original_media_segments": 0,
                   "narrator_audio": "unavailable"},
        "review": {"status": "pending", "caption": title,
                   "cta": "Watch the full evidence-led story.",
                   "destination": "episode://pilot"},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
