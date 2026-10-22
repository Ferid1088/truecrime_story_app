"""P2: automatic official-source discovery for YouTube clips — bodies derived from the case's
own facts, channels proven by stored evidence (domain link either way is the strong proof),
proven channels' uploads feed the existing clip chain. The owner never approves a channel."""

import asyncio
import json
import uuid

import httpx
import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, Fact, OfficialBody, VideoSource, YoutubeQuotaLedger
from app.documentary.visuals import official_sources as OS


@pytest.fixture(autouse=True)
def _clean_quota_cache(db_session):
    """API responses are cached in the shared test database by query — clear them so one
    test's answers never leak into the next."""
    db_session.query(YoutubeQuotaLedger).delete()
    db_session.commit()
    yield
    db_session.query(YoutubeQuotaLedger).delete()
    db_session.commit()


def _case(db):
    case = Case(canonical_title="Clip bodies", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


def _fact(db, case, claim):
    f = Fact(case_id=case.id, claim=claim, category="general", confidence=0.9)
    db.add(f)
    db.commit()
    return f


# ---- P2a: official bodies come from the case's own facts ---------------------------------------

def test_official_bodies_are_derived_from_facts_with_their_evidence(db_session):
    case = _case(db_session)
    f1 = _fact(db_session, case, "Moscow Police Department held a press conference.")
    f2 = _fact(db_session, case, "The FBI joined Moscow Police Department in the investigation.")
    _fact(db_session, case, "Latah County Prosecutor's Office filed the charges.")
    _fact(db_session, case, "Idaho State Police assisted the investigation.")
    out = OS.derive_official_bodies(db_session, case)
    names = {b.name: b for b in db_session.query(OfficialBody).filter_by(case_id=case.id)}
    assert "Moscow Police Department" in names
    assert "FBI" in names                                    # standalone federal body marker
    assert "Latah County Prosecutor" in names or "Latah County Prosecutor's Office" in names
    assert "Idaho State Police" in names
    assert out["found"] >= 4 and all(b.status == "found" for b in names.values())
    assert f1.id in json.loads(names["Moscow Police Department"].evidence_json)["fact_ids"]
    # the same body named twice merges its evidence, never duplicates the row
    assert f2.id in json.loads(names["Moscow Police Department"].evidence_json)["fact_ids"]


def test_junk_around_a_marker_never_becomes_a_body(db_session):
    case = _case(db_session)
    _fact(db_session, case, "His lawyers said in court that a surviving roommate called 911.")
    _fact(db_session, case, "Kohberger filed court documents last week.")
    OS.derive_official_bodies(db_session, case)
    assert db_session.query(OfficialBody).filter_by(case_id=case.id).count() == 0


# ---- P2b: a channel is proven official by stored evidence, never guessed ------------------------

MPD_CHANNEL = "UCmpd123"


def _api_handler(channel_items, channel_details, site_html=None):
    def handler(request):
        host = request.url.host
        if "googleapis.com" in host:
            if "search" in request.url.path:
                return httpx.Response(200, json={"items": channel_items})
            if "channels" in request.url.path:
                return httpx.Response(200, json={"items": channel_details})
            if "playlistItems" in request.url.path:
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"items": []})
        if site_html is None:
            return httpx.Response(404)
        return httpx.Response(200, text=site_html)
    return handler


def _body(db, case, name="Moscow Police Department"):
    body = OfficialBody(case_id=case.id, name=name, name_key=name.lower(),
                        body_type="police", status="found", evidence_json='{"fact_ids": [1]}')
    db.add(body)
    db.commit()
    return body


def _details(channel_id, title, links, subs, uploads="UUx"):
    return {
        "id": channel_id,
        "snippet": {"title": title, "description": "visit us at https://www.cityofmoscow.org/police"},
        "brandingSettings": {"channel": {"title": title, "links": [
            {"title": "City site", "targetUrl": "https://www.cityofmoscow.org/police"}]}},
        "statistics": {"subscriberCount": str(subs)},
        "contentDetails": {"relatedPlaylists": {"uploads": uploads}},
    }


def test_a_channel_linking_to_the_bodys_official_site_is_proven(db_session):
    case = _case(db_session)
    body = _body(db_session, case)
    items = [{"id": {"channelId": MPD_CHANNEL}, "snippet": {"title": "Moscow Police Department"}}]
    details = [_details(MPD_CHANNEL, "Moscow Police Department", [], 1200)]
    site = '<html><a href="https://youtube.com/channel/UCmpd123">YouTube</a></html>'

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                _api_handler(items, details, site_html=site))) as client:
            return await OS.prove_channels(client, db_session, case, "fake-key")

    asyncio.run(go())
    db_session.refresh(body)
    assert body.status == "proven" and body.channel_id == MPD_CHANNEL
    proof = json.loads(body.proof_json)
    assert proof["checks"]["channel_links_to_domain"] is True
    assert proof["checks"]["domain_links_to_channel"] is True
    assert proof["official_domain"] == "www.cityofmoscow.org"
    assert proof["score"] >= ai_config.official_sources.proof_min_score


