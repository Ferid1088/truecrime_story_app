"""Part 8a: host scenes in the film timeline."""

import shutil
import subprocess

import pytest

from app.core.media_tools import ffmpeg_bin, ffprobe_bin
from app.documentary.production import host_timeline as HT


def _script():
    return {
        "duration": 60.0,
        "beats": [{"beat_id": "B1", "start": 0.0, "end": 20.0}, {"beat_id": "B2", "start": 20.0, "end": 40.0},
                  {"beat_id": "B3", "start": 40.0, "end": 60.0}],
        "voice": [{"block_id": "v1", "start": 0.0, "end": 19.0}, {"block_id": "v2", "start": 21.0, "end": 59.0}],
        "silences": [{"start": 19.0, "duration": 2.0}],
        "shots": [{"index": 0, "kind": "image", "start": 0.0, "end": 25.0, "asset_id": "A1"},
                  {"index": 1, "kind": "image", "start": 25.0, "end": 60.0, "asset_id": "A2"}],
        "subtitles": [{"start": 1.0, "end": 3.0, "text": "a"}, {"start": 45.0, "end": 47.0, "text": "b"}],
        "overlays": [{"kind": "label", "text": "x", "start": 42.0, "end": 44.0}],
        "music": [{"cue_id": "m", "start": 30.0, "duration": 10.0}],
        "intro": {"mode": "gap", "start": 22.0},
        "sentence_entities": [{"beat_id": "B1", "n": 1, "start": 0.0, "end": 20.0, "entities": ["a"]},
                              {"beat_id": "B3", "n": 1, "start": 40.0, "end": 60.0, "entities": ["b"]}],
    }


def _item(scene_id, position, beat, seconds):
    return {"scene_id": scene_id, "position": position, "beat_id": beat, "seconds": seconds,
            "video": f"host_{scene_id}.mp4", "audio": f"host_{scene_id}.mp3", "gain_db": 0.0, "text": "t"}


def test_opening_goes_before_beat_one_and_everything_after_it_moves():
    out = HT.insert_host(_script(), [_item(1, "opening", None, 10.0)])
    assert out["duration"] == 70.0 and out["host_seconds"] == 10.0
    first = out["shots"][0]
    assert (first["kind"], first["start"], first["end"], first["host_position"]) == ("video", 0.0, 10.0, "opening")
    assert out["beats"][0] == {"beat_id": "B1", "start": 10.0, "end": 30.0}
    assert out["subtitles"][0]["start"] == 11.0 and out["music"][0]["start"] == 40.0
    assert out["intro"]["start"] == 32.0 and out["silences"][0]["start"] == 29.0
    assert out["voice"][0]["start"] == 10.0


def test_the_sentence_spans_move_with_the_film_so_pictures_meet_their_own_words():
    out = HT.insert_host(_script(), [_item(1, "opening", None, 10.0), _item(2, "mid", "B1", 8.0)])
    assert [(x["start"], x["end"]) for x in out["sentence_entities"]] == [(10.0, 30.0), (58.0, 78.0)]
    assert out["sentence_entities"][0]["start"] == out["beats"][0]["start"]


def test_a_mid_appearance_follows_its_beat_and_a_spanning_shot_is_split():
    out = HT.insert_host(_script(), [_item(2, "mid", "B1", 8.0)])
    host = next(s for s in out["shots"] if s.get("host_scene_id") == 2)
    assert (host["start"], host["end"]) == (20.0, 28.0)
    images = [s for s in out["shots"] if s["kind"] == "image"]
    # A1 ran 0-25 across the insertion at 20: split into 0-20 and 28-33
    assert [(s["asset_id"], s["start"], s["end"]) for s in images] == [
        ("A1", 0.0, 20.0), ("A1", 28.0, 33.0), ("A2", 33.0, 68.0)]
    assert out["beats"][1] == {"beat_id": "B2", "start": 28.0, "end": 48.0}
    assert [s["index"] for s in out["shots"]] == [0, 1, 2, 3]


def test_opening_and_two_mids_accumulate_their_offsets():
    items = [_item(3, "mid", "B2", 6.0), _item(1, "opening", None, 10.0), _item(2, "mid", "B1", 8.0)]
    out = HT.insert_host(_script(), items)
    assert out["duration"] == 84.0 and out["host_seconds"] == 24.0
    starts = {h["scene_id"]: (h["start"], h["narration_at"]) for h in out["host"]}
    assert starts == {1: (0.0, 0.0), 2: (30.0, 20.0), 3: (58.0, 40.0)}
    assert round(HT.host_share(out), 4) == round(24 / 84, 4)


