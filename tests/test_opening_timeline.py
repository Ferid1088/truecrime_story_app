"""The opening block in the film's timeline: it comes first, everything else moves by its length, the gap is a black screen, the audio has it in front."""

import json
import subprocess
from pathlib import Path

import pytest

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin, ffprobe_bin
from app.documentary import storage
from app.documentary.production import opening_timeline as OT

ITEM = {"opening_id": 7, "audio": "x.wav", "seconds": 22.0, "speech_end": 20.0, "hook_end": 3.0, "pictures": [], "text": {"hook": "h", "context": "c"}}


@pytest.fixture()
def item(tmp_path):
    """The block with its two painted pictures on disk."""
    pictures = []
    for name, scene in (("a", "a garage door standing open at night"), ("b", "a hallway with a door ajar")):
        path = tmp_path / f"opening_{name}.png"
        path.write_bytes(b"png")
        pictures.append({"scene": scene, "path": str(path), "image_id": len(pictures) + 1})
    return {**ITEM, "pictures": pictures}


def image(asset, start, end, beat="B01"):
    return {"kind": "image", "command": "NEW_IMAGE", "motion": "SLOW_PUSH", "asset_id": asset, "path": f"p/{asset}.jpg", "start": start, "end": end,
            "beat_id": beat, "transition_in": "CROSSFADE", "usage": {"x": 1}, "id": f"shot_{asset}"}


def script():
    shots = [{"kind": "video", "command": "SHOW_CLIP", "host_scene_id": 1, "host_position": "opening", "start": 0.0, "end": 18.0, "asset_id": None},
             {"kind": "intro", "command": "CHANNEL_INTRO", "start": 18.0, "end": 24.0, "asset_id": None}]
    shots += [image(f"A{i}", 24.0 + i * 12, 36.0 + i * 12) for i in range(8)]
    return {"duration": 130.0, "shots": shots, "beats": [{"beat_id": "B01", "start": 24.0, "end": 130.0}], "voice": [{"start": 24.0, "end": 30.0}],
            "subtitles": [{"start": 24.0, "end": 27.0}], "overlays": [], "silences": [{"start": 60.0, "end": 61.0}], "music": [{"start": 70.0}],
            "intro": {"mode": "after_host", "start": 18.0, "seconds": 6.0}, "host": [{"scene_id": 1, "position": "opening", "start": 0.0, "end": 18.0, "narration_at": 0.0}],
            "timeline": {"story_seconds": 106.0, "host_seconds": 18.0, "intro_seconds": 6.0, "total_seconds": 130.0}}


def test_everything_moves_by_the_length_of_the_block_and_the_block_comes_first(item):
    before = script()
    out = OT.insert_opening(before, item)
    assert out["duration"] == 152.0 and before["duration"] == 130.0              # the script it was given is not changed
    assert out["beats"][0]["start"] == 46.0 and out["voice"][0]["start"] == 46.0 and out["silences"][0]["start"] == 82.0 and out["music"][0]["start"] == 92.0
    assert out["intro"]["start"] == 40.0 and out["host"][0]["start"] == 22.0 and out["host"][0]["narration_at"] == 0.0
    host = next(s for s in out["shots"] if s.get("host_position") == "opening")
    assert host["start"] == 22.0 and host["end"] == 40.0
    assert out["timeline"]["opening_seconds"] == 22.0 and out["timeline"]["total_seconds"] == 152.0
    block = [s for s in out["shots"] if s.get("opening_block")]
    assert block[0]["start"] == 0.0 and block[-1]["end"] == 22.0 and out["shots"][0] is block[0]


def test_the_gap_is_a_black_screen_after_the_speech_and_the_pictures_fill_the_speech(item):
    out = OT.insert_opening(script(), item)
    block = [s for s in out["shots"] if s.get("opening_block")]
    gap = block[-1]
    assert gap["kind"] == "black" and (gap["start"], gap["end"]) == (20.0, 22.0) and gap["intentional_gap"] is True
    pictures = block[:-1]
    assert pictures[0]["start"] == 0.0 and pictures[-1]["end"] == 20.0 and all(a["end"] == b["start"] for a, b in zip(pictures, pictures[1:], strict=False))
    assert all(p["end"] - p["start"] >= ai_config.opening_block_settings().pictures.min_hold_seconds for p in pictures)


def test_the_pictures_are_the_two_painted_ones_and_no_shot_of_the_film(item):
    out = OT.insert_opening(script(), item)
    shots = [s for s in out["shots"] if s.get("opening_block") and s.get("kind") == "image"]
    assert [s["path"] for s in shots] == [p["path"] for p in item["pictures"]] and all(s["asset_id"] is None and s["type"] == "generated" and s["credit_due"] is False for s in shots)
    assert shots[0]["transition_in"] == "FADE_BLACK" and shots[1]["transition_in"] == "CROSSFADE" and (shots[0]["end"], shots[1]["start"]) == (10.0, 10.0)


def test_without_a_painted_picture_the_speech_is_black_too():
    out = OT.insert_opening(script(), {**ITEM, "pictures": []})
    block = [s for s in out["shots"] if s.get("opening_block")]
    assert [s["kind"] for s in block] == ["black", "black"] and block[0]["end"] == 20.0


@pytest.mark.skipif(not ffmpeg_bin(), reason="ffmpeg is needed")
def test_the_audio_of_the_film_has_the_block_in_front(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "cases_root", lambda: tmp_path / "cases")
    opening, track = tmp_path / "o.wav", tmp_path / "t.wav"
    for path, rate, seconds in ((opening, 48000, 2.0), (track, 44100, 3.0)):
        subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f=440:sample_rate={rate}", "-t", str(seconds), "-ac", "1", str(path)], check=True)
    monkeypatch.setattr(storage, "resolve", lambda p: None if p is None else (Path(p) if str(p).startswith("/") else tmp_path / p))
    monkeypatch.setattr(storage, "rel", str)
    audio = OT.with_opening_audio({"audio": {"path": "t.wav"}}, {"audio": "o.wav"}, tmp_path / "joined.wav")
    from app.documentary.audio import probe_duration

    assert audio["with_opening"] is True and probe_duration(tmp_path / "joined.wav") == pytest.approx(5.0, abs=0.1)
    assert json.loads(subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "stream=sample_rate", "-of", "json", str(tmp_path / "joined.wav")],
                                     capture_output=True, text=True).stdout)["streams"][0]["sample_rate"] == "44100"


def test_the_preflight_does_not_report_the_meant_gap_as_black_between_pictures(item):
    from app.documentary.production import preflight as PF

    out = OT.insert_opening(script(), item)
    found = PF.problems(out["shots"], out["duration"], out["host"])
    assert not [f for f in found if "black" in f], found
    meant = [s for s in out["shots"] if s.get("intentional_gap")]
    assert len(meant) == 1
    meant[0].pop("intentional_gap")
    assert any("of black" in f for f in PF.problems(out["shots"], out["duration"], out["host"]))
