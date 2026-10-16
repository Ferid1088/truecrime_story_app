"""Part 7c: the stage director agent against a fake model."""

import asyncio
import copy
import json
import uuid

import pytest

from app.agents import stage_director as SDA
from app.db.models import HostAppearance, HostLook
from app.documentary import visual_memory as VM
from test_stage_direction import good
from test_visual_memory import _scene
from tests.test_master_pipeline import ScriptGen


def _direction(**over):
    raw = copy.deepcopy(good())
    raw.update(over)
    return raw


@pytest.fixture
def setup(db_session, tmp_path, monkeypatch):
    ch = f"Chan-{uuid.uuid4().hex[:6]}"
    scene = _scene(db_session, ch)
    profile = f"STUDIO_{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(SDA.ST, "channel_profile", lambda language: {"studio_profile_id": profile})
    monkeypatch.setattr(SDA, "allowed_outfits", lambda language: ["navy_knit", "stone_knit", "black_knit"])
    for cam, fr in (("CAM06_WIDE", "WIDE"), ("CAM01_FRONT", "WIDE")):
        db_session.add(HostLook(studio_profile_id=profile, language="en", camera=cam, framing=fr,
                                outfit_id="navy_knit", pose="P01_NEUTRAL_SEATED", status="approved"))
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"x")
    chars = list("A. B.")
    (tmp_path / "voice.mp3.json").write_text(json.dumps({"alignment": {
        "characters": chars, "ends": [8.9, 9.0, 9.2, 18.0, 18.5]}}))
    monkeypatch.setattr(SDA.storage, "resolve", lambda p: voice)
    scene.voice_path, scene.voice_seconds = "voice.mp3", 18.5
    db_session.commit()
    gen = ScriptGen()
    monkeypatch.setattr(SDA, "get_generation_provider", lambda: gen)
    return scene, gen


def _wide_then_front():
    d = _direction()
    d["camera_plan"][1]["framing"] = "MEDIUM"
    return d


def test_an_accepted_direction_is_stored_with_the_scene_and_remembered(db_session, setup):
    scene, gen = setup
    gen.structured["host_stage_director"] = lambda s, u: _wide_then_front()
    row = asyncio.run(SDA.StageDirector().direct(db_session, scene))
    assert row.outfit_id == "navy_knit" and scene.direction_sha256 == row.direction_sha256
    sent = json.loads(gen.calls[0][2])
    assert sent["voice_seconds"] == 18.5 and sent["sentence_ends"] == [9.0, 18.5]
    assert ["CAM01_FRONT", "MEDIUM"] in [list(x) for x in sent["approved_looks"]]
    assert db_session.query(HostAppearance).filter_by(host_scene_id=scene.id).count() == 1


def test_a_direction_that_breaks_a_guard_rail_is_repaired_once_then_accepted(db_session, setup):
    scene, gen = setup
    bad = _wide_then_front()
    bad["camera_plan"][1]["camera"] = "CAM04_LEFT_SIDE"      # not approved
    answers = [bad, _wide_then_front()]
    gen.structured["host_stage_director"] = lambda s, u: answers.pop(0)
    asyncio.run(SDA.StageDirector().direct(db_session, scene))
    repair = json.loads(gen.calls[1][2])
    assert [e["code"] for e in repair["errors_to_fix"]] == ["look_not_approved"]


def test_a_direction_that_never_passes_leaves_no_row_and_no_stored_direction(db_session, setup):
    scene, gen = setup
    bad = _wide_then_front()
    bad["camera_plan"][1]["camera"] = "CAM04_LEFT_SIDE"
    gen.structured["host_stage_director"] = lambda s, u: bad
    with pytest.raises(SDA.StageDirectionError):
        asyncio.run(SDA.StageDirector().direct(db_session, scene))
    assert scene.direction_json is None
    assert db_session.query(HostAppearance).filter_by(host_scene_id=scene.id).count() == 0


def test_without_an_approved_look_or_a_real_voice_it_refuses_before_any_model_call(db_session, setup):
    scene, gen = setup
    scene.voice_seconds = None
    with pytest.raises(SDA.StageDirectionError, match="no_real_voice_yet"):
        asyncio.run(SDA.StageDirector().direct(db_session, scene))
    assert gen.calls == []


def test_a_repeated_whole_combination_asks_for_variety_once_and_keeps_the_better_direction(db_session, setup, monkeypatch):
    scene, gen = setup
    monkeypatch.setattr(SDA.ai_config.stage_direction, "recent_outfit_window", 1)
    older = _wide_then_front()
    older["posture"], older["outfit_id"] = "P04_ARMREST_RELAXED", "black_knit"
    newer = _wide_then_front()
    newer["posture"], newer["outfit_id"] = "P03_RELAXED_BACK", "stone_knit"
    for d in (older, newer):                       # the newest appearance is `newer`
        VM.record(db_session, _scene(db_session, scene.channel), SDA.SD.StageDirection(**d), [])
        db_session.commit()
    worse = _wide_then_front()
    worse["camera_plan"][1]["camera"] = "CAM04_LEFT_SIDE"      # not approved
    answers = [older, worse, worse]                # the first repeats `older` as a whole
    gen.structured["host_stage_director"] = lambda s, u: answers.pop(0)
    row = asyncio.run(SDA.StageDirector().direct(db_session, scene))
    assert row.outfit_id == "black_knit"            # the variety attempt failed: the first stands
    assert len(gen.calls) == 3                      # one variety request, one repair of it


def test_sensitive_wording_is_detected_per_language():
    assert SDA.is_sensitive("en", "the victim was found")
    assert not SDA.is_sensitive("en", "the house had a garden")
    assert SDA.is_sensitive("fa", "قربانی پیدا شد")
