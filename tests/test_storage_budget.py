import asyncio
import io
import json
import random
import uuid
from datetime import timedelta

import httpx
from PIL import Image

from app.agents import visual_research as VR
from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals import storage_budget as SB
from app.utils import utc_now


def _case(db):
    case = Case(canonical_title="Disk", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


def _asset(db, case, code, size, status="verified", root=None, age_days=0, **kw):
    path = root / f"{code}.jpg"
    path.write_bytes(b"x" * size)
    thumb = root / f"{code}_t.jpg"
    thumb.write_bytes(b"x" * 10)
    a = VisualAsset(case_id=case.id, asset_code=code, asset_type=kw.pop("asset_type", "photo"),
                    provider=kw.pop("provider", "wikimedia"), verification_status=status,
                    local_path=str(path), thumbnail_path=str(thumb), **kw)
    db.add(a)
    db.commit()
    a.created_at = utc_now() - timedelta(days=age_days)
    db.commit()
    return a


def test_the_disk_use_of_a_case_is_reported_by_kind_and_verdict(db_session, tmp_path):
    case = _case(db_session)
    _asset(db_session, case, "A", 1000, root=tmp_path)
    _asset(db_session, case, "B", 3000, "rejected", root=tmp_path)
    _asset(db_session, case, "G", 500, provider="generated", root=tmp_path)
    _asset(db_session, case, "V", 4000, asset_type="video", root=tmp_path)
    use = SB.usage(db_session, case.id)
    assert use["files"] == 4 and use["bytes"] == 8500 + 40
    assert use["by_status"]["rejected"] == 3010 and use["by_kind"]["video"] == 4010
    assert use["by_kind"]["photo"] == 4020 and use["by_kind"]["generated"] == 510 and use["by_status"]["verified"] == 5530


def test_the_budget_is_in_config_and_exceeded_when_the_case_holds_more(db_session, tmp_path, monkeypatch):
    case = _case(db_session)
    _asset(db_session, case, "A", 2_000_000, root=tmp_path)
    monkeypatch.setattr(ai_config.storage_budget, "max_case_mb", 1.5)
    assert SB.exceeded(db_session, case.id)
    monkeypatch.setattr(ai_config.storage_budget, "max_case_mb", 3.0)
    assert not SB.exceeded(db_session, case.id)


def test_files_of_old_rejected_pictures_are_removed_and_their_metadata_stays(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.storage_budget, "rejected_retention_days", 10)
    case = _case(db_session)
    old = _asset(db_session, case, "OLD", 1000, "rejected", root=tmp_path, age_days=11,
                 verification_json=json.dumps({"reason": "tone"}), sha256="a" * 64, phash="f" * 16)
    young = _asset(db_session, case, "YOUNG", 1000, "rejected", root=tmp_path, age_days=2)
    kept = _asset(db_session, case, "KEPT", 1000, "verified", root=tmp_path, age_days=90)
    out = SB.purge_rejected(db_session, case.id)
    assert out == {"removed": 1, "bytes": 1010}
    db_session.refresh(old)
    assert old.local_path is None and old.thumbnail_path is None and old.file_removed_at is not None
    assert (old.verification_status, old.sha256, old.phash) == ("rejected", "a" * 64, "f" * 16)
    assert json.loads(old.verification_json)["reason"] == "tone"
    assert not (tmp_path / "OLD.jpg").exists() and (tmp_path / "YOUNG.jpg").exists()
    assert (tmp_path / "KEPT.jpg").exists() and young.local_path and kept.local_path
    assert SB.purge_rejected(db_session, case.id) == {"removed": 0, "bytes": 0}


def test_a_picture_that_is_not_rejected_is_never_purged_however_old(db_session, tmp_path):
    case = _case(db_session)
    _asset(db_session, case, "N", 100, "needs_review", root=tmp_path, age_days=999)
    assert SB.purge_rejected(db_session, case.id)["removed"] == 0


def _big_client(size):
    def handler(request):
        return httpx.Response(200, content=b"\xff" * size, headers={"content-type": "image/jpeg"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_a_download_over_the_limit_is_refused_with_its_size_and_limit(monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "max_download_mb", 0.001)

    async def go():
        async with _big_client(5000) as client:
            await IM.download_image("https://img.example/big.jpg", client)

    try:
        asyncio.run(go())
    except IM.ImageTooLarge as e:
        assert e.limit_mb == 0.001 and "larger than 0.001 MB" in str(e)
    else:
        raise AssertionError("expected a refusal")


class FakeBrave:
    async def search_media(self, kind, query, language, limit=10):
        return [{"type": "image_result", "title": "huge", "url": "https://news.example/p",
                 "source": "news.example", "properties": {"url": "https://img.example/h.jpg",
                                                          "width": 900, "height": 600}}]


def test_the_agent_keeps_a_visible_record_of_a_refused_download(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.footage, "enabled", False)
    monkeypatch.setattr(ai_config.visual_search, "providers", ["brave_images"])
    monkeypatch.setattr(ai_config.visual_search, "max_download_mb", 0.001)
    monkeypatch.setattr(VR, "brave_backend", lambda: FakeBrave())
    case = _case(db_session)

    async def go():
        async with _big_client(5000) as client:
            return await VR.VisualResearchAgent(client).run(
                db_session, case, [{"query": "house", "entity": "e", "entity_type": "building", "kind": "exact"}])

    stats = asyncio.run(go())
    [a] = db_session.query(VisualAsset).filter_by(case_id=case.id).all()
    assert stats["refused"] == 1 and a.local_path is None and a.verification_status == "rejected"
    reason = json.loads(a.verification_json)
    assert reason["reason"] == "download_too_large" and reason["limit_mb"] == 0.001
    assert a.source_url == "https://img.example/h.jpg"


def test_the_agent_stops_taking_pictures_when_the_case_storage_budget_is_spent(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.footage, "enabled", False)
    monkeypatch.setattr(ai_config.visual_search, "providers", ["brave_images"])
    monkeypatch.setattr(VR, "brave_backend", lambda: FakeBrave())
    case = _case(db_session)
    _asset(db_session, case, "A", 2_000_000, root=tmp_path)
    monkeypatch.setattr(ai_config.storage_budget, "max_case_mb", 1.0)
    rng = random.Random(1)
    img = Image.new("RGB", (900, 600))
    img.putdata([(rng.randrange(256),) * 3 for _ in range(900 * 600)])
    buf = io.BytesIO()
    img.save(buf, "JPEG")

    async def go():
        def handler(request):
            return httpx.Response(200, content=buf.getvalue(), headers={"content-type": "image/jpeg"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await VR.VisualResearchAgent(client).run(
                db_session, case, [{"query": "house", "entity": "e", "entity_type": "building", "kind": "exact"}])

    stats = asyncio.run(go())
    assert stats["storage_stopped"] is True and stats["added"] == 0
    assert storage  # the storage module is the one that resolves the paths above


def test_the_pieces_of_one_video_share_its_file_and_count_once(db_session, tmp_path):
    case = _case(db_session)
    source = _asset(db_session, case, "SRC", 5000, asset_type="video_source", root=tmp_path)
    for n in range(3):
        db_session.add(VisualAsset(case_id=case.id, asset_code=f"PC{n}", asset_type="video", provider="wikimedia",
                                   verification_status="unverified", local_path=source.local_path,
                                   thumbnail_path=str(tmp_path / f"pc{n}.jpg")))
        (tmp_path / f"pc{n}.jpg").write_bytes(b"x" * 100)
    db_session.commit()
    assert SB.usage(db_session, case.id)["bytes"] == 5000 + 10 + 300
