"""The video of a short clip, 1080x1920: what is on screen when, and the ffmpeg command that makes it.

Timeline (seconds): the hook (the avatar, from the first frame), the context and the detail (the film's pictures, each a few seconds, slowly zoomed), the reaction
and the invitation (the avatar again). The voices follow one another with a short gap; the music bed starts when the hook ends and lies under the voice; the words
are burned in from the first word, a few at a time. The avatar's picture is the film's own look, cropped to the vertical frame around the host's face."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from app.core.ai_config import ClipVideoConfig, ai_config
from app.core.media_tools import ffmpeg_bin
from app.covers.compose import display_title
from app.documentary.visuals import typography as TY

ORDER = ("hook", "context", "detail", "reaction", "cta")


class ClipVideoError(RuntimeError):
    pass


@dataclass(frozen=True)
class Plan:
    starts: dict[str, float]
    seconds: dict[str, float]
    segments: list[dict]
    total: float
    music_start: float


def plan_timeline(seconds: dict[str, float], cfg: ClipVideoConfig | None = None) -> Plan:
    """The start of every voice and the three picture segments: the avatar (hook), the film's pictures (context and detail), the avatar (reaction and invitation)."""
    cfg = cfg or ai_config.short_clips_settings().video
    starts, t = {}, 0.0
    for part in ORDER:
        starts[part] = round(t, 3)
        t += seconds[part] + cfg.gap_seconds
    total = round(starts["cta"] + seconds["cta"] + cfg.tail_seconds, 3)
    segments = [{"kind": "avatar", "part": "hook", "start": 0.0, "end": starts["context"]},
                {"kind": "pictures", "start": starts["context"], "end": starts["reaction"]},
                {"kind": "avatar", "part": "reaction", "start": starts["reaction"], "end": total}]
    return Plan(starts, dict(seconds), segments, total, round(seconds["hook"], 3))


def picture_count(seconds: float, cfg: ClipVideoConfig | None = None) -> int:
    """How many pictures fill the middle: each stays between the configured minimum and maximum seconds."""
    cfg = cfg or ai_config.short_clips_settings().video
    low, high = cfg.picture_seconds
    best = max(1, round(seconds / ((low + high) / 2)))
    return max(1, min(best, cfg.pictures_max, math.floor(seconds / low) or 1))


def chunks(voices: dict[str, dict], plan: Plan, cfg: ClipVideoConfig | None = None) -> list[dict]:
    """The caption chunks: `words_per_chunk` words each, from the first word, with the time the voice says them (the times of the words of every part, moved to its start)."""
    cfg = cfg or ai_config.short_clips_settings().video
    out = []
    for part in ORDER:
        words = voices[part].get("words") or []
        for i in range(0, len(words), cfg.caption.words_per_chunk):
            group = words[i:i + cfg.caption.words_per_chunk]
            start = plan.starts[part] + group[0][1]
            end = plan.starts[part] + group[-1][2]
            nxt = words[i + cfg.caption.words_per_chunk] if i + cfg.caption.words_per_chunk < len(words) else None
            out.append({"text": " ".join(w[0] for w in group), "start": round(start, 3), "end": round(plan.starts[part] + nxt[1] if nxt else end + 0.25, 3)})
    return out


