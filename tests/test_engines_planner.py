"""The planner's use of the cinematic engines: one agent answer per beat (kept until the beat changes), every statement
checked against the stored fact, offers that carry their provenance, and the owner's inspector."""
import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.models import Case, EditorialBlueprint, StoryVersion, VisualPlan
from app.documentary.mvp import alternatives as AL
from app.documentary.mvp import engines as EN
from app.main import app

FACTS = {
    "F001": {"claim": "Prosecutors alleged Flynn shot his wife in bed.", "disputed": True, "category": "legal",
             "source_ids": [4]},
    "F005": {"claim": "Defense attorneys argued no direct evidence ties him to the murder.", "disputed": True,
             "category": "legal", "source_ids": [4]},
    "F080": {"claim": "The jury found Flynn guilty of nine charges after two hours.", "disputed": False,
             "category": "legal", "source_ids": [5]},
}
COURTROOM = {"issue": {"text": "Who killed her?", "basis": "fact", "fact_id": "F080"},
             "prosecution": [{"text": "Flynn shot his wife in bed", "basis": "allegation", "fact_id": "F001"}],
             "defense": [{"text": "No direct evidence ties him to the murder", "basis": "allegation", "fact_id": "F005"}],
             "finding": {"text": "The jury found Flynn guilty of nine charges", "basis": "finding", "fact_id": "F080"}}


def evidence() -> AL.Evidence:
    beats = [{"id": "B01", "summary": "s", "reveals": ["F001"], "viewer_knows": ["F001"]},
             {"id": "B02", "summary": "verdict", "reveals": ["F005", "F080"], "viewer_knows": ["F001", "F005", "F080"]}]
    return AL.Evidence(order={"B01": 0, "B02": 1}, beats=beats, questions=[], facts=FACTS, contradictions=[], sources={},
                       events=[], quotes=[], narration={"B01": "He shot her.", "B02": "The jury decided."},
                       starts={}, total_seconds=600.0)


@pytest.fixture
def case(db_session):
    c = Case(canonical_title="Engines proof", slug=f"eng-{uuid4().hex[:8]}", language="en")
    db_session.add(c)
    db_session.commit()
    return c


@pytest.fixture
def needs(db_session, case):
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db_session.add(sv)
    db_session.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db_session.add(bp)
    db_session.commit()
    row = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, status="needs", plan_json="{}", requirements_json="{}",
                     validation_json=json.dumps({"mvp": {"reveal_graph_id": 1}}))
    db_session.add(row)
    db_session.commit()
    return row


class Agent:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def __call__(self, payload):
        self.calls.append(payload)
        return {"beats": [{"beat": b["beat"], **self.answers.get(b["beat"], {"engine": "none", "why": "a picture"})}
                          for b in payload["beats"]]}


def planner_for(db_session, case, needs, agent):
    return EN.EnginePlanner(db_session, case, needs, planner=agent)


def test_a_chosen_engine_becomes_an_offer_with_its_sources_and_is_remembered(db_session, case, needs):
    agent = Agent({"B02": {"engine": "courtroom", "why": "both sides then the verdict", "eligible": ["courtroom"],
                           "data": COURTROOM}})
    offers = asyncio.run(EN.build_engine_offers(db_session, case, needs, evidence(), planner_for(db_session, case, needs, agent)))
    assert [(o.kind, o.key, o.best) for o in offers] == [("courtroom", "courtroom|B02", 1)]
    meta = offers[0].meta
    assert meta["why"].startswith("both sides") and meta["steps"] == ["prosecution", "defense", "finding"]
    assert {p["fact_id"] for p in meta["provenance"]} == {"F001", "F005", "F080"}
    assert 15.0 <= offers[0].seconds <= 24.0
    again = asyncio.run(EN.build_engine_offers(db_session, case, needs, evidence(), planner_for(db_session, case, needs, agent)))
    assert len(agent.calls) == 1 and [o.key for o in again] == ["courtroom|B02"]


