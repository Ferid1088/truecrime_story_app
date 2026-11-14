"""The film's title and its cover as one pair, with no extra request: the three kinds the title writer already gives, the options built from them, the choice made by the app
(never over an owner's title), the cover planner pairing its scene with one option inside its own call, and the Titles view."""

import asyncio
import json
import uuid

import pytest
from test_case_naming import FA, ScriptedGen
from test_cover_proposals import SCENE, CoverGen, item
from test_host import _plan_story
from test_short_clips import ClipGen  # noqa: F401  (the fixture's base)

from app.core.ai_config import ai_config
from app.covers import agents as CAG
from app.covers import proposals as P
from app.db.models import CaseTitleCandidate, CoverProposal, EpisodeIdentity
from app.identity import titles as T
from app.naming import agent as A
from app.naming import selection as SEL
from app.providers.generation.base import GenerationResult
from app.shortform import clip_agents as CA


def _case(db):
    """A case with people, a place and a status, each with its own slug."""
    from test_case_naming import _case as named

    return named(db, name=f"Celle lantern {uuid.uuid4().hex[:6]}")


@pytest.fixture(autouse=True)
def clean(db_session):
    db_session.query(CaseTitleCandidate).delete()
    db_session.query(EpisodeIdentity).delete()
    db_session.commit()


def cand(db, case, title, rank, angle="", language="en", status="candidate", concept="a lamp that was left burning"):
    row = CaseTitleCandidate(case_id=case.id, language=language, title=title, title_norm=title.lower(), status=status, editorial_concept=concept, origin="generated",
                             critic_json=json.dumps({"rank_score": rank, "angle": angle, "spoiler_risk": 0.1}))
    db.add(row)
    db.commit()
    return row


def three(db, case, language="en"):
    return (cand(db, case, "Rita Haas Called From the Lamp Shop at Night", 0.80, "the call", language), cand(db, case, "The Borrowed Boat Was Found Upstream", 0.78, "the boat", language),
            cand(db, case, "Two Neighbours Saw the Light Go Out Twice", 0.76, "the light", language))


def test_the_options_are_the_best_titles_that_really_differ_with_the_story_hook(db_session):
    case = _case(db_session)
    a, b, c = three(db_session, case)
    twin = cand(db_session, case, "The Borrowed Boat Was Found Upstream Again", 0.9, "the boat again")
    found = SEL.options(db_session, case, "en")
    assert [(o["id"], o["angle"], o["title"]) for o in found["options"]] == [("A", "the boat again", twin.title), ("B", "the call", a.title), ("C", "the light", c.title)]
    assert found["hook"] == "a lamp that was left burning" and {o["candidate_id"] for o in found["options"]} == {twin.id, a.id, c.id}


def test_a_less_like_title_beats_a_slightly_better_ranked_one_that_resembles_the_first(db_session):
    case = _case(db_session)
    first = cand(db_session, case, "Ashley Flynn Was Shot While She Slept in Troy", 0.9, "the shooting")
    close = cand(db_session, case, "Her Husband Said an Intruder Shot Ashley Flynn While She Slept", 0.8, "the husband's account")
    far = cand(db_session, case, "A Teacher and Volleyball Coach and Mother of Two Girls", 0.7, "the victim's life")
    assert [o["title"] for o in SEL.options(db_session, case, "en")["options"]][:2] == [first.title, far.title] and close.title in [o["title"] for o in SEL.options(db_session, case, "en")["options"]]


def test_two_titles_with_the_same_angle_or_mostly_the_same_words_are_not_two_options(db_session):
    case = _case(db_session)
    first = cand(db_session, case, "A Call at Night From the Lamp Shop", 0.9, "the call")
    cand(db_session, case, "A Phone Call Came From Another Place", 0.85, "The Call")
    cand(db_session, case, "A Call at Night From the Lamp Shop Again", 0.84, "the shop")
    third = cand(db_session, case, "The Ferryman Never Came Back Across", 0.7, "the ferryman")
    assert [o["title"] for o in SEL.options(db_session, case, "en")["options"][:2]] == [first.title, third.title]
    assert SEL.overlap("A Call at Night", "A Call at Night Again") == 1.0 and SEL.overlap("One Two", "Three Four") == 0.0


def test_fewer_different_titles_than_options_are_filled_with_the_next_best_and_a_rejected_one_is_never_an_option(db_session):
    case = _case(db_session)
    cand(db_session, case, "A Call at Night From the Lamp Shop", 0.8, "the call")
    cand(db_session, case, "A Call at Night From the Lamp Shop Again", 0.7, "the call")
    cand(db_session, case, "A Call at Night From the Old Lamp Shop", 0.6, "the call")
    cand(db_session, case, "Window Seat Eleven Was Never Used", 0.99, "the seat", status="rejected")
    assert [o["rank_score"] for o in SEL.options(db_session, case, "en")["options"]] == [0.8, 0.7, 0.6]


def test_the_app_chooses_the_best_title_and_marks_it_as_its_own_choice(db_session):
    case = _case(db_session)
    a, *_ = three(db_session, case)
    ident = A.auto_select(db_session, case, "en")
    assert ident.editorial_title == a.title and ident.title_source == "auto" and ident.title_approved and ident.youtube_title
    assert SEL.options(db_session, case, "en")["options"][0]["selected"] is True


