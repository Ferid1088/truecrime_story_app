"""What is made from an opening block: the voice of the hook and of the context text, the music, and the one audio file they make together.

Layout (seconds): the hook; `hook_pause_seconds` of pause in which the music begins, directly after the first statement; the context text with the
music under it; then `gap_seconds` of darkness and silence, after which the host's opening (or the logo) follows. The music fades out as the
context text ends, so the gap is silent. The voice is the language's own made faster and less stable (see opening_block.voice_settings_for)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import OpeningBlockConfig, ai_config
from app.core.media_tools import ffmpeg_bin
from app.db.models import FilmOpening
from app.documentary import audio as A
from app.documentary import opening_block as OB
from app.documentary import storage
from app.documentary.host_scenes import _sentences, sped_up
from app.documentary.music import MusicLibrary
from app.documentary.voice_render import VoiceRenderer


class OpeningMediaError(RuntimeError):
    pass


_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


def _music_lock(key: str) -> asyncio.Lock:
    """One lock for each music file in the running event loop (a lock belongs to the loop it is used in)."""
    return _LOCKS.setdefault((id(asyncio.get_running_loop()), key), asyncio.Lock())


def opening_dir(case_id: int, language: str) -> Path:
    d = storage.case_dir(case_id) / "opening" / language
    d.mkdir(parents=True, exist_ok=True)
    return d


def _name(path: str | None) -> str | None:
    """A saved file by its own name: the voice and music files are named after what they hold, so the opening's audio is named after its inputs and not after the
    folder the data lives in."""
    return Path(path).name if path else None


def _sha(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


async def voice_part(db: Session, case_id: int, language: str, part: str, text: str, renderer: VoiceRenderer | None = None, *, folder: Path | None = None,
                     block_id: str | None = None, tempo: float | None = None) -> dict:
    """The voice of one part (hook or context; the short clips use it for their parts with their own `folder`, `block_id` and `tempo`): the text spoken in the language's
    voice at the block's settings and local speed (the opening's own unless a `tempo` is given). Reused when the saved file was made from the same text, voice and settings."""
    vcfg = ai_config.voice.languages.get(language)
    if vcfg is None:
        raise OpeningMediaError(f"no voice configured for {language}")
    settings, tempo = OB.voice_settings_for(language), tempo or OB.opening_post_speed(language)
    d = folder or opening_dir(case_id, language)
    stem = block_id or part
    key = _sha(text, vcfg.voice_id, vcfg.model_id, settings, tempo)[:16]
    path, sidecar = d / f"voice_{stem}_{key}.mp3", d / f"voice_{stem}_{key}.json"
    if path.exists() and sidecar.exists():
        return {**json.loads(sidecar.read_text(encoding="utf-8")), "path": storage.rel(path), "reused": True}
    renderer = renderer or VoiceRenderer(db=db, budget_case_id=case_id, budget_stage=f"tts:opening:{language}",
                                         use_asr=ai_config.listens_after_voice(language))
    sentences = _sentences(text)
    block = {"block_id": block_id or f"opening_{part}", "text": " ".join(sentences), "beats": [],
             "sentences": [{"speech": s, "display": s, "tts": s, "level": None} for s in sentences]}
    result = await renderer.speak_block(block, language, vcfg, settings, ai_config.voice.seed, d / "takes")
    take = result["take"]
    return {**store_voice(case_id, language, part, text, Path(take["raw_path"]), take["alignment"], take, folder=folder, stem=stem, tempo=tempo), "reused": False}


def word_times(alignment: dict) -> list[list]:
    """[word, start, end] of every word of a take, from the provider's character times (a word is a run of characters between spaces)."""
    words, current, first, last = [], "", 0.0, 0.0
    for char, start, end in zip(alignment.get("characters", []), alignment.get("starts", []), alignment.get("ends", []), strict=False):
        if char.isspace():
            if current:
                words.append([current, round(first, 3), round(last, 3)])
                current = ""
            continue
        if not current:
            first = start
        current, last = current + char, end
    if current:
        words.append([current, round(first, 3), round(last, 3)])
    return words