def test_a_beat_told_only_what_the_story_has_told_by_then(db_session, case, needs):
    agent = Agent({})
    asyncio.run(EN.build_engine_offers(db_session, case, needs, evidence(), planner_for(db_session, case, needs, agent)))
    first = agent.calls[0]["beats"][0]
    assert first["beat"] == "B01" and [f["id"] for f in first["facts"]] == ["F001"]


def test_an_engine_citing_a_fact_not_yet_told_is_dropped(db_session, case, needs):
    agent = Agent({"B01": {"engine": "courtroom", "why": "x", "data": COURTROOM}})
    offers = asyncio.run(EN.build_engine_offers(db_session, case, needs, evidence(), planner_for(db_session, case, needs, agent)))
    assert offers == []
    stored = json.loads(needs.validation_json)["mvp"]["engine_decisions"]["B01"]
    assert stored["engine"] is None and "not told" in stored["problems"][0]


def test_an_ungrounded_statement_drops_the_engine_and_says_why(db_session, case, needs):
    bad = json.loads(json.dumps(COURTROOM))
    bad["prosecution"][0]["text"] = "Flynn shot his wife 3 times in the garage"
    agent = Agent({"B02": {"engine": "courtroom", "why": "x", "data": bad}})
    assert asyncio.run(EN.build_engine_offers(db_session, case, needs, evidence(),
                                              planner_for(db_session, case, needs, agent))) == []
    assert "figure" in json.loads(needs.validation_json)["mvp"]["engine_decisions"]["B02"]["problems"][0]


def test_the_owner_can_switch_an_engine_off_and_it_is_not_offered(db_session, case, needs):
    agent = Agent({"B02": {"engine": "courtroom", "why": "x", "data": COURTROOM}})
    asyncio.run(EN.build_engine_offers(db_session, case, needs, evidence(), planner_for(db_session, case, needs, agent)))
    validation = json.loads(needs.validation_json)
    validation["mvp"]["engine_overrides"] = {"disabled": ["courtroom|B02"]}
    needs.validation_json = json.dumps(validation)
    db_session.commit()
    assert asyncio.run(EN.build_engine_offers(db_session, case, needs, evidence(),
                                              planner_for(db_session, case, needs, agent))) == []


def test_a_forced_engine_is_asked_for_and_a_different_answer_is_refused(db_session, case, needs):
    agent = Agent({"B02": {"engine": "courtroom", "why": "x", "data": COURTROOM}})
    decisions = asyncio.run(planner_for(db_session, case, needs, agent).decide(evidence(), {"B02": "sentencing"}))
    assert agent.calls[0]["forced"] == {"B02": "sentencing"}
    assert decisions["B02"].engine is None and "owner asked for sentencing" in decisions["B02"].problems[-1]


def test_the_inspector_lists_choices_previews_a_step_and_applies_the_owners_changes(db_session, case, needs):
    agent = Agent({"B02": {"engine": "courtroom", "why": "both sides", "data": COURTROOM}})
    asyncio.run(EN.build_engine_offers(db_session, case, needs, evidence(), planner_for(db_session, case, needs, agent)))
    client = TestClient(app)
    listing = client.get(f"/api/cases/{case.id}/mvp/engines").json()
    row = next(b for b in listing["beats"] if b["beat"] == "B02")
    assert listing["chosen"] == 1 and row["steps"] == ["prosecution", "defense", "finding"] and row["provenance"]
    assert {e["kind"] for e in listing["engines"]} == set(EN.SPECS)
    png = client.get(f"/api/cases/{case.id}/mvp/engines/preview", params={"beat": "B02", "step": 3})
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    off = client.post(f"/api/cases/{case.id}/mvp/engines/override", json={"beat": "B02", "action": "disable"}).json()
    assert next(b for b in off["beats"] if b["beat"] == "B02")["disabled"]
    short = client.post(f"/api/cases/{case.id}/mvp/engines/override", json={"beat": "B02", "action": "timing", "seconds": 5})
    assert short.status_code == 422
    ok = client.post(f"/api/cases/{case.id}/mvp/engines/override", json={"beat": "B02", "action": "timing", "seconds": 30})
    assert next(b for b in ok.json()["beats"] if b["beat"] == "B02")["seconds"] == 30
