"""Photos and related videos are searched for every case and every beat that needs a picture."""

import asyncio
import json
import uuid

from app.db.models import Case, Fact, MediaLink
from app.documentary.visuals.case_search import case_queries, has_stored_candidates, search_case_visuals
from app.documentary.visuals.planner import research_queries
from app.services.research_jobs import _search_case_visuals


class FakeAgent:
    def __init__(self):
        self.calls = []

    async def run(self, db, case, queries, progress=None, found_during="research"):
        self.calls.append((queries, found_during))
        return {"candidates": 3, "added": 2, "video_links": 4}


def _case(db_session):
    case = Case(canonical_title="Lizzie Borden axe murders", slug=f"lizzie-{uuid.uuid4().hex[:8]}")
    db_session.add(case)
    db_session.commit()
    db_session.add_all([
        Fact(case_id=case.id, claim="c1", people_json=json.dumps(["Lizzie Borden", "Abby Borden"]),
             locations_json=json.dumps(["Fall River"])),
        Fact(case_id=case.id, claim="c2", people_json=json.dumps(["Lizzie Borden"]),
             locations_json=json.dumps(["Fall River", "Second Street"]))])
    db_session.commit()
    return case


def test_a_case_without_stored_pictures_triggers_the_search(db_session):
    case = _case(db_session)
    agent = FakeAgent()
    stats = asyncio.run(search_case_visuals(db_session, case, agent))
    assert stats["added"] == 2
    queries, found_during = agent.calls[0]
    assert found_during == "research"
    texts = [q["query"] for q in queries]
    assert texts[0] == "Lizzie Borden axe murders"
    assert "Lizzie Borden Lizzie Borden axe murders" in texts and "Fall River Lizzie Borden axe murders" in texts
    assert queries[0]["footage"] is True


def test_a_case_that_already_has_candidates_is_not_searched_again(db_session):
    case = _case(db_session)
    db_session.add(MediaLink(case_id=case.id, kind="video", url="https://www.youtube.com/watch?v=x"))
    db_session.commit()
    agent = FakeAgent()
    assert has_stored_candidates(db_session, case)
    assert asyncio.run(search_case_visuals(db_session, case, agent)) is None and agent.calls == []


def test_the_research_job_search_never_fails_the_research(db_session, monkeypatch):
    case = _case(db_session)

    async def broken(db, case, agent=None):
        raise RuntimeError("brave down")

    monkeypatch.setattr("app.documentary.visuals.case_search.search_case_visuals", broken)
    assert "picture search failed: brave down" in asyncio.run(_search_case_visuals(db_session, case))


def test_every_beat_that_needs_a_picture_gets_a_query_even_when_the_cap_cuts(db_session):
    """Entity A is needed in 12 beats and has 12 queries; the cap used to spend all
    of them on A and leave the beats of B, C and D without any search."""
    entities = [{"key": "a", "type": "person", "search_queries": [f"A query {i}" for i in range(12)]},
                {"key": "b", "type": "place", "search_queries": ["B query"]},
                {"key": "c", "type": "object", "search_queries": ["C query"]},
                {"key": "d", "type": "document", "search_queries": ["D query"]}]
    beats = [{"beat_id": f"a{i}", "requirements": [{"entity": "a", "priority": "high"}]} for i in range(12)]
    beats += [{"beat_id": "b1", "requirements": [{"entity": "b", "priority": "low"}]},
              {"beat_id": "c1", "requirements": [{"entity": "c", "priority": "low"}]},
              {"beat_id": "d1", "requirements": [{"entity": "d", "priority": "low"}]}]
    out = research_queries({"entities": entities, "beats": beats}, max_queries=4)
    assert {"B query", "C query", "D query"} <= {q["query"] for q in out}
    assert len(out) == 4