def store_voice(case_id: int, language: str, part: str, text: str, raw: Path, alignment: dict, take: dict, *, folder: Path | None = None,
                stem: str | None = None, tempo: float | None = None) -> dict:
    """A take as the part's voice: at the block's local speed (the opening's own unless a `tempo` is given), saved with a sidecar (the file the film's audio is made from)."""
    vcfg = ai_config.voice.languages[language]
    settings, tempo = OB.voice_settings_for(language), tempo or OB.opening_post_speed(language)
    d = folder or opening_dir(case_id, language)
    key = _sha(text, vcfg.voice_id, vcfg.model_id, settings, tempo)[:16]
    name = stem or part
    path, sidecar = d / f"voice_{name}_{key}.mp3", d / f"voice_{name}_{key}.json"
    audio, al = sped_up(raw.read_bytes(), tempo, alignment)
    storage.write_atomic(path, audio)
    meta = {"text": text, "tts_text": take.get("text"), "seconds": round(max(al["ends"]), 3), "settings": settings, "post_speed": tempo, "voice_id": vcfg.voice_id,
            "character_cost": take.get("character_cost"), "request_id": take.get("request_id"), "words": word_times(al)}
    storage.write_atomic(sidecar, json.dumps(meta, ensure_ascii=False))
    return {**meta, "path": storage.rel(path)}


def reassemble(db: Session, row: FilmOpening) -> dict:
    """The block's audio made again from the parts the row has now (after a part's voice was replaced in the review); returns the new audio record."""
    media = json.loads(row.media_json or "{}")
    cfg = ai_config.opening_block_settings()
    hook, context, music = media["hook"], media["context"], (media.get("music") or {}).get("tracks") or []
    out = opening_dir(row.case_id, row.language) / f"opening_{_sha(_name(hook['path']), _name(context['path']), *(_name(t["path"]) for t in music), cfg.model_dump())[:12]}.wav"
    timings = assemble(storage.resolve(hook["path"]), storage.resolve(context["path"]), [storage.resolve(t["path"]) for t in music],
                       hook["seconds"], context["seconds"], out, cfg)
    media["audio"] = {"path": storage.rel(out), "sha256": hashlib.sha256(out.read_bytes()).hexdigest(), **timings}
    row.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    return media["audio"]


async def ensure_music(db: Session, case_id: int, library: MusicLibrary | None = None) -> dict:
    """The opening's music: the case's different piano tracks, one for each entry (one set for all its languages: missing ones are made while the library is below its
    target, booked in the ledger as an estimate, else taken at random; see documentary/opening_music.py). The languages of a film run side by side, so the choice is made one at a time."""
    from app.documentary.opening_music import OpeningMusic

    async with _music_lock("opening-library"):
        tracks = await OpeningMusic(db, library).choose(case_id)
    return {"tracks": [{"path": t.file_path, "track_id": t.id, "code": t.track_code, "prompt": t.prompt, "seconds": t.seconds} for t in tracks]}


def pictures_prompt(scene: str) -> str:
    """What the image model is told for one of the block's pictures: the scene in the configured mystery style."""
    return f"{scene.strip().rstrip('.')}. {ai_config.opening_block_settings().pictures.style}"


async def ensure_pictures(db: Session, case_id: int, master: FilmOpening, provider=None) -> list[dict]:
    """The block's pictures, painted once for the film from the English opening's scenes (every language shows the same ones) and booked in the ledger as estimates.
    Kept in the master row; a picture whose file and scene are unchanged is not paid for again. Returns [{scene, path, image_id}] (empty when the image model gave none:
    the block then shows a black screen under its words)."""
    from app.covers import images as IM
    from app.db.models import Case

    cfg = ai_config.opening_block_settings().pictures
    scenes = json.loads(master.opening_json or "{}").get("scenes") or []
    async with _music_lock(f"pictures:{master.id}"):
        media = json.loads(master.media_json or "{}")
        known = {p["scene"]: p for p in media.get("pictures") or [] if storage.resolve(p["path"]).exists()}
        case = db.get(Case, case_id)
        made = []
        for n, scene in enumerate(scenes[:cfg.count]):
            if scene in known:
                made.append(known[scene])
                continue
            folder = storage.case_dir(case_id) / "opening" / "pictures"
            folder.mkdir(parents=True, exist_ok=True)
            record = await IM.paint(db, case, "en", f"opening{n + 1}", master.id, pictures_prompt(scene), ai_config.covers_settings().scene_image["sizes"]["long"], folder, f"opening_{master.id}_{n + 1}",
                                    cfg.ledger_stage, provider)
            made.append({"scene": scene, "path": record.file_path, "image_id": record.id})
        media["pictures"] = made
        master.media_json = json.dumps(media, ensure_ascii=False)
        db.commit()
        return made


