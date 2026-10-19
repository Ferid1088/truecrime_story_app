"""Part 7d: the host's visual memory."""

import json
import uuid

import pytest

from app.db.models import Case, HostAppearance, HostScene, HostSegments
from app.documentary import stage_direction as SD
from app.documentary import visual_memory as VM
from app.utils import slugify
from test_stage_direction import good


def _ch(name="ClueVera"):
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _scene(db, channel, language="en"):
    from test_blueprint_performance import _story
    from app.db.models import EditorialBlueprint, HostPlan

    case, master = _story(db)
    bp = EditorialBlueprint(case_id=case.id, story_version_id=master.id, status="valid")
    db.add(bp)
    db.flush()
    plan = HostPlan(case_id=case.id, blueprint_id=bp.id, status="valid")
    db.add(plan)
    db.flush()
    seg = HostSegments(case_id=case.id, story_version_id=master.id, host_plan_id=plan.id,
                       language=language, version=1, status="valid")
    db.add(seg)
    db.flush()
    scene = HostScene(case_id=case.id, story_version_id=master.id, host_segments_id=seg.id,
                      host_segment_id="S1", language=language, channel=channel, position="opening",
                      text="t", text_sha256="h", studio_profile_id="p", studio_asset_id="a",
                      framing_preset="HOST_MEDIUM", background_mode="x", status="voice_ready")
    db.add(scene)
    db.commit()
    return scene


def _direction(**over):
    raw = good()
    raw.update(over)
    return SD.StageDirection(**raw)


def test_an_accepted_direction_is_written_once_with_the_scene_in_one_transaction(db_session):
    ch = _ch()
    scene = _scene(db_session, ch)
    row = VM.record(db_session, scene, _direction(), [], look_id="look1", persona_sha256="abc")
    db_session.commit()
    assert scene.direction_sha256 == row.direction_sha256 and scene.direction_json
    assert (row.channel, row.outfit_id, row.look_id, row.persona_sha256) == (
        ch, "navy_knit", "look1", "abc")
    assert json.loads(row.gestures_json) == ["G02_SMALL_OPEN_PALM"]
    with pytest.raises(ValueError, match="already recorded"):
        VM.record(db_session, scene, _direction(), [])


def test_a_failed_or_rejected_direction_leaves_no_row(db_session):
    scene = _scene(db_session, _ch())
    with pytest.raises(ValueError):
        VM.record(db_session, scene, _direction(), [{"code": "look_not_approved"}])
    assert db_session.query(HostAppearance).filter_by(host_scene_id=scene.id).count() == 0
    assert scene.direction_json is None


def test_recent_is_newest_first_and_filtered_to_the_channel(db_session):
    ch = _ch()
    a = _scene(db_session, ch)
    b = _scene(db_session, _ch("Fallspur"), "de")
    c = _scene(db_session, ch)
    VM.record(db_session, a, _direction(outfit_id="stone_knit"), [])
    VM.record(db_session, b, _direction(outfit_id="black_knit"), [])
    VM.record(db_session, c, _direction(outfit_id="olive_overshirt"), [])
    db_session.commit()
    assert [r["outfit_id"] for r in VM.recent(db_session, ch)] == [
        "olive_overshirt", "stone_knit"]
    assert VM.last_outfit_any_channel(db_session) == "olive_overshirt"   # one person, all channels


def test_a_whole_repeated_combination_is_detected_but_a_partial_one_is_not(db_session):
    ch = _ch()
    scene = _scene(db_session, ch)
    VM.record(db_session, scene, _direction(), [])
    db_session.commit()
    past = VM.recent(db_session, ch)
    assert VM.repeats_full_combination(_direction(), past)
    assert not VM.repeats_full_combination(_direction(outfit_id="stone_knit"), past)


def test_the_memory_is_listed_and_a_wrong_entry_can_be_removed(client, db_session):
    ch = _ch()
    scene = _scene(db_session, ch)
    row = VM.record(db_session, scene, _direction(), [])
    db_session.commit()
    rid = row.id
    listing = client.get(f"/api/host-appearances?channel={ch}").json()
    assert [a["outfit_id"] for a in listing] == ["navy_knit"]
    assert client.delete(f"/api/host-appearances/{rid}").json()["deleted"] is True
    assert client.get(f"/api/host-appearances?channel={ch}").json() == []


def test_direction_never_changes_the_audio(db_session):
    """Part 7f: recording a direction leaves the scene's voice file, its sha256
    and its timing untouched."""
    scene = _scene(db_session, _ch())
    scene.voice_sha256, scene.voice_seconds, scene.voice_path = "v" * 64, 18.5, "voice.mp3"
    text_sha = scene.text_sha256
    db_session.commit()
    VM.record(db_session, scene, _direction(), [])
    db_session.commit()
    assert (scene.voice_sha256, scene.voice_seconds, scene.voice_path, scene.text_sha256) == (
        "v" * 64, 18.5, "voice.mp3", text_sha)


def test_the_directors_long_purpose_is_kept_whole_as_the_interaction_pattern(db_session):
    from sqlalchemy import Text

    assert isinstance(HostAppearance.__table__.c.interaction_pattern.type, Text)
    purpose = ("Establish a personal connection before the logo, inviting the viewer to "
               "distinguish familiarity from evidence without anticipating the story.")
    assert len(purpose) > 80
    scene = _scene(db_session, _ch())
    row = VM.record(db_session, scene, _direction(purpose=purpose), [])
    db_session.commit()
    assert row.interaction_pattern == purpose
