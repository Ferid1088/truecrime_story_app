"""P15: YouTube video discovery through the Data API — finding and ranking only, cheap calls
first (playlistItems before search), the real license fed to the existing classifier, caching
so a query never runs twice, moment alignment from the existing (non-quota) transcript path."""

import asyncio
import uuid

import httpx

from app.core.ai_config import ai_config
from app.db.models import Case, YoutubeQuotaLedger
from app.documentary.visuals import youtube_discovery as YD
from app.providers.transcript.base import TranscriptSegmentData


def _case(db):
    case = Case(canonical_title="Discovery", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


# ---- cheap calls first: channel uploads via playlistItems, never search -----------------------

def test_channel_uploads_lists_the_uploads_playlist_cheaply(db_session):
    case = _case(db_session)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if "channels" in request.url.path:
            return httpx.Response(200, json={"items": [
                {"contentDetails": {"relatedPlaylists": {"uploads": "UUabc"}}}]})
        return httpx.Response(200, json={"items": [
            {"snippet": {"title": "1122 King Road walkthrough"}},
            {"snippet": {"title": "unrelated local news"}},
        ]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await YD.channel_uploads(client, db_session, case.id, "UCxyz", "fake-key")

    items = asyncio.run(go())
    assert len(items) == 2
    assert any("channels" in c for c in calls) and any("playlistItems" in c for c in calls)
    assert not any("search" in c for c in calls)
    rows = db_session.query(YoutubeQuotaLedger).filter_by(case_id=case.id).all()
    assert {r.endpoint for r in rows} == {"channels.list", "playlistItems.list"}


def test_filter_by_title_drops_uploads_that_do_not_name_the_entity():
    items = [{"snippet": {"title": "1122 King Road walkthrough"}},
            {"snippet": {"title": "unrelated local news"}}]
    out = YD.filter_by_title(items, ["1122 King Road"])
    assert len(out) == 1 and "King Road" in out[0]["snippet"]["title"]


# ---- caching: the same query is never run twice ------------------------------------------------

def test_the_same_call_is_never_repeated_within_its_cache_expiry(db_session):
    case = _case(db_session)
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        return httpx.Response(200, json={"items": [{"id": "vcache1"}]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            a = await YD.video_details(client, db_session, case.id, ["vcache1"], "fake-key")
            b = await YD.video_details(client, db_session, case.id, ["vcache1"], "fake-key")
            return a, b

    a, b = asyncio.run(go())
    assert a == b and hits["n"] == 1


# ---- videos.list batching ----------------------------------------------------------------------

def test_video_details_batches_up_to_the_configured_limit(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.youtube_data_api, "max_video_ids_per_call", 2)
    case = _case(db_session)
    calls = []

    def handler(request):
        calls.append(request.url.params.get("id"))
        return httpx.Response(200, json={"items": []})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await YD.video_details(client, db_session, case.id, ["a", "b", "c"], "fake-key")

    asyncio.run(go())
    assert calls == ["a,b", "c"]


# ---- search.list: budgeted, gated by both the discovery share and the daily search cap --------

def test_search_videos_is_refused_once_the_discovery_share_is_used_up(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.youtube_data_api, "daily_quota_units", 100)
    monkeypatch.setattr(ai_config.youtube_data_api, "discovery_share_percent", 1.0)
    case = _case(db_session)
    from app.documentary.visuals import youtube_quota as Q
    Q.record_call(db_session, stage="discovery", endpoint="videos.list", case_id=case.id, units=10)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"items": [1]}))) as client:
            return await YD.search_videos(client, db_session, case.id, "q", "fake-key")

    assert asyncio.run(go()) is None


def test_search_videos_runs_within_budget(db_session):
    case = _case(db_session)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"items": [{"id": {"videoId": "v1"}}]}))) as client:
            return await YD.search_videos(client, db_session, case.id, "q", "fake-key")

    assert asyncio.run(go()) == [{"id": {"videoId": "v1"}}]


# ---- the real license feeds the EXISTING classifier (never duplicated) ------------------------

