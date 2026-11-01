"""The planner's check of every card's time: held for its own length within its limits, cut by nothing."""

from __future__ import annotations

from app.documentary.production import card_audit as CA


def image(start, end):
    return {"kind": "image", "start": start, "end": end, "beat_id": "B"}


def graphic(start, end):
    return {"kind": "graphic", "start": start, "end": end, "beat_id": "B"}


def board(start, end, beat="B"):
    return {"kind": "detective_board", "start": start, "end": end, "beat_id": beat}


def test_a_board_that_a_later_edit_moved_and_stretched_gets_the_planners_window_back_and_is_cut_by_nothing():
    shots = [image(0, 104.2), board(104.2, 189.6), graphic(104.6, 120.6), image(135.3, 189.6)]
    CA.audit(shots, [{"beat_id": "B", "start": 129.7, "end": 150.7}])
    cards = {s["kind"]: s for s in shots if s["kind"] == "detective_board"}
    assert (cards["detective_board"]["start"], cards["detective_board"]["end"]) == (129.7, 150.7)
    assert all(not (s["kind"] == "image" and s["start"] < 150.7 < s["end"]) for s in shots), "no picture runs under the board"
    after = sorted(shots, key=lambda s: s["start"])
    assert all(b["start"] >= a["end"] - 0.01 for a, b in zip(after, after[1:])), "nothing overlaps"


def test_a_card_longer_than_its_limit_is_shortened():
    shots = [graphic(0, 40), image(40, 60)]
    done = CA.audit(shots)
    assert shots[0]["end"] == 25.0 and done[0]["fix"] == "shortened"


def test_a_picture_that_starts_inside_a_card_starts_when_the_card_ends():
    shots = [graphic(0, 20), image(5, 60)]
    CA.audit(shots)
    assert shots[1]["start"] == 20 and shots[0]["end"] == 20


def test_a_card_that_the_next_card_cuts_below_its_minimum_is_taken_out():
    shots = [image(0, 10), graphic(10, 30), graphic(18, 40), image(40, 50)]
    done = CA.audit(shots)
    assert [s["kind"] for s in shots].count("graphic") == 1
    assert {d["fix"] for d in done} >= {"cut_by_the_next_card", "removed_too_short"}


def test_a_hole_between_two_cards_is_closed_by_holding_the_first_longer():
    shots = [graphic(0, 15), graphic(20, 40)]
    CA.audit(shots)
    assert shots[0]["end"] == 20


def test_a_clean_timeline_is_left_alone():
    shots = [image(0, 10), graphic(10, 26), image(26, 40), board(40, 60), image(60, 70)]
    before = [dict(s) for s in shots]
    assert CA.audit(shots) == [] and shots == before


def test_a_hole_that_holding_the_first_card_longer_cannot_close_is_a_bare_moment_not_a_stretch_no_shot_covers():
    shots = [board(0, 24.0), graphic(30.5, 51.0)]
    done = CA.audit(shots)
    assert [d["fix"] for d in done] == ["gap_between_cards"]
    hole = [s for s in shots if s["kind"] == "black"]
    assert len(hole) == 1 and hole[0]["bare"] and hole[0]["start"] == 24.0 and hole[0]["end"] == 30.5
    ordered = sorted(shots, key=lambda s: s["start"])
    assert all(abs(b["start"] - a["end"]) < 0.011 for a, b in zip(ordered, ordered[1:], strict=False)), "no hole is left"


def test_the_hosts_video_is_never_moved_or_stretched_to_close_a_hole_the_hole_is_a_bare_moment():
    host = {"kind": "video", "start": 100.0, "end": 117.8, "clip_start": 0.0, "clip_end": 17.8, "beat_id": "B", "host_scene_id": 7}
    card = graphic(0, 20)
    shots = [card, host, image(117.8, 140)]
    done = CA.audit(shots)
    assert (host["start"], host["end"]) == (100.0, 117.8), "the avatar's video stays where its audio is"
    assert any(s["kind"] == "black" and s["start"] == card["end"] and s["end"] == 100.0 for s in shots) and [d["fix"] for d in done] == ["gap_between_cards"]
    video = {"kind": "video", "start": 0.0, "end": 20.0, "clip_start": 0.0, "clip_end": 20.0, "beat_id": "B"}
    ordinary = [video, graphic(40, 60)]
    CA.audit(ordinary)
    assert video["end"] == 40.0, "an ordinary clip may still be held for the hole"


def test_a_short_gap_after_the_hosts_video_is_not_covered_by_freezing_the_avatar():
    from app.documentary.production import picture_rules as PR

    host = {"kind": "video", "start": 0.0, "end": 25.0, "clip_start": 0.0, "clip_end": 25.0, "beat_id": "B", "host_scene_id": 7}
    gap = {"kind": "black", "start": 25.0, "end": 30.0, "beat_id": "B", "bare": True}
    shots = [host, gap, {"kind": "map", "start": 30.0, "end": 60.0, "zoom_video": True, "beat_id": "B"}]
    PR.cover_short_gaps(shots, 60.0)
    assert host["end"] == 25.0 and gap.get("left_black"), "the gap stays black, reported, and the avatar is not frozen"
