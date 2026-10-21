import asyncio
import json
import math
import uuid

from app.core.ai_config import ai_config
from app.db.models import Case, ProviderCall, VisualAsset, VisualSearchLog
from app.documentary.visuals import supply
from app.documentary.visuals.planner import supply_queries

REQ = {"entities": [{"key": "lb", "type": "person", "name": "Lizzie Borden", "aliases": ["Lizzie"],
                     "search_queries": ["Lizzie Borden"], "context_queries": []}],
       "beats": [{"beat_id": "b1", "requirements": [{"entity": "lb", "priority": "high",
                                                    "acceptable_roles": ["evidence", "context"]}]}]}
BLUEPRINT = {"beats": [{"id": "b1", "words": 60}]}


def _case(db_session):
    case = Case(canonical_title="Supply", slug=uuid.uuid4().hex, language="en")
    db_session.add(case)
    db_session.commit()
    return case


def _add_verified(db, case, n, start, **kw):
    for i in range(n):
        db.add(VisualAsset(case_id=case.id, asset_code=f"V{start + i}", asset_type="photo",
                           verification_status="verified", provider="wikimedia", local_path="x.jpg", rights_status="public_domain",
                           entities_json='["lb"]', asset_role="evidence", **kw))
    db.commit()


def _seed_candidate(db, case):
    """An unchecked picture of the beat's entity: the beat has a candidate, nothing counts as usable."""
    db.add(VisualAsset(case_id=case.id, asset_code="U0", asset_type="photo", provider="wikimedia",
                       verification_status="unverified", local_path="x.jpg", rights_status="public_domain",
                       entities_json='["lb"]', asset_role="evidence"))
    db.commit()


def _top_up(db, case, film_seconds, research, verify, beats=None):
    return asyncio.run(supply.top_up(db, case, REQ, BLUEPRINT, beats, film_seconds, research, verify))


async def _never(*a):
    raise AssertionError("no search needed")


def test_the_target_comes_from_the_editing_rhythm_not_from_a_flat_forty_seconds(monkeypatch):
    cfg = ai_config.supply
    monkeypatch.setattr(cfg, "still_shot_seconds", 10.0)
    monkeypatch.setattr(cfg, "uses_per_picture", 2)
    monkeypatch.setattr(cfg, "video_share", 0.1)
    monkeypatch.setattr(cfg, "video_cut_seconds", 8.0)
    t = supply.target(3600)
    assert t.stills == math.ceil(3600 * 0.9 / 10 / 2) == 162
    assert t.cuts == math.ceil(3600 * 0.1 / 8) == 45
    assert t.total == 207


def test_a_sixty_minute_film_needs_about_two_hundred_distinct_pictures_and_cuts():
    assert 180 <= supply.target(3600).total <= 225


def test_a_ten_minute_film_needs_far_more_than_the_old_forty_second_rule_gave():
    assert supply.target(600).total > math.ceil(600 / 40) * 2


def test_more_uses_per_picture_lower_the_target(monkeypatch):
    base = supply.target(3600).stills
    monkeypatch.setattr(ai_config.supply, "uses_per_picture", 3)
    assert supply.target(3600).stills < base


def test_search_rounds_never_repeat_a_query(monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "max_expansions_per_entity", 3)
    r1 = [q["query"] for q in supply_queries(REQ, None, 1)]
    r2 = [q["query"] for q in supply_queries(REQ, None, 2)]
    assert "Lizzie" in r1 and "Lizzie" not in r2
    assert r1 and r2 and not set(r1) & set(r2)


def test_top_up_does_nothing_when_the_target_is_met(db_session):
    case = _case(db_session)
    _add_verified(db_session, case, supply.target(100).total, 0)
    report = _top_up(db_session, case, 100, _never, _never)
    assert report["reached"] and report["searches"] == 1 and report["stopped_because"] == "reached"


