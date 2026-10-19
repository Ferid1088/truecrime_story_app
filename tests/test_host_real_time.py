import json
import pytest
from types import SimpleNamespace

from app.core.ai_config import ai_config
from app.documentary import host_scenes as HS


def _s(i, position, seconds, planned=None):
    return SimpleNamespace(id=i, host_segment_id=f"S{i}", position=position, voice_seconds=seconds,
                           planned_duration=planned if planned is not None else seconds)


def test_real_seconds_within_caps_pass_with_a_share_warning_in_a_pilot():
    r = HS.real_time_report([_s(1, "opening", 70), _s(2, "mid", 40)], 420)
    assert r["host_seconds"] == 110 and not r["errors"]
    assert [w["code"] for w in r["warnings"]] == ["host_share_too_high"]


def test_over_cap_appearances_and_total_are_errors():
    r = HS.real_time_report([_s(1, "opening", 80), _s(2, "mid", 130), _s(3, "final", 60)], 3000)
    codes = [e["code"] for e in r["errors"]]
    assert codes.count("appearance_over_cap") == 3 and "host_time_over_cap" in codes


def test_share_is_an_error_only_for_a_film_of_600_seconds_or_more():
    long_film = HS.real_time_report([_s(1, "opening", 70)], 800)
    assert [e["code"] for e in long_film["errors"]] == ["host_share_too_high"]


def test_scene_cap_is_the_longest_role_of_its_position_within_the_global_cap():
    roles = ai_config.host.roles
    assert HS.scene_cap("opening") == roles["opening"].max_seconds
    assert HS.scene_cap("final") == roles["outro"].max_seconds
    assert HS.scene_cap("mid") == max(r.max_seconds for r in roles.values() if r.position == "mid")
    assert all(HS.scene_cap(p) <= ai_config.host.max_appearance_seconds
               for p in ("opening", "mid", "final"))


def test_planned_and_real_seconds_are_compared_and_the_timeline_is_recalculated_from_real_audio():
    r = HS.real_time_report([_s(1, "opening", 44.0, planned=40.0), _s(2, "mid", 30.0, planned=20.0)],
                            3000, intro_seconds=6.0)
    assert [x["deviation"] for x in r["scenes"]] == [0.1, 0.5]
    assert [w["code"] for w in r["warnings"]] == ["seconds_off_plan"]      # only the 50 % miss
    assert r["warnings"][0]["segment"] == "S2"
    assert r["timeline"] == {"story_seconds": 3000.0, "host_seconds": 74.0,
                             "intro_seconds": 6.0, "total_seconds": 3080.0}


def test_deviation_is_a_share_of_the_planned_seconds():
    assert HS.deviation(44.0, 40.0) == pytest.approx(0.1)
    assert HS.deviation(None, 40.0) == 0.0 and HS.deviation(10.0, None) == 0.0


def _scene(voice, planned, position="mid"):
    return SimpleNamespace(position=position, voice_seconds=voice, planned_duration=planned,
                           text="x " * 100, history_json="[]", attempts_json="{}",
                           updated_at=None, voice_checks_json="{}")


def _patch_voice(monkeypatch, durations):
    """_step_voice that 'speaks' the next duration; returns the list of voiced durations."""
    voiced = []

    async def step_voice(db, s, renderer=None, editor=None):
        s.voice_seconds = durations.pop(0)
        voiced.append(s.voice_seconds)
        return {}

    monkeypatch.setattr(HS, "_step_voice", step_voice)
    monkeypatch.setattr(HS, "_record_real_seconds", lambda db, s: None)
    return voiced


def test_a_voice_off_the_plan_by_more_than_ten_percent_is_rewritten_and_voiced_again(monkeypatch):
    import asyncio

    voiced = _patch_voice(monkeypatch, [40.0, 29.0])       # planned 30: 33 % over, then 3 % under
    asked = []

    async def retimer(db, s, target, avoid=()):
        asked.append(target)

    scene = _scene(None, 30.0)
    asyncio.run(HS._voice_within_plan(None, scene, None, retimer))
    assert asked == [30.0] and voiced == [40.0, 29.0] and scene.voice_seconds == 29.0


def test_a_voice_within_ten_percent_is_kept_as_it_is(monkeypatch):
    import asyncio

    voiced = _patch_voice(monkeypatch, [32.9])             # 9.7 % over
    asked = []

    async def retimer(db, s, target, avoid=()):
        asked.append(target)

    asyncio.run(HS._voice_within_plan(None, _scene(None, 30.0), None, retimer))
    assert asked == [] and voiced == [32.9]


