"""The cover planner: the checks of a proposal (a title of one to four words that gives nothing away, a scene among the candidates, a stated certainty), what stays open
for the owner, and the agent with a scripted model. Nothing is approved by the planner."""

import asyncio
import json

import pytest
from test_host import _plan_story, _version, story
from test_short_clips import ClipGen

from app.covers import agents as CAG
from app.covers import proposals as P
from app.covers import scene as SC
from app.db.models import CoverProposal
from app.providers.generation.base import GenerationResult
from app.shortform import clip_agents as CA

CODES = {"VIS_1", "VIS_2", "VIS_3", "VIS_4", "VIS_5", "VIS_6"}


def item(key="long", title="The open door", scene="VIS_1", confidence=0.9, reason="a front door, as the title says"):
    return {"key": key, "title": title, "scene_code": scene, "confidence": confidence, "reason": reason}


def types(issues):
    return {i["type"] for i in issues}


def test_a_good_proposal_has_no_issue_and_is_proposed():
    s = story()
    assert P.item_issues(item(), CODES, s, 3, "en") == [] and P.status_of(item(), []) == "proposed"


def test_a_long_title_a_late_detail_a_scene_that_is_no_candidate_and_no_certainty_are_refused():
    s = story()
    found = types(P.item_issues(item(title="one two three four five", scene="VIS_99", confidence=None, reason=""), CODES, s, 3, "en"))
    assert {"title_length", "scene_not_a_candidate", "no_confidence"} <= found
    assert "tells_what_comes_later" in types(P.item_issues(item(title="Wexford lock"), CODES, s, 3, "en"))
    assert "greeting" in types(P.item_issues(item(title="Hello and welcome"), CODES, s, 3, "en"))


def test_a_scene_without_a_reason_is_refused():
    assert "reason" in types(P.item_issues(item(reason=""), CODES, story(), 3, "en"))


def test_an_uncertain_or_missing_scene_or_an_issue_leaves_the_cover_open():
    assert P.status_of(item(confidence=0.4), []) == "open" and P.status_of(item(scene=None), []) == "open"
    assert P.status_of(item(), [{"type": "greeting"}]) == "open"


def test_the_set_needs_every_cover_with_its_own_title_and_scene():
    keys = ["long", "C1", "C2"]
    assert P.set_issues([item(), item("C1", "Other title", "VIS_2"), item("C2", "Third", "VIS_3")], keys, 6) == []
    found = types(P.set_issues([item(), item("C1", "The open door", "VIS_1"), item("C9", "x", "VIS_3")], keys, 6))
    assert {"missing_cover", "unknown_cover", "same_title", "same_scene"} <= found
    assert "same_scene" not in types(P.set_issues([item(), item("C1", "Other", "VIS_1")], ["long", "C1"], 1))     # one picture only: it is reused


class CoverGen(ClipGen):
    def __init__(self, bad_first=False, unsure=()):
        super().__init__()
        self.bad_first, self.unsure = bad_first, set(unsure)

    async def generate_structured(self, role, system, user, images=None):
        if role != "cover_planner":
            return await super().generate_structured(role, system, user, images)
        data = json.loads(user)
        self.calls.append((role, data))
        codes = [c["code"] for c in data["candidates"]]
        covers = [item(c["key"], f"Title {n}", codes[n % len(codes)], 0.4 if c["key"] in self.unsure else 0.9) for n, c in enumerate(data["covers"])]
        if self.bad_first and "problems_to_fix" not in data:
            covers[0]["scene_code"] = "VIS_NOPE"
        return {"covers": covers}, GenerationResult(text="{}", model="m/cover_planner", provider="fake")