def test_the_search_continues_round_after_round_until_the_target_is_reached(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "max_expansions_per_entity", 1)
    monkeypatch.setattr(ai_config.supply, "max_searches", 9)
    case = _case(db_session)
    need = supply.target(100).total
    seen: list[list[str]] = []

    async def research(queries):
        seen.append([q["query"] for q in queries])
        _add_verified(db_session, case, 1, 100 * len(seen))
        return {"added": 1}

    async def verify():
        return {"checked": 1}

    report = _top_up(db_session, case, 100, research, verify)
    assert report["reached"] and report["stopped_because"] == "reached"
    assert len(seen) == need - 0 or len(seen) <= ai_config.supply.max_searches - 1
    flat = [q for r in seen for q in r]
    assert len(flat) == len(set(flat))                      # never the same query twice


def test_the_search_stops_at_max_searches_and_says_so(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "max_expansions_per_entity", 1)
    monkeypatch.setattr(ai_config.supply, "max_searches", 3)
    case = _case(db_session)
    _seed_candidate(db_session, case)
    n = []

    async def research(queries):
        n.append(1)
        _add_verified(db_session, case, 1, 10 * len(n))
        return {"added": 1}

    async def verify():
        return {"checked": 1}

    report = _top_up(db_session, case, 3600, research, verify)
    assert len(n) == 2 and report["searches"] == 3 and not report["reached"]
    assert report["stopped_because"] == "max_searches"


def test_the_search_stops_when_the_sources_are_exhausted_and_names_what_ran_out(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.supply, "max_searches", 12)
    monkeypatch.setattr(ai_config.visual_search, "max_expansions_per_entity", 3)
    case = _case(db_session)

    async def research(queries):
        return {"added": 1}

    async def verify():
        return {"checked": 1}

    report = _top_up(db_session, case, 3600, research, verify)
    assert report["stopped_because"] == "no_new_queries" and not report["reached"]
    assert "query slices" in " ".join(report["ran_out"])


def test_two_empty_rounds_in_a_row_end_the_search(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.supply, "max_searches", 12)
    monkeypatch.setattr(ai_config.supply, "max_empty_rounds", 2)
    monkeypatch.setattr(ai_config.visual_search, "max_expansions_per_entity", 1)
    case = _case(db_session)
    _seed_candidate(db_session, case)
    calls = []

    async def research(queries):
        calls.append(1)
        return {"added": 0}

    async def verify():
        raise AssertionError("nothing new to check")

    report = _top_up(db_session, case, 3600, research, verify)
    assert len(calls) == 2 and report["stopped_because"] == "nothing_added"


def test_the_search_stops_before_a_round_when_its_research_cost_guard_is_spent(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.supply, "research_cost_cap_eur", 0.05)
    monkeypatch.setattr(ai_config.visual_search, "max_expansions_per_entity", 1)
    case = _case(db_session)
    calls = []

    async def research(queries):
        calls.append(1)
        db_session.add(ProviderCall(case_id=case.id, host="x", method="CALL", path="/x", status=200,
                                    ok=True, duration_ms=0, stage="visual_search", cost_eur=0.06))
        db_session.commit()
        return {"added": 1}

    async def verify():
        return {"checked": 1}

    report = _top_up(db_session, case, 3600, research, verify)
    assert len(calls) == 1 and report["stopped_because"] == "cost_guard"
    assert report["research_cost_eur"] >= 0.06


def test_the_search_stops_when_the_case_budget_is_spent(db_session):
    case = _case(db_session)
    case.total_cost_cap_eur = 0.01
    db_session.add(ProviderCall(case_id=case.id, host="x", method="CALL", path="/x", status=200, ok=True,
                                duration_ms=0, stage="spoken", cost_eur=0.02))
    db_session.commit()
    report = _top_up(db_session, case, 3600, _never, _never)
    assert report["stopped_because"] == "case_budget" and report["searches"] == 1


def test_a_search_that_hit_the_asset_limit_says_so(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "max_assets", 2)
    case = _case(db_session)
    _add_verified(db_session, case, 3, 0)
    report = _top_up(db_session, case, 3600, _never, _never)
    assert report["stopped_because"] == "asset_limit"


