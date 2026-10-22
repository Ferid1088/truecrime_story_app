"""V2: the Data API discovery is wired into visual research — queries become classified
VideoSource candidates (real channel + real license), film candidates get their transcript
stored for sentence alignment, and the pipeline stops cleanly when the share is spent."""

import asyncio
import uuid

import httpx
import pytest

from app.core.ai_config import ai_config
from app.db.models import AppSetting, Case, TranscriptSegment, VideoSource
from app.documentary.visuals import youtube_clips as YC
from app.documentary.visuals import youtube_discovery as YD
from app.providers.transcript.base import TranscriptResult, TranscriptSegmentData


@pytest.fixture(autouse=True)
def _switch_starts_off(db_session):
    db_session.query(AppSetting).filter(AppSetting.key == YC.SETTING_KEY).delete()
    db_session.commit()
    yield
    db_session.query(AppSetting).filter(AppSetting.key == YC.SETTING_KEY).delete()
    db_session.commit()


def _case(db):
    case = Case(canonical_title="Clips", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


OFFICIAL_VIDEO = {
    "id": "off1",
    "snippet": {"title": "Press conference on the investigation", "channelTitle": "Moscow Police Department",
                "channelId": "UCmpd", "description": "raw feed"},
    "status": {"license": "youtube"}, "contentDetails": {"duration": "PT25M"},
}
CREATOR_VIDEO = {
    "id": "cre1",
    "snippet": {"title": "My take on the case", "channelTitle": "True Crime Daily Clips",
                "channelId": "UCcre", "description": "analysis"},
    "status": {"license": "youtube"}, "contentDetails": {"duration": "PT40M"},
}
CC_VIDEO = {
    "id": "cc1",
    "snippet": {"title": "Council archive meeting", "channelTitle": "City Archive",
                "channelId": "UCarc", "description": ""},
    "status": {"license": "creativeCommon"}, "contentDetails": {"duration": "PT9M30S"},
}


def _handler(items):
    def handler(request):
        if "search" in request.url.path:
            return httpx.Response(200, json={"items": [
                {"id": {"videoId": v["id"]}} for v in items]})
        if "videos" in request.url.path:
            return httpx.Response(200, json={"items": items})
        return httpx.Response(404)
    return handler


class FakeTranscripts:
    def __init__(self):
        self.calls = []

    async def get_transcript(self, video_id, preferred_languages=None):
        self.calls.append(video_id)
        return TranscriptResult(
            available=True, language="en", transcript_type="manual",
            retrieval_method="fake",
            segments=[TranscriptSegmentData(0.0, 5.0, "the police held a press conference"),
                      TranscriptSegmentData(5.0, 10.0, "detectives named a suspect")])


def test_discovery_stores_an_official_channel_video_as_a_film_candidate(db_session):
    case = _case(db_session)
    tp = FakeTranscripts()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler([OFFICIAL_VIDEO]))) as client:
            return await YD.discover_for_case(client, db_session, case, ["press conference"],
                                              "fake-key", transcript_provider=tp)

    stats = asyncio.run(go())
    row = db_session.query(VideoSource).filter_by(case_id=case.id, video_id="off1").one()
    assert stats["searched"] == 1 and stats["film_candidates"] == 1 and stats["new"] == 1
    assert row.use == "film_candidate" and row.source_class == "official_body"
    assert row.youtube_license == "youtube" and row.channel_name == "Moscow Police Department"
    assert row.channel_url.endswith("/channel/UCmpd") and row.duration_seconds == 1500
    # the transcript is fetched for a film candidate (no Data API quota) so sentences can be aligned
    assert tp.calls == ["off1"] and row.transcript_status == "available"
    assert db_session.query(TranscriptSegment).filter_by(video_source_id=row.id).count() == 2
    aligned = YD.align_sentence(db_session.query(TranscriptSegment).filter_by(
        video_source_id=row.id).order_by(TranscriptSegment.segment_index).all(),
        "the police held a press conference", 1.0)
    assert aligned is not None


def test_a_creator_or_reuploader_video_is_research_only_and_needs_no_transcript(db_session):
    case = _case(db_session)
    tp = FakeTranscripts()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler([CREATOR_VIDEO]))) as client:
            return await YD.discover_for_case(client, db_session, case, ["case talk"],
                                              "fake-key", transcript_provider=tp)

    stats = asyncio.run(go())
    row = db_session.query(VideoSource).filter_by(case_id=case.id, video_id="cre1").one()
    assert stats["film_candidates"] == 0 and row.use == "research_only"
    # 'clips' names a re-uploader; a creator stays out of the film either way
    assert tp.calls == []


def test_discovery_dedupes_by_video_id_and_refreshes_the_existing_row(db_session):
    case = _case(db_session)
    old = VideoSource(case_id=case.id, video_id="off1",
                      url="https://www.youtube.com/watch?v=off1", title="old title",
                      classification="unknown", use="research_only")
    db_session.add(old)
    db_session.commit()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler([OFFICIAL_VIDEO]))) as client:
            return await YD.discover_for_case(client, db_session, case, ["press conference"],
                                              "fake-key", transcript_provider=FakeTranscripts())

    stats = asyncio.run(go())
    rows = db_session.query(VideoSource).filter_by(case_id=case.id, video_id="off1").all()
    assert len(rows) == 1 and stats["updated"] == 1 and stats["new"] == 0
    assert rows[0].use == "film_candidate" and rows[0].channel_name == "Moscow Police Department"


def test_discovery_stops_cleanly_when_the_discovery_share_is_used_up(db_session, monkeypatch):
    case = _case(db_session)
    monkeypatch.setattr(ai_config.youtube_data_api, "daily_quota_units", 10)
    monkeypatch.setattr(ai_config.youtube_data_api, "discovery_share_percent", 1.0)
    from app.documentary.visuals import youtube_quota as Q
    Q.record_call(db_session, stage="discovery", endpoint="videos.list", case_id=case.id, units=10)

    def handler(request):
        raise AssertionError("no API call may be made once the share is spent")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await YD.discover_for_case(client, db_session, case, ["q1", "q2"], "fake-key")

    stats = asyncio.run(go())
    assert stats["quota_stopped"] is True and stats["searched"] == 0
    assert db_session.query(VideoSource).filter_by(case_id=case.id).count() == 0


def test_discovery_respects_the_per_case_query_cap(db_session, monkeypatch):
    case = _case(db_session)
    monkeypatch.setattr(ai_config.youtube_data_api, "discovery_max_queries_per_case", 2)
    searched = []

    def handler(request):
        searched.append(request.url.path)
        if "search" in request.url.path:
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"items": []})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await YD.discover_for_case(client, db_session, case, ["a", "b", "c", "d"],
                                              "fake-key")

    asyncio.run(go())
    assert len([p for p in searched if "search" in p]) == 2


def test_discovery_runs_only_with_the_switch_and_a_key(db_session, monkeypatch):
    """The gate: the owner's downloader switch ON and an API key configured, nothing else."""
    from app.agents.visual_research import clip_discovery_enabled
    from app.core.config import settings

    _case(db_session)
    assert clip_discovery_enabled(db_session) is False           # the switch is off by default
    YC.set_enabled(db_session, True)
    monkeypatch.setattr(settings, "youtube_api_key", None)
    assert clip_discovery_enabled(db_session) is False           # no key: nothing calls the API
    monkeypatch.setattr(settings, "youtube_api_key", "key")
    assert clip_discovery_enabled(db_session) is True