def test_the_rewrite_is_bounded_and_a_voice_over_the_hard_cap_fails(monkeypatch):
    import asyncio

    limit = ai_config.host.max_retime_attempts
    voiced = _patch_voice(monkeypatch, [40.0] * (limit + 1))
    asked = []

    async def retimer(db, s, target, avoid=()):
        asked.append(target)

    scene = _scene(None, 30.0)
    asyncio.run(HS._voice_within_plan(None, scene, None, retimer))   # still 33 % off: kept, reported
    assert len(asked) == limit and len(voiced) == limit + 1

    cap = HS.scene_cap("mid")
    _patch_voice(monkeypatch, [cap + 10] * (limit + 1))
    with pytest.raises(HS.HostTooLong, match="cap"):
        asyncio.run(HS._voice_within_plan(None, _scene(None, cap + 10), None, None))


def test_the_real_seconds_are_written_into_the_stored_segment(db_session):
    from test_blueprint_performance import _story
    from app.db.models import HostPlan, HostScene, HostSegments

    case, master = _story(db_session)
    plan = HostPlan(case_id=case.id, status="valid")
    db_session.add(plan)
    db_session.flush()
    seg = HostSegments(case_id=case.id, story_version_id=master.id, host_plan_id=plan.id,
                       language="en", status="valid", segments_json=json.dumps([
                           {"segment_id": "S1", "target_seconds": 40.0}]))
    db_session.add(seg)
    db_session.flush()
    scene = HostScene(case_id=case.id, story_version_id=master.id, host_segments_id=seg.id,
                      host_segment_id="S1", language="en", channel="c", position="opening",
                      text="t", text_sha256="h", studio_profile_id="p", studio_asset_id="a",
                      framing_preset="HOST_MEDIUM", background_mode="x", status="voice_ready",
                      voice_seconds=42.0, planned_duration=40.0)
    db_session.add(scene)
    db_session.commit()
    HS._record_real_seconds(db_session, scene)
    stored = json.loads(db_session.get(HostSegments, seg.id).segments_json)[0]
    assert stored["real_seconds"] == 42.0 and stored["seconds_deviation"] == 0.05


def test_scenes_are_planned_with_the_target_seconds_of_their_segment(db_session):
    from test_blueprint_performance import _story
    from app.db.models import HostPlan, HostSegments

    case, master = _story(db_session)
    plan = HostPlan(case_id=case.id, status="valid")
    db_session.add(plan)
    db_session.flush()
    seg = HostSegments(case_id=case.id, story_version_id=master.id, host_plan_id=plan.id,
                       language="en", status="valid", segments_json=json.dumps([{
                           "segment_id": "S1", "position": "opening", "role": "opening",
                           "avatar_dialogue": "Can you see it? " * 8, "target_seconds": 31.5,
                           "estimated_seconds": 28.0, "quality": {"pass": True}}]))
    db_session.add(seg)
    db_session.commit()
    scene = HS.plan_host_scenes(db_session, seg)[0]
    assert scene.planned_duration == 31.5


def test_host_time_endpoint_reports_real_seconds(client, db_session):
    from test_blueprint_performance import _story
    from app.db.models import EditorialBlueprint, HostPlan, HostScene, HostSegments

    case, master = _story(db_session)
    bp = EditorialBlueprint(case_id=case.id, story_version_id=master.id, status="valid")
    db_session.add(bp)
    db_session.flush()
    plan = HostPlan(case_id=case.id, blueprint_id=bp.id, status="valid")
    db_session.add(plan)
    db_session.flush()
    seg = HostSegments(case_id=case.id, story_version_id=master.id, host_plan_id=plan.id, language=master.language or "en",
                       version=1, status="valid")
    db_session.add(seg)
    db_session.flush()
    db_session.add(HostScene(case_id=case.id, story_version_id=master.id, host_segments_id=seg.id,
                             host_segment_id="S1", language=seg.language, channel="c",
                             position="opening", text="t", text_sha256="h", studio_profile_id="p",
                             studio_asset_id="a", framing_preset="HOST_MEDIUM", voice_seconds=70.0,
                             background_mode="x", status="voice_ready"))
    db_session.commit()
    body = client.get(f"/api/cases/{case.id}/host-time/{seg.language}").json()
    assert body["host_seconds"] == 70.0 and body["scenes"][0]["cap"] == HS.scene_cap("opening")
    assert body["timeline"]["host_seconds"] == 70.0 and body["timeline"]["total_seconds"] >= 70.0


def test_the_stage_director_runs_after_the_voice_and_a_failure_is_recorded_on_the_scene(db_session, monkeypatch):
    import asyncio

    from app.documentary import host_scenes as HS

    scene = SimpleNamespace(voice_seconds=10.0, direction_json=None, direction_sha256="s" * 64,
                            history_json="[]", attempts_json="{}", status="planned", position="mid",
                            failed_step=None, last_error=None, running_since=None,
                            voice_sha256="v" * 64)
    order = []

    async def voice(db, s, renderer, retimer, editor):
        order.append("voice")
        return {}

    async def director(db, s):
        order.append("direction")

    monkeypatch.setattr(HS, "_voice_within_plan", voice)
    monkeypatch.setattr(db_session, "commit", lambda: None)
    asyncio.run(HS.run_scene(db_session, scene, "voice", director=director))
    assert order == ["voice", "direction"]

    async def failing(db, s):
        raise RuntimeError("guard-rails failed")

    scene.direction_json = None
    with pytest.raises(RuntimeError):
        asyncio.run(HS.run_scene(db_session, scene, "voice", director=failing))
    assert scene.failed_step == "stage_direction"


