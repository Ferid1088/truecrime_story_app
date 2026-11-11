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


def _sha(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


async def voice_part(db: Session, case_id: int, language: str, part: str, text: str, renderer: VoiceRenderer | None = None) -> dict:
    """The voice of one part (hook or context): the text spoken in the language's voice at the block's settings and local speed. Reused when the saved file
    was made from the same text, voice and settings."""
    vcfg = ai_config.voice.languages.get(language)
    if vcfg is None:
        raise OpeningMediaError(f"no voice configured for {language}")
    settings, tempo = OB.voice_settings_for(language), OB.post_speed_for(language)
    d = opening_dir(case_id, language)
    key = _sha(text, vcfg.voice_id, vcfg.model_id, settings, tempo)[:16]
    path, sidecar = d / f"voice_{part}_{key}.mp3", d / f"voice_{part}_{key}.json"
    if path.exists() and sidecar.exists():
        return {**json.loads(sidecar.read_text(encoding="utf-8")), "path": storage.rel(path), "reused": True}
    renderer = renderer or VoiceRenderer(db=db, budget_case_id=case_id, budget_stage=f"tts:opening:{language}",
                                         use_asr=ai_config.listens_after_voice(language))
    sentences = _sentences(text)
    block = {"block_id": f"opening_{part}", "text": " ".join(sentences), "beats": [],
             "sentences": [{"speech": s, "display": s, "tts": s, "level": None} for s in sentences]}
    result = await renderer.speak_block(block, language, vcfg, settings, ai_config.voice.seed, d / "takes")
    take = result["take"]
    return {**store_voice(case_id, language, part, text, Path(take["raw_path"]), take["alignment"], take), "reused": False}


def store_voice(case_id: int, language: str, part: str, text: str, raw: Path, alignment: dict, take: dict) -> dict:
    """A take as the part's voice: at the block's local speed, saved with a sidecar (the file the film's audio is made from)."""
    vcfg = ai_config.voice.languages[language]
    settings, tempo = OB.voice_settings_for(language), OB.post_speed_for(language)
    d = opening_dir(case_id, language)
    key = _sha(text, vcfg.voice_id, vcfg.model_id, settings, tempo)[:16]
    path, sidecar = d / f"voice_{part}_{key}.mp3", d / f"voice_{part}_{key}.json"
    audio, al = sped_up(raw.read_bytes(), tempo, alignment)
    storage.write_atomic(path, audio)
    meta = {"text": text, "tts_text": take.get("text"), "seconds": round(max(al["ends"]), 3), "settings": settings, "post_speed": tempo, "voice_id": vcfg.voice_id,
            "character_cost": take.get("character_cost"), "request_id": take.get("request_id")}
    storage.write_atomic(sidecar, json.dumps(meta, ensure_ascii=False))
    return {**meta, "path": storage.rel(path)}


def reassemble(db: Session, row: FilmOpening) -> dict:
    """The block's audio made again from the parts the row has now (after a part's voice was replaced in the review); returns the new audio record."""
    media = json.loads(row.media_json or "{}")
    cfg = ai_config.opening_block_settings()
    hook, context, music = media["hook"], media["context"], media.get("music")
    out = opening_dir(row.case_id, row.language) / f"opening_{_sha(hook['path'], context['path'], music['path'] if music else None, cfg.model_dump())[:12]}.wav"
    timings = assemble(storage.resolve(hook["path"]), storage.resolve(context["path"]), storage.resolve(music["path"]) if music else None,
                       hook["seconds"], context["seconds"], out, cfg)
    media["audio"] = {"path": storage.rel(out), "sha256": hashlib.sha256(out.read_bytes()).hexdigest(), **timings}
    row.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    return media["audio"]


async def ensure_music(db: Session, case_id: int, master: dict, library: MusicLibrary | None = None) -> dict:
    """The opening's music, generated once for the film from the director's brief (every language of the film hears the same music) and booked in
    the ledger as an estimate. Reused when the file of this brief exists."""
    cfg = ai_config.opening_block_settings()
    prompt = OB.music_prompt(master, cfg)
    library = library or MusicLibrary()
    key = _sha(prompt, cfg.music.generated_seconds)[:12]
    path = library.dir / f"opening_{key}.wav"
    paid = 0
    async with _music_lock(key):      # the languages of a film run side by side: the music is paid for once
        if not path.exists():
            paid = await library._generate(prompt, cfg.music.generated_seconds, False, path)
            from app.documentary.music_cost import book_opening

            book_opening(db, case_id, key, cfg.music.generated_seconds)
    return {"path": storage.rel(path), "prompt": prompt, "seconds": cfg.music.generated_seconds, "characters_paid": paid}


def assemble_command(hook: Path, context: Path, music: Path | None, hook_seconds: float, context_seconds: float, out: Path,
                     cfg: OpeningBlockConfig | None = None, sample_rate: int | None = None) -> tuple[list[str], dict]:
    """ffmpeg command and timings of the block's audio: the hook, the pause, the context text and the dark gap; the music starts when the hook ends, gives
    way under the voice and has faded out when the context text ends."""
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
        fade = min(cfg.music.fade_out_seconds, music_len / 2)
        ratio = round(1.0 / max(1.0 - cfg.music.duck, 0.05), 2)
        cmd += ["-i", str(music)]
        graph += [f"[2:a]{fmt},atrim=0:{music_len:.3f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.8,afade=t=out:st={music_len - fade:.3f}:d={fade:.3f},"
                  f"adelay={int(hook_seconds * 1000)}:all=1,apad=whole_dur={total:.3f},volume={cfg.music.volume}[m]",
                  "[voice]asplit=2[v1][v2]", f"[m][v1]sidechaincompress=threshold=0.02:ratio={ratio}:attack=20:release=400[md]",
                  "[v2][md]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.95[a]"]
    else:
        graph += ["[voice]alimiter=limit=0.95[a]"]
    return cmd + ["-filter_complex", ";".join(graph), "-map", "[a]", "-t", f"{total:.3f}", str(out)], timings


def assemble(hook: Path, context: Path, music: Path | None, hook_seconds: float, context_seconds: float, out: Path,
             cfg: OpeningBlockConfig | None = None) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd, timings = assemble_command(hook, context, music, hook_seconds, context_seconds, out, cfg)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise OpeningMediaError(f"ffmpeg could not assemble the opening audio: {r.stderr[-300:]}")
    return {**timings, "seconds": round(A.probe_duration(out), 3)}


async def make_media(db: Session, case_id: int, row: FilmOpening, master: FilmOpening, renderer: VoiceRenderer | None = None,
                     library: MusicLibrary | None = None) -> dict:
    """Voice, music and the assembled audio of one opening row (the English row is its own master; a native row hears the film's music). The result is kept in the row's media_json."""
    opening = json.loads(row.opening_json or "{}")
    master_opening = json.loads(master.opening_json or "{}")
    cfg = ai_config.opening_block_settings()
    hook = await voice_part(db, case_id, row.language, "hook", opening["hook"], renderer)
    context = await voice_part(db, case_id, row.language, "context", opening["context"], renderer)
    music = await ensure_music(db, case_id, master_opening, library)
    out = opening_dir(case_id, row.language) / f"opening_{_sha(hook['path'], context['path'], music['path'], cfg.model_dump())[:12]}.wav"
    timings = await asyncio.to_thread(
        assemble, storage.resolve(hook["path"]), storage.resolve(context["path"]), storage.resolve(music["path"]) if music else None,
        hook["seconds"], context["seconds"], out, cfg)
    media = {"hook": hook, "context": context, "music": music, "audio": {"path": storage.rel(out), "sha256": hashlib.sha256(out.read_bytes()).hexdigest(), **timings}}
    row.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    return media
