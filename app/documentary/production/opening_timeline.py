"""The opening block in the film's timeline: the hook, the pause, the context text and the dark, silent gap come first; the host's opening (or the logo), the
story and everything else move later by the block's length. The block's own pictures are the mystery pictures painted once for the film (the same in every language); the gap is a black screen."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin, ffprobe_bin
from app.documentary import storage
from app.documentary.opening_block import latest_opening
from app.documentary.production.host_timeline import _shift


class OpeningTimelineError(RuntimeError):
    pass


def opening_item(db: Session, case_id: int, language: str) -> dict | None:
    """The language's opening block as the film takes it: its newest row when the checks passed and its audio is on disk, unchanged. Anything else
    leaves the block out (a block that needs a person's look is not put in a film)."""
    row = latest_opening(db, case_id, language)
    if row is None or row.status != "valid":
        return None
    media = json.loads(row.media_json or "{}")
    audio = media.get("audio") or {}
    path = storage.resolve(audio.get("path")) if audio.get("path") else None
    if path is None or not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != audio.get("sha256"):
        return None
    opening = json.loads(row.opening_json or "{}")
    return {"opening_id": row.id, "audio": audio["path"], "seconds": float(audio["total"]), "speech_end": float(audio["context"][1]),
            "hook_end": float(audio["hook"][1]), "pictures": media.get("pictures") or [], "text": {"hook": opening.get("hook"), "context": opening.get("context")}}


def picture_shot(picture: dict) -> dict:
    """The film's shot fields of one painted picture of the block (it is no library asset: nothing to credit, no rights to check)."""
    return {"kind": "image", "command": "NEW_IMAGE", "asset_id": None, "path": picture["path"], "type": "generated", "role": "illustration", "width": 1536, "height": 1024, "focus": None,
            "crop": None, "highlight": None, "marker": None, "rights": "owned", "credit": None, "credit_due": False, "credit_on_screen": False, "subject_type": None, "tier": 5,
            "entity_type": None, "entities": []}


def insert_opening(script: dict, item: dict) -> dict:
    """The block at the start of the film: every later time moves by the block's seconds."""
    script = copy.deepcopy(script)
    d, speech_end = float(item["seconds"]), float(item["speech_end"])
    pictures = [p for p in item.get("pictures") or [] if storage.resolve(p["path"]).exists()]
    for key in ("shots", "beats", "voice", "subtitles", "overlays", "silences", "sentence_entities", "board"):
        _shift(script.get(key) or [], 0.0, d)
    _shift(script.get("music") or [], 0.0, d, keys=("start",))
    _shift(script.get("host") or [], 0.0, d)
    if script.get("intro") and script["intro"].get("start") is not None:
        script["intro"]["start"] = round(script["intro"]["start"] + d, 3)
    block = []
    if pictures:
        edges = [round(speech_end * i / len(pictures), 3) for i in range(len(pictures) + 1)]
        for n, s in enumerate(pictures):
            shot = picture_shot(s)
            shot.update({"beat_id": None, "start": edges[n], "end": edges[n + 1], "motion": "SLOW_PUSH", "speed": 1.0, "transition_in": "FADE_BLACK" if n == 0 else "CROSSFADE",
                         "why": "a mystery picture painted for the opening block", "opening_block": True})
            block.append(shot)
    else:
        block.append({"kind": "black", "command": "BLACK_SCREEN", "motion": "NONE", "speed": 1.0, "start": 0.0, "end": round(speech_end, 3),
                      "asset_id": None, "transition_in": "NONE", "beat_id": None, "why": "no picture for the opening block", "opening_block": True})
    block.append({"kind": "black", "command": "BLACK_SCREEN", "motion": "NONE", "speed": 1.0, "start": round(speech_end, 3), "end": round(d, 3),
                  "asset_id": None, "transition_in": "FADE_BLACK", "beat_id": None, "why": "the dark, silent gap before the host", "opening_block": True,
                  "intentional_gap": True})
    script["shots"] = sorted([*block, *script["shots"]], key=lambda s: (s["start"], s["end"]))
    for n, s in enumerate(script["shots"]):
        s["index"] = n
    script["opening_block"] = {**{k: item[k] for k in ("opening_id", "text", "hook_end", "speech_end")}, "start": 0.0, "end": round(d, 3), "audio": item["audio"]}
    timeline = script.get("timeline") or {"story_seconds": round(script["duration"], 3), "host_seconds": 0.0, "intro_seconds": 0.0}
    script["timeline"] = {**timeline, "opening_seconds": round(d, 3), "total_seconds": round(script["duration"] + d, 3)}
    script["duration"] = round(script["duration"] + d, 3)
    return script


def _stream(path: Path) -> tuple[int, int]:
    probe = ffprobe_bin()
    if not probe:
        raise OpeningTimelineError("ffprobe is not available")
    r = subprocess.run([probe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels", "-of", "json", str(path)],
                       capture_output=True, text=True)
    info = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
    return int(info.get("sample_rate") or ai_config.loudness.sample_rate), int(info.get("channels") or 2)


def prepend_audio(opening: Path, track: Path, out: Path) -> Path:
    """The film's audio with the block's audio in front: one continuous track at the track's own rate and channels."""
    ff = ffmpeg_bin()
    if not ff:
        raise OpeningTimelineError("ffmpeg is not available")
    rate, channels = _stream(track)
    layout = "mono" if channels == 1 else "stereo"
    fmt = f"aformat=sample_rates={rate}:channel_layouts={layout}"
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([ff, "-y", "-v", "error", "-i", str(opening), "-i", str(track), "-filter_complex",
                        f"[0:a]{fmt}[a];[1:a]{fmt}[b];[a][b]concat=n=2:v=0:a=1[o]", "-map", "[o]", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise OpeningTimelineError(f"ffmpeg could not put the opening audio in front: {r.stderr[-300:]}")
    return out


def with_opening_audio(script: dict, item: dict, out: Path) -> dict:
    """The script's audio track with the block's audio in front."""
    track = storage.resolve(script["audio"]["path"])
    opening = storage.resolve(item["audio"])
    if track is None or opening is None:
        raise OpeningTimelineError("the film's audio or the opening's audio is missing")
    prepend_audio(opening, track, out)
    return {**script["audio"], "path": storage.rel(out), "with_opening": True}