def _plain_script():
    script = _script()
    script.pop("intro")
    return script


def test_the_order_is_host_opening_then_logo_then_story():
    out = HT.insert_host(_plain_script(), [_item(1, "opening", None, 10.0)], intro_seconds=6.0)
    host, logo, first = out["shots"][0], out["shots"][1], out["shots"][2]
    assert (host["kind"], host["start"], host["end"]) == ("video", 0.0, 10.0)
    assert (logo["kind"], logo["command"], logo["start"], logo["end"]) == ("intro", "CHANNEL_INTRO", 10.0, 16.0)
    assert (first["kind"], first["start"]) == ("image", 16.0)             # the story starts last
    assert out["intro"] == {"mode": "after_host", "start": 10.0, "seconds": 6.0}
    assert out["beats"][0]["start"] == 16.0 and out["voice"][0]["start"] == 16.0
    assert out["subtitles"][0]["start"] == 17.0 and out["music"][0]["start"] == 46.0
    assert [s["index"] for s in out["shots"]] == [0, 1, 2, 3]
    # the host's own audio keeps a silence of the logo's length behind it
    assert out["host"][0]["pad_after"] == 6.0 and out["host"][0]["narration_at"] == 0.0


def test_the_film_is_story_plus_host_plus_logo_from_the_real_durations():
    items = [_item(1, "opening", None, 10.0), _item(2, "mid", "B2", 8.0)]
    out = HT.insert_host(_plain_script(), items, intro_seconds=6.0)
    assert out["timeline"] == {"story_seconds": 60.0, "host_seconds": 18.0,
                               "intro_seconds": 6.0, "total_seconds": 84.0}
    assert out["duration"] == 84.0 and out["host_seconds"] == 18.0
    # the mid appearance stands after beat 2 of the story, which now starts after host and logo
    mid = next(h for h in out["host"] if h["scene_id"] == 2)
    assert mid["start"] == 16.0 + 40.0 and mid["narration_at"] == 40.0
    assert round(HT.host_share(out), 4) == round(18 / 84, 4)


def test_without_an_opening_the_scripts_own_intro_stays_where_it_is():
    out = HT.insert_host(_script(), [_item(2, "mid", "B1", 8.0)], intro_seconds=None)
    assert out["intro"]["mode"] == "gap" and out["intro"]["start"] == 30.0
    assert not [s for s in out["shots"] if s["kind"] == "intro"]
    assert out["timeline"]["intro_seconds"] == 0.0


def test_a_script_that_already_placed_the_logo_is_refused_when_a_host_opening_places_it():
    with pytest.raises(HT.HostTimelineError, match="already placed"):
        HT.insert_host(_script(), [_item(1, "opening", None, 10.0)], intro_seconds=6.0)


def test_the_splice_pads_the_host_audio_with_the_logo_silence():
    host = [{"audio": "h1.wav", "narration_at": 0.0, "gain_db": 0.0, "pad_after": 6.0},
            {"audio": "h2.wav", "narration_at": 12.0, "gain_db": -3.0, "pad_after": 0.0}]
    cmd = HT.splice_command(__import__("pathlib").Path("n.wav"), host, __import__("pathlib").Path("o.wav"),
                            lambda p: p)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "apad=pad_dur=6.000" in graph and graph.count("apad") == 1


def test_a_planned_beat_that_is_not_in_the_film_and_two_openings_are_refused():
    with pytest.raises(HT.HostTimelineError, match="not in the film"):
        HT.insert_host(_script(), [_item(1, "mid", "B9", 5.0)])
    with pytest.raises(HT.HostTimelineError, match="more than one"):
        HT.insert_host(_script(), [_item(1, "opening", None, 5.0), _item(2, "opening", None, 5.0)])


def test_no_host_items_leave_the_script_unchanged():
    assert HT.insert_host(_script(), []) == _script()


@pytest.mark.skipif(not (ffmpeg_bin() and shutil.which(ffmpeg_bin()) or ffmpeg_bin() and __import__("os").path.exists(ffmpeg_bin())),
                    reason="ffmpeg not available")