def test_the_title_the_cover_pairs_wins_inside_the_margin_and_not_outside_it(db_session):
    case = _case(db_session)
    a, b, c = three(db_session, case)
    assert A.auto_select(db_session, case, "en", prefer=b.id).editorial_title == b.title
    far = cand(db_session, case, "Nine Steps Upstream Led Nowhere Near the Road", 0.80 - ai_config.case_naming.pair_margin - 0.05, "the steps", language="de")
    cand(db_session, case, "Der stille Fährmann kam nie mehr zurück über den Fluss", 0.80, "der Fährmann", language="de")
    assert A.auto_select(db_session, case, "de", prefer=far.id).editorial_title == "Der stille Fährmann kam nie mehr zurück über den Fluss"


def test_an_owners_title_a_published_one_and_one_approved_before_the_source_existed_are_never_replaced(db_session):
    case = _case(db_session)
    a, b, _ = three(db_session, case)
    for source, published, approved in (("owner", False, True), ("", False, True), ("auto", True, True)):
        db_session.query(EpisodeIdentity).delete()
        db_session.commit()
        ident = T.sync_identity(db_session, case, "en", title="My Own Title")
        ident.title_approved, ident.title_source, ident.published = approved, source, published
        db_session.commit()
        assert A.auto_select(db_session, case, "en", prefer=b.id) is None and T.get_identity(db_session, case.id, "en").editorial_title == "My Own Title"


def test_the_choice_of_the_app_follows_a_later_pairing_and_a_title_the_gates_refuse_gives_way(db_session):
    case = _case(db_session)
    cand(db_session, case, "The Mystery", 0.95, "none")
    a, b, _ = three(db_session, case)
    assert A.auto_select(db_session, case, "en").editorial_title == a.title
    assert A.auto_select(db_session, case, "en", prefer=b.id).editorial_title == b.title


def test_the_title_writer_gives_its_angle_and_the_critic_its_clarity_in_their_own_calls_and_the_app_chooses_without_another_request(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.case_naming, "min_words", 2)
    case = _case(db_session)

    class AngleGen(ScriptedGen):
        async def generate_structured(self, role, system, user, images=None):
            out, res = await super().generate_structured(role, system, user, images)
            if role == "case_naming_agent":
                payload = json.loads(user)
                assert payload["limits"]["words"] == [ai_config.case_naming.min_words, ai_config.case_naming.max_words] and payload["limits"]["characters"] > 50 and "bad" in payload["example"]
                for i, c in enumerate(out["candidates"]):
                    c["angle"] = f"angle {i}"
            return out, res

    gen = AngleGen({"fa": FA * 2})
    asyncio.run(A.generate_case_titles(db_session, case, gen, None, ["fa"]))
    rows = db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, language="fa").filter(CaseTitleCandidate.status != "rejected").all()
    assert len({json.loads(r.critic_json)["angle"] for r in rows}) == len(rows) and all(json.loads(r.critic_json)["clarity"] == 0.8 for r in rows)
    ident = T.get_identity(db_session, case.id, "fa")
    assert ident.title_source == "auto" and ident.title_approved and gen.calls.count("case_naming_agent") == 1 and set(gen.calls) == {"case_naming_agent", "case_title_critic", "native_title_critic"}


def test_a_title_the_critic_finds_unclear_is_rejected_whatever_its_other_scores(db_session):
    case = _case(db_session)
    gen = ScriptedGen({"en": ["Her Classroom His Pulpit Burned Down", "She Called Police at 2 AM Then Vanished", "The Teacher Of Troy Remembered Always", "A Man Was Found Dead In A Hotel Room", "The Garage Door Was Open That Night"]},
                      bad={"Her Classroom His Pulpit Burned Down": {"clarity": 0.2}, "The Teacher Of Troy Remembered Always": {"clarity": 0.3}})
    asyncio.run(A.generate_case_titles(db_session, case, gen, None, ["en"]))
    rows = {r.title: r for r in db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, language="en")}
    assert rows["Her Classroom His Pulpit Burned Down"].status == "rejected" and "unclear or abstract" in rows["Her Classroom His Pulpit Burned Down"].rejection_reason
    assert rows["She Called Police at 2 AM Then Vanished"].status != "rejected"


# ---------------------------------------------------------------- the cover planner pairs its scene with an option in its own call


class PairGen(CoverGen):
    def __init__(self, pick="B", cover_title="Title number 0 here", reason="a boat the title only hints at"):
        super().__init__()
        self.pick, self.cover_title, self.reason = pick, cover_title, reason

    async def generate_structured(self, role, system, user, images=None):
        out, res = await super().generate_structured(role, system, user, images)
        if role == "cover_planner" and "youtube_titles" in json.loads(user):
            long = next(c for c in out["covers"] if c["key"] == "long")
            long.update(title=self.cover_title or long["title"], title_pick=self.pick, pair_reason=self.reason)
        return out, res


