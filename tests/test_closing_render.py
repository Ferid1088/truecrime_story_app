import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.models import Case, ProviderCall
from app.providers.avatar.base import AvatarJob, AvatarProviderError
from app.services.budget import BudgetStopped
from app.shortform import closing_render as CR
from app.shortform.closing_library import LookSpec

SENTENCES = ["Follow us for true crime.", "I'm Fereidoun, and here I tell you real true crime stories."]
LOOK = LookSpec(look_id=1, heygen_look_id="hg-1", outfit_label="Brown sweater",
                position="SEATED CAM01_FRONT WIDE")


class FakeProvider:
    def __init__(self, jobs):
        self.jobs, self.created = list(jobs), []

    async def upload_asset(self, path, mime):
        return f"asset-{Path(path).suffix}"

    async def create_video(self, look_id, audio_id, key, **kw):
        self.created.append((look_id, audio_id, kw))
        return AvatarJob(job_id="job-1", status="waiting")

    async def get_video(self, job_id):
        return self.jobs.pop(0)

    async def download(self, url, dest):
        Path(dest).write_bytes(b"video")
        return 5


@pytest.fixture
def case(db_session):
    row = Case(canonical_title="Closing render", slug="closing-render-test", language="en")
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(ProviderCall).filter_by(case_id=row.id).delete()
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def wired(monkeypatch, tmp_path):
    audio = tmp_path / "closing.mp3"
    audio.write_bytes(b"mp3")

    async def voice(self, db, language, sentences, case_id):
        return audio, 0.01

    monkeypatch.setattr(CR.HeyGenClosingRenderer, "_voice", voice)
    monkeypatch.setattr(CR.ST, "channel_profile", lambda language: {
        "heygen_key_env": "X", "channel_name": "ClueVera"})
    monkeypatch.setattr(CR.ST, "resolve", lambda language: {
        "profile": SimpleNamespace(background_mode="transparent"), "presets": {}})

    def make(jobs):
        provider = FakeProvider(jobs)
        monkeypatch.setattr(CR, "get_avatar_provider", lambda key_env=None: provider)
        return provider

    return make


def run(coro):
    return asyncio.run(coro)


def test_the_estimate_is_voice_characters_plus_avatar_seconds():
    estimate = CR.HeyGenClosingRenderer().estimate("en", SENTENCES)
    assert estimate.characters == sum(len(s) for s in SENTENCES)
    assert estimate.seconds > 0 and estimate.voice_eur > 0 and estimate.avatar_eur > 0
    assert estimate.total_eur == round(estimate.voice_eur + estimate.avatar_eur, 4)


def test_a_finished_job_is_downloaded_and_its_seconds_are_booked_once(db_session, case, wired, tmp_path):
    provider = wired([AvatarJob("job-1", "processing"),
                      AvatarJob("job-1", "completed", video_url="https://x/v.mp4", duration=10.0)])
    dest = tmp_path / "clip.mp4"
    got = run(CR.HeyGenClosingRenderer(sleep=lambda s: asyncio.sleep(0)).render(
        db_session, "en", SENTENCES, LOOK, dest, case.id))
    assert dest.read_bytes() == b"video" and got.duration_seconds == 10.0
    assert provider.created[0][0] == "hg-1"
    booked = db_session.query(ProviderCall).filter_by(case_id=case.id, stage=CR.STAGE).all()
    assert len(booked) == 1 and booked[0].request_id == "heygen:job-1" and booked[0].units == 10.0
    assert got.cost_eur == round(0.01 + booked[0].cost_eur, 6)


def test_a_failed_job_raises_with_the_providers_reason_and_books_nothing(db_session, case, wired, tmp_path):
    wired([AvatarJob("job-1", "failed", failure_message="Insufficient credit")])
    with pytest.raises(AvatarProviderError, match="Insufficient credit"):
        run(CR.HeyGenClosingRenderer(sleep=lambda s: asyncio.sleep(0)).render(
            db_session, "en", SENTENCES, LOOK, tmp_path / "clip.mp4", case.id))
    assert db_session.query(ProviderCall).filter_by(case_id=case.id, stage=CR.STAGE).count() == 0


def test_nothing_is_paid_once_the_case_is_at_its_cap(db_session, case, wired, tmp_path):
    provider = wired([])
    case.total_cost_cap_eur = 1.0
    db_session.add(ProviderCall(case_id=case.id, stage="x", cost_eur=1.5, host="h", method="CALL",
                                path="/x", status=200, ok=True, duration_ms=0))
    db_session.commit()
    with pytest.raises(BudgetStopped):
        run(CR.HeyGenClosingRenderer().render(db_session, "en", SENTENCES, LOOK,
                                              tmp_path / "clip.mp4", case.id))
    assert provider.created == []
