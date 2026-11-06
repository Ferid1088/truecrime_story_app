"""The avatar's voice has the story voice's parameters in every language: stability 0.2 everywhere, speed 1.2 and the 1.1 post speed for Persian, the
narration's pace for the others; a host style that differs is refused before anything is paid; a voice made under other values is made again."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess

import pytest
from test_studio import FakeVoice, _renderer, _segments

from app.core.ai_config import ai_config
from app.documentary import audio as A
from app.documentary import host_scenes as HS
from app.documentary import storage
from app.documentary.studio import StudioError


@pytest.mark.parametrize("language", ["fa", "en", "de", "ar"])
def test_the_host_style_has_the_story_voices_stability_and_the_language_pace(language):
    host = ai_config.voice.settings_for(ai_config.host.voice_style, language)
    story = ai_config.voice.settings_for(ai_config.voice.default_style, language)
    assert host["stability"] == story["stability"] == (0.7 if language == "de" else 0.2) and host["speed"] == story["speed"]
    assert HS.required_voice(language, host) == ai_config.voice.post_speed.get(language, 1.0)
    assert (HS.required_voice("fa", ai_config.voice.settings_for(ai_config.host.voice_style, "fa")), host["speed"] == 1.2) == (1.1, language == "fa")


@pytest.mark.parametrize("language,settings", [("fa", {"stability": 0.2, "speed": 1.02}), ("en", {"stability": 0.4, "speed": 0.9}), ("ar", {"stability": 0.5, "speed": 1.02})])
def test_a_host_style_that_is_not_the_story_voices_is_refused_before_anything_is_paid(language, settings):
    with pytest.raises(StudioError, match="not the story voice's"):
        HS.required_voice(language, settings)


def test_a_post_speed_in_the_config_that_the_language_does_not_require_is_refused(monkeypatch):
    monkeypatch.setitem(ai_config.voice.post_speed, "de", 1.1)
    with pytest.raises(StudioError, match="post speed 1.1 instead of 1.0"):
        HS.required_voice("de", ai_config.voice.settings_for(ai_config.host.voice_style, "de"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for audio tests")
def test_the_host_audio_is_sped_up_by_the_post_speed_and_its_times_with_it(tmp_path):
    raw = tmp_path / "raw.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=3:sample_rate=44100", "-ac", "1", str(raw)], check=True)
    audio, alignment = HS.sped_up(raw.read_bytes(), 1.1, {"characters": ["a", "b"], "starts": [0.0, 1.1], "ends": [1.1, 2.2]})
    out = tmp_path / "fast.mp3"
    out.write_bytes(audio)
    assert A.probe_duration(out) == pytest.approx(3 / 1.1, abs=0.15)
    assert alignment["starts"] == pytest.approx([0.0, 1.0]) and alignment["ends"] == pytest.approx([1.0, 2.0]) and alignment["characters"] == ["a", "b"]
    same, untouched = HS.sped_up(b"x", 1.0, {"starts": [1], "ends": [2]})
    assert same == b"x" and untouched == {"starts": [1], "ends": [2]}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for audio tests")
def test_the_persian_host_voice_is_made_at_12_02_and_sped_up_by_11_and_an_older_one_gets_the_speed_up_without_a_new_take(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import plan_host_scenes, run_scene

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    scene = plan_host_scenes(db_session, _segments(db_session, "fa"))[0]
    voice = FakeVoice()
    asyncio.run(run_scene(db_session, scene, "voice", renderer=_renderer(voice)))
    meta = json.loads(scene.voice_meta_json)
    assert (meta["settings"]["speed"], meta["settings"]["stability"], meta["post_speed"]) == (1.2, 0.2, 1.1)
    asyncio.run(run_scene(db_session, scene, "voice", renderer=_renderer(voice)))
    assert voice.calls == 1, "the same voice is kept"
    del meta["post_speed"]                                  # a voice made before the post speed existed
    scene.voice_meta_json = json.dumps(meta)
    sha = scene.voice_sha256
    assert storage.resolve(scene.voice_path).exists()
    asyncio.run(run_scene(db_session, scene, "voice", renderer=_renderer(voice)))
    assert voice.calls == 1, "the take is in the cache: the speed-up is made again, nothing is paid"
    assert json.loads(scene.voice_meta_json)["post_speed"] == 1.1 and scene.voice_sha256 == sha
