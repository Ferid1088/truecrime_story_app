"""S6c: the YouTube clip route — off by default, only for film-candidate classes, a source record per
clip, an on-screen credit, stored once."""

import asyncio
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.footage import FootageAgent
from app.core.ai_config import ai_config
from app.db.models import AppSetting, Case, VisualAsset
from app.documentary.visuals import rights as R
from app.documentary.visuals import youtube_clips as YC
from app.services.video_rights import RightsRefused


@pytest.fixture(autouse=True)
def _switch_starts_off(db_session):
    """The switch lives in the shared test database: every test starts from the default (off)."""
    db_session.query(AppSetting).filter(AppSetting.key == YC.SETTING_KEY).delete()
    db_session.commit()
    yield
    db_session.query(AppSetting).filter(AppSetting.key == YC.SETTING_KEY).delete()
    db_session.commit()


def _case(db):
    case = Case(canonical_title="YT", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


def _video(**kw):
    base = dict(url="https://www.youtube.com/watch?v=abcdefghijk", title="Press conference", channel_name="Moscow Police Department",
                channel_url="https://www.youtube.com/@mpd", use="film_candidate", use_reason="official body",
                rights_status="editorial_review_required", license_evidence_url=None, license_checked_at=None,
                class_evidence="channel kind official", source_class="official_body", classification="official",
                attribution_text="Press conference / Moscow Police Department", duration_seconds=600,
                discovery_query="Idaho murders press conference", published_at="2022-12-30", video_id="abcdefghijk")
    base.update(kw)
    return SimpleNamespace(**base)


class FakeDownloader:
    def __init__(self, content=b"video-bytes"):
        self.calls, self.content = [], content

    async def fetch(self, url, dest):
        self.calls.append(url)
        Path(dest).write_bytes(self.content)
        return Path(dest)


def test_the_switch_is_off_until_the_owner_turns_it_on(db_session):
    assert YC.enabled(db_session) is False
    assert ai_config.youtube_clips.default_enabled is False
    YC.set_enabled(db_session, True)
    assert YC.enabled(db_session) is True
    YC.set_enabled(db_session, False)
    assert YC.enabled(db_session) is False
    assert db_session.get(AppSetting, YC.SETTING_KEY) is not None


def test_nothing_in_the_code_turns_the_switch_on():
    root = Path(__file__).resolve().parent.parent
    callers = [p for p in (root / "app").rglob("*.py")
               if re.search(r"(YC|youtube_clips)\.set_enabled\(", p.read_text())]
    assert [p.name for p in callers] == ["api.py"]       # only the owner's Settings endpoint


def test_with_the_switch_off_the_downloader_is_never_called(db_session):
    case = _case(db_session)
    dl = FakeDownloader()
    with pytest.raises(RightsRefused, match="switch is off"):
        asyncio.run(FootageAgent(client=None, downloader=dl).ingest_film_candidate(db_session, case, _video()))
    assert dl.calls == []


def test_only_film_candidate_classes_are_downloaded(db_session):
    case = _case(db_session)
    YC.set_enabled(db_session, True)
    dl = FakeDownloader()
    agent = FootageAgent(client=None, downloader=dl)
    for video, why in ((_video(use="research_only"), "research only"),
                       (_video(source_class=None, class_evidence=None), "no stored license evidence"),
                       (_video(classification="documentary"), "another creator"),
                       (_video(classification="creator_narration"), "another creator")):
        with pytest.raises(RightsRefused, match=why):
            asyncio.run(agent.ingest_film_candidate(db_session, case, video))
    assert dl.calls == []


def test_a_video_longer_than_the_limit_is_not_downloaded(db_session, monkeypatch):
    case = _case(db_session)
    YC.set_enabled(db_session, True)
    monkeypatch.setattr(ai_config.youtube_clips, "max_source_seconds", 100)
    dl = FakeDownloader()
    with pytest.raises(RightsRefused, match="longer than"):
        asyncio.run(FootageAgent(client=None, downloader=dl).ingest_film_candidate(
            db_session, case, _video(duration_seconds=600)))
    assert dl.calls == []


def test_the_downloader_gets_a_height_size_and_single_video_limit_from_config():
    opts = YC.build_options(Path("/tmp/x.%(ext)s"))
    cfg = ai_config.youtube_clips
    assert opts["noplaylist"] is True and opts["max_filesize"] == int(cfg.max_download_mb * 1024 * 1024)
    assert f"height<={cfg.max_height}" in opts["format"] and opts["outtmpl"] == "/tmp/x.%(ext)s"
    assert opts["quiet"] is True and opts["no_warnings"] is True


def _stub_ingest(monkeypatch, seen):
    async def fake(self, db, case, c, status, reason, hashes, found_during, fetched=None, record=None):
        seen.update(candidate=c, status=status, reason=reason, fetched=fetched, record=record)
        return "added"

    monkeypatch.setattr(FootageAgent, "_ingest_pieces", fake)


def test_an_allowed_video_is_downloaded_once_and_carries_a_full_source_record(db_session, monkeypatch):
    case = _case(db_session)
    YC.set_enabled(db_session, True)
    seen = {}
    _stub_ingest(monkeypatch, seen)
    dl = FakeDownloader()
    out = asyncio.run(FootageAgent(client=None, downloader=dl).ingest_film_candidate(db_session, case, _video()))
    assert out == "added" and dl.calls == ["https://www.youtube.com/watch?v=abcdefghijk"]
    c, record = seen["candidate"], seen["record"]
    assert c.provider == "youtube_clip" and c.credit == "Press conference / Moscow Police Department (YouTube)"
    assert record["url"] == c.url and record["channel"] == "Moscow Police Department"
    assert record["title"] == "Press conference" and record["source_class"] == "official_body"
    assert record["class_evidence"] == "channel kind official" and record["video_date"] == "2022-12-30"
    assert record["sha256"] and record["downloaded_at"] and record["channel_url"].endswith("@mpd")


def test_the_same_download_is_stored_once_and_reused(db_session, monkeypatch):
    case = _case(db_session)
    YC.set_enabled(db_session, True)
    db_session.add(VisualAsset(case_id=case.id, asset_code="V1", asset_type="video_source", provider="youtube_clip",
                               sha256=__import__("hashlib").sha256(b"video-bytes").hexdigest()))
    db_session.commit()
    seen = {}
    _stub_ingest(monkeypatch, seen)
    out = asyncio.run(FootageAgent(client=None, downloader=FakeDownloader()).ingest_film_candidate(
        db_session, case, _video()))
    assert out == "duplicates" and seen == {}


def test_a_youtube_clip_is_credited_on_screen_even_though_its_status_is_not_a_licence():
    class A:
        rights_status, provider, credit = "editorial_review_required", "youtube_clip", "X / Channel (YouTube)"

    assert R.credit_due(A)


def test_a_clip_from_youtube_is_cut_to_its_own_length_and_share_of_the_film(monkeypatch):
    from app.documentary.production import script as S

    monkeypatch.setattr(ai_config.youtube_clips, "max_clip_seconds", 4.0)
    monkeypatch.setattr(ai_config.youtube_clips, "max_share", 0.05)
    monkeypatch.setattr(ai_config.footage, "max_third_party_clip_seconds", 100.0)
    monkeypatch.setattr(ai_config.footage, "max_third_party_share", 1.0)
    assets = {"Y1": SimpleNamespace(asset_code="Y1", provider="youtube_clip"),
              "Y2": SimpleNamespace(asset_code="Y2", provider="youtube_clip"),
              "P1": SimpleNamespace(asset_code="P1", provider="upload")}
    shots = [{"kind": "video", "asset_id": "Y1", "clip_start": 0.0, "clip_end": 30.0, "start": 0.0, "end": 30.0,
              "beat_id": "B1"},
             {"kind": "video", "asset_id": "Y2", "clip_start": 0.0, "clip_end": 30.0, "start": 30.0, "end": 100.0,
              "beat_id": "B2"}]
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
    seconds = sum(s["end"] - s["start"] for s in out if s["kind"] == "video")
    assert seconds <= 0.05 * 100 + 1e-6
    assert max(s["end"] - s["start"] for s in out if s["kind"] == "video") <= 4.0 + 1e-6
    assert json.dumps(out)


def _row(db, case, **kw):
    from app.db.models import VideoSource

    base = dict(case_id=case.id, video_id=uuid.uuid4().hex[:11], url="https://www.youtube.com/watch?v=x", title="t",
                channel_name="Moscow Police Department", channel_url="https://y/@mpd", classification="official",
                use="film_candidate", use_reason="official body", rights_status="editorial_review_required",
                source_class="official_body", class_evidence="channel kind official", duration_seconds=100)
    base.update(kw)
    row = VideoSource(**base)
    db.add(row)
    db.commit()
    return row


def test_film_candidates_are_ingested_and_the_refused_ones_are_counted_by_reason(db_session, monkeypatch):
    case = _case(db_session)
    _row(db_session, case, url="https://www.youtube.com/watch?v=a1")
    _row(db_session, case, url="https://www.courts.state.gov/h.mp4", source_class="official_body")
    _row(db_session, case, use="research_only", url="https://www.youtube.com/watch?v=b2")
    seen = []

    async def fake_ingest(self, db, case_, video, profile=None, found_during="research"):
        if YC.is_youtube(video.url):
            YC.check_allowed(db, video, profile)
        seen.append(video.url)
        return "added"

    monkeypatch.setattr(FootageAgent, "ingest_film_candidate", fake_ingest)
    out = asyncio.run(FootageAgent(client=None).ingest_film_candidates(db_session, case))
    assert seen == ["https://www.courts.state.gov/h.mp4"] or "https://www.courts.state.gov/h.mp4" in seen
    assert out["added"] == 1 and out["refused"] == {"the YouTube clip downloader switch is off (Settings)": 1}
    YC.set_enabled(db_session, True)
    again = asyncio.run(FootageAgent(client=None).ingest_film_candidates(db_session, case))
    assert again["added"] == 2 and again["refused"] == {}


def test_a_video_already_in_the_library_is_not_ingested_again(db_session, monkeypatch):
    case = _case(db_session)
    _row(db_session, case, url="https://www.courts.state.gov/h.mp4")
    db_session.add(VisualAsset(case_id=case.id, asset_code="V9", asset_type="video_source", provider="film_candidate",
                               page_url=None, source_url="https://www.courts.state.gov/h.mp4"))
    db_session.commit()

    async def boom(self, *a, **k):
        raise AssertionError("already stored")

    monkeypatch.setattr(FootageAgent, "ingest_film_candidate", boom)
    assert asyncio.run(FootageAgent(client=None).ingest_film_candidates(db_session, case))["skipped"] == 1
