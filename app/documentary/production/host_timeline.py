"""Host scenes in the film timeline (Part 8a).

The host's opening comes before beat 1, each mid appearance after its
planned beat and the outro after the last second of the story. Everything after an insertion moves by the host's length: shots,
beats, voice blocks, subtitles, overlays, music, silences and the intro; a shot
that spans the insertion is split. Host shots are ordinary `video` shots (the
renderer already plays clips); the host's own audio is spliced into the
narration track at the same points, with a level gain."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin

_EPS = 0.02


class HostTimelineError(RuntimeError):
    pass


def _insertion_point(script: dict, item: dict) -> float:
    if item["position"] == "opening":
        return 0.0
    if item["position"] == "final":
        return float(script["duration"])
    for b in script.get("beats") or []:
        if b["beat_id"] == item.get("beat_id"):
            return float(b["end"])
    raise HostTimelineError(f"host scene {item['scene_id']}: beat {item.get('beat_id')!r} "
                            "is not in the film")


def _shift(rows: list[dict], t: float, d: float, keys=("start", "end")) -> None:
    for r in rows:
        if r.get("start") is not None and r["start"] >= t - _EPS:
            for k in keys:
                if r.get(k) is not None:
                    r[k] = round(r[k] + d, 3)


def _split_spanning(shots: list[dict], t: float) -> list[dict]:
    out = []
    for s in shots:
        if s["start"] < t - _EPS and s["end"] > t + _EPS:
            a, b = dict(s), dict(s)
            a["end"] = round(t, 3)
            b["start"] = round(t, 3)
            b["transition_in"] = "CUT"
            if s.get("kind") == "video" and s.get("clip_start") is not None:
                b["clip_start"] = round(float(s["clip_start"]) + (t - s["start"]), 3)
            out += [a, b]
        else:
            out.append(s)
    return out


def insert_host(script: dict, items: list[dict], intro_seconds: float | None = None) -> dict:
    """items: [{scene_id, position (opening|mid|final), beat_id, seconds, video, audio,
    gain_db, text}] -> a new script with the host in it (position: opening | mid | final).

    The film's order is the host's opening, then the channel's logo video (`intro_seconds`),
    then the story: the logo is placed right after the opening, so the script must not have
    placed it anywhere else. Without an opening the script's own intro placement stands."""
    script = copy.deepcopy(script)
    if not items:
        return script
    if sum(1 for i in items if i["position"] == "opening") > 1:
        raise HostTimelineError("more than one host opening")
    if intro_seconds and script.get("intro"):
        raise HostTimelineError("the script already placed the channel intro; a host opening "
                                "places it right after itself")
    story_seconds = float(script["duration"])
    original = {id(i): _insertion_point(script, i) for i in items}
    ordered = sorted(items, key=lambda i: (original[id(i)], i["position"] != "opening"))
    offset = 0.0
    host_seconds = 0.0
    host: list[dict] = []
    for item in ordered:
        d = float(item["seconds"])
        t_orig = original[id(item)]
        t = t_orig + offset
        logo = float(intro_seconds) if item["position"] == "opening" and intro_seconds else 0.0
        script["shots"] = _split_spanning(script["shots"], t)
        for key in ("shots", "beats", "voice", "subtitles", "overlays", "silences", "sentence_entities"):
            _shift(script.get(key) or [], t, d + logo)
        _shift(script.get("music") or [], t, d + logo, keys=("start",))
        if script.get("intro") and script["intro"].get("start") is not None \
                and script["intro"]["start"] >= t - _EPS:
            script["intro"]["start"] = round(script["intro"]["start"] + d, 3)
        if logo:
            script["shots"].append({
                "kind": "intro", "command": "CHANNEL_INTRO", "motion": "NONE", "speed": 1.0,
                "start": round(t + d, 3), "end": round(t + d + logo, 3), "asset_id": None,
                "transition_in": "NONE", "beat_id": None, "intro": {"seconds": logo},
                "why": "the channel intro right after the host's opening"})
            script["intro"] = {"mode": "after_host", "start": round(t + d, 3), "seconds": logo}
        script["shots"].append({
            "kind": "video", "command": "SHOW_CLIP", "motion": "NONE", "path": item["video"],
            "clip_start": 0.0, "clip_end": round(d, 3), "start": round(t, 3),
            "end": round(t + d, 3), "asset_id": None, "transition_in": "CUT",
            "host_scene_id": item["scene_id"], "host_position": item["position"],
            "beat_id": item.get("beat_id")})
        host.append({"scene_id": item["scene_id"], "position": item["position"],
                     "beat_id": item.get("beat_id"), "start": round(t, 3),
                     "end": round(t + d, 3), "narration_at": round(t_orig, 3),
                     "tail": item["position"] == "final",
                     "audio": item["audio"], "gain_db": item["gain_db"], "text": item.get("text"),
                     "pad_after": logo})
        offset += d + logo
        host_seconds += d
    script["shots"].sort(key=lambda s: (s["start"], s["end"]))
    for n, s in enumerate(script["shots"]):
        s["index"] = n
    script["host"] = host
    script["host_seconds"] = round(host_seconds, 3)
    script["duration"] = round(script["duration"] + offset, 3)
    script["timeline"] = {"story_seconds": round(story_seconds, 3),
                          "host_seconds": round(host_seconds, 3),
                          "intro_seconds": round(offset - host_seconds, 3),
                          "total_seconds": script["duration"]}
    return script


def host_share(script: dict) -> float:
    d = float(script.get("duration") or 0.0)
    return round(float(script.get("host_seconds") or 0.0) / d, 4) if d else 0.0


