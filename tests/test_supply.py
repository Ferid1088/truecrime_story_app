import asyncio
import uuid

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary.visuals import supply
from app.documentary.visuals.planner import supply_queries

REQ = {"entities": [{"key": "lb", "type": "person", "name": "Lizzie Borden", "aliases": ["Lizzie"],
                     "search_queries": ["Lizzie Borden"], "context_queries": []}],
       "beats": [{"beat_id": "b1", "requirements": [{"entity": "lb", "priority": "high"}]}]}


def test_target_scales_with_film_length():
    assert supply.target(3600) == 90
    assert supply.target(7200) == 180


def test_supply_rounds_never_repeat_a_query(monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "max_expansions_per_entity", 3)
    r1 = [q["query"] for q in supply_queries(REQ, None, 1)]
    r2 = [q["query"] for q in supply_queries(REQ, None, 2)]
    assert "Lizzie" in r1 and "Lizzie" not in r2
    assert r1 and r2 and not set(r1) & set(r2)


def _case(db_session):
    case = Case(canonical_title="Supply", slug=uuid.uuid4().hex, language="en")
    db_session.add(case)
    db_session.commit()
    return case


def _add_verified(db, case, n, start):
    for i in range(n):
        db.add(VisualAsset(case_id=case.id, asset_code=f"V{start + i}", asset_type="photo",
                           verification_status="verified", provider="wikimedia"))
    db.commit()


def test_top_up_stops_after_max_searches(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "max_expansions_per_entity", 3)
    case = _case(db_session)
    searches = []

    async def research(queries):
        searches.append(queries)
        _add_verified(db_session, case, 1, 100 * len(searches))
        return {"added": 1}

    async def verify():
        return {"checked": 1}

    report = asyncio.run(supply.top_up(db_session, case, REQ, None, 3600, research, verify))
    assert len(searches) == ai_config.supply.max_searches - 1
    assert report["searches"] == ai_config.supply.max_searches and not report["reached"]


def test_top_up_does_nothing_when_target_is_met(db_session):
    case = _case(db_session)
    _add_verified(db_session, case, 5, 0)

    async def never(*a):
        raise AssertionError("no search needed")

    report = asyncio.run(supply.top_up(db_session, case, REQ, None, 100, never, never))
    assert report["reached"] and report["searches"] == 1


def test_top_up_stops_when_a_search_adds_nothing(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "max_expansions_per_entity", 3)
    case = _case(db_session)
    calls = []

    async def research(queries):
        calls.append(1)
        return {"added": 0}

    async def verify():
        raise AssertionError("nothing new to check")

    report = asyncio.run(supply.top_up(db_session, case, REQ, None, 3600, research, verify))
    assert len(calls) == 1 and not report["reached"]
