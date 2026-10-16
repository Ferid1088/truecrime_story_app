import json
import pytest
from types import SimpleNamespace

from app.documentary import host_scenes as HS


def _s(i, position, seconds):
    return SimpleNamespace(id=i, host_segment_id=f"S{i}", position=position, voice_seconds=seconds)


def test_real_seconds_within_caps_and_opening_longest_pass_with_a_share_warning_in_a_pilot():
    r = HS.real_time_report([_s(1, "opening", 80), _s(2, "mid", 40)], 420)
    assert r["host_seconds"] == 120 and not r["errors"]
    assert [w["code"] for w in r["warnings"]] == ["host_share_too_high"]


def test_over_cap_total_and_non_longest_opening_are_errors():
    r = HS.real_time_report([_s(1, "opening", 130), _s(2, "mid", 130)], 3000)
    codes = [e["code"] for e in r["errors"]]
    assert codes.count("appearance_over_cap") == 2 and "host_time_over_cap" in codes
    assert "opening_not_longest" in codes


def test_share_is_an_error_only_for_a_film_of_600_seconds_or_more():
    long_film = HS.real_time_report([_s(1, "opening", 100)], 1000)
    assert [e["code"] for e in long_film["errors"]] == ["host_share_too_high"]


def test_scene_cap_follows_position_and_the_global_appearance_cap():
    assert HS.scene_cap("opening") == 120 and HS.scene_cap("mid") == 60


def test_an_over_long_real_voice_is_rewritten_shorter_and_voiced_again(db_session, monkeypatch):
    import asyncio

    from app.core.ai_config import ai_config
    from app.documentary import host_scenes as HS

    scene = SimpleNamespace(position="mid", voice_seconds=90.0, text="x " * 100, history_json="[]",
                            attempts_json="{}")
    seen = []

    async def step_voice(db, s, provider):
        seen.append(s.voice_seconds)
        return {}

    async def shortener(db, s, cap):
        s.voice_seconds = 40.0          # the new text voices shorter
        s.text = "x " * 50

    monkeypatch.setattr(HS, "_step_voice", step_voice)
    asyncio.run(HS._voice_within_cap(db_session, scene, None, shortener))
    assert scene.voice_seconds == 40.0 and len(seen) == 2

    scene.voice_seconds = 90.0
    try:
        asyncio.run(HS._voice_within_cap(db_session, scene, None, None))
    except HS.HostTooLong as e:
        assert "cap" in str(e)
    else:
        raise AssertionError("an over-long voice without a shortener must fail")


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
    assert body["host_seconds"] == 70.0 and body["scenes"][0]["cap"] == 120


def test_the_stage_director_runs_after_the_voice_and_a_failure_is_recorded_on_the_scene(db_session, monkeypatch):
    import asyncio

    from app.documentary import host_scenes as HS

    scene = SimpleNamespace(voice_seconds=10.0, direction_json=None, direction_sha256="s" * 64,
                            history_json="[]", attempts_json="{}", status="planned", position="mid",
                            failed_step=None, last_error=None, running_since=None,
                            voice_sha256="v" * 64)
    order = []

    async def voice(db, s, p, sh):
        order.append("voice")
        return {}

    async def director(db, s):
        order.append("direction")

    monkeypatch.setattr(HS, "_voice_within_cap", voice)
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
    assert "leans slightly forward" in motion and "small open-palm gesture" in motion
    assert "mostly direct" in motion and level == "medium"        # intensity 0.4
    raw["emotion"]["intensity"] = 0.1
    scene.direction_json = json.dumps(raw)
    assert direction_controls(scene)[1] == "low"


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