def caption_image(text: str, language: str, path: Path, cfg: ClipVideoConfig | None = None) -> tuple[int, int]:
    """One caption as a transparent picture the width of the frame (white words with a dark outline); returns its size."""
    cfg = cfg or ai_config.short_clips_settings().video
    c = cfg.caption
    size = round(c.size * cfg.height)
    font = TY.font(language, "sans", "bold", size)
    lines = TY.wrap(display_title(text, language), font, language, round(c.max_width * cfg.width))
    line_h = round(size * 1.25)
    height = line_h * len(lines) + size
    image = Image.new("RGBA", (cfg.width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    stroke = max(1, round(size * c.stroke_fraction))
    for i, line in enumerate(lines):
        TY.draw_text(draw, (cfg.width / 2, size / 2 + i * line_h), line, font, language, fill=c.ink, anchor="ma", stroke_width=stroke, stroke_fill=c.stroke)
    image.save(path)
    return image.size


def avatar_crop(frame_size: tuple[int, int], face_center_x: float, cfg: ClipVideoConfig | None = None) -> tuple[int, int]:
    """(width, left) of the vertical window cut out of the avatar's landscape frame: the full height, centred on the host's face and kept inside the frame."""
    cfg = cfg or ai_config.short_clips_settings().video
    width = round(frame_size[1] * cfg.width / cfg.height * cfg.avatar_crop_width_fraction)
    width -= width % 2
    left = min(max(round(face_center_x - width / 2), 0), frame_size[0] - width)
    return width, left - left % 2


def assemble_command(plan: Plan, *, voices: dict[str, Path], avatars: dict[str, Path], crops: dict[str, tuple[int, int]], pictures: list[Path], captions: list[tuple[Path, dict]],
                     music: Path | None, out: Path, cfg: ClipVideoConfig | None = None) -> list[str]:
    """The ffmpeg command: segments of video one after the other, the captions over them, the voices at their starts, the music bed ducked under the voice."""
    cfg = cfg or ai_config.short_clips_settings().video
    ff = ffmpeg_bin()
    if not ff:
        raise ClipVideoError("ffmpeg is not available")
    W, H, fps = cfg.width, cfg.height, cfg.fps
    cmd, graph, labels, n = [ff, "-y", "-v", "error"], [], [], 0
    mid = plan.segments[1]
    each = (mid["end"] - mid["start"]) / len(pictures)
    for seg in plan.segments:
        length = seg["end"] - seg["start"]
        if seg["kind"] == "avatar":
            crop_w, left = crops[seg["part"]]
            cmd += ["-i", str(avatars[seg["part"]])]
            graph.append(f"[{n}:v]crop={crop_w}:ih:{left}:0,scale={W}:{H}:flags=lanczos,fps={fps},tpad=stop_mode=clone:stop_duration={length:.3f},trim=0:{length:.3f},setpts=PTS-STARTPTS,setsar=1[s{n}]")
            labels.append(f"[s{n}]")
            n += 1
        else:
            for k, picture in enumerate(pictures):
                frames = max(2, round(each * fps))
                zoom = f"min(zoom+{cfg.picture_zoom / frames:.6f},{1 + cfg.picture_zoom:.4f})" if k % 2 == 0 else f"if(eq(on,0),{1 + cfg.picture_zoom:.4f},max(zoom-{cfg.picture_zoom / frames:.6f},1.0))"
                cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{each:.3f}", "-i", str(picture)]
                graph.append(f"[{n}:v]split=2[bg{n}][fg{n}];[bg{n}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=28:4,eq=brightness=-0.12[b{n}];"
                             f"[fg{n}]scale={W}:-2[f{n}];[b{n}][f{n}]overlay=(W-w)/2:(H-h)/2,zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps},"
                             f"trim=0:{each:.3f},setpts=PTS-STARTPTS,setsar=1[s{n}]")
                labels.append(f"[s{n}]")
                n += 1
    graph.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[base]")
    last, y = "base", round(cfg.caption.center_y * H)
    for k, (image, chunk) in enumerate(captions):
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{plan.total:.3f}", "-i", str(image)]
        top = y - Image.open(image).height // 2
        graph.append(f"[{last}][{n}:v]overlay=0:{top}:enable='between(t,{chunk['start']:.3f},{chunk['end']:.3f})'[c{k}]")
        last, n = f"c{k}", n + 1
    voice_labels = []
    for part in ORDER:
        cmd += ["-i", str(voices[part])]
        graph.append(f"[{n}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={round(plan.starts[part] * 1000)}:all=1[v{n}]")
        voice_labels.append(f"[v{n}]")
        n += 1
    graph.append("".join(voice_labels) + f"amix=inputs={len(voice_labels)}:normalize=0:duration=longest,apad=whole_dur={plan.total:.3f}[voice]")
    if music is not None:
        m = cfg.music
        length = plan.total - plan.music_start
        fade_out = min(m.fade_out, length / 2)
        ratio = round(1.0 / max(1.0 - m.duck, 0.05), 2)
        cmd += ["-i", str(music)]
        graph += [f"[{n}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{length:.3f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d={m.fade_in},afade=t=out:st={length - fade_out:.3f}:d={fade_out:.3f},"
                  f"adelay={round(plan.music_start * 1000)}:all=1,apad=whole_dur={plan.total:.3f},volume={m.volume}[m]",
                  "[voice]asplit=2[va][vb]", f"[m][va]sidechaincompress=threshold=0.02:ratio={ratio}:attack=20:release=400[md]", "[vb][md]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.95[a]"]
    else:
        graph.append("[voice]alimiter=limit=0.95[a]")
    return cmd + ["-filter_complex", ";".join(graph), "-map", f"[{last}]", "-map", "[a]", "-t", f"{plan.total:.3f}", "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
                  "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]


def assemble(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ClipVideoError(f"ffmpeg could not make the clip video: {result.stderr[-400:]}")