def test_the_stored_direction_becomes_a_motion_prompt_and_an_expressiveness_level():
    import json

    from app.documentary.host_scenes import direction_controls
    from test_stage_direction import good

    scene = SimpleNamespace(direction_json=None)
    assert direction_controls(scene) == (None, None)
    raw = good()
    scene.direction_json = json.dumps(raw)
    motion, level = direction_controls(scene)
    assert "holds the hands loosely joined" in motion and "one small open-palm gesture" in motion
    assert "around second 6" in motion and "hands rest still" in motion
    assert "mostly direct" in motion and "no repeated hand movement" in motion
    assert level == "low"          # intensity 0.4 would be medium; a calm host is never above low
    raw["performance_timeline"] = [{"start_sec": 0, "end_sec": 18.5, "action": "hands_rest"}]
    scene.direction_json = json.dumps(raw)
    assert "keeps the hands resting and still" in direction_controls(scene)[0].lower().replace("keeps", "keeps")


def test_heygen_create_video_sends_motion_prompt_and_expressiveness_only_when_given(monkeypatch):
    import asyncio

    import httpx

    from app.providers.avatar.heygen import HeyGenAvatarProvider

    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"data": {"video_id": "v1", "status": "waiting"}})

    monkeypatch.setenv("TRUECRIME_HEYGEN_API_KEY", "k")
    prov = HeyGenAvatarProvider(key_env="TRUECRIME_HEYGEN_API_KEY", transport=httpx.MockTransport(handler))
    asyncio.run(prov.create_video("look", "audio", "key", "mp4", "1080p"))
    asyncio.run(prov.create_video("look", "audio", "key", "mp4", "1080p",
                                  motion_prompt="calm", expressiveness="low"))
    assert "motion_prompt" not in bodies[0] and "expressiveness" not in bodies[0]
    assert (bodies[1]["motion_prompt"], bodies[1]["expressiveness"]) == ("calm", "low")


def test_a_word_the_voice_still_says_wrong_makes_the_writer_use_other_words(monkeypatch):
    import asyncio

    asked = []

    async def step_voice(db, s, renderer=None, editor=None):
        s.voice_seconds = voiced_seconds.pop(0)
        s.voice_checks_json = json.dumps({"flags": flags.pop(0)})
        return {}

    voiced_seconds, flags = [29.0, 29.5], [["pronunciation_unresolved:فاصله", "asr_check_failed"], []]
    monkeypatch.setattr(HS, "_step_voice", step_voice)
    monkeypatch.setattr(HS, "_record_real_seconds", lambda db, s: None)

    async def retimer(db, s, target, avoid=()):
        asked.append(list(avoid))

    scene = _scene(None, 30.0)
    asyncio.run(HS._voice_within_plan(None, scene, None, retimer))
    assert asked == [["فاصله"]]                          # on the plan, but a word is wrong
    assert voiced_seconds == [] and HS.unresolved_words(scene) == []
    assert "avoid" in json.loads(scene.history_json)[0]["detail"]


def test_a_scene_is_redone_with_a_new_direction_and_a_new_video_and_keeps_its_history(db_session):
    from test_visual_memory import _ch, _scene
    from app.db.models import HostAppearance
    from app.documentary import host_scenes as HS
    from app.documentary import stage_direction as SD
    from app.documentary import visual_memory as VM
    from test_stage_direction import good

    scene = _scene(db_session, _ch())
    VM.record(db_session, scene, SD.StageDirection(**good()), [])
    scene.voice_path, scene.provider_job_id, scene.provider_generation = "v.mp3", "job-1", 0
    scene.avatar_video_path, scene.avatar_video_sha256, scene.status = "a.mp4", "x" * 64, "avatar_ready"
    db_session.commit()
    HS.redo_video(db_session, scene, "calmer hands")
    assert (scene.direction_json, scene.provider_job_id, scene.avatar_video_path) == (None, None, None)
    assert scene.provider_generation == 1 and scene.status == "voice_ready"
    redo = [h for h in json.loads(scene.history_json) if h["step"] == "redo"][0]["detail"]
    assert redo["old_provider_job_id"] == "job-1" and redo["old_avatar_video_path"] == "a.mp4"
    assert redo["old_direction"]["posture"] == "P05_HANDS_LOOSELY_JOINED" and redo["reason"] == "calmer hands"
    calmer = good()
    calmer["posture"] = "P04_ARMREST_RELAXED"
    VM.record(db_session, scene, SD.StageDirection(**calmer), [])      # the memory keeps one row
    db_session.commit()
    rows = db_session.query(HostAppearance).filter_by(host_scene_id=scene.id).all()
    assert len(rows) == 1 and rows[0].posture == "P04_ARMREST_RELAXED"
