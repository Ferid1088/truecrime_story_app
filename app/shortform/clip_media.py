"""What is made from the short clips of a language: the voice of each part of each clip (the hook, the context, the detail, the reaction, the invitation), in the voice
of the opening block's hook before the host: the language's own style, the local speed made faster, the stability lower (opening_block.voice). One voice for each part,
so each text can be listened to and corrected on its own."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin
from app.covers import avatar as AV
from app.db.models import Case, ShortClip
from app.documentary import opening_block as OB
from app.documentary import opening_media as OM
from app.documentary import storage
from app.documentary.voice_render import VoiceRenderer
from app.publishing import platform_specs as PS
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
    for part in (p for p in PARTS if script[p].strip()):          # a part with no words is not voiced
        voice[part] = await OM.voice_part(db, row.case_id, row.language, part, script[part], renderer, folder=folder, block_id=block_id(row.clip_key, part), tempo=OB.post_speed_for(row.language))
    media = json.loads(row.media_json or "{}")
    if stale := OM.stale_voices({p: script[p] for p in PARTS if script[p].strip()}, voice):
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


def _platform_check(video: Path) -> dict:
    """What each platform's file rules say about the finished video ({platform: what is wrong}; empty lists mean it meets them), kept with the clip so publishing knows before it tries."""
    try:
        return PS.check_all(video)
    except (PS.SpecError, OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return {"error": f"the file could not be checked: {exc}"}


async def make_video(db: Session, case: Case, row: ShortClip, *, renderer: VoiceRenderer | None = None, avatar_provider=None, library=None, sleep=asyncio.sleep) -> dict:
    """The finished 1080x1920 video of one clip (kept in the row's media_json): the voices, the avatar's two videos (hook; reaction and invitation), the film's pictures,
    the captions and the music bed. What was paid for (a voice, an avatar video) is reused when its audio is unchanged."""
    cfg = ai_config.short_clips_settings().video
    media = await voice_clip(db, row, renderer)
    voices = media["voice"]
    folder = clip_dir(row.case_id, row.language)
    plan = CV.plan_timeline({p: (voices.get(p) or {}).get("seconds", 0.0) for p in CV.ORDER}, cfg)
    closing = [storage.resolve(voices[p]["path"]) for p in ("reaction", "cta") if voices.get(p)]
    audio = {"hook": storage.resolve(voices["hook"]["path"]),
             "reaction": closing[0] if len(closing) == 1 else _join_audio(closing, cfg.gap_seconds, folder / f"voice_{row.clip_key}_reaction_cta.mp3")}
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
    return await _assemble_video(db, row, media, plan, avatars, crops, pictures, music, cfg)


async def _assemble_video(db: Session, row: ShortClip, media: dict, plan, avatars: dict[str, Path], crops: dict, pictures: list[dict], music: dict, cfg) -> dict:
    """The last step of a clip: one caption picture per word, the ffmpeg assembly, the platform check, and the record in the row. Nothing here is paid for."""
    voices = media["voice"]
    folder = clip_dir(row.case_id, row.language)
    captions = []
    for k, chunk in enumerate(CV.chunks(voices, plan, cfg)):
        image = folder / "captions" / f"{row.clip_key}_{k:03d}.png"
        image.parent.mkdir(exist_ok=True)
        CV.caption_image(chunk["text"], row.language, image, cfg)
        captions.append((image, chunk))
    out = folder / f"clip_{row.clip_key}.mp4"
    command = CV.assemble_command(plan, voices={p: storage.resolve(voices[p]["path"]) for p in CV.ORDER if p in voices}, avatars=avatars, crops=crops, pictures=[p["path"] for p in pictures],
                                  captions=captions, music=storage.resolve(music["path"]), out=out, cfg=cfg)
    await asyncio.to_thread(CV.assemble, command)
    media["video"] = {"path": storage.rel(out), "seconds": plan.total, "size": [cfg.width, cfg.height], "bytes": out.stat().st_size, "pictures": [{k: p[k] for k in ("code", "what")} for p in pictures],
                      "music": music, "captions": len(captions), "plan": {"starts": plan.starts, "segments": plan.segments, "music": [plan.music_start, plan.music_end]}, "platform_check": await asyncio.to_thread(_platform_check, out)}
    row.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    return media["video"]


class NotRebuildable(RuntimeError):
    """A clip that cannot be built again without paying or choosing again (a voice, an avatar video, a picture or its music is missing)."""


async def reassemble(db: Session, row: ShortClip, *, keep_previous: bool = True) -> dict:
    """The same clip built again from what it kept: its voices, its two avatar videos, the same pictures in the same order and the same music bed. Only what is made from code changes (the captions, the
    encode), so a clip made before a caption or encode change gets it without a new voice, a new avatar video or a new choice. The file it replaces is first copied to previous_versions/ beside it."""
    from app.db.models import VisualAsset

    cfg = ai_config.short_clips_settings().video
    media = json.loads(row.media_json or "{}")
    voices, kept = media.get("voice") or {}, media.get("avatars") or {}
    old = media.get("video") or {}
    if not voices or not all(p in kept for p in ("hook", "reaction")) or not old.get("pictures") or not (old.get("music") or {}).get("path"):
        raise NotRebuildable(f"{row.language} {row.clip_key} has no complete voice, avatar videos, pictures and music to build from")
    folder = clip_dir(row.case_id, row.language)
    plan = CV.plan_timeline({p: (voices.get(p) or {}).get("seconds", 0.0) for p in CV.ORDER}, cfg)
    avatars, crops = {}, {}
    for part in ("hook", "reaction"):
        avatars[part] = storage.resolve(kept[part]["path"])
        if not avatars[part].exists():
            raise NotRebuildable(f"the {part} avatar video of {row.language} {row.clip_key} is missing")
        frame = _frame(avatars[part], folder / f"frame_{row.clip_key}_{part}.png")
        x, _y, w, _h = AV.host_face(frame)
        with Image.open(frame) as picture:
            crops[part] = CV.avatar_crop(picture.size, x + w / 2, cfg)
    pictures = []
    for kept_picture in old["pictures"]:
        asset = db.query(VisualAsset).filter_by(case_id=row.case_id, asset_code=kept_picture["code"]).first()
        if asset is None or not asset.local_path or not storage.resolve(asset.local_path).exists():
            raise NotRebuildable(f"the picture {kept_picture['code']} of {row.language} {row.clip_key} is missing")
        pictures.append({"code": asset.asset_code, "path": storage.resolve(asset.local_path), "what": kept_picture["what"]})
    music = old["music"]                                                                  # the bed the clip was built with: its file and length are kept in the clip's record
    if not storage.resolve(music["path"]).exists():
        raise NotRebuildable(f"the music bed of {row.language} {row.clip_key} is missing")
    original = storage.resolve(old["path"]) if old.get("path") else None
    if keep_previous and original is not None and original.exists():
        saved = original.parent / "previous_versions" / f"{original.stem}_{int(original.stat().st_mtime)}{original.suffix}"
        saved.parent.mkdir(exist_ok=True)
        if not saved.exists():
            shutil.copy2(original, saved)
    return await _assemble_video(db, row, media, plan, avatars, crops, pictures, music, cfg)
