"""The planner's alternatives (docs/alternative_graphics.md): offers built only from stored evidence, placed once
each, never before the story tells them, in the bare stretches of a beat; the gate before render; the checks of
what is not a picture; the owner's view of the empty moments."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    EditorialBlueprint,
    MvpCandidate,
    MvpRequirement,
    StoryVersion,
    VisualAsset,
    VisualPlan,
)
from app.documentary.graphics import shots as GS
from app.documentary.graphics.base import GraphicUnavailable
from app.documentary.mvp import alternatives as AL
from app.documentary.mvp import copy as CP
from app.documentary.mvp import coverage as CV
from app.documentary.mvp import evidence as EV
from app.documentary.mvp import headlines as HL
from app.documentary.mvp import pipeline as PL
from app.documentary.pipeline.errors import AwaitingPictureReview
from app.main import app
from app.providers.generation.base import GenerationResult


def evidence(**kw) -> AL.Evidence:
    beats = [{"id": f"B{n:02d}", "summary": f"summary {n}", "reveals": [], "viewer_knows": []} for n in range(1, 13)]
    base = dict(order={b["id"]: i for i, b in enumerate(beats)}, beats=beats, questions=[], facts={},
                contradictions=[], sources={}, events=[], quotes=[], narration={}, starts={},
                total_seconds=3600.0)
    base.update(kw)
    return AL.Evidence(**base)


def gap_beat(seconds=60.0, sizes=(20.0, 20.0, 20.0)):
    """A beat of bare stretches only (as the direction leaves them)."""
    return [{"command": "BLACK_SCREEN", "share": s / seconds, "gap": True, "said": f"said {n}", "motion": "NONE",
             "speed": 1.0, "transition_in": "CROSSFADE", "why": "no approved visual for this moment"}
            for n, s in enumerate(sizes)], seconds


def quote_offer(key="F1", best=0, drift=2, seconds=10.0, minimum=6.0):
    data = {"text": f"Quote {key}.", "fact_id": key}
    return AL.Offer("quote", f"quote|{key}", best, best + drift, best, data, seconds, minimum, frozenset({key}))


# -- the two small agents are checked by rules ---------------------------------------------------------------


def test_a_short_line_keeps_every_figure_of_its_claim_and_its_limit():
    claim = "Two gunshots were fired inside the home at 2:30 a.m. on February 16."
    assert CP.valid_line(claim, "Two gunshots at 2:30 a.m. on February 16", 60)
    assert not CP.valid_line(claim, "Two gunshots in the home at 2:30 a.m.", 60)       # the date is gone
    assert not CP.valid_line(claim, "Two gunshots in the home at night", 60)          # the time is gone
    assert not CP.valid_line(claim, "Two gunshots at 2:30 a.m. on February 16, the family says", 40)
    assert not CP.valid_line(claim, "", 60)


def conflict_row():
    return {"id": "C001", "topic": "Daughters in court",
            "description": "Accounts conflict over whether the daughters appeared in court to read their statements. "
                           "Courtroom Insider says the daughters read them themselves. BBC News reports the daughters "
                           "did not attend and family members read the letters.", "source_ids": [381, 409]}


def raw_conflict(**over):
    raw = {"title": "Did the daughters read their statements in court?", "kind": "witnesses",
           "summary": "Who read the daughters' letters",
           "sides": [{"who": "Courtroom Insider", "claim": "The daughters read their statements themselves.",
                      "source": "Courtroom Insider, live updates", "source_ids": [409]},
                     {"who": "BBC News", "claim": "The daughters did not attend; family members read the letters.",
                      "source": "BBC News", "source_ids": [381]}]}
    raw.update(over)
    return raw


def test_a_conflict_needs_two_sides_from_the_description_each_with_a_listed_source():
    ok = CP.valid_conflict(raw_conflict(), conflict_row(), {})
    assert ok and [s["fact_id"] for s in ok["sides"]] == ["C001", "C001"] and ok["kind"] == "witnesses"
    assert CP.valid_conflict({"skip": "one account only"}, conflict_row(), {}) is None
    assert CP.valid_conflict(raw_conflict(sides=raw_conflict()["sides"][:1]), conflict_row(), {}) is None
    bad_source = raw_conflict()
    bad_source["sides"][0]["source_ids"] = [999]
    assert CP.valid_conflict(bad_source, conflict_row(), {}) is None
    invented = raw_conflict()
    invented["sides"][1]["claim"] = "A detective found a second weapon under the porch."
    assert CP.valid_conflict(invented, conflict_row(), {}) is None
    too_long = raw_conflict(title="x" * 80)
    assert CP.valid_conflict(too_long, conflict_row(), {}) is None


class FakeAgent:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def __call__(self, agent, generation, user, system=None):
        self.calls.append((agent, user))
        answer = self.answers[agent]
        if isinstance(answer, Exception):
            raise answer
        return answer, GenerationResult(text="{}", model="m/fake", provider="fake")


@pytest.fixture
def case(db_session):
    c = Case(canonical_title="Alt proof", slug=f"alt-{uuid4().hex[:8]}", language="en")
    db_session.add(c)
    db_session.commit()
    return c


def test_agent_answers_are_remembered_but_a_failed_call_never_is(db_session, case, monkeypatch):
    from app.providers.generation.base import GenerationError

    agent = FakeAgent({"mvp.conflict": raw_conflict(), "mvp.copy": {"lines": {"F1": "Police arrived at 11:58 p.m."}},
                       "mvp.headline": GenerationError("unavailable", "down")})
    monkeypatch.setattr(CP, "run_agent", agent)
    writer = CP.Copywriter(db_session, case, {}, generation=object())
    first = asyncio.run(writer.conflicts([conflict_row()], {381: {"publisher": "BBC", "title": "t"},
                                                            409: {"publisher": "CI", "title": "t"}}))
    asyncio.run(writer.conflicts([conflict_row()], {}))
    assert len(first) == 1 and [c[0] for c in agent.calls] == ["mvp.conflict"]       # asked once, then remembered
    lines = asyncio.run(writer.lines([{"id": "F1", "claim": "Police arrived at 11:58 p.m. on the night.",
                                       "max_chars": 60}]))
    assert lines == {"F1": "Police arrived at 11:58 p.m."}
    data = {"fact_id": "S1", "headline": "h", "source": "BBC News", "published": {"year": 2026, "month": 1, "day": 2}}
    beats = [{"id": "B01"}, {"id": "B02"}]
    assert asyncio.run(writer.headline_where(data, "k", beats, {"B01": 0, "B02": 1})) == {}
    assert "k" not in writer.cache                                  # a failure is retried next time
    agent.answers["mvp.headline"] = {"decision": "use", "earliest_beat": "B01", "best_beat": "B02"}
    assert asyncio.run(writer.headline_where(data, "k", beats, {"B01": 0, "B02": 1})) == {
        "earliest": "B01", "best": "B02"}
    agent.answers["mvp.headline"] = {"decision": "use", "earliest_beat": "B02", "best_beat": "B01"}
    assert asyncio.run(writer.headline_where(data, "other", beats, {"B01": 0, "B02": 1})) == {}   # best before earliest


# -- offers --------------------------------------------------------------------------------------------------


def test_headlines_come_only_from_listed_newspapers_with_the_headline_as_printed():
    sources = {1: {"publisher": "BBC News", "title": "Former Idol contestant found guilty of murder | BBC News",
                   "url": "https://www.bbc.com/news/articles/c5rm", "published_at": "2026-09-29", "language": "en"},
               2: {"publisher": "ABC News", "title": "Ex-mistress &#39;takes stand&#39; - ABC7 New York",
                   "url": "https://abc7ny.com/post/x", "published_at": "2026-09-23", "language": "en"},
               3: {"publisher": "Some Blog", "title": "A blog about the case", "url": "https://blog.example.com/a",
                   "published_at": "2026-09-23", "language": "en"},
               4: {"publisher": "BBC News", "title": "Ohne Datum Schlagzeile hier", "url": "https://www.bbc.com/x",
                   "published_at": None, "language": "en"}}
    found = {c["fact_id"]: c for c in HL.candidates(sources)}
    assert set(found) == {"S1", "S2"}
    assert found["S1"]["headline"] == "Former Idol contestant found guilty of murder"
    assert found["S2"]["headline"] == "Ex-mistress 'takes stand'" and found["S2"]["source"] == "ABC7 New York"


def test_timeline_offers_group_events_in_story_order_and_never_before_they_are_told():
    events = [{"id": f"T{n}", "date": f"2026-0{n}-1{n}", "first_beat": f"B{n + 2:02d}", "label": {"en": f"Event {n}"}}
              for n in range(1, 8)]
    ev = evidence(events=events)
    offers = AL.timeline_offers(ev, {})
    assert [len(o.data["events"]) for o in offers] == [5, 7] and [o.data["shown"] for o in offers] == [0, 5]
    assert [e["fact_id"] for e in offers[1].data["events"]] == [f"T{n}" for n in range(1, 8)]   # the road goes on
    assert offers[1].items == {"T6", "T7"} and AL.graphic("timeline").required_cues(offers[1].data) == 2
    first = offers[0]
    assert first.best == ev.order["B07"] and first.first == first.best      # not before its last event is told
    assert first.seconds == pytest.approx(1.5 * (4.0 + 3.4 * 5)) and first.items == {"T1", "T2", "T3", "T4", "T5"}


def test_quotes_are_complete_the_fullest_wording_and_once_per_fact():
    ev = evidence(
        facts={"F1": {"claim": "x"}},
        quotes=[{"text": "I was up until 4am last night crying, literally thinking of ways to", "fact_id": "F1",
                 "beat": "B03"},
                {"text": "I was up until 4am last night crying, literally thinking of ways to kill her.",
                 "fact_id": "F1", "beat": None, "speaker": "Caleb Flynn"},
                {"text": "Flynn claimed someone broke into their home and shot his wife in", "fact_id": "F2",
                 "beat": "B01"}])
    ev.beats[2]["reveals"] = ["F1"]
    offers = AL.quote_offers(ev)
    assert [o.key for o in offers] == ["quote|F1"] and offers[0].data["text"].endswith("kill her.")
    assert offers[0].data["speaker"] == "Caleb Flynn" and offers[0].best == 2


def test_key_facts_show_the_figure_of_a_stated_fact_and_never_a_disputed_one():
    facts = {"F1": {"claim": "Two gunshots were fired inside the home at 2:30 a.m.", "confidence": 0.9},
             "F2": {"claim": "He paid 500 dollars.", "confidence": 0.9, "disputed": True},
             "F3": {"claim": "No figure here at all.", "confidence": 0.9}}
    ev = evidence(facts=facts)
    for fid in facts:
        ev.beats[4]["reveals"].append(fid)
    lines = {"F1": "Two gunshots in the home at 2:30 a.m.", "F2": "He paid 500 dollars", "F3": "No figure"}
    offers = AL.key_fact_offers(ev, lines)
    assert len(offers) == 1 and offers[0].data["facts"] == [
        {"value": "2:30 a.m.", "caption": "Two gunshots in the home at 2:30 a.m.", "fact_id": "F1"}]


def test_a_recap_holds_only_what_the_story_told_by_then_and_each_item_once():
    facts = {f"F{n}": {"claim": f"Fact number {n} happened at 1{n}:00.", "confidence": 0.9 - n / 100,
                       "evidence_strength": "strong_secondary"} for n in range(1, 9)}
    ev = evidence(facts=facts, questions=[{"id": "Q1", "question": "Who did it?", "opened_in": "B02",
                                           "resolved_in": "B12"}])
    ev.beats[7]["viewer_knows"] = [f"F{n}" for n in range(1, 6)]
    ev.beats[11]["viewer_knows"] = [f"F{n}" for n in range(1, 9)]
    ev.starts = {b["id"]: 100.0 * i for i, b in enumerate(ev.beats)}
    lines = {fid: f"short {fid}" for fid in facts} | {"Q1": "Who did it?"}
    offers = AL.recap_offers(ev, lines, {}, exclude={"F1"})
    assert len(offers) == 1 and offers[0].best == 7                      # a recap after beat 8 (every 8 beats)
    shown = [x["fact_id"] for s in ("confirmed", "disputed", "unknown") for x in offers[0].data[s]]
    assert "F1" not in shown and "F6" not in shown and len(shown) == len(set(shown))   # F6 is told later
    assert offers[0].data["unknown"] == [{"text": "Who did it?", "fact_id": "Q1"}]
    assert "progress" not in offers[0].data


def test_a_conflict_is_offered_at_the_beat_that_tells_it_and_only_if_one_does():
    ev = evidence(narration={"B05": "The daughters read their victim impact statements in court, accounts conflict"})
    structured = [CP.valid_conflict(raw_conflict(), conflict_row(), {})]
    offers = AL.conflict_offers(ev, structured)
    assert len(offers) == 1 and offers[0].best == 4 and offers[0].data["conflict"]["status"] == "open"
    assert AL.conflict_offers(evidence(), structured) == [] and AL.conflict_offers(evidence(), structured) == []


# -- placement -----------------------------------------------------------------------------------------------


def test_offers_fill_the_longest_bare_stretch_first_and_each_is_shown_once():
    shots, length = gap_beat(60.0, (10.0, 40.0, 10.0))
    state = AL.State()
    offers = [quote_offer("F1"), quote_offer("F2"), quote_offer("F3"), quote_offer("F4")]
    AL.fill_beat(shots, length, 0, offers, state)
    graphics = [s for s in shots if s["command"] == "SHOW_GRAPHIC"]
    assert len(graphics) == 4 and {s["graphic"]["data"]["fact_id"] for s in graphics} == {"F1", "F2", "F3", "F4"}
    assert sum(1 for o in offers if o.placed) == 4 and abs(sum(s["share"] for s in shots) - 1.0) < 1e-6
    again, _ = gap_beat(60.0)
    AL.fill_beat(again, length, 0, offers, state)                      # nothing left to show: the beat stays bare
    assert all(s["command"] == "BLACK_SCREEN" for s in again)


def test_no_graphic_reaches_into_the_silence_where_a_chapter_card_may_be_laid():
    shots, length = gap_beat(60.0, (60.0,))
    AL.fill_beat(shots, length, 0, [quote_offer("F1", seconds=60.0)], AL.State(), tail=12.0)
    graphic = next(s for s in shots if s["command"] == "SHOW_GRAPHIC")
    bare = [s for s in shots if s.get("gap")]
    assert round(graphic["share"] * length, 3) == 48.0 and round(sum(s["share"] for s in bare) * length, 3) == 12.0
    assert abs(sum(s["share"] for s in shots) - 1.0) < 1e-6


def test_an_offer_is_never_placed_before_its_beat_or_far_after_it():
    state, offers = AL.State(), [quote_offer("F1", best=5, drift=2)]
    for idx, expect in ((3, False), (4, False), (5, True)):
        shots, length = gap_beat(30.0, (30.0,))
        AL.fill_beat(shots, length, idx, offers, state)
        assert any(s["command"] == "SHOW_GRAPHIC" for s in shots) is expect
    late = [quote_offer("F2", best=5, drift=2)]
    shots, length = gap_beat(30.0, (30.0,))
    AL.fill_beat(shots, length, 8, late, AL.State())
    assert all(s["command"] == "BLACK_SCREEN" for s in shots)


def test_an_offer_that_belongs_to_the_beat_goes_before_one_that_only_may_come_here():
    here, floating = quote_offer("F1", best=3, drift=0), quote_offer("F2", best=1, drift=4)
    shots, length = gap_beat(10.0, (10.0,))
    AL.fill_beat(shots, length, 3, [floating, here], AL.State())
    assert shots[0]["graphic"]["data"]["fact_id"] == "F1"


def test_a_stretch_too_short_for_an_offer_stays_bare_and_nothing_is_cut_below_its_minimum():
    shots, length = gap_beat(20.0, (5.0, 15.0))
    AL.fill_beat(shots, length, 0, [quote_offer("F1", seconds=10.0, minimum=6.0)], AL.State())
    placed = [s for s in shots if s["command"] == "SHOW_GRAPHIC"]
    assert len(placed) == 1 and placed[0]["share"] * length >= 6.0
    only_short, length = gap_beat(5.0, (5.0,))
    AL.fill_beat(only_short, length, 0, [quote_offer("F2", minimum=6.0)], AL.State())
    assert only_short[0]["command"] == "BLACK_SCREEN"


def test_a_fact_is_never_shown_by_two_graphics():
    first, second = quote_offer("F1"), quote_offer("F9")
    second.items = frozenset({"F1"})                                    # the same evidence on another card
    shots, length = gap_beat(40.0, (20.0, 20.0))
    AL.fill_beat(shots, length, 0, [first, second], AL.State())
    assert sum(1 for s in shots if s["command"] == "SHOW_GRAPHIC") == 1


def test_conflict_pages_and_recaps_are_held_three_times_longer_than_the_old_cards():
    assert ai_config.graphics_settings().hold_seconds["conflict"] >= 3 * 8.0
    assert ai_config.graphics_settings().hold_seconds["recap"] >= 3 * 8.0
    data = {"conflict": {"id": "C1", "number": 1, "title": "Was the door open?", "kind": "witnesses", "status": "open",
                         "summary": "Two accounts"},
            "sides": [{"who": "A", "claim": "Open.", "source": "s", "fact_id": "C1"},
                      {"who": "B", "claim": "Shut.", "source": "s", "fact_id": "C1"}]}
    page = AL.offer("conflict", "conflict|C1", data, 0, items=("C1",))
    shots, length = gap_beat(60.0, (60.0,))
    AL.fill_beat(shots, length, 0, [page], AL.State())
    assert shots[0]["share"] * length == pytest.approx(1.5 * 24.0)


def test_the_board_comes_after_two_or_more_conflict_pages_and_lists_only_those_shown():
    state = AL.State()
    assert AL.board_offer(state, 5) is None
    for n in (1, 2):
        state.pages.append({"id": f"C{n}", "title": f"T{n}", "summary": f"S{n}", "a": "A", "b": "B", "status": "open"})
    board = AL.board_offer(state, 5)
    assert [c["id"] for c in board.data["cards"]] == ["C1", "C2"] and board.data["new_card"] == "C2"
    assert [c["title"] for c in board.data["cards"]] == ["S1", "S2"]            # the board shows each page's summary
    pages = [AL.Offer("conflict", f"conflict|C{n}", 0, 3, 0, {}, 24.0, 12.0, frozenset({f"C{n}"})) for n in (1, 2)]
    pages[0].placed = True
    assert not AL.board_due(pages, state, 1)                            # one page can still be placed
    pages[1].placed = True
    assert AL.board_due(pages, state, 1)
    assert not AL.board_due([*pages, board], state, 1)                  # offered once


# -- the film's plan -------------------------------------------------------------------------------------------


def test_direction_puts_offers_where_nothing_else_can_be_shown_and_reports_them(db_session):
    from test_mvp_direct import _manifest, _plan

    case, plan = _plan(db_session, 0)
    from app.documentary.mvp import direct as D

    offers = [quote_offer("F1", best=0, drift=0, seconds=10.0), quote_offer("F2", best=0, drift=0)]
    report = asyncio.run(D.direct_film(db_session, case, plan, _manifest([60.0]), None, {"B01": 0}, {},
                                       offers=offers))
    shots = json.loads(plan.plan_json)["beats"][0]["shots"]
    assert sum(1 for s in shots if s["command"] == "SHOW_GRAPHIC") == 2
    assert report["alternatives"] == {"quote": 20.0} and report["alternatives_unplaced"] == []
    assert abs(sum(s["share"] for s in shots) - 1.0) < 1e-3


def test_the_run_stops_before_render_while_a_moment_has_nothing_to_show():
    plan = SimpleNamespace(validation_json=json.dumps({"plan": {"gaps": [
        {"beat": "B03", "start": 100.0, "end": 130.0, "said": "x"}, {"beat": "B07", "start": 400.0, "end": 410.0,
                                                                      "said": "y"}]}}))
    with pytest.raises(AwaitingPictureReview, match=r"Awaiting picture review: 2 moment\(s\) \(40.0s\).*B03, B07"):
        PL.stage_complete(plan, "en")
    done = SimpleNamespace(validation_json=json.dumps({"plan": {"gaps": [], "alternatives": {"quote": 20.0}}}))
    assert PL.stage_complete(done, "en") == {"gaps": 0, "alternatives": {"quote": 20.0}}


def test_the_owner_is_told_how_to_resume_a_run_waiting_for_picture_review():
    from app.documentary.jobs import failure_details

    details = failure_details("en: AwaitingPictureReview: Awaiting picture review: 3 moment(s)")
    assert details["resume_allowed"] and "Review pictures" in details["next_action"]


# -- what is checked on the finished script ------------------------------------------------------------------------


def graphic_shot(start, end, fact="F1", language="en", motion="NONE"):
    return {"kind": "graphic", "start": start, "end": end, "motion": motion,
            "graphic": {"type": "quote", "data": {"text": "Q.", "fact_id": fact}, "language": language}}


def test_the_film_is_checked_for_repeats_moving_text_other_languages_and_dropped_graphics():
    clean = CV.graphic_checks({"language": "en", "shots": [graphic_shot(0, 10, "F1"), graphic_shot(20, 30, "F2")]})
    assert clean["repeated"] == [] and clean["moving"] == [] and clean["wrong_language"] == []
    assert clean["seconds"] == {"quote": 20.0}
    bad = CV.graphic_checks({"language": "en", "graphics_dropped": [{"type": "recap"}], "shots": [
        graphic_shot(0, 10, "F1"), graphic_shot(20, 30, "F1"), graphic_shot(40, 50, "F3", language="de"),
        {"kind": "chapter", "start": 60, "end": 64, "motion": "SLOW_PUSH"}]})
    assert [r["evidence"] for r in bad["repeated"]] == ["F1"] and len(bad["moving"]) == 1
    assert [w["language"] for w in bad["wrong_language"]] == ["de"] and len(bad["dropped"]) == 1
    verdict = CV.verdict({"temporal_coverage": 1.0, "longest_black": 0, "unapproved_assets": [], "long_stills": [],
                          "over_reused": {}, "requirements": {"uncovered": 0, "shortage": 0}, "graphics": bad})
    assert not verdict["review_ready"] and len(verdict["blocking"]) == 4


def test_no_black_is_allowed_in_the_review_film():
    assert ai_config.mvp().coverage.max_uncovered_gap_seconds <= 0.5
    assert ai_config.mvp().coverage.min_temporal_coverage >= 0.995


# -- compose and render ----------------------------------------------------------------------------------------------


def test_a_planned_graphic_becomes_a_still_shot_with_its_cues_in_seconds():
    from test_chapters import _manifest

    from app.documentary.production.script import compose

    data = {"text": "I did not do it.", "fact_id": "F1"}
    plan = {"candidates": {}, "beats": [{"beat_id": "B02", "shots": [
        {"command": "SHOW_GRAPHIC", "share": 1.0, **GS.plan_fields("quote", data), "motion": "NONE"}]}]}
    script = compose(_manifest(), plan, {}, {}, "en")
    shot = next(s for s in script["shots"] if s.get("kind") == "graphic")
    assert shot["motion"] == "NONE" and shot["graphic"]["language"] == "en"
    seconds = shot["end"] - shot["start"]
    assert shot["graphic"]["cues"] == [round(shot["graphic"]["cue_fractions"][0] * seconds, 3)]
    assert script["graphics_dropped"] == []


def test_a_graphic_that_no_longer_fits_is_dropped_and_reported_not_stretched():
    shots = [{"kind": "image", "start": 0.0, "end": 10.0},
             {"kind": "graphic", "start": 10.0, "end": 12.0,
              "graphic": {"type": "recap", "cue_fractions": [0.1, 0.4, 0.7],
                          "data": {"confirmed": [{"text": "A fact.", "fact_id": "F1"}],
                                   "disputed": [{"text": "A claim.", "fact_id": "F2"}],
                                   "unknown": [{"text": "A question?", "fact_id": "F3"}]}}}]
    dropped = GS.finalize(shots, "en")
    assert [s["kind"] for s in shots] == ["image"] and shots[0]["end"] == 12.0
    assert dropped[0]["type"] == "recap" and "too short" in dropped[0]["why"]


def test_the_renderer_plays_a_graphic_as_a_page_that_only_changes_on_its_cues():
    import numpy as np

    from app.documentary.render.engine import FrameMaker

    shot = {"kind": "graphic", "start": 10.0, "end": 24.0, "motion": "NONE",
            "graphic": {"type": "quote", "data": {"text": "A quote.", "fact_id": "F1"}, "cues": [2.0],
                        "cue_fractions": [0.15], "language": "en"}}
    maker = FrameMaker({"language": "en", "shots": []}, 960, 540)
    before, settled, later = (maker.shot_frame(shot, t) for t in (11.0, 13.0, 22.0))
    assert settled.shape == (540, 960, 3) and not np.array_equal(before, settled)
    assert np.array_equal(settled, later)                             # no zoom, no drift


# -- the owner's view of the empty moments ----------------------------------------------------------------------------


def seed_gaps(db):
    case = Case(canonical_title="Gaps proof", slug=f"gp-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=2, status="planned", validation_json=json.dumps({
        "mvp": {"language": "en"}, "plan": {"gaps": [{"beat": "B03", "start": 1.0, "end": 9.0, "said": "x"}],
                                            "alternatives": {"quote": 10.0}}}))
    db.add(plan)
    db.commit()
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B03.1", beat_id="B03",
                         function="evidence", entity_key="e", role_needed="evidence", priority="high",
                         spec_json="{}")
    db.add(req)
    db.commit()
    for status in ("rejected", "needs_review", "approved"):
        asset = VisualAsset(case_id=case.id, asset_code=f"V{uuid4().hex[:6]}", local_path="x.jpg")
        db.add(asset)
        db.commit()
        db.add(MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, final_status=status, role="evidence",
                            provenance_status="passed", rights_status="cleared", ethics_status="approved",
                            scene_status="approved", identity_status="needs_review"))
    db.commit()
    return case


def test_the_owner_sees_the_empty_moments_and_can_list_what_was_rejected_or_not_sure(db_session):
    case = seed_gaps(db_session)
    client = TestClient(app)
    gaps = client.get(f"/api/cases/{case.id}/mvp/gaps").json()
    assert gaps["gap_count"] == 1 and gaps["gap_seconds"] == 8.0 and gaps["alternatives"] == {"quote": 10.0}
    assert gaps["beats"] == [{"beat_id": "B03", "needs_review": 1, "rejected": 1}]
    listed = client.get(f"/api/cases/{case.id}/mvp/candidates?status=needs_review,rejected&beat=B03").json()
    assert sorted(c["status"] for c in listed) == ["needs_review", "rejected"]
    assert client.get(f"/api/cases/{case.id}/mvp/candidates?status=rejected&beat=B99").json() == []
    assert client.get(f"/api/cases/{case.id}/mvp/gaps?language=de").status_code == 404
    rejected = next(c for c in listed if c["status"] == "rejected")
    approved = client.post(f"/api/mvp/candidates/{rejected['id']}/decision", json={"decision": "approve"}).json()
    assert approved["status"] == "approved"                               # the owner can approve what was rejected


# -- the texts of a placed graphic in the film's language -------------------------------------------------------------------


def test_the_text_of_a_placed_graphic_is_translated_but_names_numbers_and_headlines_are_not(db_session, case,
                                                                                            monkeypatch):
    asked = {}

    async def fake_localize(db, case_id, items, language, narration_excerpt=""):
        asked.update(items)
        return {k: f"[{language}] {v}" for k, v in items.items()}

    monkeypatch.setattr("app.documentary.visuals.generated.localize_texts", fake_localize)
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db_session.add(sv)
    db_session.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db_session.add(bp)
    db_session.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, status="planned", plan_json=json.dumps({"beats": [
        {"beat_id": "B01", "shots": [
            {"command": "SHOW_GRAPHIC", "share": 0.5, **GS.plan_fields(
                "key_facts", {"facts": [{"value": "11:58 PM", "caption": "Police arrive", "fact_id": "F1"}]})},
            {"command": "SHOW_GRAPHIC", "share": 0.5, **GS.plan_fields("headline", {
                "headline": "Four found dead", "source": "BBC News", "url": "https://www.bbc.com/a",
                "published": {"year": 2022, "month": 11, "day": 14}, "fact_id": "S1", "language": "en"})}]}]}))
    db_session.add(plan)
    db_session.commit()
    done = asyncio.run(EV.localize_graphics(db_session, case, plan, "de"))
    shots = json.loads(plan.plan_json)["beats"][0]["shots"]
    facts, headline = (s["graphic"] for s in shots)
    assert done == {"localized": 1} and asked == {"g0": "Police arrive"}
    assert facts["data"]["facts"][0] == {"value": "11:58 PM", "caption": "[de] Police arrive", "fact_id": "F1"}
    assert headline["data"]["headline"] == "Four found dead" and headline["language"] == "de"
    assert asyncio.run(EV.localize_graphics(db_session, case, plan, "en")) == {"localized": 0}


def test_an_unknown_graphic_type_and_a_missing_cue_are_refused_at_plan_time():
    with pytest.raises(GraphicUnavailable, match="unknown graphic type"):
        GS.render_for({"type": "hologram", "data": {}, "cue_fractions": []}, 10.0, "en")


def test_a_page_that_cannot_be_set_is_never_offered():
    data = {"conflict": {"id": "C1", "number": 1, "title": "Was the door open?", "kind": "witnesses", "status": "open",
                         "summary": "Two accounts"},
            "sides": [{"who": "W" * 200, "claim": "Open.", "source": "s", "fact_id": "C1"},
                      {"who": "B", "claim": "W" * 150, "source": "s", "fact_id": "C1"}]}
    assert AL.offer("conflict", "conflict|C1", data, 0) is None


def test_a_recap_does_not_list_two_facts_that_say_the_same():
    facts = {"F1": {"claim": "x", "confidence": 0.9, "evidence_strength": "strong_secondary"},
             "F2": {"claim": "y", "confidence": 0.85, "evidence_strength": "strong_secondary"},
             "F3": {"claim": "z", "confidence": 0.8, "evidence_strength": "strong_secondary"}}
    ev = evidence(facts=facts)
    ev.beats[7]["viewer_knows"] = ["F1", "F2", "F3"]
    ev.starts = {b["id"]: 10.0 * i for i, b in enumerate(ev.beats)}
    lines = {"F1": "Ashley born to Todd and Jill Smith", "F2": "Todd and Jill Smith: Ashley's parents",
             "F3": "Refrigerator blocked garage entry"}
    recap = AL.recap_offers(ev, lines, {}, set())[0]
    assert [x["fact_id"] for x in recap.data["confirmed"]] == ["F1", "F3"]


def test_a_quotation_found_word_for_word_in_a_court_record_is_shown_as_that_document():
    record = "The judge said: I did not kill my wife! The court then adjourned for the day."
    ev = evidence(facts={"F1": {"claim": "x"}, "F2": {"claim": "y"}}, quotes=[
        {"text": "I did not kill my wife!", "fact_id": "F1", "beat": "B03", "speaker": "Caleb Flynn"},
        {"text": "It was never my gun.", "fact_id": "F2", "beat": "B04", "speaker": "Caleb Flynn"}],
        official={7: {"text": record, "type": "LIVE UPDATES: the sentencing hearing", "issuer": "Courtroom Insider",
                      "published_at": "2026-10-05", "language": "en"}})
    ev.beats[2]["reveals"], ev.beats[3]["reveals"] = ["F1"], ["F2"]
    offers = {o.key: o for o in AL.quote_offers(ev)}
    page = offers["document|F1"]
    assert page.kind == "document" and page.data["document"]["id"] == 7 and page.data["quote"]["verified"] is True
    assert page.data["document"]["date"].startswith("OCT 5") and page.data["context_before"] == []
    assert offers["quote|F2"].kind == "quote"                         # not in any record: an ordinary quote card


def test_a_map_is_only_placed_where_its_whole_zoom_and_the_hold_on_the_place_fit():
    from app.documentary.mvp import direct as D

    need = D.map_seconds()
    assert need == pytest.approx(24.0) and need >= 16.0 + 8.0          # the camera move, then a hold on the place
    for stretch, expected in ((need, True), (10.0, False)):
        shots = [{"command": "BLACK_SCREEN", "share": stretch / 60.0, "gap": True, "said": "He drove to Tipp City.",
                  "motion": "NONE", "speed": 1.0, "transition_in": "CROSSFADE"},
                 {"command": "NEW_IMAGE", "share": 1.0 - stretch / 60.0, "asset_id": "A", "motion": "NONE"}]
        D.add_map_cards(shots, {}, 60.0, ["Tipp City"], set(), None)
        assert (shots[0]["command"] == "SHOW_MAP") is expected
        if expected:
            assert shots[0]["share"] * 60.0 == pytest.approx(need)


def text_offer(key, text, best, drift=3, seconds=30.0, kind="quote"):
    return AL.Offer(kind, f"{kind}|{key}", best, best + drift, best, {"text": text, "fact_id": key}, seconds, 6.0,
                    frozenset({key}))


def test_an_alternative_away_from_its_own_beat_needs_the_words_spoken_to_be_about_it():
    offer = text_offer("F1", "Kayla Keyt read the victim statement.", best=2)
    shots, length = gap_beat(30.0, (30.0,))
    shots[0]["said"] = "The jury returned after two hours."
    AL.fill_beat(shots, length, 3, [offer], AL.State())
    assert shots[0]["command"] == "BLACK_SCREEN" and not offer.placed         # nothing here is about Kayla
    shots[0]["said"] = "Next, Kayla Keyt stood to speak."
    AL.fill_beat(shots, length, 3, [offer], AL.State())
    assert shots[0]["command"] == "SHOW_GRAPHIC" and offer.placed
    at_home, length = gap_beat(30.0, (30.0,))
    other = text_offer("F2", "Kayla Keyt read the victim statement.", best=3)
    AL.fill_beat(at_home, length, 3, [other], AL.State())                      # its own beat: always logical
    assert at_home[0]["command"] == "SHOW_GRAPHIC"


def test_the_recap_and_the_board_sum_up_the_film_and_need_no_shared_words():
    recap = text_offer("R1", "What is confirmed.", best=0, kind="recap")
    shots, length = gap_beat(30.0, (30.0,))
    AL.fill_beat(shots, length, 2, [recap], AL.State())
    assert shots[0]["command"] == "SHOW_GRAPHIC"


def test_a_wider_round_reaches_further_but_only_to_what_the_words_are_about():
    offer = text_offer("F1", "Kayla Keyt read the victim statement.", best=0, drift=0)
    shots, length = gap_beat(30.0, (30.0,))
    shots[0]["said"] = "Kayla Keyt spoke."
    AL.fill_beat(shots, length, 3, [offer], AL.State())
    assert shots[0]["command"] == "BLACK_SCREEN"                                # too far from its beat
    AL.fill_beat(shots, length, 3, [offer], AL.State(relax={"extra_drift": 3}))
    assert shots[0]["command"] == "SHOW_GRAPHIC"


def _two_beats(db_session, monkeypatch, offers):
    from test_mvp_direct import _manifest, _plan

    from app.documentary.mvp import direct as D

    monkeypatch.setitem(GS.T.config().planner, "escalation", [{"extra_drift": 1}, {"extra_drift": 4}])
    case, plan = _plan(db_session, 0)
    report = asyncio.run(D.direct_film(db_session, case, plan, _manifest([60.0, 60.0]), None, {"B01": 0, "B02": 1}, {},
                                       offers=offers))
    return report, json.loads(plan.plan_json)["beats"]


def test_bare_moments_make_the_planner_plan_the_whole_film_again_with_wider_limits(db_session, monkeypatch):
    first = text_offer("F1", "Flynn house at night.", best=0, drift=0, seconds=60.0)
    second = text_offer("F2", "The Flynn house burned.", best=0, drift=0, seconds=60.0)
    report, beats = _two_beats(db_session, monkeypatch, [first, second])
    assert report["gaps"] == [] and [r["gap_seconds"] for r in report["rounds"]] == [60.0, 0.0]
    assert not any(s.get("gap") for b in beats for s in b["shots"])
    assert [s["command"] for s in beats[1]["shots"]] == ["SHOW_GRAPHIC"]
    assert first.placed and second.placed


def test_the_round_with_the_fewest_bare_seconds_is_kept_when_no_round_covers_everything(db_session, monkeypatch):
    first = text_offer("F1", "Flynn house at night.", best=0, drift=0, seconds=60.0)
    unrelated = text_offer("F2", "Verdict of the jury.", best=0, drift=0, seconds=60.0)
    report, beats = _two_beats(db_session, monkeypatch, [first, unrelated])
    assert [r["gap_seconds"] for r in report["rounds"]] == [60.0, 60.0, 60.0] and report["gap_seconds"] == 60.0
    assert beats[1]["shots"][0].get("gap") and not unrelated.placed
