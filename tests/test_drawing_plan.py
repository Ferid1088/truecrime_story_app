"""The pencil drawing: how many portraits, how fast, and no gap of the film without a portrait."""

from __future__ import annotations

from app.core.ai_config import ai_config
from app.documentary.drawing import plan as DP


def pic(start, end):
    return {"kind": "image", "start": start, "end": end}


def gap(start, end):
    return {"kind": "black", "start": start, "end": end, "bare": True}


def card(start, end):
    return {"kind": "graphic", "start": start, "end": end}


def film(total=2700.0, gaps=((100, 130), (1500, 1520))):
    shots, t = [], 0.0
    for a, b in sorted(gaps):
        shots += [pic(t, a), gap(a, b)]
        t = b
    shots.append(pic(t, total))
    return shots


def test_the_drawing_is_on_screen_over_pictures_and_in_gaps_of_four_seconds_or_more_not_over_cards_or_short_gaps():
    shots = [pic(0, 20), card(20, 40), gap(40, 42), gap(42, 60), pic(60, 70)]
    got = [(i.start, i.end, i.mode) for i in DP.intervals_of(shots)]
    assert got == [(0, 20, "window"), (42, 60, "full"), (60, 70, "window")] or got == [(0, 20, "window"), (60, 70, "window")]
    assert (40, 42, "full") not in got, "a gap under four seconds is covered by the neighbours"


def test_the_number_of_portraits_and_the_speed_make_every_drawing_finish_by_the_end_nearest_to_normal_speed():
    pool = [DP.Portrait(f"P{n}", n) for n in range(1, 10)]
    plan = DP.plan_drawing(film(), pool)
    cfg = ai_config.drawing_settings()
    assert not plan.uncovered and cfg.min_speed <= plan.speed <= cfg.max_speed
    assert 6 <= len(plan.portraits) <= 8 and all(p["finished"] is not None for p in plan.portraits)
    assert [p["code"] for p in plan.portraits] == [f"P{n}" for n in range(1, len(plan.portraits) + 1)], "owner's priority order"
    assert all(abs(a["end"] - b["start"]) < 1e-6 or a["end"] <= b["start"] for a, b in zip(plan.segments, plan.segments[1:]))


def test_a_portrait_waits_until_its_person_has_been_named_and_the_next_in_priority_covers_the_gap_before():
    pool = [DP.Portrait("LATE", 1, earned_from=500.0), DP.Portrait("EARLY", 2, earned_from=0.0)]
    plan = DP.plan_drawing(film(), pool)
    assert not plan.uncovered
    first_gap = next(s for s in plan.segments if s["mode"] == "full")
    assert first_gap["portrait"] == "EARLY", "at the first gap only the second photo may be shown"


def test_a_gap_that_no_photo_of_the_pool_may_cover_is_returned_with_the_reason_for_every_photo():
    pool = [DP.Portrait("A", 1, earned_from=900.0), DP.Portrait("B", 2, earned_from=800.0),
            DP.Portrait("C", 3, refused="shows an injury")]
    plan = DP.plan_drawing(film(), pool)
    assert plan.uncovered and plan.uncovered[0]["start"] == 100
    assert {t["code"]: t["why"] for t in plan.tried} == {"A": "the person is first named at 900 s",
                                                          "B": "the person is first named at 800 s", "C": "shows an injury"}


def test_a_film_without_gaps_needs_no_portrait_for_a_gap_and_the_drawing_may_be_hidden_early():
    pool = [DP.Portrait("A", 1, earned_from=300.0)]
    plan = DP.plan_drawing([pic(0, 600), pic(600, 1400)], pool)
    assert not plan.uncovered and plan.portraits[0]["started"] == 300.0


def test_a_film_too_short_to_finish_even_one_portrait_uses_one_at_the_fastest_speed():
    plan = DP.plan_drawing([pic(0, 150)], [DP.Portrait("A", 1), DP.Portrait("B", 2)])
    assert plan.fits and len(plan.portraits) == 1 and plan.speed == ai_config.drawing_settings().max_speed
    assert plan.portraits[0]["finished"] is None, "it is not finished: the film ends first"


def test_a_black_stretch_of_four_seconds_or_more_is_a_gap_whether_or_not_it_is_flagged_bare():
    shots = [pic(0, 20), {"kind": "black", "start": 20.0, "end": 26.0}, {"kind": "black", "start": 26.0, "end": 28.0}, pic(28, 40)]
    assert [(i.start, i.end, i.mode) for i in DP.intervals_of(shots)] == [(0, 20, "window"), (20.0, 26.0, "full"), (28, 40, "window")]


def test_gaps_one_after_the_other_are_one_gap_so_the_window_grows_once():
    shots = [pic(0, 20), gap(20, 30), gap(30, 41), pic(41, 60)]
    assert [(i.start, i.end, i.mode) for i in DP.intervals_of(shots)] == [(0, 20, "window"), (20, 41, "full"), (41, 60, "window")]


def test_the_settings_of_the_owners_decisions_of_31_october():
    cfg = ai_config.drawing_settings()
    assert cfg.gap_full_seconds == 8.0, "a gap under 8 s is covered by the neighbours, from 8 s the portrait"
    assert cfg.base_seconds == 380.0, "three times faster than the 19 minutes"
    assert cfg.pencil.length == 18.0 and cfg.pencil.thickness == 3.6, "four times bigger than 4.5 x 0.9 px"
