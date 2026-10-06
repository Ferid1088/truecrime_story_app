"""Music library and documentary mix.

MusicLibrary: the cues named in config `music_library` (beds as seamless
loops, bridges, a reveal sting, room tone) are generated once with
ElevenLabs sound generation, normalized to one reference loudness and
cached by content — shared by every film and every language.

DocumentaryMixer: lays the audio director's plan over a rendered
narration, using the narration's real timeline:
  * beds run under consecutive beats with the same mood, very quietly,
    fading in and out — never switching every beat;
  * music bridges / emotional moments / chapter breaks fill the planned
    gap after a beat (starting softly under the last words, fading under
    the first words of the next beat);
  * a sting marks a turn; a silence is near-silence (room tone, never
    dead digital air).
Then the whole mix is normalized to the narration target loudness.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

from app.core.ai_config import MusicCue, ai_config
from app.documentary import audio as A

ROOT = Path(__file__).resolve().parents[2]
MUSIC_TYPES = {"music_bridge", "emotional_moment", "chapter_break", "sting"}


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


class MusicLibrary:
    def __init__(self, provider=None):
        self.cfg = ai_config.music_library
        self._provider = provider

    @property
    def provider(self):
        if self._provider is None:
            from app.providers.voice.elevenlabs import ElevenLabsSoundProvider

            self._provider = ElevenLabsSoundProvider()
        return self._provider

    @property
    def dir(self) -> Path:
        d = Path(self.cfg.dir)
        return d if d.is_absolute() else ROOT / d

    def _key(self, cue: MusicCue) -> str:
        payload = json.dumps({"prompt": cue.prompt, "seconds": cue.seconds,
                              "loop": cue.loop,
                              "influence": self.cfg.prompt_influence,
                              "ref": self.cfg.reference_lufs}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def path_for(self, cue: MusicCue) -> Path:
        return self.dir / f"{cue.id}__{self._key(cue)}.wav"

    async def ensure(self, cue: MusicCue) -> tuple[Path, int | None]:
        """Path of the normalized cue; generates it once (returns the
        characters paid, None when it came from cache)."""
        path = self.path_for(cue)
        if path.exists():
            return path, None
        self.dir.mkdir(parents=True, exist_ok=True)
        audio, cost = await self.provider.generate(
            cue.prompt, cue.seconds, cue.loop, self.cfg.prompt_influence)
        raw = path.with_suffix(".mp3")
        raw.write_bytes(audio)
        await asyncio.to_thread(
            A.normalize_loudness, raw, path, self.cfg.reference_lufs,
            ai_config.loudness.true_peak_db, ai_config.loudness.lra,
            ai_config.loudness.sample_rate, 2,
        )
        return path, cost


def _cue_for(kind: str, mood: str | None) -> MusicCue | None:
    lib = ai_config.music_library
    if kind == "sting":
        return lib.find("sting", mood) or lib.find("sting")
    if kind == "silence":
        return lib.find("room_tone")
    if kind == "bed":
        return lib.find("bed", mood)
    wanted = "emotional" if kind == "emotional_moment" and not mood else mood
    return lib.find("bridge", wanted)


def plan_placements(manifest: dict, script: dict) -> list[dict]:
    """Where each cue goes on the narration timeline (seconds)."""
    direction = ai_config.audio_direction
    beat_audio = script.get("beat_audio") or {}
    spans = sorted(manifest["timeline"].get("beats") or [], key=lambda b: b["start"])
    total = manifest.get("duration_seconds") or (spans[-1]["end"] if spans else 0.0)
    if not spans:
        return []
    gaps = {}
    for a, b in zip(spans, spans[1:]):
        gaps[a["beat_id"]] = max(b["start"] - a["end"], 0.0)

    out: list[dict] = []
    # beds: one placement per run of beats with the same mood and level
    run: list[dict] = []

    def flush():
        if not run:
            return
        ba = beat_audio.get(run[0]["beat_id"], {})
        last = run[-1]
        after = (beat_audio.get(last["beat_id"], {}).get("after") or {}).get("type")
        end = last["end"] + (0.6 if after in MUSIC_TYPES else gaps.get(last["beat_id"], 0.0))
        start = max(run[0]["start"] - 0.8, 0.0)
        dur = min(end, total) - start
        if dur > 1.0:
            out.append({
                "role": "bed", "cue_kind": "bed", "mood": ba.get("bed"),
                "beats": [r["beat_id"] for r in run],
                "start": round(start, 3), "duration": round(dur, 3),
                "fade_in": min(2.0, dur / 3), "fade_out": min(2.5, dur / 3),
                "level_db": direction.bed_levels_db.get(ba.get("bed_level"), -26.0),
            })
        run.clear()

    for s in spans:
        ba = beat_audio.get(s["beat_id"], {})
        key = (ba.get("bed"), ba.get("bed_level"))
        if ba.get("bed") in (None, "none"):
            flush()
            continue
        if run:
            prev = beat_audio.get(run[-1]["beat_id"], {})
            if (prev.get("bed"), prev.get("bed_level")) != key:
                flush()
        run.append(s)
    flush()

    # moments in the gap after a beat
    for s in spans:
        after = (beat_audio.get(s["beat_id"], {}).get("after") or {})
        kind = after.get("type")
        gap = gaps.get(s["beat_id"])
        if gap is None or kind not in MUSIC_TYPES | {"silence"}:
            continue
        if kind == "silence":
            out.append({"role": "silence", "cue_kind": "silence", "mood": "neutral",
                        "after_beat": s["beat_id"], "start": round(s["end"], 3),
                        "duration": round(gap, 3), "fade_in": 0.3, "fade_out": 0.3,
                        "level_db": direction.room_tone_level_db})
            continue
        sting = kind == "sting"
        # Music moments rise softly under the beat's last words and keep
        # playing under the next beat's first words (never a hard stop).
        lead = 0.0 if sting else min(direction.music_lead_seconds, s["end"] - s["start"])
        tail = 2.0 if sting else direction.music_tail_seconds
        start = max(s["end"] - lead, 0.0)
        dur = min(gap + lead + tail, total - start)
        out.append({
            "role": kind, "cue_kind": "sting" if sting else kind,
            "mood": after.get("mood"), "after_beat": s["beat_id"],
            "start": round(start, 3), "duration": round(dur, 3),
            "fade_in": 0.02 if sting else round(lead + 0.4, 3),
            "fade_out": 1.2 if sting else round(max(tail, 1.5), 3),
            "level_db": direction.sting_level_db if sting else direction.moment_level_db,
        })
    return sorted(out, key=lambda p: p["start"])


class DocumentaryMixer:
    def __init__(self, library: MusicLibrary | None = None):
        self.library = library or MusicLibrary()
        self.loud = ai_config.loudness

    async def mix(self, manifest: dict, script: dict, out_dir: Path) -> dict:
        narration = out_dir / "narration.wav"
        placements = plan_placements(manifest, script)
        cost = 0
        resolved = []
        for p in placements:
            cue = _cue_for(p["cue_kind"], p.get("mood"))
            if cue is None:
                p["skipped"] = "no_cue_configured"
                continue
            path, paid = await self.library.ensure(cue)
            cost += paid or 0
            resolved.append({**p, "cue_id": cue.id, "path": path, "loop": cue.loop,
                             "cue_seconds": cue.seconds})

        mix_raw = out_dir / "documentary_unnormalized.wav"
        final_wav = out_dir / "documentary.wav"
        final_mp3 = out_dir / "documentary.mp3"
        if not resolved:
            await asyncio.to_thread(shutil.copyfile, narration, mix_raw)
        else:
            await asyncio.to_thread(_ffmpeg_mix, narration, resolved, mix_raw,
                                    self.loud.sample_rate)
        await asyncio.to_thread(
            A.normalize_loudness, mix_raw, final_wav,
            self.loud.narration_target_lufs, self.loud.true_peak_db,
            self.loud.lra, self.loud.sample_rate, 2,
        )
        after = await asyncio.to_thread(
            A.measure_loudness, final_wav, self.loud.narration_target_lufs,
            self.loud.true_peak_db, self.loud.lra)
        await asyncio.to_thread(A.encode_mp3, final_wav, final_mp3)
        mix_raw.unlink(missing_ok=True)
        music_only = sum(p["duration"] for p in resolved
                         if p["role"] in MUSIC_TYPES)
        return {
            "placements": [{k: (v if k != "path" else _rel(v)) for k, v in p.items()}
                           for p in resolved],
            "music_characters_paid": cost,
            "music_moments": sum(1 for p in resolved if p["role"] in MUSIC_TYPES),
            "beds": sum(1 for p in resolved if p["role"] == "bed"),
            "music_only_seconds": round(music_only, 1),
            "loudness_lufs": after["integrated_lufs"],
            "true_peak_db": after["true_peak_db"],
            "files": {"documentary_wav": _rel(final_wav),
                      "documentary_mp3": _rel(final_mp3)},
        }


def _tone_shaping(role: str) -> str:
    """Music under words leaves room for the voice: beds lose their top
    end and get a gentle dip in the speech-presence band; room tone is
    darkened so it reads as air, not hiss. Music-only moments are left
    untouched."""
    if role == "bed":
        return "lowpass=f=9000,equalizer=f=2500:t=q:w=1.2:g=-5,"
    if role == "silence":
        return "lowpass=f=5000,"
    return ""


def _ffmpeg_mix(narration: Path, placements: list[dict], dst: Path, sr: int) -> None:
    args = ["ffmpeg", "-y", "-v", "error", "-i", str(narration)]
    for p in placements:
        if p["loop"]:
            args += ["-stream_loop", "-1"]
        args += ["-i", str(p["path"])]
    chains = [f"[0:a]aformat=sample_rates={sr}:channel_layouts=stereo[n]"]
    labels = ["[n]"]
    for i, p in enumerate(placements, 1):
        dur = max(p["duration"], 0.05)
        fi = max(min(p["fade_in"], dur / 2), 0.01)
        fo = max(min(p["fade_out"], dur / 2), 0.01)
        delay = int(round(p["start"] * 1000))
        chains.append(
            f"[{i}:a]aformat=sample_rates={sr}:channel_layouts=stereo,"
            f"atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,{_tone_shaping(p['role'])}"
            f"afade=t=in:st=0:d={fi:.3f},afade=t=out:st={dur - fo:.3f}:d={fo:.3f},"
            f"volume={p['level_db']:.1f}dB,adelay={delay}|{delay}[m{i}]"
        )
        labels.append(f"[m{i}]")
    graph = ";".join(chains) + ";" + "".join(labels) + (
        f"amix=inputs={len(labels)}:normalize=0:duration=first:dropout_transition=0[out]"
    )
    args += ["-filter_complex", graph, "-map", "[out]", "-ac", "2", "-ar", str(sr),
             "-c:a", "pcm_s16le", str(dst)]
    A._run(args)