def assemble_command(hook: Path, context: Path, music: list[Path], hook_seconds: float, context_seconds: float, out: Path,
                     cfg: OpeningBlockConfig | None = None, sample_rate: int | None = None) -> tuple[list[str], dict]:
    """ffmpeg command and timings of the block's audio: the hook, the pause, the context text and the dark gap; the music starts when the hook ends, gives
    way under the voice and has faded out when the context text ends; the music is `music.tracks` different tracks, each played from its beginning at `music.speed` for an equal part of that time."""
    cfg = cfg or ai_config.opening_block_settings()
    ff = ffmpeg_bin()
    if not ff:
        raise OpeningMediaError("ffmpeg is not available")
    sr = sample_rate or ai_config.loudness.sample_rate
    pause, gap = cfg.hook_pause_seconds, cfg.gap_seconds
    speech_end = hook_seconds + pause + context_seconds
    total = speech_end + gap
    timings = {"hook": [0.0, round(hook_seconds, 3)], "pause": [round(hook_seconds, 3), round(hook_seconds + pause, 3)],
               "context": [round(hook_seconds + pause, 3), round(speech_end, 3)], "gap": [round(speech_end, 3), round(total, 3)],
               "music": [round(hook_seconds, 3), round(speech_end, 3)] if music else None, "total": round(total, 3)}
    fmt = f"aformat=sample_rates={sr}:channel_layouts=stereo"
    graph = [f"[0:a]{fmt},apad=pad_dur={pause}[h]", f"[1:a]{fmt},apad=pad_dur={gap}[c]", "[h][c]concat=n=2:v=0:a=1[voice]"]
    cmd = [ff, "-y", "-v", "error", "-i", str(hook), "-i", str(context)]
    if music:
        music_len = speech_end - hook_seconds
        part = music_len / len(music)
        fade = min(cfg.music.fade_out_seconds, part / 3)
        ratio = round(1.0 / max(1.0 - cfg.music.duck, 0.05), 2)
        for track in music:
            cmd += ["-i", str(track)]
        slow = f"atempo={cfg.music.speed}," if cfg.music.speed != 1.0 else ""
        graph += [f"[{2 + i}:a]{fmt},{slow}atrim=0:{part:.3f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.5,afade=t=out:st={part - fade:.3f}:d={fade:.3f}[p{i}]" for i in range(len(music))]
        graph += ["".join(f"[p{i}]" for i in range(len(music))) + f"concat=n={len(music)}:v=0:a=1,adelay={int(hook_seconds * 1000)}:all=1,apad=whole_dur={total:.3f},volume={cfg.music.volume}[m]",
                  "[voice]asplit=2[v1][v2]", f"[m][v1]sidechaincompress=threshold=0.02:ratio={ratio}:attack=20:release=400[md]",
                  "[v2][md]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.95[a]"]
    else:
        graph += ["[voice]alimiter=limit=0.95[a]"]
    return cmd + ["-filter_complex", ";".join(graph), "-map", "[a]", "-t", f"{total:.3f}", str(out)], timings


def assemble(hook: Path, context: Path, music: list[Path], hook_seconds: float, context_seconds: float, out: Path,
             cfg: OpeningBlockConfig | None = None) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd, timings = assemble_command(hook, context, music, hook_seconds, context_seconds, out, cfg)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise OpeningMediaError(f"ffmpeg could not assemble the opening audio: {r.stderr[-300:]}")
    return {**timings, "seconds": round(A.probe_duration(out), 3)}


def stale_voices(texts: dict[str, str], voices: dict[str, dict]) -> list[str]:
    """The parts whose saved voice was not made from the text the part has now (a text written again, edited or replaced after the voice was made); a voice
    that a review corrected keeps its corrected words as the part's text, so it matches."""
    return [part for part, text in texts.items() if (voices.get(part) or {}).get("text") != text]


async def make_media(db: Session, case_id: int, row: FilmOpening, master: FilmOpening, renderer: VoiceRenderer | None = None,
                     library: MusicLibrary | None = None) -> dict:
    """Voice, music and the assembled audio of one opening row (the English row is its own master; a native row hears the film's music). The result is kept in the row's media_json."""
    opening = json.loads(row.opening_json or "{}")
    cfg = ai_config.opening_block_settings()
    hook = await voice_part(db, case_id, row.language, "hook", opening["hook"], renderer)
    context = await voice_part(db, case_id, row.language, "context", opening["context"], renderer)
    music = await ensure_music(db, case_id, library)
    out = opening_dir(case_id, row.language) / f"opening_{_sha(_name(hook['path']), _name(context['path']), *(_name(t["path"]) for t in music["tracks"]), cfg.model_dump())[:12]}.wav"
    timings = await asyncio.to_thread(
        assemble, storage.resolve(hook["path"]), storage.resolve(context["path"]), [storage.resolve(t["path"]) for t in music["tracks"]],
        hook["seconds"], context["seconds"], out, cfg)
    pictures = json.loads(master.media_json or "{}").get("pictures") or []
    media = {"hook": hook, "context": context, "music": music, "pictures": pictures, "audio": {"path": storage.rel(out), "sha256": hashlib.sha256(out.read_bytes()).hexdigest(), **timings}}
    row.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    return media