@pytest.fixture()
def film(db_session, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = PairGen()
    monkeypatch.setattr("app.shortform.clip_agents.get_generation_provider", lambda: gen)
    monkeypatch.setattr("app.covers.agents.get_generation_provider", lambda: gen)
    asyncio.run(CA.ClipPlanner().plan_english(db_session, case, master))
    return case, gen


def plan(db, case):
    return asyncio.run(CAG.CoverPlanner().propose(db, case, "en"))


def test_the_planner_gets_the_options_in_its_one_call_picks_one_and_the_app_chooses_that_pair(db_session, film):
    case, gen = film
    a, b, c = three(db_session, case)
    rows = {r.cover_key: r for r in plan(db_session, case)}
    payload = gen.calls[-1][1]
    assert [o["id"] for o in payload["youtube_titles"]["options"]] == ["A", "B", "C"] and set(payload["youtube_titles"]["options"][0]) == {"id", "angle", "title"}
    assert payload["youtube_titles"]["story_hook"] == "a lamp that was left burning" and len([c for c in gen.calls if c[0] == "cover_planner"]) == 1
    pairing = json.loads(rows["long"].validation_json)["pairing"]
    assert pairing == {"pick": "B", "title": c.title, "angle": "the light", "reason": "a boat the title only hints at"} and all("pairing" not in json.loads(r.validation_json) for k, r in rows.items() if k != "long")
    ident = T.get_identity(db_session, case.id, "en")
    assert ident.editorial_title == c.title and ident.title_source == "auto" and rows["long"].status == "proposed"


def test_without_options_the_planner_works_as_before(db_session, film):
    case, gen = film
    rows = plan(db_session, case)
    assert "youtube_titles" not in gen.calls[-1][1] and {r.status for r in rows} == {"proposed"} and all("pairing" not in json.loads(r.validation_json) for r in rows)
    assert T.get_identity(db_session, case.id, "en") is None


def test_the_pick_must_be_an_option_and_the_cover_title_must_not_repeat_the_picked_title(db_session, film):
    case, gen = film
    three(db_session, case)
    gen.pick = "Z"
    issues = {i["type"] for i in json.loads({r.cover_key: r for r in plan(db_session, case)}["long"].validation_json)["issues"]}
    assert "title_pick" in issues
    gen.pick, gen.cover_title = "B", "Two Neighbours Saw the Light Go Out Twice"
    long = {r.cover_key: r for r in plan(db_session, case)}["long"]
    assert "repeats_title" in {i["type"] for i in json.loads(long.validation_json)["issues"]} and long.status == "open"
    gen.cover_title, gen.reason = "A different promise here", ""
    assert "pair_reason" in {i["type"] for i in json.loads({r.cover_key: r for r in plan(db_session, case)}["long"].validation_json)["issues"]}


def test_a_picked_title_of_an_owner_is_not_replaced_by_the_planner(db_session, film):
    case, _ = film
    a, b, _ = three(db_session, case)
    A.approve_candidate(db_session, case, a.id)
    plan(db_session, case)
    ident = T.get_identity(db_session, case.id, "en")
    assert ident.editorial_title == a.title and ident.title_source == "owner"


def test_the_repeat_share_is_the_share_of_the_cover_words_the_title_already_says():
    assert SEL.repeat_share("The last silent call", "She made the last call") == pytest.approx(3 / 4)
    assert SEL.repeat_share("", "x") == 0.0 and SEL.repeat_share("Another idea", "The Borrowed Boat") == 0.0
    assert {i["type"] for i in P.pairing_issues({"title": "The Borrowed Boat", "title_pick": "A", "pair_reason": "ok"}, {"A": {"title": "The Borrowed Boat"}})} == {"repeats_title"}


# ---------------------------------------------------------------- the Titles view


def test_the_titles_view_shows_the_choice_the_options_the_pairing_and_the_cover(db_session, client, film):
    case, _ = film
    three(db_session, case)
    plan(db_session, case)
    out = client.get(f"/api/cases/{case.id}/titles").json()
    en = next(x for x in out["languages"] if x["language"] == "en")
    assert en["title"] == "Two Neighbours Saw the Light Go Out Twice" and en["source"] == "auto" and en["approved"] and [o["id"] for o in en["options"]] == ["A", "B", "C"] and [o["selected"] for o in en["options"]] == [False, True, False]
    assert en["hook"] and en["pairing"]["pick"] == "B" and en["cover"]["title"] == "Title number 0 here" and en["validation"] == {"ok": True, "issues": []}
    de = next(x for x in out["languages"] if x["language"] == "de")
    assert de["title"] is None and de["options"] == [] and de["cover"] is None
    db_session.query(CoverProposal).filter(CoverProposal.case_id == case.id, CoverProposal.cover_key == "long").update({"title": "Two Neighbours Saw the Light Go Out Twice"})
    db_session.commit()
    again = next(x for x in client.get(f"/api/cases/{case.id}/titles").json()["languages"] if x["language"] == "en")
    assert again["validation"]["ok"] is False and "repeats the title" in again["validation"]["issues"][0]
    assert client.get("/api/cases/99999/titles").status_code == 404
