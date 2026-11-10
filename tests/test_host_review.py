"""The host (avatar) in a semantically reviewed language: its voice is listened to before any avatar is made, and the audio of the avatar video
is listened to after it. A voice that fails never reaches the avatar; a video whose audio fails is made again from the same voice, twice at
most. No network: the reviewer, the voice and the avatar provider are fakes (a real silent video is made by FFmpeg)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess

import pytest
from test_studio import FakeAvatar, FakeVoice, _nosleep, _renderer, _segments, _with_look

from app.core.ai_config import ai_config

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.semantic_review, "languages", ["de"])
    monkeypatch.setattr(ai_config.semantic_review, "avatar_max_regenerations", 2)
    monkeypatch.setenv("TrueCrime_Avatar_ID_Heygen", "group-1")


def verdict(kind="pass"):
    return {"verdict": kind, "confidence": 0.9, "errors": [] if kind == "pass" else [
        {"type": "pronunciation", "severity": "major", "expected": "Fallspur", "heard": "Falschpur", "reason": "x"}],
        "ezafe_missing": [], "notation_read_aloud": False, "corrected_text": None, "corrected_pronunciation_rules": [],
        "corrected_delivery_instruction": None}


class FakeReviewer:
    """Answers the voice review (`run`) and the avatar audio review (`listen`) from lists."""

    def __init__(self, voice="approved", avatar=("pass",)):
        self.voice, self.avatar = voice, list(avatar)
        self.runs, self.listened = [], []

    async def run(self, result, speak, seed):
        self.runs.append(result["block"].get("source_text"))
        errors = [] if self.voice == "approved" else [verdict("fail")["errors"][0]]
        result["semantic_review"] = {"block_id": result["block"]["block_id"], "voice_attempts": 1, "semantic_review_attempts": 1,
                                     "voice_review_result": "pass" if self.voice == "approved" else "fail", "avatar_review_result": "not_applicable",
                                     "errors": errors, "final_status": self.voice, "reviews": []}
        return result

    def context(self, block):
        return {"display_text": block["text"], "tts_text": block["tts_text"]}

    async def listen(self, context, audio, label):
        self.listened.append((context["display_text"], audio.suffix, audio.exists()))
        return verdict(self.avatar.pop(0) if self.avatar else "pass")


class VideoAvatar(FakeAvatar):
    """The provider's download is a real video with an audio track."""

    async def download(self, url, dest):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=1", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                        "-shortest", "-c:v", "libx264", "-c:a", "aac", "-f", "mp4", str(dest)], check=True)
        return 10


@pytest.fixture
def scene(db_session):
    from app.documentary.host_scenes import plan_host_scenes

    scene = plan_host_scenes(db_session, _segments(db_session, "de"))[0]
    _with_look(db_session, scene)
    return scene


def run(db, scene, until, reviewer, voice, avatar=None):
    from app.documentary.host_scenes import run_scene

    return asyncio.run(run_scene(db, scene, until, renderer=_renderer(voice), avatar_provider=avatar, sleep=_nosleep, reviewer=reviewer))


def checks(scene):
    return json.loads(scene.voice_checks_json)


def test_the_host_voice_is_listened_to_with_its_own_words_as_the_source_and_the_record_is_kept(db_session, scene):
    reviewer = FakeReviewer()
    run(db_session, scene, "voice", reviewer, FakeVoice())
    assert reviewer.runs == [scene.text] and scene.status == "voice_ready"
    record = checks(scene)["semantic_review"]
    assert record["final_status"] == "approved" and record["voice_review_result"] == "pass" and record["avatar_review_result"] == "not_applicable"


def test_a_host_voice_that_fails_the_review_never_reaches_the_avatar(db_session, scene):
    avatar = VideoAvatar()
    with pytest.raises(Exception, match="did not pass the listening review"):
        run(db_session, scene, "avatar", FakeReviewer(voice="failed"), FakeVoice(), avatar)
    assert avatar.uploads == 0 and avatar.requests == 0, "nothing was sent to the avatar provider"
    assert checks(scene)["semantic_review"]["errors"][0]["heard"] == "Falschpur" and scene.failed_step == "voice"


def test_the_audio_of_the_avatar_video_is_listened_to_after_it_is_made_and_a_pass_is_kept(db_session, scene):
    reviewer, avatar = FakeReviewer(avatar=["pass"]), VideoAvatar()
    run(db_session, scene, "avatar", reviewer, FakeVoice(), avatar)
    assert scene.status == "avatar_ready" and avatar.requests == 1
    assert reviewer.listened == [(scene.text, ".wav", True)], "it is the audio track of the video that is listened to"
    assert checks(scene)["semantic_review"]["avatar_review_result"] == "pass"
    run(db_session, scene, "avatar", reviewer, FakeVoice(), avatar)
    assert len(reviewer.listened) == 1 and avatar.requests == 1, "a resumed run does not listen to or pay for the same video again"


def test_a_video_whose_audio_fails_is_made_again_from_the_same_voice(db_session, scene):
    reviewer, avatar, voice = FakeReviewer(avatar=["fail", "pass"]), VideoAvatar(), FakeVoice()
    run(db_session, scene, "avatar", reviewer, voice, avatar)
    assert avatar.requests == 2 and voice.calls == 1, "a new video, the same voice"
    assert scene.status == "avatar_ready" and [r["verdict"] for r in checks(scene)["semantic_review"]["avatar_reviews"]] == ["fail", "pass"]
    assert any(h["step"] == "redo" for h in json.loads(scene.history_json))


def test_after_two_new_videos_that_still_fail_the_scene_fails_and_nothing_is_published(db_session, scene):
    reviewer, avatar = FakeReviewer(avatar=["fail", "fail", "fail"]), VideoAvatar()
    with pytest.raises(Exception, match="did not pass the listening review after 2 new videos"):
        run(db_session, scene, "avatar", reviewer, FakeVoice(), avatar)
    assert avatar.requests == 3, "no endless loop: one video and two new ones"
    assert checks(scene)["semantic_review"]["final_status"] == "failed" and scene.failed_step == "avatar_review"


def test_a_language_that_is_not_reviewed_makes_the_avatar_without_any_listening(db_session, scene, monkeypatch):
    monkeypatch.setattr(ai_config.semantic_review, "languages", [])
    reviewer, avatar = FakeReviewer(), VideoAvatar()
    run(db_session, scene, "avatar", reviewer, FakeVoice(), avatar)
    assert scene.status == "avatar_ready" and reviewer.runs == [] and reviewer.listened == [] and "semantic_review" not in checks(scene)