def test_a_creative_commons_license_from_the_api_is_a_film_candidate():
    item = {"id": "v1", "snippet": {"title": "City council meeting", "channelTitle": "Some Channel",
                                   "description": ""},
           "status": {"license": "creativeCommon"}, "contentDetails": {"duration": "PT1M30S"}}
    out = YD.classify_video(item)
    assert out["license"] == "creativeCommon" and out["duration_seconds"] == 90
    assert out["use_reason"]  # some reason recorded either way


def test_a_standard_youtube_license_alone_is_not_enough_for_a_film_candidate():
    item = {"id": "v2", "snippet": {"title": "random vlog", "channelTitle": "Random Channel",
                                   "description": ""},
           "status": {"license": "youtube"}, "contentDetails": {}}
    out = YD.classify_video(item)
    assert out["license"] == "youtube"
    assert out["use"] == "research_only"


# ---- moment alignment: from the existing transcript path, not the Data API --------------------

def test_align_sentence_finds_the_best_overlapping_segment():
    segments = [
        TranscriptSegmentData(0.0, 4.0, "the weather today is sunny"),
        TranscriptSegmentData(4.0, 9.0, "police found no murder weapon at the scene"),
        TranscriptSegmentData(9.0, 12.0, "thanks for watching"),
    ]
    out = YD.align_sentence(segments, "Police found no murder weapon inside the house.", margin=1.0)
    assert out["start"] == 3.0 and out["end"] == 10.0
    assert 0 < out["score"] <= 1


def test_align_sentence_returns_none_when_nothing_overlaps():
    segments = [TranscriptSegmentData(0.0, 4.0, "completely unrelated content here")]
    assert YD.align_sentence(segments, "Police found no murder weapon.", margin=1.0) is None


# ---- ranking: alignment score first, then channel-type bonus ----------------------------------

# ---- the full chain: cheap channel path never calls search.list -------------------------------

def test_candidates_for_sentence_never_searches_when_the_channel_path_answers(db_session, monkeypatch):
    case = _case(db_session)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if "channels" in request.url.path:
            return httpx.Response(200, json={"items": [
                {"contentDetails": {"relatedPlaylists": {"uploads": "UUsentence1"}}}]})
        if "playlistItems" in request.url.path:
            return httpx.Response(200, json={"items": [
                {"snippet": {"title": "1122 King Road walkthrough",
                            "resourceId": {"videoId": "vsentence1"}}}]})
        if "videos" in request.url.path:
            return httpx.Response(200, json={"items": [{
                "id": "vsentence1", "snippet": {"title": "1122 King Road walkthrough",
                                               "channelTitle": "Local News Channel", "description": ""},
                "status": {"license": "youtube"}, "contentDetails": {"duration": "PT30S"}}]})
        raise AssertionError(f"unexpected call to {request.url.path}")

    class FakeTranscriptProvider:
        async def get_transcript(self, video_id, preferred_languages=None):
            from app.providers.transcript.base import TranscriptResult, TranscriptSegmentData

            return TranscriptResult(available=True, segments=[
                TranscriptSegmentData(0.0, 5.0, "police found the house at 1122 king road")])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await YD.candidates_for_sentence(
                client, db_session, case.id, "Police found the house on King Road.",
                "1122 King Road", "fake-key", channel_id="UCsentence1", must_name=["1122 King Road"],
                transcript_provider=FakeTranscriptProvider())

    out = asyncio.run(go())
    assert out["searched"] is False
    assert not any("search" in c for c in calls)
    [c] = out["candidates"]
    assert c["video_id"] == "vsentence1" and c["alignment"]["score"] > 0


def test_rank_candidates_prefers_better_alignment_and_official_channels(monkeypatch):
    monkeypatch.setattr(ai_config.youtube_research, "classification_rank_bonus",
                        {"official_body": 0.6, "witness_family": 0.1})
    candidates = [
        {"video_id": "a", "use": "film_candidate", "source_class": "witness_family",
         "alignment": {"score": 0.9}},
        {"video_id": "b", "use": "film_candidate", "source_class": "official_body",
         "alignment": {"score": 0.5}},
        {"video_id": "c", "use": "research_only", "source_class": None, "alignment": {"score": 0.95}},
    ]
    ranked = YD.rank_candidates(candidates, limit=3)
    assert [c["video_id"] for c in ranked] == ["b", "a", "c"]