@pytest.fixture()
def clips(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = CoverGen()
    monkeypatch.setattr("app.shortform.clip_agents.get_generation_provider", lambda: gen)
    monkeypatch.setattr("app.covers.agents.get_generation_provider", lambda: gen)
    english = asyncio.run(CA.ClipPlanner().plan_english(db_session, case, master))
    options = [{"code": f"VIS_{i}", "kind": "place", "path": None, "what": f"picture {i}"} for i in range(1, 7)]
    monkeypatch.setattr(SC, "candidates", lambda db, case_, language: options)
    return case, master, english, gen


def mine(db, case):
    return db.query(CoverProposal).filter(CoverProposal.case_id == case.id)


def test_the_planner_proposes_the_film_cover_and_five_short_covers_and_approves_none(db_session, clips):
    case, _, _, gen = clips
    rows = asyncio.run(CAG.CoverPlanner().propose(db_session, case, "en"))
    assert [r.cover_key for r in rows] == ["long", "C1", "C2", "C3", "C4", "C5"] and [r.kind for r in rows] == ["long"] + ["short"] * 5
    assert {r.status for r in rows} == {"proposed"} and {r.source for r in rows} == {"agent"} and all(r.scene_code and r.reason for r in rows)
    payload = gen.calls[-1][1]
    assert [c["key"] for c in payload["covers"]] == ["long", "C1", "C2", "C3", "C4", "C5"] and "english_covers" not in payload
    assert all("Wexford" not in json.dumps(v) for k, v in payload.items() if k != "answer_acts"), "nothing of the answering acts goes to the planner"


def test_a_cover_with_an_uncertain_scene_stays_open_and_keeps_the_suggestion(db_session, clips, monkeypatch):
    case, _, _, _ = clips
    gen = CoverGen(unsure={"C2"})
    monkeypatch.setattr("app.covers.agents.get_generation_provider", lambda: gen)
    rows = {r.cover_key: r for r in asyncio.run(CAG.CoverPlanner().propose(db_session, case, "en"))}
    assert rows["C2"].status == "open" and rows["C2"].scene_code is None and json.loads(rows["C2"].validation_json)["suggested_scene"]
    assert rows["C1"].status == "proposed"


def test_a_scene_outside_the_candidates_is_asked_again(db_session, clips, monkeypatch):
    case, _, _, _ = clips
    gen = CoverGen(bad_first=True)
    monkeypatch.setattr("app.covers.agents.get_generation_provider", lambda: gen)
    rows = asyncio.run(CAG.CoverPlanner().propose(db_session, case, "en"))
    planner_calls = [c for c in gen.calls if c[0] == "cover_planner"]
    assert len(planner_calls) == 2 and "long" in planner_calls[1][1]["problems_to_fix"] and {r.status for r in rows} == {"proposed"}


def test_a_new_run_writes_newer_versions_and_the_current_rows_are_the_newest(db_session, clips):
    case, _, _, _ = clips
    planner = CAG.CoverPlanner()
    asyncio.run(planner.propose(db_session, case, "en"))
    again = asyncio.run(planner.propose(db_session, case, "en"))
    assert {r.version for r in again} == {2} and mine(db_session, case).count() == 12
    assert {k: r.id for k, r in P.current(db_session, case.id, "en").items()} == {r.cover_key: r.id for r in again}


def test_another_language_gets_the_english_covers_as_reference_and_needs_its_own_clips(db_session, clips, monkeypatch):
    case, master, english, gen = clips
    planner = CAG.CoverPlanner()
    asyncio.run(planner.propose(db_session, case, "en"))
    with pytest.raises(P.ProposalError, match="no short clips"):
        asyncio.run(planner.propose(db_session, case, "de"))
    de = _version(db_session, case, master, "en")
    de.language = "de"
    db_session.commit()
    asyncio.run(CA.ClipPlanner().write_native(db_session, case, de, english))
    asyncio.run(planner.propose(db_session, case, "de"))
    payload = gen.calls[-1][1]
    assert payload["language"] == "de" and [c["key"] for c in payload["english_covers"]] == ["long", "C1", "C2", "C3", "C4", "C5"]