def splice_command(narration: Path, host: list[dict], out: Path, resolve) -> list[str]:
    """ffmpeg command: narration cut at each insertion point, the host's own
    audio (with its gain) in between, one continuous track."""
    ff = ffmpeg_bin()
    if not ff:
        raise HostTimelineError("ffmpeg is not available")
    cmd = [ff, "-y", "-v", "error", "-i", str(narration)]
    for h in host:
        cmd += ["-i", str(resolve(h["audio"]))]
    cuts = [h["narration_at"] for h in host]
    parts, labels = [], []
    start = 0.0
    for n, c in enumerate(cuts):
        if c - start > _EPS:
            parts.append(f"[0:a]atrim={start:.3f}:{c:.3f},asetpts=PTS-STARTPTS[n{n}]")
            labels.append(f"[n{n}]")
        pad = f",apad=pad_dur={host[n]['pad_after']:.3f}" if host[n].get("pad_after") else ""
        parts.append(f"[{n + 1}:a]volume={host[n]['gain_db']}dB{pad},asetpts=PTS-STARTPTS[h{n}]")
        labels.append(f"[h{n}]")
        start = c
    if not (host and host[-1].get("tail")):     # an outro leaves no narration after it
        parts.append(f"[0:a]atrim=start={start:.3f},asetpts=PTS-STARTPTS[nend]")
        labels.append("[nend]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(labels)}:v=0:a=1[a]"
    return cmd + ["-filter_complex", graph, "-map", "[a]", str(out)]


def splice_host_audio(narration: Path, host: list[dict], out: Path, resolve) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(splice_command(narration, host, out, resolve), capture_output=True, text=True)
    if r.returncode != 0:
        raise HostTimelineError(f"ffmpeg splice failed: {r.stderr[-300:]}")
    return out


def host_gain_db() -> float:
    return ai_config.host.audio_gain_db


def avatar_present(db, production_script_id: int | None) -> bool:
    """Owner decision 2026-10-22: the avatar is required only for the FINAL version, never for
    building or rendering a draft (A2/A3). This is the one place that answers "does this
    production script actually carry the host" — read back from the `avatar_used` flag the
    script itself was stamped with when built (`build_production_script`), never re-derived, so
    a draft and its eventual publish gate always agree on the same fact."""
    from app.db.models import ProductionScript

    if production_script_id is None:
        return False
    ps = db.get(ProductionScript, production_script_id)
    if ps is None:
        return False
    return bool(json.loads(ps.script_json or "{}").get("avatar_used"))


def act_end_beats(master_text: str, beats: list[dict]) -> dict[str, str]:
    """act id -> the last beat of that act. The Host plan anchors a mid appearance to the act it follows
    (`after_act`); the film is cut in beats. The blueprint numbers its paragraphs over the master text,
    counting each `[[ACT:id]]` marker line as one, so an act ends at the running count of its marker plus
    its paragraphs, and its last beat is the last one that starts inside that count."""
    from app.agents.story import _parse_sections

    out: dict[str, str] = {}
    index = 0
    for section in _parse_sections(master_text or ""):
        index += 1 + len([p for p in section["text"].split("\n\n") if p.strip()])
        inside = [b["id"] for b in beats if b.get("paragraphs") and b["paragraphs"][0] <= index]
        if inside:
            out[section["id"]] = inside[-1]
    return out


def _anchor_beats(db, version, items: list[dict]) -> None:
    """Resolve an act anchor to a beat of the film, in place; a beat id stays as it is."""
    from app.db.models import StoryVersion
    from app.documentary.blueprint import latest_blueprint

    row = latest_blueprint(db, version.master_version_id) if version.master_version_id else None
    beats = (json.loads(row.blueprint_json or "{}") if row else {}).get("beats") or []
    known = {b["id"] for b in beats}
    if not any(i.get("beat_id") and i["beat_id"] not in known for i in items):
        return
    master = db.get(StoryVersion, version.master_version_id)
    ends = act_end_beats(master.story_text if master else "", beats)
    for item in items:
        if item.get("beat_id") and item["beat_id"] not in known and item["beat_id"] in ends:
            item["act_id"], item["beat_id"] = item["beat_id"], ends[item["beat_id"]]


def host_items(db, version) -> list[dict]:
    """The version's host scenes that are ready (their avatar video is on disk
    and unchanged): what goes into the film."""
    import hashlib

    from app.db.models import HostScene
    from app.documentary import storage
    from app.documentary.host import latest_host_segments

    seg = latest_host_segments(db, version.id)
    if seg is None:
        return []
    items = []
    for s in db.query(HostScene).filter(HostScene.host_segments_id == seg.id).order_by(HostScene.id):
        video = storage.resolve(s.avatar_video_path) if s.avatar_video_path else None
        voice = storage.resolve(s.voice_path) if s.voice_path else None
        if (video is None or not video.exists() or voice is None or not voice.exists()
                or hashlib.sha256(video.read_bytes()).hexdigest() != s.avatar_video_sha256
                or not s.voice_seconds or s.position not in ("opening", "mid", "final")):
            continue
        items.append({"scene_id": s.id, "position": s.position, "beat_id": s.beat_id,
                      "seconds": float(s.voice_seconds), "video": s.avatar_video_path,
                      "audio": s.voice_path, "gain_db": host_gain_db(), "text": s.text})
    _anchor_beats(db, version, items)
    return items


def mark_composited(db, host: list[dict]) -> None:
    """The scenes that went into the film's timeline: status composited."""
    from app.db.models import HostScene

    ids = [h["scene_id"] for h in host]
    if ids:
        db.query(HostScene).filter(HostScene.id.in_(ids)).update(
            {"status": "composited"}, synchronize_session=False)
        db.commit()
