"""R2/R3/R4/R7: two kinds of video. Other channels' videos never become film candidates, an open
archive video with stored license evidence does, no evidence means no download, third-party footage
is capped."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.ai_config import ai_config
from app.services.video_rights import (
    FILM_CANDIDATE,
    RESEARCH_ONLY,
    RightsRefused,
    VideoRightsGate,
    VideoUseClassifier,
)


def _youtube(**kw):
    return {"url": "https://www.youtube.com/watch?v=abcdefghijk", "title": "Case X",
            "channel_name": "Dark Files", "classification": "creator_narration", **kw}


def test_a_video_of_another_true_crime_channel_is_always_research_only_even_with_cc():
    for classification in ("creator_narration", "analysis", "documentary", "interview", "unknown"):
        use = VideoUseClassifier().classify(_youtube(classification=classification, license="creativeCommon",
                                                      license_url="https://www.youtube.com/t/creative_commons"))
        assert use.use == RESEARCH_ONLY, classification
    assert use.reason in (ai_config.video_rights.reasons["no_class"],
                          ai_config.video_rights.reasons["other_creator_edited"])


def test_an_official_youtube_cc_video_with_evidence_is_a_film_candidate():
    use = VideoUseClassifier().classify(_youtube(
        classification="official", channel_name="State Archives", license="creativeCommon",
        license_url="https://www.youtube.com/t/creative_commons"))
    assert use.use == FILM_CANDIDATE and use.rights_status == "creative_commons"
    assert use.license_evidence_url and use.attribution_text.endswith("State Archives")


def test_a_cc_video_without_stored_evidence_or_from_a_reuploader_is_research_only():
    base = _youtube(classification="news", channel_name="A Local Station", license="creativeCommon")
    assert VideoUseClassifier().classify(base).use == RESEARCH_ONLY            # no evidence url
    assert "no stored license evidence" in VideoUseClassifier().classify(base).reason
    reup = dict(base, channel_name="Case Clips Mirror", license_url="https://x.example/l")
    assert "re-uploads" in VideoUseClassifier().classify(reup).reason
    not_cc = dict(base, license="youtube", license_url="https://x.example/l")
    assert VideoUseClassifier().classify(not_cc).use == RESEARCH_ONLY          # no class, no Creative Commons


def test_open_archives_need_an_open_license_and_evidence():
    ok = VideoUseClassifier().classify({
        "url": "https://archive.org/download/trial-1893/trial.mp4", "title": "Trial film",
        "license": "Public Domain Mark", "license_url": "https://creativecommons.org/publicdomain/mark/1.0/"})
    assert ok.use == FILM_CANDIDATE and ok.rights_status == "public_domain"
    assert ok.attribution_text == "Trial film / Internet Archive"
    no_license = VideoUseClassifier().classify({"url": "https://archive.org/download/a/b.mp4", "license": ""})
    assert no_license.use == RESEARCH_ONLY
    unknown = VideoUseClassifier().classify({"url": "https://example.com/video.mp4", "license": "CC0",
                                             "license_url": "https://example.com/l"})
    assert unknown.use == RESEARCH_ONLY and "host" in unknown.reason


def test_a_public_broadcaster_needs_a_stored_license_page():
    cand = {"url": "https://www.bbc.co.uk/clip.mp4", "title": "News film"}
    assert VideoUseClassifier().classify(cand).use == RESEARCH_ONLY
    assert VideoUseClassifier().classify({**cand, "license_url": "https://bbc.example/permission"}).use == FILM_CANDIDATE


def _video(**kw):
    base = dict(use=FILM_CANDIDATE, use_reason="Internet Archive", rights_status="public_domain",
                license_evidence_url="https://archive.org/details/x",
                license_checked_at=datetime(2026, 1, 1, tzinfo=UTC),
                url="https://archive.org/download/x/x.mp4", title="t", attribution_text="t / Internet Archive",
                channel_name=None, discovery_query=None)
    return SimpleNamespace(**{**base, **kw})


def test_nothing_is_downloaded_for_rendering_without_use_rights_and_evidence():
    gate = VideoRightsGate()
    assert gate.check(_video()) is None
    assert "research only" in gate.check(_video(use=RESEARCH_ONLY))
    assert "not allowed" in gate.check(_video(rights_status="permission_required"))
    assert "no stored license evidence" in gate.check(_video(license_evidence_url=None))
    assert "no stored license evidence" in gate.check(_video(license_checked_at=None))
    with pytest.raises(RightsRefused):
        gate.require(_video(use=RESEARCH_ONLY))


def test_the_footage_agent_refuses_a_research_video_a_page_and_a_missing_evidence(db_session):
    from app.agents.footage import FootageAgent

    agent = FootageAgent(client=None)
    for video, message in ((_video(use=RESEARCH_ONLY), "research only"),
                           (_video(license_evidence_url=None), "no stored license evidence"),
                           (_video(url="https://archive.org/details/some-item"), "web page")):
        with pytest.raises(RightsRefused, match=message):
            asyncio.run(agent.ingest_film_candidate(None, None, video))
    with pytest.raises(RightsRefused, match="switch is off"):     # a YouTube page: the owner's switch decides
        asyncio.run(agent.ingest_film_candidate(db_session, None, _video(url="https://www.youtube.com/watch?v=abc")))


def test_a_film_candidate_that_passes_the_gate_goes_through_the_normal_footage_ingest(monkeypatch):
    from app.agents.footage import FootageAgent

    seen = {}

    async def fake_ingest(self, db, case, c, status, reason, hashes, found_during):
        seen.update(candidate=c, status=status, reason=reason)
        return "added"

    monkeypatch.setattr(FootageAgent, "_ingest", fake_ingest)
    out = asyncio.run(FootageAgent(client=None).ingest_film_candidate(None, None, _video()))
    assert out == "added" and seen["status"] == "public_domain"
    assert seen["candidate"].credit == "t / Internet Archive" and seen["candidate"].provider == "film_candidate"
    assert "film candidate" in seen["reason"]


def test_third_party_footage_is_capped_per_clip_and_in_total(monkeypatch):
    from app.documentary.production import script as S

    monkeypatch.setattr(ai_config.footage, "max_third_party_clip_seconds", 5.0)
    monkeypatch.setattr(ai_config.footage, "max_third_party_share", 0.10)
    assets = {"V1": SimpleNamespace(asset_code="V1", provider="internet_archive"),
              "V2": SimpleNamespace(asset_code="V2", provider="internet_archive"),
              "P1": SimpleNamespace(asset_code="P1", provider="upload")}
    shots = [{"kind": "video", "asset_id": "V1", "clip_start": 0.0, "clip_end": 30.0,
              "start": 0.0, "end": 30.0, "beat_id": "B1"},
             {"kind": "video", "asset_id": "V2", "clip_start": 0.0, "clip_end": 30.0,
              "start": 30.0, "end": 60.0, "beat_id": "B2"}]
    monkeypatch.setattr(S, "_earned_pool", lambda *a, **k: [assets["P1"]])
    monkeypatch.setattr(S, "_asset_info", lambda a: {"asset_id": a.asset_code})
    monkeypatch.setattr(S, "_base", lambda sh: {"beat_id": sh["beat_id"]})
    monkeypatch.setattr(S, "_repeat_fields", lambda choice: {})
    monkeypatch.setattr(S, "named_between", lambda *a: [])

    class Tracker:
        def pick(self, pool, a, b, named, avoid=None):
            return {"asset_id": "P1", "reason": "photo"}
        def discard(self, *a): ...
        def add(self, *a): ...

    out = S._clip_overruns(shots, {}, assets, [], None, Tracker(), [], [])
    third = sum(s["end"] - s["start"] for s in out if s["kind"] == "video")
    assert third <= 0.10 * 60 + 1e-6              # at most 10 percent of the film
    assert max(s["end"] - s["start"] for s in out if s["kind"] == "video") <= 5.0 + 1e-6
    assert [s["asset_id"] for s in out if s["kind"] == "image"]
