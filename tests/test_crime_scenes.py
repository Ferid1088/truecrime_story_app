"""The crime scene(s) of a case are named once from the evidence and kept (app/documentary/mvp/crime_scenes.py)."""

import asyncio
import json
from uuid import uuid4

from app.db.models import Case, Fact, StoryVersion
from app.documentary.mvp import crime_scenes as CS
from app.providers.generation.base import GenerationResult


def _case(db, location="Troy, Miami County, Ohio, United States"):
    case = Case(canonical_title="Scene proof", slug=f"cs-{uuid4().hex[:8]}", language="en", location=location)
    db.add(case)
    db.commit()
    db.add(Fact(case_id=case.id, claim="She was found dead at the Cunningham Court home in Tipp City.", confidence=0.9,
                locations_json=json.dumps(["Cunningham Court home", "Tipp City"])))
    db.add(Fact(case_id=case.id, claim="The trial was held at the Miami County courthouse.", confidence=0.5,
                locations_json=json.dumps(["Miami County courthouse"])))
    db.commit()
    return case


def _agent(answer, calls):
    async def run_agent(name, gen, body, **kw):
        calls.append(name)
        return answer, GenerationResult(text="{}", model="m", provider="fake")
    return run_agent


def _patch(monkeypatch, answer, calls):
    monkeypatch.setattr(CS, "run_agent", _agent(answer, calls))
    monkeypatch.setattr(CS, "get_generation_provider", lambda: object())


def test_scenes_are_found_once_stored_and_never_asked_again(db_session, monkeypatch):
    case, calls = _case(db_session), []
    _patch(monkeypatch, {"scenes": [{"place": "Cunningham Court, Tipp City, Ohio, United States", "kind": "address",
                                     "evidence": "Cunningham Court home"}]}, calls)
    first = asyncio.run(CS.ensure(db_session, case))
    second = asyncio.run(CS.ensure(db_session, case))
    assert [s.place for s in first] == [s.place for s in second] == ["Cunningham Court, Tipp City, Ohio, United States"]
    assert first[0].kind == "address" and calls == ["mvp.crime_scenes"]


def test_a_place_the_evidence_never_names_is_dropped(db_session, monkeypatch):
    case = _case(db_session)
    _patch(monkeypatch, {"scenes": [{"place": "Elm Street 5, Dayton, Ohio", "kind": "address"},
                                    {"place": "Tipp City, Ohio, United States", "kind": "city"}]}, [])
    assert [s.place for s in asyncio.run(CS.ensure(db_session, case))] == ["Tipp City, Ohio, United States"]


def test_when_no_scene_survives_the_cases_own_location_is_the_scene(db_session, monkeypatch):
    case = _case(db_session)
    _patch(monkeypatch, {"scenes": [{"place": "Nowhere Road 1, Atlantis"}]}, [])
    rows = asyncio.run(CS.ensure(db_session, case))
    assert [(s.place, s.kind) for s in rows] == [("Troy, Miami County, Ohio, United States", "city")]


def test_a_case_with_no_scene_and_no_location_has_no_crime_scene_and_asks_again_next_time(db_session, monkeypatch):
    case, calls = _case(db_session, location=None), []
    _patch(monkeypatch, {"scenes": []}, calls)
    assert asyncio.run(CS.ensure(db_session, case)) == [] and asyncio.run(CS.ensure(db_session, case)) == []
    assert len(calls) == 2


def test_the_share_of_the_story_where_the_scene_is_first_named_is_kept(db_session, monkeypatch):
    case = _case(db_session)
    db_session.add(StoryVersion(case_id=case.id, version=1, narrative_angle="proof", language="en", story_text="She lived quietly. " * 10 + "Cunningham Court was her home."))
    db_session.commit()
    _patch(monkeypatch, {"scenes": [{"place": "Cunningham Court, Tipp City, Ohio, United States", "kind": "address"}]}, [])
    share = asyncio.run(CS.ensure(db_session, case))[0].mention_share
    assert share is not None and 0.8 < share < 1.0


def test_a_scene_the_story_does_not_name_has_no_share():
    assert CS.mention_share("Cunningham Court, Ohio", "A story about another place.") is None


def test_an_unknown_kind_becomes_city():
    view = {"case_location": "Troy, Ohio", "facts": [{"claim": "Troy", "locations": ["Troy"]}]}
    assert CS.clean({"scenes": [{"place": "Troy, Ohio", "kind": "weird"}]}, view)[0]["kind"] == "city"


def test_a_weak_fact_that_names_the_street_still_reaches_the_finder(db_session, monkeypatch):
    from app.core.ai_config import ai_config
    case = _case(db_session)
    for n in range(6):
        db_session.add(Fact(case_id=case.id, claim=f"A strong fact without any place {n}.", confidence=0.99, locations_json="[]"))
    db_session.add(Fact(case_id=case.id, claim="Officers went to the Elm Row house.", confidence=0.1, locations_json=json.dumps(["Elm Row"])))
    db_session.commit()
    monkeypatch.setattr(ai_config.zoom().crime_maps, "evidence_facts", 3)
    claims = [f["claim"] for f in CS.evidence_view(db_session, case)["facts"]]
    assert "Officers went to the Elm Row house." in claims and len(claims) < 9
