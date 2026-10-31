"""The end of the film: after the avatar's last word the film goes on for `after_seconds`, with a deep, reflective music that begins quietly
`lead_seconds` before the avatar finishes (so it never covers the voice), grows once the voice is done and fades out at the very end, and
the channel's logo fading in slowly on the dark ground. The music is a track of the music library (a loopable, unresolved interlude): nothing is
generated, nothing is paid. The film's audio gets a new file with the music mixed in; the script gets an `outro` shot and the new length."""

from __future__ import annotations

import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin, ffprobe_bin
from app.db.models import MusicTrack
from app.documentary import storage

FLOOR_DB = -60.0


class OutroError(RuntimeError):
    pass


def stages(lead: float, total: float) -> list[tuple[float, float, float, float]]:
    """The music's level in dB, as straight lines in dB over the music's own time: (from second, to second, from dB, to dB)."""
    cfg = ai_config.outro_settings()
    rise_end = min(lead + cfg.rise_seconds, total)
    fade_start = max(total - cfg.fade_out_seconds, rise_end)
    return [(0.0, lead, cfg.start_db, cfg.speech_db), (lead, rise_end, cfg.speech_db, cfg.full_db),
            (rise_end, fade_start, cfg.full_db, cfg.full_db), (fade_start, total, cfg.full_db, FLOOR_DB)]


def gain_db(t: float, lead: float, total: float) -> float:
    """The music's level at second `t` of the music (the same line the mix uses)."""
    for a, b, da, db in stages(lead, total):
        if b - a > 1e-9 and t < b:
            return da + (db - da) * min(max((t - a) / (b - a), 0.0), 1.0)
    return FLOOR_DB


def expression(lead: float, total: float) -> str:
    """The same envelope as an ffmpeg `volume` expression of the time `t` (linear in dB, so the rise is smooth to the ear)."""
    parts = []
    for a, b, da, db in stages(lead, total):
        if b - a < 1e-9:
            continue
        parts.append((b, f"pow(10,({da:.3f}+({db - da:.3f})*(t-{a:.3f})/{b - a:.3f})/20)"))
    expr = f"pow(10,{FLOOR_DB}/20)"
    for b, e in reversed(parts):
        expr = f"if(lt(t,{b:.3f}),{e},{expr})"
    return expr


def track_path(db: Session) -> Path:
    """The library's loopable track of the configured kind and mood (an unresolved, reflective interlude), the first variant whose file
    exists. Nothing is generated: a library without such a file stops the run with a clear message."""
    cfg = ai_config.outro_settings().music
    for track in db.query(MusicTrack).filter_by(kind=cfg.kind, mood=cfg.mood).order_by(MusicTrack.variant).all():
        path = storage.resolve(track.file_path)
        if path is not None and path.exists():
            return path
    raise OutroError(f"the music library has no {cfg.kind} track of the mood {cfg.mood!r} for the end of the film")


def audio_format(path: Path) -> tuple[int, str]:
    """(sample rate, channel layout name) of an audio file: the film's voice keeps exactly its own format."""
    probe = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    rate, channels = (probe.stdout.strip().split(",") + ["44100", "2"])[:2]
    return int(rate), "mono" if int(channels) == 1 else "stereo"


def mix_command(base: Path, music: Path, out: Path, speech_end: float, new_duration: float) -> list[str]:
    """ffmpeg: the film's audio padded to its new length (its own sample rate and channels, untouched), the music looped, brought to that
    format, shaped by the envelope and laid in at `speech_end - lead`."""
    cfg = ai_config.outro_settings()
    rate, layout = audio_format(base)
    start = max(speech_end - cfg.lead_seconds, 0.0)
    total = new_duration - start
    lead = speech_end - start
    delay = round(start * 1000)
    graph = (f"[0:a]apad=whole_dur={new_duration:.3f}[b];"
             f"[1:a]aformat=channel_layouts={layout},aresample={rate},atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
             f"volume='{expression(lead, total)}':eval=frame,adelay={delay}|{delay}[m];"
             f"[b][m]amix=inputs=2:normalize=0:duration=first:dropout_transition=0,atrim=0:{new_duration:.3f}[a]")
    return [ffmpeg_bin(), "-y", "-v", "error", "-i", str(base), "-stream_loop", "-1", "-i", str(music),
            "-filter_complex", graph, "-map", "[a]", "-c:a", "pcm_s16le", str(out)]


def speech_end_of(script: dict) -> float:
    """Where the avatar's last word ends (the outro host scene), else the end of the film's story."""
    host = [h for h in script.get("host") or [] if h.get("position") == "final"]
    return float(host[-1]["end"]) if host else float(script["duration"])


def apply(db: Session, script: dict, case_id: int, language: str, version_id: int) -> dict:
    """The ending of this film: the script is `after_seconds` longer, ends with an `outro` shot, and its audio carries the music."""
    cfg = ai_config.outro_settings()
    if not cfg.enabled:
        return script
    base = storage.resolve(script["audio"]["path"])
    music = track_path(db)
    speech_end = speech_end_of(script)
    new_duration = round(speech_end + cfg.after_seconds, 3)
    out = storage.production_dir(case_id, language) / f"audio_outro_v{version_id}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    run = subprocess.run(mix_command(base, music, out, speech_end, new_duration), capture_output=True, text=True)
    if run.returncode != 0:
        raise OutroError(f"ffmpeg could not mix the music of the ending: {run.stderr[-300:]}")
    start = max(speech_end - cfg.lead_seconds, 0.0)
    script["shots"].append({"kind": "outro", "command": "OUTRO", "beat_id": None, "start": round(speech_end, 3), "end": new_duration,
                            "motion": "NONE", "speed": 1.0, "transition_in": "CROSSFADE",
                            "why": "the story is told: the music goes on and the logo fades in"})
    script["audio"] = {**script["audio"], "path": storage.rel(out), "outro": True}
    script["duration"] = new_duration
    script["music"] = [*(script.get("music") or []), {"role": "outro", "cue_id": None, "mood": cfg.music.mood, "start": round(start, 3),
                                                       "duration": round(new_duration - start, 3), "level_db": cfg.full_db,
                                                       "track_code": music.name,
                                                       "why": "deep, reflective music from the avatar's last words to the end"}]
    script["outro"] = {"start": round(speech_end, 3), "seconds": cfg.after_seconds, "music_start": round(start, 3)}
    return script