def test_the_host_audio_is_spliced_into_the_narration_with_the_real_lengths(tmp_path):
    def tone(path, seconds, freq):
        subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={freq}:duration={seconds}", str(path)], check=True)

    narration, h1, h2 = tmp_path / "n.wav", tmp_path / "h1.wav", tmp_path / "h2.wav"
    tone(narration, 30, 440)
    tone(h1, 4, 880)
    tone(h2, 3, 660)
    host = [{"audio": "h1.wav", "narration_at": 0.0, "gain_db": 0.0},
            {"audio": "h2.wav", "narration_at": 12.0, "gain_db": -3.0}]
    out = HT.splice_host_audio(narration, host, tmp_path / "out.wav", lambda p: tmp_path / p)
    seconds = float(subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True).stdout)
    assert abs(seconds - 37.0) < 0.15          # 30 s narration + 4 s + 3 s of host
    padded = [{**host[0], "pad_after": 6.0}, host[1]]
    out = HT.splice_host_audio(narration, padded, tmp_path / "padded.wav", lambda p: tmp_path / p)
    seconds = float(subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True).stdout)
    assert abs(seconds - 43.0) < 0.15          # ... and 6 s of silence for the channel logo


def test_the_final_checks_flag_host_time_over_the_caps():
    from app.documentary.production.critics import deterministic_checks

    script = HT.insert_host(_script(), [_item(1, "opening", None, 130.0)])
    issues = deterministic_checks(script, {})["issues"]
    host = [i for i in issues if i["check"] == "host_time"]
    assert len(host) == 1 and "cap" in host[0]["why"]       # one appearance over 120 s
    ok = deterministic_checks(HT.insert_host(_script(), [_item(1, "opening", None, 60.0)]), {})["issues"]
    assert not [i for i in ok if i["check"] == "host_time"]


def test_scenes_that_went_into_the_film_are_marked_composited(db_session):
    from test_visual_memory import _scene

    scene = _scene(db_session, "ChanX")
    HT.mark_composited(db_session, [{"scene_id": scene.id}])
    db_session.refresh(scene)
    assert scene.status == "composited"


def test_the_renderer_lays_the_logo_sound_into_the_silence_after_the_host_without_ducking():
    from app.documentary.render.engine import _intro_audio_graph

    graph = _intro_audio_graph("after_host", 10.0, 6.0)
    assert "adelay=10000|10000" in graph and "volume=" not in graph
    assert "duration=longest" not in graph
    assert "volume=" in _intro_audio_graph("gap", 10.0, 6.0)            # the cold-open break ducks


def test_the_outro_goes_after_the_last_second_of_the_story():
    items = [_item(1, "opening", None, 10.0), _item(2, "mid", "B2", 8.0), _item(3, "final", None, 12.0)]
    out = HT.insert_host(_plain_script(), items, intro_seconds=6.0)
    outro = next(h for h in out["host"] if h["scene_id"] == 3)
    assert outro["position"] == "final" and outro["narration_at"] == 60.0 and outro["tail"] is True
    assert (outro["start"], outro["end"]) == (16.0 + 60.0 + 8.0, 16.0 + 60.0 + 8.0 + 12.0)
    assert out["duration"] == 60.0 + 10.0 + 6.0 + 8.0 + 12.0 and out["host_seconds"] == 30.0
    last = out["shots"][-1]
    assert (last["host_scene_id"], last["host_position"], last["end"]) == (3, "final", out["duration"])


def test_the_splice_leaves_no_narration_after_an_outro():
    host = [{"audio": "h1.wav", "narration_at": 40.0, "gain_db": 0.0, "pad_after": 0.0, "tail": False},
            {"audio": "h2.wav", "narration_at": 60.0, "gain_db": 0.0, "pad_after": 0.0, "tail": True}]
    path = __import__("pathlib").Path
    graph = HT.splice_command(path("n.wav"), host, path("o.wav"), lambda p: p)
    graph = graph[graph.index("-filter_complex") + 1]
    assert "nend" not in graph and graph.endswith("concat=n=4:v=0:a=1[a]")
    host[-1]["tail"] = False
    graph = HT.splice_command(path("n.wav"), host, path("o.wav"), lambda p: p)
    assert "nend" in graph[graph.index("-filter_complex") + 1]


def test_an_act_anchor_resolves_to_the_last_beat_of_that_act():
    from app.documentary.production.host_timeline import act_end_beats

    text = ("[[ACT:act1]]\n\nOne.\n\nTwo.\n\n[[ACT:act2]]\n\nThree.\n\nFour.\n\nFive.\n\n[[ACT:act3]]\n\nSix.")
    # paragraphs counted with each marker as one: act1 = 1..3, act2 = 4..8, act3 = 9..10
    beats = [{"id": "B01", "paragraphs": [1, 2]}, {"id": "B02", "paragraphs": [3, 3]},
             {"id": "B03", "paragraphs": [4, 6]}, {"id": "B04", "paragraphs": [7, 8]},
             {"id": "B05", "paragraphs": [9, 10]}]
    assert act_end_beats(text, beats) == {"act1": "B02", "act2": "B04", "act3": "B05"}