def test_the_supply_report_lists_target_found_shortage_and_each_beats_share(db_session):
    case = _case(db_session)
    _add_verified(db_session, case, 2, 0)

    async def nothing(queries):
        return {"added": 0}

    report = _top_up(db_session, case, 3600, nothing, _never)
    assert report["target"]["total"] == supply.target(3600).total
    assert report["usable_after"] == 2 and report["shortage"] == report["target"]["total"] - 2
    [beat] = report["beats"]
    assert beat["beat"] == "b1" and beat["found"] == 2 and beat["needed"] >= 1
    assert beat["shortage"] == max(beat["needed"] - 2, 0)
    json.dumps(report)


def test_searches_already_logged_are_not_counted_as_new_queries(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "max_expansions_per_entity", 3)
    monkeypatch.setattr(ai_config.supply, "max_searches", 6)
    case = _case(db_session)
    _seed_candidate(db_session, case)
    for provider in ("wikimedia", "brave_images"):
        for q in supply_queries(REQ, None, 1) + supply_queries(REQ, None, 2) + supply_queries(REQ, None, 3):
            db_session.add(VisualSearchLog(case_id=case.id, provider=provider,
                                           query_key=q["query"].lower(), query=q["query"]))
    db_session.commit()
    report = _top_up(db_session, case, 3600, _never, _never)
    assert report["stopped_because"] == "no_new_queries"


def _beat_requirements(entity_key, entity_name, period=None):
    entity = {"key": entity_key, "type": "object", "name": entity_name, "aliases": [],
              "search_queries": [entity_name], "context_queries": []}
    if period:
        entity["period"] = period
    return {"entities": [REQ["entities"][0], entity],
            "beats": [{"beat_id": "B04", "requirements": [
                {"entity": entity_key, "priority": "high", "purpose": "evidence",
                 "acceptable_roles": ["evidence", "context"]}]}]}


def test_a_beat_without_candidates_is_searched_for_before_anything_else(db_session):
    """Case 9, beats B04/B05: the Ka-Bar sheath had no candidate at all."""
    case = _case(db_session)
    case.location = "Moscow, Idaho"
    db_session.commit()
    requirements = _beat_requirements("kabar_sheath", "Ka-Bar knife sheath", "November 2022")
    blueprint = {"beats": [{"id": "B04", "words": 27}]}
    seen = []

    async def research(queries):
        seen.append(queries)
        return {"added": 0}

    asyncio.run(supply.top_up(db_session, case, requirements, blueprint, None, 600, research, _never))
    first = [q["query"] for q in seen[0] if q["kind"] == "exact"]
    assert first and all("Ka-Bar knife sheath" in q for q in first)
    assert any("Moscow" in q for q in first) and any("November 2022" in q for q in first)
    assert {q["entity"] for q in seen[0]} == {"kabar_sheath"}


def test_a_beat_that_has_candidates_triggers_no_targeted_search(db_session):
    case = _case(db_session)
    _add_verified(db_session, case, supply.target(100).total, 0)
    report = _top_up(db_session, case, 100, _never, _never)
    assert report["rounds"] == []


def test_the_director_is_offered_the_configured_number_of_candidates_best_first(db_session, monkeypatch):
    from app.documentary.visuals import director as D

    case = _case(db_session)
    for n in range(20):
        _add_verified(db_session, case, 1, n, quality_score=n / 20)
    monkeypatch.setattr(ai_config.visual_direction, "candidates_per_requirement", 12)
    offered = D.beat_candidates(db_session, case, BLUEPRINT, REQ)["b1"]
    assert len(offered) == 12
    scores = [sc for sc, _ in offered]
    assert scores == sorted(scores, reverse=True)
    assert max(a.quality_score for _, a in offered) == 0.95
    monkeypatch.setattr(ai_config.visual_direction, "candidates_per_requirement", 5)
    assert len(D.beat_candidates(db_session, case, BLUEPRINT, REQ)["b1"]) == 5


def test_the_default_number_of_candidates_per_requirement_is_twelve():
    assert ai_config.visual_direction.candidates_per_requirement == 12
