"""The owner hears the narration of a language first: the run stops after the voice and goes on only with an approval of that exact audio."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, StoryVersion
from app.documentary import voice_review as VR
from app.documentary.jobs import failure_details
from app.documentary.pipeline.errors import AwaitingVoiceReview


@pytest.fixture(autouse=True)
def work_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    monkeypatch.setattr(ai_config.voice, "review_languages", ["fa"])


@pytest.fixture
def version(db_session):
    case = Case(canonical_title="Voice review", slug=f"vr-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.flush()
    row = StoryVersion(case_id=case.id, version=1, kind="spoken", language="fa", story_text="t", narrative_angle="a", status="ready")
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.delete(case)
    db_session.commit()


def make_audio(version, data=b"voice"):
    folder = VR.folder(version)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "narration.mp3").write_bytes(data)


def test_a_language_not_listed_never_waits(version):
    version.language = "en"
    VR.guard(version, "en")


def test_the_run_stops_until_this_exact_audio_is_approved(version):
    make_audio(version)
    with pytest.raises(AwaitingVoiceReview) as stop:
        VR.guard(version, "fa")
    assert f"/api/documentary/versions/{version.id}/audio/narration" in str(stop.value)
    VR.approve(version, "sounds right")
    VR.guard(version, "fa")
    make_audio(version, b"another take")             # the narration was made again: it needs a new approval
    assert VR.state(version)["approved"] is False
    with pytest.raises(AwaitingVoiceReview):
        VR.guard(version, "fa")


def test_nothing_to_approve_before_the_narration_exists(version):
    with pytest.raises(FileNotFoundError):
        VR.approve(version)


def test_the_stop_is_resumable_with_the_next_step_named():
    details = failure_details("Awaiting voice review: listen")
    assert details["resume_allowed"] is True and "approve" in details["next_action"]


def test_the_api_lists_the_audio_serves_it_and_records_the_approval(client, version, tmp_path, monkeypatch):
    from app.core import files

    monkeypatch.setattr(files, "allowed_roots", lambda: [tmp_path.resolve()])
    make_audio(version, b"abc")
    url = f"/api/documentary/versions/{version.id}"
    body = client.get(f"{url}/voice-review").json()
    assert body["required"] and not body["approved"] and body["audio"] == {"narration": f"{url}/audio/narration"}
    assert client.get(body["audio"]["narration"]).status_code == 200
    assert client.get(f"{url}/audio/documentary").status_code == 404
    assert client.post(f"{url}/voice-review", json={}).json()["approved"] is True
    assert client.get(f"{url}/voice-review").json()["approved"] is True
