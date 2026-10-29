import asyncio
import json
from uuid import uuid4

from app.core.ai_config import ai_config
from app.db.models import (Case, EditorialBlueprint, MvpCandidate, MvpRequirement, MvpSearchRound,
                           StoryVersion, VisualAsset, VisualPlan)
from app.documentary.mvp import research as RS

ENTITY = {"key": "caleb_flynn", "type": "person", "name": "Caleb Flynn", "aliases": [],
          "search_queries": ["Caleb Flynn trial", "Caleb Flynn sentencing"], "footage": False}
REQS = {"entities": [ENTITY], "beats": []}


def _setup(db):
    case = Case(canonical_title="Research proof", slug=f"rs-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t",
                      narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1)
    db.add(plan)
    db.commit()
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                         function="evidence", entity_key="caleb_flynn", role_needed="evidence",
                         priority="high", spec_json=json.dumps({"acceptable_roles": ["evidence"]}))
    db.add(req)
    db.commit()
    return case, plan, req


class Fakes:
    def __init__(self, db, finds_per_round, approve_on_round=None):
        self.db, self.finds, self.approve_on, self.calls, self.queries = db, finds_per_round, approve_on_round, 0, []

    async def research(self, db, case, items):
        self.queries.append([i["query"] for i in items])
        self.caps = [i.get("max_downloads") for i in items]
        self.calls += 1
        for _ in range(self.finds[min(self.calls - 1, len(self.finds) - 1)]):
            db.add(VisualAsset(case_id=case.id, asset_code=f"V{uuid4().hex[:6]}", local_path="x.jpg",
                               entity_key="caleb_flynn", found_for="Caleb Flynn"))
        db.commit()
        return {"candidates": 1, "added": 1}

    async def verify(self, db, case, plan, reqs):
        if self.approve_on == self.calls:
            for c in db.query(MvpCandidate):
                c.final_status, c.role = "approved", "evidence"
            db.commit()
        return {}

    async def planner(self, db, case, req, entity, history, round_no):
        return [{"query": f"alt query {round_no}", "kind": "exact"},
                {"query": "caleb flynn trial", "kind": "exact"}]


def run(coro):
    return asyncio.run(coro)


def test_requirement_met_in_the_first_round_stops_searching(db_session):
    case, plan, req = _setup(db_session)
    f = Fakes(db_session, [2], approve_on_round=1)
    report = run(RS.research_unmet(db_session, case, plan, REQS, f.research, f.verify, f.planner))
    db_session.refresh(req)
    assert f.calls == 1 and req.status == "met" and report["shortages"] == 0
    assert db_session.query(MvpSearchRound).filter_by(requirement_id=req.id).count() == 2


def test_never_more_than_one_initial_and_four_further_rounds(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.mvp().search, "extra_rounds_max", 4)
    case, plan, req = _setup(db_session)
    f = Fakes(db_session, [1, 1, 1, 1, 1, 1, 1])
    report = run(RS.research_unmet(db_session, case, plan, REQS, f.research, f.verify, f.planner))
    db_session.refresh(req)
    assert f.calls == 5 and req.rounds_used == 5 and req.status == "shortage"
    shortage = json.loads(req.shortage_json)
    assert shortage["rounds_used"] == 5 and shortage["queries"][0] == "Caleb Flynn trial"
    assert report["shortages"] == 1


def test_repeated_queries_are_dropped_and_a_barren_round_stops_early(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.mvp().search, "extra_rounds_max", 4)
    case, plan, req = _setup(db_session)
    f = Fakes(db_session, [1, 0, 0])
    run(RS.research_unmet(db_session, case, plan, REQS, f.research, f.verify, f.planner))
    assert f.calls == 2                       # round 2 found nothing: round 3 is not run
    flat = [q.lower() for qs in f.queries for q in qs]
    assert flat.count("caleb flynn trial") == 1


def test_illustration_requirements_are_never_searched(db_session):
    case, plan, req = _setup(db_session)
    req.role_needed = "illustration"
    db_session.commit()
    f = Fakes(db_session, [1])
    run(RS.research_unmet(db_session, case, plan, REQS, f.research, f.verify, f.planner))
    assert f.calls == 0


def test_with_the_shipped_config_the_case_search_is_one_round_and_the_general_search_is_the_second(db_session):
    case, plan, req = _setup(db_session)
    f = Fakes(db_session, [1, 1, 1])
    run(RS.research_unmet(db_session, case, plan, REQS, f.research, f.verify, f.planner))
    assert f.calls == 1, "one case round; the owner reviews, then the general search runs"


def test_each_unmet_requirement_may_bring_in_only_a_few_new_pictures_and_videos(db_session):
    case, plan, req = _setup(db_session)
    f = Fakes(db_session, [1])
    run(RS.research_unmet(db_session, case, plan, REQS, f.research, f.verify, f.planner))
    assert f.caps and set(f.caps) == {ai_config.mvp().search.max_new_assets_per_requirement}, "one requirement, its cap"


def test_an_entitys_download_cap_is_the_smallest_cap_of_its_queries_and_never_above_the_agents_limit():
    from app.agents.visual_research import entity_cap

    queries = [{"entity": "e1", "max_downloads": 6}, {"entity": "e1", "max_downloads": 3},
               {"entities": ["e2"], "max_downloads": 99}, {"entity": "e3"}]
    assert entity_cap("e1", queries, 5) == 3
    assert entity_cap("e2", queries, 5) == 5, "the agent's own limit stays the ceiling"
    assert entity_cap("e3", queries, 5) == 5, "a query with no cap leaves the agent's limit"
    assert entity_cap("unknown", queries, 5) == 5


def test_the_shipped_caps_on_new_pictures_and_videos_are_five_for_a_requirement_a_gap_and_an_entity():
    assert ai_config.mvp().search.max_new_assets_per_requirement == 5
    assert ai_config.mvp().general.max_assets_per_moment == 5
    assert ai_config.visual_search.max_downloads_per_entity == 5


def test_the_cap_of_a_search_is_the_same_whatever_number_of_requirements_share_its_entity(db_session):
    case, plan, req = _setup(db_session)
    for n in (2, 3):
        db_session.add(MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key=f"B0{n}.1", beat_id=f"B0{n}",
                                      function="evidence", entity_key="caleb_flynn", role_needed="evidence",
                                      priority="high", spec_json=json.dumps({"acceptable_roles": ["evidence"]})))
    db_session.commit()
    f = Fakes(db_session, [1])
    run(RS.research_unmet(db_session, case, plan, REQS, f.research, f.verify, f.planner))
    assert set(f.caps) == {5}, "three requirements of one entity: still five per search item"
