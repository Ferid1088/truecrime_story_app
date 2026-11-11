"""The cover planner: the checks of a proposal (a title of one to four words that gives nothing away, a symbolic scene description, a stated certainty), what stays open
for the owner, and the agent with a scripted model. Nothing is approved by the planner."""

import asyncio
import json

import pytest
from test_host import _plan_story, _version, story
from test_short_clips import ClipGen

from app.covers import agents as CAG
from app.covers import proposals as P
from app.db.models import CoverProposal
from app.providers.generation.base import GenerationResult
from app.shortform import clip_agents as CA



SCENE = "A front door standing slightly open at night, a narrow strip of warm light falling across the dark floor"


def item(key="long", title="The open door", scene=SCENE, confidence=0.9, reason="a front door, as the title says"):
    return {"key": key, "title": title, "scene": scene, "confidence": confidence, "reason": reason}


def types(issues):
    return {i["type"] for i in issues}


def test_a_good_proposal_has_no_issue_and_is_proposed():
    s = story()
    assert P.item_issues(item(), s, 3, "en") == [] and P.status_of(item(), []) == "proposed"


def test_a_long_title_a_late_detail_a_short_scene_and_no_certainty_are_refused():
    s = story()
    found = types(P.item_issues(item(title="one two three four five", scene="a door", confidence=None, reason=""), s, 3, "en"))
    assert {"title_length", "scene_length", "no_confidence", "reason"} <= found
    assert "tells_what_comes_later" in types(P.item_issues(item(title="Wexford lock"), s, 3, "en"))
    assert "greeting" in types(P.item_issues(item(title="Hello and welcome"), s, 3, "en"))


def test_a_scene_with_a_person_a_victim_or_a_weapon_is_refused():
    s = story()
    for scene in ("A man standing in the doorway of a dark house at night with a pistol in his hand",
                  "The dead victim lying on the kitchen floor next to an overturned chair at night"):
        assert "scene_not_symbolic" in types(P.item_issues(item(scene=scene), s, 3, "en"))


def test_an_uncertain_or_missing_scene_or_an_issue_leaves_the_cover_open():
    assert P.status_of(item(confidence=0.4), []) == "open" and P.status_of(item(scene=""), []) == "open"
    assert P.status_of(item(), [{"type": "greeting"}]) == "open"


def test_the_set_needs_every_cover_with_its_own_title_and_scene():
    keys = ["long", "C1", "C2"]
    assert P.set_issues([item(), item("C1", "Other title", SCENE + " and a hall"), item("C2", "Third", SCENE + " in snow")], keys) == []
    found = types(P.set_issues([item(), item("C1", "The open door", SCENE), item("C9", "x", "another")], keys))
    assert {"missing_cover", "unknown_cover", "same_title", "same_scene"} <= found


class CoverGen(ClipGen):
    def __init__(self, bad_first=False, unsure=()):
        super().__init__()
        self.bad_first, self.unsure = bad_first, set(unsure)

    async def generate_structured(self, role, system, user, images=None):
        if role != "cover_planner":
            return await super().generate_structured(role, system, user, images)
        data = json.loads(user)
        self.calls.append((role, data))
        covers = [item(c["key"], f"Title {n}", f"{SCENE} number {n}", 0.4 if c["key"] in self.unsure else 0.9) for n, c in enumerate(data["covers"])]
        if self.bad_first and "problems_to_fix" not in data:
            covers[0]["scene"] = "A man with a pistol stands in the doorway of the dark house at night"
        return {"covers": covers}, GenerationResult(text="{}", model="m/cover_planner", provider="fake")


@pytest.fixture()
def clips(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = CoverGen()
    monkeypatch.setattr("app.shortform.clip_agents.get_generation_provider", lambda: gen)
    monkeypatch.setattr("app.covers.agents.get_generation_provider", lambda: gen)
    english = asyncio.run(CA.ClipPlanner().plan_english(db_session, case, master))
    return case, master, english, gen


def mine(db, case):
    return db.query(CoverProposal).filter(CoverProposal.case_id == case.id)


def test_the_planner_proposes_the_film_cover_and_five_short_covers_and_approves_none(db_session, clips):
    case, _, _, gen = clips
    rows = asyncio.run(CAG.CoverPlanner().propose(db_session, case, "en"))
    assert [r.cover_key for r in rows] == ["long", "C1", "C2", "C3", "C4", "C5"] and [r.kind for r in rows] == ["long"] + ["short"] * 5
    assert {r.status for r in rows} == {"proposed"} and {r.source for r in rows} == {"agent"} and all(r.scene_prompt and r.reason and r.image_id is None for r in rows)
    payload = gen.calls[-1][1]
    assert [c["key"] for c in payload["covers"]] == ["long", "C1", "C2", "C3", "C4", "C5"] and "english_covers" not in payload
    assert all("Wexford" not in json.dumps(v) for k, v in payload.items() if k != "answer_acts"), "nothing of the answering acts goes to the planner"


def test_a_cover_with_an_uncertain_scene_stays_open_and_keeps_the_description(db_session, clips, monkeypatch):
    case, _, _, _ = clips
    gen = CoverGen(unsure={"C2"})
    monkeypatch.setattr("app.covers.agents.get_generation_provider", lambda: gen)
    rows = {r.cover_key: r for r in asyncio.run(CAG.CoverPlanner().propose(db_session, case, "en"))}
    assert rows["C2"].status == "open" and rows["C2"].scene_prompt and rows["C2"].confidence == 0.4
    assert rows["C1"].status == "proposed"


def test_a_scene_with_a_person_is_asked_again(db_session, clips, monkeypatch):
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
    assert payload["language"] == "de" and [c["key"] for c in payload["english_covers"]] == ["long", "C1", "C2", "C3", "C4", "C5"] and payload["english_covers"][0]["scene"]
