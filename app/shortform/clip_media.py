"""What is made from the short clips of a language: the voice of each part of each clip (the hook, the context, the detail, the reaction, the invitation), in the voice
of the opening block's hook before the host: the language's own style, the local speed made faster, the stability lower (opening_block.voice). One voice for each part,
so each text can be listened to and corrected on its own."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin
from app.covers import avatar as AV
from app.db.models import Case, ShortClip
from app.documentary import opening_media as OM
from app.documentary import storage
from app.documentary.voice_render import VoiceRenderer
from app.shortform import clip_avatar as CA
from app.shortform import clip_music as CMU
from app.shortform import clip_pictures as CP
from app.shortform import clip_video as CV
from app.shortform import clips as C

PARTS = C.TEXT_PARTS


def clip_dir(case_id: int, language: str) -> Path:
    d = storage.case_dir(case_id) / "clips" / language
    d.mkdir(parents=True, exist_ok=True)
    return d


def block_id(clip_key: str, part: str) -> str:
    return f"clip_{clip_key}_{part}"


async def voice_clip(db: Session, row: ShortClip, renderer: VoiceRenderer | None = None) -> dict:
    """The voice of every part of one clip; kept in the row's media_json. A part whose saved voice was made from the same text, voice and settings is reused."""
    script = json.loads(row.script_json)
    folder = clip_dir(row.case_id, row.language)
    voice = {}
    for part in PARTS:
        voice[part] = await OM.voice_part(db, row.case_id, row.language, part, script[part], renderer, folder=folder, block_id=block_id(row.clip_key, part))
    media = json.loads(row.media_json or "{}")
    if stale := OM.stale_voices({p: script[p] for p in PARTS}, voice):
        raise OM.OpeningMediaError(f"the voice of {', '.join(stale)} was not made from the clip's text")
    media["voice"] = voice
    media["voice_seconds"] = round(sum(v["seconds"] for v in voice.values()), 3)
    row.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    return media


