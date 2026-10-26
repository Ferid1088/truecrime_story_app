"""Photos and related videos are searched for every case and every beat that needs a picture."""

import asyncio
import json
import uuid

from app.db.models import Case, Fact, MediaLink
from app.documentary.visuals.case_search import (
    has_stored_candidates,
    search_case_visuals,
)
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


def test_a_providers_date_range_fits_the_assets_date_columns():
    """A Wikimedia range ("1900-01-01/1900-12-31", 21 characters) overflowed date_start and
    stopped the whole case search after the 23rd stored picture."""
    from app.documentary.visuals.research import date_span

    assert date_span("1900-01-01/1900-12-31") == ("1900-01-01", "1900-12-31")
    assert date_span("1892-08-04") == ("1892-08-04", None)
    assert date_span(None) == (None, None) and date_span("") == (None, None)
    start, end = date_span("2026-10-17T10:11:12+00:00 and some more words/2027-01-01T00:00:00+00:00 trailing")
    assert len(start) <= 20 and len(end) <= 20
