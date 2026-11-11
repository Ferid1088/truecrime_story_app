"""The audio of an opening block: the hook, the pause with the music beginning, the context text and the dark, silent gap (real ffmpeg on tones)."""

import asyncio
import json
import re
import subprocess
from pathlib import Path

import pytest
from test_host import _plan_story

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin
from app.db.models import FilmOpening, ProviderCall
from app.documentary import opening_block as OB
from app.documentary import opening_media as OM
from app.documentary import storage

pytestmark = pytest.mark.skipif(not ffmpeg_bin(), reason="ffmpeg is needed")


def tone(path: Path, freq: int, seconds: float, volume: float = 0.3) -> Path:
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f={freq}:sample_rate=44100", "-t", str(seconds),
                    "-af", f"volume={volume}", str(path)], check=True)
    return path


def level_db(path: Path, start: float, seconds: float, lowpass: int | None = None) -> float:
    """The loudest level of a stretch of the file (optionally only below `lowpass` Hz: the music is low, the voices are not)."""
    af = (f"lowpass=f={lowpass}," if lowpass else "") + "volumedetect"
    r = subprocess.run([ffmpeg_bin(), "-v", "info", "-ss", str(start), "-t", str(seconds), "-i", str(path), "-af", af, "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"max_volume: (-?[\d.]+|-inf) dB", r.stderr)
    return float("-inf") if m is None or m.group(1) == "-inf" else float(m.group(1))


def test_the_music_begins_after_the_hook_and_the_gap_is_dark_and_silent(tmp_path):
    hook, context, music = tone(tmp_path / "h.wav", 5000, 2.0), tone(tmp_path / "c.wav", 6000, 4.0), tone(tmp_path / "m.wav", 200, 12.0, 0.5)
    cfg = ai_config.opening_block_settings()
    out = tmp_path / "opening.wav"
    timings = OM.assemble(hook, context, music, 2.0, 4.0, out, cfg)
    assert timings["context"] == [2.0 + cfg.hook_pause_seconds, 6.0 + cfg.hook_pause_seconds]
    assert timings["gap"][1] - timings["gap"][0] == cfg.gap_seconds and timings["seconds"] == pytest.approx(timings["total"], abs=0.1)
    assert level_db(out, 0.1, 1.5, lowpass=400) < -50                       # no music under the first statement
    assert level_db(out, 2.2, cfg.hook_pause_seconds - 0.4, lowpass=400) > -45          # the music begins in the pause after it
    assert level_db(out, timings["gap"][0] + 0.2, cfg.gap_seconds - 0.3) < -50          # the gap is silent: voice and music are over


def test_without_music_only_the_voice_and_the_gap_are_made(tmp_path):
    hook, context = tone(tmp_path / "h.wav", 5000, 1.5), tone(tmp_path / "c.wav", 6000, 3.0)
    out = tmp_path / "opening.wav"
    timings = OM.assemble(hook, context, None, 1.5, 3.0, out)
    assert timings["music"] is None and level_db(out, 1.6, 0.8, lowpass=400) < -50
    assert level_db(out, timings["gap"][0] + 0.2, 1.5) < -50


class FakeRenderer:
    def __init__(self, tmp_path):
        self.tmp, self.calls = tmp_path, []

    async def speak_block(self, block, language, vcfg, settings, seed, takes_dir):
        self.calls.append((block["block_id"], language, settings))
        seconds = 2.0 if block["block_id"].endswith("hook") else 4.0
        raw = tone(self.tmp / f"{block['block_id']}.mp3", 1000, seconds)
        return {"take": {"raw_path": str(raw), "alignment": {"characters": ["a"], "starts": [0.0], "ends": [seconds]}, "character_cost": 12, "request_id": "r1"}}


class FakeLibrary:
    def __init__(self, tmp_path):
        self.dir, self.made = tmp_path / "library", []

    async def _generate(self, prompt, seconds, loop, path):
        self.dir.mkdir(exist_ok=True)
        tone(path, 200, seconds, 0.5)
        self.made.append(prompt)
        return 77


def test_a_row_is_voiced_in_its_language_with_one_music_for_the_film(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "cases_root", lambda: tmp_path / "cases")
    case, master_version, _ = _plan_story(db_session)
    opening = {"hook": "Someone locked the door.", "context": "A lantern in the hall. Nobody explains it.", "music": {"mood": "tension", "prompt": "slow cello drone"}}
    en = FilmOpening(case_id=case.id, language="en", kind="master", story_version_id=master_version.id, status="valid", opening_json=json.dumps(opening))
    fa = FilmOpening(case_id=case.id, language="fa", kind="native", story_version_id=master_version.id, status="valid", opening_json=json.dumps(opening))
    db_session.add_all([en, fa])
    db_session.commit()
    fa.master_opening_id = en.id
    renderer, library = FakeRenderer(tmp_path), FakeLibrary(tmp_path)
    media_en = asyncio.run(OM.make_media(db_session, case.id, en, en, renderer, library))
    media_fa = asyncio.run(OM.make_media(db_session, case.id, fa, en, renderer, library))
    assert len(library.made) == 1 and "slow cello drone" in library.made[0] and media_fa["music"]["path"] == media_en["music"]["path"]
    fa_hook = next(c for c in renderer.calls if c[1] == "fa")
    assert fa_hook[2]["stability"] == OB.voice_settings_for("fa")["stability"]
    assert media_fa["hook"]["post_speed"] == OB.post_speed_for("fa") == 1.2
    audio = storage.resolve(media_fa["audio"]["path"])
    assert audio.exists() and media_fa["audio"]["seconds"] > media_fa["audio"]["hook"][1] + media_fa["audio"]["gap"][1] - media_fa["audio"]["gap"][0]
    assert json.loads(fa.media_json)["audio"]["sha256"] == media_fa["audio"]["sha256"]
    booked = db_session.query(ProviderCall).filter(ProviderCall.request_id.like("music_opening:%"), ProviderCall.case_id == case.id).all()
    assert len(booked) == 1 and booked[0].case_id == case.id


def test_a_saved_voice_is_not_paid_for_again(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "cases_root", lambda: tmp_path / "cases")
    renderer = FakeRenderer(tmp_path)
    first = asyncio.run(OM.voice_part(db_session, 1, "en", "hook", "Someone locked the door.", renderer))
    again = asyncio.run(OM.voice_part(db_session, 1, "en", "hook", "Someone locked the door.", renderer))
    changed = asyncio.run(OM.voice_part(db_session, 1, "en", "hook", "Someone locked another door.", renderer))
    assert first["reused"] is False and again["reused"] is True and len(renderer.calls) == 2 and changed["path"] != first["path"]


def test_the_pictures_are_painted_once_for_the_film_and_every_language_shows_the_same_ones(db_session, tmp_path, monkeypatch):
    from test_cover_images import FakeImages, good

    monkeypatch.setattr(storage, "cases_root", lambda: tmp_path / "cases")
    case, master_version, _ = _plan_story(db_session)
    scenes = ["A brick house at night in snow with its garage door standing wide open and a warm beam on the driveway.",
              "A long empty hallway in a quiet house at night with one bedroom door ajar and a faint cool glow at its end."]
    opening = {"hook": "Someone locked the door.", "context": "A lantern in the hall. Nobody explains it.", "music": {"mood": "tension", "prompt": "slow cello drone"}, "scenes": scenes}
    en = FilmOpening(case_id=case.id, language="en", kind="master", story_version_id=master_version.id, status="valid", opening_json=json.dumps(opening))
    fa = FilmOpening(case_id=case.id, language="fa", kind="native", story_version_id=master_version.id, status="valid", opening_json=json.dumps(opening))
    db_session.add_all([en, fa])
    db_session.commit()
    provider = FakeImages([good(), good()])
    made = asyncio.run(OM.ensure_pictures(db_session, case.id, en, provider))
    assert [p["scene"] for p in made] == scenes and all(storage.resolve(p["path"]).exists() for p in made) and len(provider.calls) == 2
    assert scenes[0] in provider.calls[0][1] and ai_config.opening_block_settings().pictures.style in provider.calls[0][1] and provider.calls[0][2] == "1536x1024"
    assert [c.stage for c in db_session.query(ProviderCall).filter(ProviderCall.case_id == case.id, ProviderCall.stage == "image:opening").all()] == ["image:opening"] * 2
    again = asyncio.run(OM.ensure_pictures(db_session, case.id, en, provider))
    assert again == made and len(provider.calls) == 2                                              # a picture that exists is not paid for again
    renderer, library = FakeRenderer(tmp_path), FakeLibrary(tmp_path)
    media_en = asyncio.run(OM.make_media(db_session, case.id, en, en, renderer, library))
    media_fa = asyncio.run(OM.make_media(db_session, case.id, fa, en, renderer, library))
    assert media_en["pictures"] == media_fa["pictures"] == made and json.loads(fa.media_json)["pictures"] == made
