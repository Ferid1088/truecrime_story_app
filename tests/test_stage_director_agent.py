"""Part 7c: the stage director agent against a fake model."""

import asyncio
import copy
import json
import uuid

import pytest
from test_stage_direction import good
from test_visual_memory import _scene

from app.agents import stage_director as SDA
from app.db.models import HostAppearance, HostLook, HostOutfit
from app.documentary import visual_memory as VM
from tests.factories import ScriptGen


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
    outfit = HostOutfit(case_id=scene.case_id, language=scene.language, name="Navy knit",
                        outfit_ref="navy_knit", status="selected")
    db_session.add(outfit)
    db_session.flush()
    for cam, fr in (("CAM06_WIDE", "WIDE"), ("CAM01_FRONT", "WIDE")):
        db_session.add(HostLook(studio_profile_id=profile, language=scene.language, camera=cam,
                                framing=fr, outfit_id="navy_knit", host_outfit_id=outfit.id,
                                stance="SEATED", pose="P01_NEUTRAL_SEATED", status="approved",
                                heygen_look_id=f"hg-{cam}"))
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
    assert {"stance": "SEATED", "camera": "CAM01_FRONT", "framing": "MEDIUM"} in sent["approved_looks"]
    assert sent["outfit"] == {"id": "navy_knit", "name": "Navy knit"}
    assert "outfit_ids" not in sent
    # the director's plan was mapped to an approved look of THIS outfit, and the scene says which
    assert scene.look_id and scene.host_outfit_id
    choice = json.loads(scene.look_choice_json)
    assert choice["wanted"]["framing"] == "WIDE" and choice["considered"]
    assert any(h["step"] == "look" and h["outcome"] == "chosen" for h in json.loads(scene.history_json))
    assert row.look_id == str(scene.look_id)
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


def test_a_repeated_whole_combination_asks_for_variety_once_and_keeps_the_better_direction(db_session, setup):
    scene, gen = setup
    older = _wide_then_front()
    older["posture"] = "P04_ARMREST_RELAXED"
    newer = _wide_then_front()
    newer["posture"] = "P03_RELAXED_BACK"
    for d in (older, newer):                       # the newest appearance is `newer`
        VM.record(db_session, _scene(db_session, scene.channel), SDA.SD.StageDirection(**d), [])
        db_session.commit()
    worse = _wide_then_front()
    worse["camera_plan"][1]["camera"] = "CAM04_LEFT_SIDE"      # not approved
    answers = [older, worse, worse]                # the first repeats `older` as a whole
    gen.structured["host_stage_director"] = lambda s, u: answers.pop(0)
    row = asyncio.run(SDA.StageDirector().direct(db_session, scene))
    assert row.posture == "P04_ARMREST_RELAXED"     # the variety attempt failed: the first stands
    assert len(gen.calls) == 3                      # one variety request, one repair of it


def test_without_a_selected_outfit_or_an_approved_look_it_stops_and_says_what_to_do(db_session, setup):
    from app.documentary import look_selection as LS

    scene, gen = setup
    outfit = db_session.query(HostOutfit).filter_by(case_id=scene.case_id).one()
    for look in db_session.query(HostLook).filter_by(host_outfit_id=outfit.id):
        look.status = "generated"
    db_session.commit()
    with pytest.raises(LS.NoApprovedLook, match="approve it"):
        asyncio.run(SDA.StageDirector().direct(db_session, scene))
    outfit.status = "draft"
    db_session.commit()
    with pytest.raises(SDA.StageDirectionError, match="no_outfit_selected"):
        asyncio.run(SDA.StageDirector().direct(db_session, scene))
    assert gen.calls == []


def test_the_stage_directors_look_never_comes_from_another_outfit_or_language(db_session, setup):
    """A look of another outfit, or of the same outfit name in another language's studio, is not
    among the looks the director is given and not one the app maps its plan to."""
    scene, gen = setup
    outfit = db_session.query(HostOutfit).filter_by(case_id=scene.case_id).one()
    other = HostOutfit(case_id=scene.case_id, language=scene.language, name="Other", outfit_ref="x",
                       status="draft")
    foreign = HostOutfit(case_id=scene.case_id, language="de", name="Navy knit", outfit_ref="navy_knit",
                         status="selected")
    db_session.add_all([other, foreign])
    db_session.flush()
    profile = db_session.query(HostLook).filter_by(host_outfit_id=outfit.id).first().studio_profile_id
    db_session.add_all([
        HostLook(studio_profile_id=profile, language=scene.language, camera="CAM04_LEFT_SIDE",
                 framing="MEDIUM", outfit_id="x", host_outfit_id=other.id, stance="SEATED",
                 pose="P01_NEUTRAL_SEATED", status="approved", heygen_look_id="hg-other"),
        HostLook(studio_profile_id="STUDIO_DE", language="de", camera="CAM02_LEFT_45",
                 framing="MEDIUM", outfit_id="navy_knit", host_outfit_id=outfit.id, stance="SEATED",
                 pose="P01_NEUTRAL_SEATED", status="approved", heygen_look_id="hg-de")])
    db_session.commit()
    gen.structured["host_stage_director"] = lambda s, u: _wide_then_front()
    asyncio.run(SDA.StageDirector().direct(db_session, scene))
    sent = json.loads(gen.calls[0][2])
    cameras = {x["camera"] for x in sent["approved_looks"]}
    assert "CAM04_LEFT_SIDE" not in cameras and "CAM02_LEFT_45" not in cameras
    assert db_session.get(HostLook, scene.look_id).host_outfit_id == outfit.id


def test_sensitive_wording_is_detected_per_language():
    assert SDA.is_sensitive("en", "the victim was found")
    assert not SDA.is_sensitive("en", "the house had a garden")
    assert SDA.is_sensitive("fa", "قربانی پیدا شد")
