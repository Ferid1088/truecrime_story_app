"""P8 (failures are retries, not verdicts: a gateway that cannot answer stops the stage with a
visible status instead of burning every candidate as an error) and P14 (the intake limit applies
to the UNCHECKED BACKLOG, not the whole library) of docs/visual_supply_checklist.md."""

import asyncio
import uuid

import httpx

from app.agents import visual_research as VR
from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary import jobs as J
from app.documentary.visuals.research import intake_status


def _case(db):
    case = Case(canonical_title="Gateway", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


class _DownGen:
    def __init__(self, status="forbidden"):
        self.status = status

    async def provider_status(self):
        return {"status": self.status}


class _UpGen:
    async def provider_status(self):
        return {"status": "ok"}


class FakeBrave:
    async def search_media(self, kind, query, language, limit=10):
        return [{"type": "image_result", "title": "house", "url": "https://img.example/h.jpg",
                 "source": "news.example", "properties": {"url": "https://img.example/h.jpg",
                                                           "width": 900, "height": 600}}]


# ---- P8: the research stage never downloads while the vision gateway cannot answer -------------

def test_research_does_not_download_while_the_vision_gateway_is_down(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.footage, "enabled", False)
    monkeypatch.setattr(ai_config.visual_search, "providers", ["brave_images"])
    monkeypatch.setattr(VR, "brave_backend", lambda: FakeBrave())
    case = _case(db_session)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=b"x"))) as client:
            agent = VR.VisualResearchAgent(client)
            agent.gen = _DownGen()
            return await agent.run(
                db_session, case,
                [{"query": "house", "entity": "e", "entity_type": "building", "kind": "exact"}])

    stats = asyncio.run(go())
    assert stats["added"] == 0
    assert stats["vision_unavailable"] == "forbidden"
    assert "vision checks unavailable" in stats["degraded"]
    assert db_session.query(VisualAsset).filter_by(case_id=case.id).count() == 0


def test_research_downloads_normally_once_the_gateway_answers(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.footage, "enabled", False)
    monkeypatch.setattr(ai_config.visual_search, "providers", ["brave_images"])
    monkeypatch.setattr(VR, "brave_backend", lambda: FakeBrave())
    case = _case(db_session)
    img_bytes = _jpeg_bytes()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=img_bytes,
                                         headers={"content-type": "image/jpeg"}))) as client:
            agent = VR.VisualResearchAgent(client)
            agent.gen = _UpGen()
            return await agent.run(
                db_session, case,
                [{"query": "house", "entity": "e", "entity_type": "building", "kind": "exact"}])

    stats = asyncio.run(go())
    assert stats["added"] == 1
    assert "vision_unavailable" not in stats


def _jpeg_bytes():
    import io

    from PIL import Image

    img = Image.new("RGB", (900, 600), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


# ---- P8: the check stage never attempts a batch it already knows will fail ----------------------

ENTITIES = [{"key": "fall_river", "name": "Fall River", "type": "place"}]


def _photo(db, case, code, **kw):
    a = VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", provider="wikimedia",
                    local_path="x.jpg", thumbnail_path="t.jpg", rights_status="public_domain",
                    verification_status="unverified", **kw)
    db.add(a)
    db.commit()
    return a


async def _down_status(gen):
    return False, "forbidden"


def test_the_check_stage_stops_before_any_attempt_when_the_gateway_is_down(db_session, monkeypatch):
    monkeypatch.setattr(J, "gateway_available", _down_status)
    case = _case(db_session)
    a = _photo(db_session, case, "A1")
    out = asyncio.run(J.verify_assets(db_session, case, [a], ENTITIES))
    assert out == {"checked": 0, "vision_unavailable": "forbidden",
                   "degraded": "vision checks unavailable: forbidden — 1 picture(s) stay unverified"}
    assert a.verification_status == "unverified"


# ---- P14: the intake limit applies to the unchecked backlog, not the whole library --------------

def test_only_unchecked_pictures_use_up_the_asset_budget():
    rows = [VisualAsset(verification_status=v) for v in
            ("rejected", "rejected", "verified", "needs_review", "unverified")]
    assert VR.VisualResearchAgent(client=object()).asset_budget(rows) == ai_config.visual_search.max_assets - 1


def test_intake_status_reports_stored_checked_unchecked_and_backlog():
    rows = [VisualAsset(verification_status=v) for v in
            ("verified", "verified", "needs_review", "rejected", "unverified", "unverified")]
    out = intake_status(rows, max_assets=5)
    assert out == {"stored": 6, "checked": 4, "unchecked": 2, "max_unchecked": 5, "backlog_remaining": 3}


def test_intake_backlog_remaining_never_goes_negative():
    rows = [VisualAsset(verification_status="unverified") for _ in range(10)]
    assert intake_status(rows, max_assets=4)["backlog_remaining"] == 0


def test_case_9s_fixture_frees_up_room_once_most_pictures_are_checked():
    # case 9 before this fix: 704 stored, 316 unchecked, 388 checked (verified/needs_review/rejected)
    rows = ([VisualAsset(verification_status="unverified") for _ in range(316)]
            + [VisualAsset(verification_status="verified") for _ in range(124)]
            + [VisualAsset(verification_status="needs_review") for _ in range(70)]
            + [VisualAsset(verification_status="rejected") for _ in range(194)])
    out = intake_status(rows, max_assets=400)
    assert out["stored"] == 704 and out["unchecked"] == 316 and out["checked"] == 388
    # old rule (max_assets - non_rejected) was already full at 450/400; the backlog rule leaves room
    assert out["backlog_remaining"] == 84