async def voice_clips(db: Session, case_id: int, language: str, renderer: VoiceRenderer | None = None) -> list[dict]:
    """The voices of all the valid clips of a language, one clip after the other."""
    return [await voice_clip(db, row, renderer) for row in C.clips_of(db, case_id, language)]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frame(video: Path, out: Path) -> Path:
    """One frame (a second in) of an avatar video, to find the host's face in it."""
    subprocess.run([ffmpeg_bin() or "ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(video), "-frames:v", "1", str(out)], check=True, capture_output=True)
    return out


def _join_audio(parts: list[Path], gap: float, out: Path) -> Path:
    """The invitation's audio for the second avatar video: the reaction, a short silence, the invitation."""
    cmd = [ffmpeg_bin() or "ffmpeg", "-y", "-v", "error"]
    for p in parts:
        cmd += ["-i", str(p)]
    graph = (f"[0:a]aformat=sample_rates=44100:channel_layouts=mono,apad=pad_dur={gap}[a0];[1:a]aformat=sample_rates=44100:channel_layouts=mono[a1];[a0][a1]concat=n=2:v=0:a=1[o]")
    subprocess.run(cmd + ["-filter_complex", graph, "-map", "[o]", str(out)], check=True, capture_output=True)
    return out


def pictures_of_other_ideas(db: Session, row: ShortClip) -> Counter:
    """How often each picture already shows in the language's other clips: a clip prefers pictures no other clip of its language shows."""
    used: Counter = Counter()
    for other in C.clips_of(db, row.case_id, row.language):
        if other.clip_key != row.clip_key:
            used.update(p["code"] for p in (json.loads(other.media_json or "{}").get("video") or {}).get("pictures", []))
    return used


async def ensure_music(db: Session, row: ShortClip, library=None) -> dict:
    """The bed of this clip: its own, from the database (made now while the library is below its target, else taken at random), kept in the row's media."""
    media = json.loads(row.media_json or "{}")
    track = await CMU.ClipMusic(db, library).choose(row.case_id, row.clip_key)
    if (media.get("music") or {}).get("track_id") != track.id:
        track.use_count += 1
    media["music"] = {"track_id": track.id, "code": track.track_code}
    row.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    return {"path": track.file_path, "seconds": track.seconds, "track_id": track.id, "code": track.track_code}


async def make_video(db: Session, case: Case, row: ShortClip, *, renderer: VoiceRenderer | None = None, avatar_provider=None, library=None, sleep=asyncio.sleep) -> dict:
    """The finished 1080x1920 video of one clip (kept in the row's media_json): the voices, the avatar's two videos (hook; reaction and invitation), the film's pictures,
    the captions and the music bed. What was paid for (a voice, an avatar video) is reused when its audio is unchanged."""
    cfg = ai_config.short_clips_settings().video
    media = await voice_clip(db, row, renderer)
    voices = media["voice"]
    folder = clip_dir(row.case_id, row.language)
    plan = CV.plan_timeline({p: voices[p]["seconds"] for p in CV.ORDER}, cfg)
    audio = {"hook": storage.resolve(voices["hook"]["path"]),
             "reaction": _join_audio([storage.resolve(voices["reaction"]["path"]), storage.resolve(voices["cta"]["path"])], cfg.gap_seconds, folder / f"voice_{row.clip_key}_reaction_cta.mp3")}
    if avatar_provider is None:
        from app.documentary import studio as ST
        from app.providers.avatar import get_avatar_provider

        avatar_provider = get_avatar_provider(key_env=ST.channel_profile(row.language)["heygen_key_env"])
    avatars, crops = {}, {}
    for part, audio_path in audio.items():
        name = f"{row.clip_key}_{part}"
        known = (media.get("avatars") or {}).get(part)
        if known and known.get("audio_sha") == _sha(audio_path) and storage.resolve(known["path"]).exists():
            made = known
        else:
            made = {**await CA.make_video(db, row.case_id, row.language, audio_path, name, folder, avatar_provider, sleep=sleep), "audio_sha": _sha(audio_path)}
            media.setdefault("avatars", {})[part] = made
            row.media_json = json.dumps(media, ensure_ascii=False)
            db.commit()
        avatars[part] = storage.resolve(made["path"])
        frame = _frame(avatars[part], folder / f"frame_{name}.png")
        x, _y, w, _h = AV.host_face(frame)
        with Image.open(frame) as picture:
            crops[part] = CV.avatar_crop(picture.size, x + w / 2, cfg)
    middle = plan.segments[1]
    pictures = CP.choose(db, case, row.language, json.loads(row.script_json), CV.picture_count(middle["end"] - middle["start"], cfg), used=pictures_of_other_ideas(db, row))
    music = await ensure_music(db, row, library)
    media["music"] = {"track_id": music["track_id"], "code": music["code"]}
    captions = []
    for k, chunk in enumerate(CV.chunks(voices, plan, cfg)):
        image = folder / "captions" / f"{row.clip_key}_{k:03d}.png"
        image.parent.mkdir(exist_ok=True)
        CV.caption_image(chunk["text"], row.language, image, cfg)
        captions.append((image, chunk))
    out = folder / f"clip_{row.clip_key}.mp4"
    command = CV.assemble_command(plan, voices={p: storage.resolve(voices[p]["path"]) for p in CV.ORDER}, avatars=avatars, crops=crops, pictures=[p["path"] for p in pictures],
                                  captions=captions, music=storage.resolve(music["path"]), out=out, cfg=cfg)
    await asyncio.to_thread(CV.assemble, command)
    media["video"] = {"path": storage.rel(out), "seconds": plan.total, "size": [cfg.width, cfg.height], "bytes": out.stat().st_size, "pictures": [{k: p[k] for k in ("code", "what")} for p in pictures],
                      "music": music, "captions": len(captions), "plan": {"starts": plan.starts, "segments": plan.segments}}
    row.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    return media["video"]
