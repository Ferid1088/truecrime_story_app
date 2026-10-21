import asyncio
import io
import random
import uuid

import httpx
from PIL import Image

from app.agents import visual_research as VR
from app.core.ai_config import ai_config
from app.db.models import Case, ProviderCall, VisualAsset, VisualSearchLog


class FakeBrave:
    def __init__(self):
        self.calls = []

    async def search_media(self, kind, query, language, limit=10):
        self.calls.append((kind, query))
        n = len(self.calls)
        return [{"type": "image_result", "title": f"photo {n}", "url": f"https://news.example/{n}",
                 "source": "news.example",
                 "properties": {"url": f"https://img.example/{n}.jpg", "width": 900, "height": 600}}]


def _jpeg(seed):
    rng = random.Random(seed)
    img = Image.new("RGB", (900, 600))
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(900 * 600)])
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _setup(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.footage, "enabled", False)
    monkeypatch.setattr(ai_config.visual_search, "providers", ["brave_images"])
    case = Case(canonical_title="Ledger case", slug=uuid.uuid4().hex, language="en")
    db_session.add(case)
    db_session.commit()
    brave = FakeBrave()
    monkeypatch.setattr(VR, "brave_backend", lambda: brave)
    return case, brave


def _run(db_session, case, queries):
    images = {}

    def handler(request):
        data = images.setdefault(str(request.url), _jpeg(len(images) + 1))
        return httpx.Response(200, content=data, headers={"content-type": "image/jpeg"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await VR.VisualResearchAgent(client).run(db_session, case, queries)

    return asyncio.run(go())


def _q(text):
    return {"query": text, "entity": "e", "entity_type": "building", "kind": "exact"}


def test_a_query_a_provider_already_ran_is_not_run_again(db_session, tmp_path, monkeypatch):
    case, brave = _setup(db_session, monkeypatch, tmp_path)
    first = _run(db_session, case, [_q("Borden house")])
    second = _run(db_session, case, [_q("Borden house"), _q("Borden house exterior")])
    assert [c[1] for c in brave.calls] == ["Borden house", "Borden house exterior"]
    assert first["repeated_queries"] == 0 and second["repeated_queries"] == 1
    logged = db_session.query(VisualSearchLog).filter_by(case_id=case.id).order_by(VisualSearchLog.id).all()
    assert [(r.provider, r.query, r.candidates) for r in logged] == [
        ("brave_images", "Borden house", 1), ("brave_images", "Borden house exterior", 1)]


def test_every_brave_search_is_booked_at_its_list_price(db_session, tmp_path, monkeypatch):
    case, _ = _setup(db_session, monkeypatch, tmp_path)
    _run(db_session, case, [_q("a"), _q("b")])
    rows = db_session.query(ProviderCall).filter_by(case_id=case.id, stage="visual_search").all()
    assert len(rows) == 2 and all(r.cost_eur and r.cost_eur > 0 for r in rows)
    assert all(r.cost_source == "assumed_web_price" and len(r.cost_source) <= 24 for r in rows)   # varchar(24)
    assert round(sum(r.cost_eur for r in rows), 6) == round(
        2 * float(__import__("app.services.cost_model", fromlist=["load_pricing"]).load_pricing()
                  ["search"]["brave_web"]["eur_per_request"]), 6)


def test_no_search_is_made_once_the_case_budget_is_spent(db_session, tmp_path, monkeypatch):
    case, brave = _setup(db_session, monkeypatch, tmp_path)
    case.total_cost_cap_eur = 0.001
    db_session.add(ProviderCall(case_id=case.id, host="x", method="CALL", path="/x", status=200, ok=True,
                                duration_ms=0, stage="spoken", cost_eur=0.01))
    db_session.commit()
    stats = _run(db_session, case, [_q("Borden house")])
    assert brave.calls == [] and stats["budget_stopped"] is True
    assert db_session.query(VisualAsset).filter_by(case_id=case.id).count() == 0
