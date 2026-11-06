"""The angle of a host appearance: the state of the story at its place, and one concrete moment quoted from the narration the viewer has heard (so it is concrete,
cannot come from the future and is never made up), decided before the words are written; the writer builds the appearance on it and it is stored with the text."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_host import HostGen, _plan_story

from app.agents.host import HostDirector
from app.core.ai_config import ai_config
from app.documentary import host_angle as HA
from app.documentary.host_story import HostAct, HostStory


def story(texts: list[str], payoffs: tuple[int, ...] = (), words: int = 100) -> HostStory:
    acts = tuple(HostAct(f"B{i}", i, f"title {i}", "", t, (), (f"q{i}",), ("q1",) if i == 2 else (), words, hook=i == 1, payoff=i in payoffs)
                 for i, t in enumerate(texts, 1))
    return HostStory("The Case", "Who did it?", "fa", acts)


TEXTS = ["او ساعت سه صبح زنگ زد و گفت یه نفر اومده تو خونه.", "در گاراژ باز بود و سگ‌ها هیچ‌وقت پارس نکردن.", "پیام آخرش این بود: فردا دست‌به‌کار می‌شم.", "دادگاه او را به حبس ابد محکوم کرد."]


def angle(moment="m", quote="", feeling="f", thought="t", turn="u", move="move", question="Why?") -> HA.Angle:
    return HA.Angle(moment, quote, feeling, thought, turn, move, question)


def seg(role="reflection", position="mid", cutoff=2):
    return {"id": "S2", "role": role, "position": position, "cutoff": cutoff, "target_seconds": 20}


def test_the_state_says_where_the_viewer_stands_and_what_is_settled():
    s = story(TEXTS, payoffs=(3,))
    assert HA.story_state(s, 0)["phase"] == "opening"
    early, late, ended = HA.story_state(s, 1), HA.story_state(s, 3), HA.story_state(s, 4)
    assert (early["phase"], late["phase"], ended["phase"]) == ("early", "late", "ended")
    assert (early["payoff_passed"], late["payoff_passed"], ended["story_over"], late["story_over"]) == (False, True, True, False)
    assert HA.story_state(s, 2)["settled"] == ["q1"] and [b["act"] for b in ended["last_beats"]] == ["B2", "B3", "B4"]


def test_a_quote_must_be_in_the_narration_the_viewer_has_heard_whatever_its_vowel_marks_and_punctuation():
    s, a = story(TEXTS), angle("a detail", "در گاراژ باز بود و سگ‌ها هیچ‌وقت پارس نکردن")
    assert HA.issues(a, s, seg(cutoff=2), []) == []
    a.quote = "در گاراژ بسته بود و سگ‌ها پارس کردن"
    assert "not word for word" in HA.issues(a, s, seg(cutoff=2), [])[0]
    a.quote = "دادگاه او را به حبس ابد محکوم کرد"
    assert "not word for word" in HA.issues(a, s, seg(cutoff=2), [])[0], "a moment of a later act is not a moment the viewer has heard"
    assert HA.issues(a, s, seg(role="outro", position="final", cutoff=4), []) == []


def test_a_short_quote_an_earlier_moment_a_missing_question_and_missing_parts_are_refused():
    s = story(TEXTS)
    good = angle(quote="در گاراژ باز بود و سگ‌ها هیچ‌وقت پارس نکردن")
    assert any("fewer than" in p for p in HA.issues(angle(quote="در گاراژ باز"), s, seg(), []))
    assert any("already reacts to this moment" in p for p in HA.issues(good, s, seg(cutoff=3), [good]))
    assert any("needs a question" in p for p in HA.issues(angle(quote=good.quote, question=""), s, seg(role="viewer_question"), []))
    assert HA.issues(angle(quote=good.quote, question=""), s, seg(role="reflection"), []) == []
    assert {"moment is empty", "feeling is empty", "thought is empty", "move is empty", "quote is empty", "turn is empty"} <= set(HA.issues(angle("", "", "", "", "", "", ""), s, {**seg(), "target_seconds": 40}, []))
    assert "turn is empty" not in HA.issues(angle(quote=good.quote, turn=""), s, {**seg(), "target_seconds": 12}, []), "a short appearance needs no turn"


def test_the_opening_has_heard_nothing_so_it_needs_no_quote():
    opening = {"id": "S1", "role": "opening", "position": "opening", "cutoff": 0, "target_seconds": 30}
    assert HA.issues(angle("the van at the quarry", "", "it nags at me", "I think the lunch matters", "but maybe I read too much into it", "highlight a detail", "Who leaves lunch?"), story(TEXTS), opening, []) == []


def test_the_writer_builds_on_the_angle_which_is_kept_with_the_text_and_a_made_up_quote_goes_back_to_the_editor(db_session, isolated_host_history, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = HostGen()
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, None, master))
    original, asked = gen.generate_structured, {"n": 0}

    async def inventing(role, system, user, images=None):
        raw, res = await original(role, system, user)
        if role == "host_angle" and json.loads(user)["segment"]["position"] != "opening" and asked["n"] == 0:
            asked["n"] += 1
            raw["quote"] = "a sentence the narration never said at all"
        return raw, res

    monkeypatch.setattr(gen, "generate_structured", inventing)
    row = asyncio.run(HostDirector().write(db_session, case, master, plan_row))
    segments = json.loads(row.segments_json)
    angles = [c for r, c in gen.calls if r == "host_angle"]
    assert any("problems_to_fix" in a and "not word for word" in a["problems_to_fix"][0] for a in angles), "the made-up quote went back with its problem"
    writers = [c for r, c in gen.calls if r.startswith("host_writer") and c["segment"]["id"] == segments[1]["segment_id"]]
    assert writers[0]["angle"]["quote"] == segments[1]["angle"]["quote"] and writers[0]["state"]["phase"] in HA.PHASES
    assert all(s["angle"]["moment"] for s in segments) and len({s["angle"]["quote"] for s in segments if s["angle"]["quote"]}) == len([s for s in segments if s["angle"]["quote"]])


def test_an_angle_that_stays_wrong_stops_the_writing(db_session, isolated_host_history, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = HostGen()
    monkeypatch.setattr("app.agents.host.get_generation_provider", lambda: gen)
    plan_row = asyncio.run(HostDirector().create_plan(db_session, case, None, master))
    original = gen.generate_structured

    async def never_good(role, system, user, images=None):
        raw, res = await original(role, system, user)
        if role == "host_angle":
            raw["feeling"] = ""
        return raw, res

    monkeypatch.setattr(gen, "generate_structured", never_good)
    with pytest.raises(RuntimeError, match=f"stays wrong after {ai_config.host.angle_repairs} repairs"):
        asyncio.run(HostDirector().write(db_session, case, master, plan_row))


def test_the_host_never_repeats_the_narration_but_may_use_its_names_and_facts_in_its_own_words():
    s = story(TEXTS)
    assert HA.retelling_issues("پیام آخرش این بود: فردا دست‌به‌کار می‌شم و بعد سکوت", s, 3)[0]["type"] == "repeats_the_narration"
    assert HA.retelling_issues("اون پیام آخر، همون «فردا»، تو سرم مونده. فردا چی؟", s, 3) == []
    assert HA.retelling_issues("دادگاه او را به حبس ابد محکوم کرد", s, 3) == [], "a sentence of a later act is not narration the viewer has heard"