def test_a_channel_with_only_a_matching_name_is_skipped_with_the_reason(db_session):
    case = _case(db_session)
    body = _body(db_session, case)
    items = [{"id": {"channelId": "UCfan"}, "snippet": {"title": "Moscow Police Department"}}]
    details = [{"id": "UCfan",
                "snippet": {"title": "Moscow Police Department",
                            "description": "fan page about the department"},
                "brandingSettings": {"channel": {"title": "Moscow Police Department"}},
                "statistics": {"subscriberCount": "55"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UUx"}}}]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                _api_handler(items, details, site_html=None))) as client:
            return await OS.prove_channels(client, db_session, case, "fake-key")

    asyncio.run(go())
    db_session.refresh(body)
    assert body.status == "skipped" and "no domain link" in body.skip_reason
    assert body.channel_id is None
    # the skipped candidate is still recorded for the audit trail
    proof = json.loads(body.proof_json)
    assert proof["candidates"][0]["channel_id"] == "UCfan"


def test_a_wrong_named_channel_is_never_proven(db_session):
    case = _case(db_session)
    body = _body(db_session, case)
    items = [{"id": {"channelId": "UCother"}, "snippet": {"title": "Idaho Crime Today"}}]
    details = [{"id": "UCother",
                "snippet": {"title": "Idaho Crime Today",
                            "description": "news about idaho https://idahocrime.tv"},
                "brandingSettings": {"channel": {"title": "Idaho Crime Today"}},
                "statistics": {"subscriberCount": "900000"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UUy"}}}]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                _api_handler(items, details, site_html=None))) as client:
            return await OS.prove_channels(client, db_session, case, "fake-key")

    asyncio.run(go())
    db_session.refresh(body)
    assert body.status == "skipped"


# ---- P2c: proven channels' uploads feed the existing clip chain --------------------------------

def _search_and_details(videos):
    details = [
        {"id": v["id"],
         "snippet": {"title": v["title"], "channelTitle": "Moscow Police Department",
                     "channelId": MPD_CHANNEL, "description": ""},
         "status": {"license": "youtube"},
         "contentDetails": {"duration": "PT10M"}} for v in videos]

    def handler(request):
        if "search" in request.url.path:
            return httpx.Response(200, json={"items": [
                {"id": {"channelId": MPD_CHANNEL},
                 "snippet": {"title": "Moscow Police Department"}}]})
        if "channels" in request.url.path:
            return httpx.Response(200, json={"items": [
                _details(MPD_CHANNEL, "Moscow Police Department", [], 1200)]})
        if "playlistItems" in request.url.path:
            return httpx.Response(200, json={"items": [
                {"snippet": {"title": v["title"],
                             "resourceId": {"videoId": v["id"]}}} for v in videos]})
        if "videos" in request.url.path:
            return httpx.Response(200, json={"items": details})
        return httpx.Response(200, text='<a href="https://youtube.com/channel/UCmpd123">Y</a>')
    return handler


def test_proven_channel_uploads_become_official_film_candidates_with_the_proof(db_session):
    case = _case(db_session)
    _body(db_session, case)
    videos = [{"id": "vidA", "title": "Moscow Police press conference: King Road update"},
              {"id": "vidB", "title": "Parks and trails announcement"}]
    tp_called = []

    class TP:
        async def get_transcript(self, video_id, preferred_languages=None):
            tp_called.append(video_id)
            from app.providers.transcript.base import TranscriptResult, TranscriptSegmentData
            return TranscriptResult(available=True, language="en", transcript_type="manual",
                                    segments=[TranscriptSegmentData(0.0, 5.0, "the police said")])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_search_and_details(videos))) as client:
            await OS.prove_channels(client, db_session, case, "fake-key")
            return await OS.search_official_channel_videos(
                client, db_session, case, ["1122 King Road", "King Road", "Moscow"],
                "fake-key", transcript_provider=TP())

    stats = asyncio.run(go())
    row = db_session.query(VideoSource).filter_by(case_id=case.id, video_id="vidA").first()
    assert row is not None and row.use == "film_candidate" and row.source_class == "official_body"
    assert json.loads(row.channel_proof_json)["status"] == "proven"
    assert "proven" in (row.class_evidence or "")
    assert tp_called == ["vidA"] and row.transcript_status == "available"
    # the parks video names no case entity — it stays out of the clip chain
    assert db_session.query(VideoSource).filter_by(case_id=case.id, video_id="vidB").first() is None
    assert stats["candidates"] == 1


# ---- P2d: proven channels count as reviewed; everything else keeps the blocking need ------------

def test_a_clip_from_a_proven_channel_needs_no_review_but_an_unproven_one_does(db_session):

    from app.autopilot.runner import _youtube_clips_needing_review
    from app.db.models import ProductionScript, Video

    case = _case(db_session)
    from app.db.models import VisualAsset

    db_session.add(VisualAsset(case_id=case.id, asset_code="P1", asset_type="video",
                               provider="youtube_clip", rights_status="editorial_review_required",
                               spec_json=json.dumps({"source_record": {
                                   "channel_proof": {"status": "proven", "score": 8}}})))
    db_session.add(VisualAsset(case_id=case.id, asset_code="P2", asset_type="video",
                               provider="youtube_clip", rights_status="editorial_review_required",
                               spec_json=json.dumps({"source_record": {}})))
    db_session.commit()
    ps = ProductionScript(case_id=case.id, story_version_id=1, language="en", mode="full",
                          status="rendered", script_json=json.dumps(
                              {"shots": [{"asset_id": "P1"}, {"asset_id": "P2"}]}))
    db_session.add(ps)
    db_session.commit()
    video = Video(case_id=case.id, job_id=1, language="en", state="rendered",
                  production_script_id=ps.id)
    db_session.add(video)
    db_session.commit()
    assert _youtube_clips_needing_review(db_session, video) == ["P2"]
