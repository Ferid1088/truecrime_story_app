"""The audience can follow the pictures: a named person is shown, a repeat fits its text, no picture is held too long."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.documentary.production import picture_rules as PR


def image(start, end, code, entities=(), beat="B01"):
    return {"kind": "image", "start": start, "end": end, "asset_id": code, "entities": list(entities), "beat_id": beat}


def sentence(start, end, text):
    return {"start": start, "end": end, "display": text}


def photo(code):
    return SimpleNamespace(asset_code=code)


def test_a_name_is_found_by_its_full_name_or_last_word():
    assert PR.mentions("Alleigha Botner testified.", "alleigha_botner")
    assert PR.mentions("Botner said no.", "alleigha_botner")
    assert not PR.mentions("Caleb said no.", "alleigha_botner")


def test_the_first_time_the_text_names_a_person_her_photo_is_shown():
    shots = [image(0, 60, "VIS_A", ["caleb_flynn"])]
    sentences = [sentence(20, 30, "Alleigha Botner testified in court.")]
    pool = [photo("VIS_B")]
    made = []

    def make(target, asset, start, end, why):
        made.append(why)
        return {**target, "asset_id": asset.asset_code, "entities": ["alleigha_botner"], "start": start, "end": end}

    done = PR.cover_people(shots, sentences, {"alleigha_botner": ["VIS_B"]}, lambda beat: pool, make, lambda t: "B01",
                           lambda a: ["alleigha_botner"])
    assert done[0]["first_mention"] and done[0]["person"] == "alleigha_botner"
    assert made == ["the first time the text names this person"]
    on_screen = [s for s in shots if s["start"] <= 25 < s["end"]]
    assert on_screen[0]["asset_id"] == "VIS_B"
    assert [s["start"] for s in sorted(shots, key=lambda s: s["start"])][0] == 0, "the picture before stays"
    assert max(s["end"] for s in shots) == 60 and all(b["start"] >= a["end"] - 1e-6 for a, b in
                                                      zip(sorted(shots, key=lambda s: s["start"]),
                                                          sorted(shots, key=lambda s: s["start"])[1:]))


def test_a_person_already_on_screen_or_without_a_photo_changes_nothing():
    shots = [image(0, 60, "VIS_B", ["alleigha_botner"])]
    sentences = [sentence(20, 30, "Botner testified.")]
    assert PR.cover_people(shots, sentences, {"alleigha_botner": ["VIS_B"]}, lambda beat: [photo("VIS_B")],
                           lambda *a: {}, lambda t: "B01", lambda a: ["alleigha_botner"]) == []
    other = [image(0, 60, "VIS_A", ["caleb_flynn"])]
    assert PR.cover_people(other, sentences, {"alleigha_botner": []}, lambda beat: [], lambda *a: {}, lambda t: "B01",
                           lambda a: []) == []
    assert len(other) == 1


def test_a_repeated_picture_that_does_not_fit_the_text_becomes_a_bare_moment():
    shots = [image(0, 10, "VIS_DOG", ["rex_the_dog"]), image(50, 60, "VIS_DOG", ["rex_the_dog"])]
    sentences = [sentence(50, 60, "Caleb had installed an AI app to create a love song.")]
    done = PR.drop_unrelated_repeats(shots, sentences, lambda code: {"police", "canine"})
    assert done[0]["fix"] == "unrelated_repeat_removed"
    assert [s["kind"] for s in sorted(shots, key=lambda s: s["start"])] == ["image", "black"]


def test_a_repeat_that_names_its_person_or_shares_a_word_stays():
    shots = [image(0, 10, "VIS_A", ["caleb_flynn"]), image(50, 60, "VIS_A", ["caleb_flynn"])]
    assert PR.drop_unrelated_repeats(shots, [sentence(50, 60, "Flynn said nothing.")], lambda code: set()) == []
    assert PR.drop_unrelated_repeats(shots, [sentence(50, 60, "The garage door stayed open.")], lambda code: {"garage"}) == []


def test_no_picture_stays_on_screen_longer_than_the_limit_the_rest_is_a_bare_moment():
    shots = [image(0, 89, "VIS_A")]
    done = PR.limit_picture_seconds(shots)
    assert shots[0]["end"] == 27.0 and shots[1]["kind"] == "black" and shots[1]["start"] == 27.0 and shots[1]["end"] == 89
    assert done[0]["was"] == 89.0
    assert PR.limit_picture_seconds([image(0, 27, "VIS_B")]) == []


def test_the_photo_that_fits_the_words_is_chosen_for_a_named_person():
    shots = [image(0, 60, "VIS_A", ["caleb_flynn"])]
    sentences = [sentence(20, 30, "Alleigha Botner testified about the messages in court.")]
    pool = [photo("VIS_PORTRAIT"), photo("VIS_COURT")]
    desc = {"VIS_PORTRAIT": "a smiling woman outdoors", "VIS_COURT": "a woman who testified in court at the witness stand"}
    PR.cover_people(shots, sentences, {"alleigha_botner": ["VIS_PORTRAIT", "VIS_COURT"]}, lambda beat: pool,
                    lambda target, a, start, end, why: {**target, "asset_id": a.asset_code, "entities": ["alleigha_botner"],
                                                        "start": start, "end": end},
                    lambda t: "B01", lambda a: ["alleigha_botner"],
                    lambda a, text: len(PR.tokens(text) & PR.tokens(desc[a.asset_code])))
    assert next(s for s in shots if s["start"] <= 25 < s["end"])["asset_id"] == "VIS_COURT"


def test_a_picture_stretched_over_the_hole_a_removed_card_left_is_cut_again_at_the_limit():
    from app.documentary.production import card_audit as CA

    shots = [image(0, 20, "VIS_A"), {"kind": "graphic", "start": 20.0, "end": 30.0}, image(60, 80, "VIS_B")]
    CA.audit(shots)                                    # the card is too short: taken out, and the picture before it holds the hole
    assert shots[0]["end"] - shots[0]["start"] > 27
    PR.limit_picture_seconds(shots)
    assert shots[0]["end"] - shots[0]["start"] <= 27 and any(s.get("bare") for s in shots)


def black(start, end):
    return {"kind": "black", "start": start, "end": end}


def card(start, end, kind="graphic"):
    return {"kind": kind, "start": start, "end": end}


def test_a_black_stretch_under_eight_seconds_is_covered_by_the_picture_before_it_whatever_its_length():
    shots = [image(0, 10, "A"), black(10, 12.5), image(12.5, 30, "B")]
    done = PR.cover_short_gaps(shots)
    assert [s["kind"] for s in shots] == ["image", "image"] and shots[0]["end"] == 12.5 and done[0]["seconds"] == 2.5
    long_hold = [image(0, 27, "A"), black(27, 33), image(33, 50, "B")]          # no 27 s limit when a neighbour covers a gap
    PR.cover_short_gaps(long_hold)
    assert long_hold[0]["end"] == 33 and long_hold[0]["gap_cover_seconds"] == 6 and len(long_hold) == 2


def test_two_cards_around_a_gap_the_card_before_is_held_longer_until_the_gap_is_covered():
    shots = [card(0, 20), black(20, 26), card(26, 45, "detective_board")]
    PR.cover_short_gaps(shots)
    assert [s["kind"] for s in shots] == ["graphic", "detective_board"] and shots[0]["end"] == 26 and shots[1]["start"] == 26


def test_a_card_before_and_a_picture_after_share_the_gap_half_each():
    shots = [card(0, 20), black(20, 26), image(26, 50, "B")]
    PR.cover_short_gaps(shots)
    assert shots[0]["end"] == shots[1]["start"] == 23.0 and len(shots) == 2 and shots[1]["gap_cover_seconds"] == 3.0
    assert "gap_cover_seconds" not in shots[0], "(a card has no picture limit to note)"


def test_a_clip_before_cannot_be_held_so_the_picture_after_starts_earlier_and_with_a_card_after_the_clips_last_frame_is_held():
    clip_then_picture = [{"kind": "video", "start": 0.0, "end": 10.0}, black(10, 14), image(14, 40, "B")]
    PR.cover_short_gaps(clip_then_picture)
    assert clip_then_picture[1]["start"] == 10 and len(clip_then_picture) == 2
    clip_then_card = [{"kind": "video", "start": 0.0, "end": 10.0}, black(10, 14), card(14, 30)]
    PR.cover_short_gaps(clip_then_card)
    assert [s["kind"] for s in clip_then_card] == ["video", "graphic"] and clip_then_card[0]["end"] == 14.0
    assert clip_then_card[0]["frozen_tail_seconds"] == 4.0, "the clip's last frame is held for the gap (the renderer does that once a shot outlasts its clip)"
    map_then_card = [{"kind": "map", "zoom_video": True, "start": 0.0, "end": 10.0}, black(10, 14), card(14, 30)]
    left = PR.cover_short_gaps(map_then_card)
    assert [s["kind"] for s in map_then_card] == ["map", "black", "graphic"], "a zoom map has its own length: nothing can take the gap, it is never drawn"
    assert left == [{"fix": "short_gap_left", "start": 10.0, "seconds": 4.0, "before": "map", "after": "graphic"}]


def test_two_black_stretches_next_to_each_other_are_one_gap_and_a_gap_of_eight_seconds_or_the_films_fade_is_left_alone():
    shots = [image(0, 10, "A"), black(10, 14), black(14, 19), image(19, 30, "B")]       # 9 s together: a drawing gap, not covered
    assert PR.cover_short_gaps(shots) == [] and [s["kind"] for s in shots] == ["image", "black", "image"]
    seven = [image(0, 10, "A"), black(10, 14), black(14, 17), image(17, 30, "B")]       # 7 s together: one gap, covered by the neighbour
    PR.cover_short_gaps(seven)
    assert [s["kind"] for s in seven] == ["image", "image"] and seven[0]["end"] == 17
    fade = [image(0, 10, "A"), black(10, 12)]
    assert PR.cover_short_gaps(fade, 12.0) == [] and fade[-1]["kind"] == "black"


def test_a_picture_between_two_gaps_is_held_fifteen_seconds_the_time_taken_half_from_each_gap():
    shots = [image(0, 20, "A"), black(20, 38), image(38, 44.5, "B"), black(44.5, 112.5), image(112.5, 130, "C")]
    done = PR.hold_pictures_between_gaps(shots)
    pic = next(s for s in shots if s.get("asset_id") == "B")
    assert pic["end"] - pic["start"] == pytest.approx(15.0) and done == [{"fix": "picture_held_between_gaps", "start": 33.8, "from": 6.5, "to": 15.0}]
    blacks = [s for s in shots if s["kind"] == "black"]
    assert [round(b["end"] - b["start"], 2) for b in blacks] == [13.75, 63.75], "4.25 s from each gap"
    assert shots[0]["end"] == 20 and shots[-1]["start"] == 112.5, "nothing else moves"


def test_a_gap_that_is_used_up_gives_what_it_has_and_the_rest_comes_from_the_other_and_the_gaps_are_judged_again_by_the_general_rules():
    shots = [image(0, 20, "A"), black(20, 23), image(23, 29, "B"), black(29, 59), image(59, 80, "C")]
    PR.hold_pictures_between_gaps(shots)
    pic = next(s for s in shots if s.get("asset_id") == "B")
    assert pic["end"] - pic["start"] == pytest.approx(15.0) and [s["kind"] for s in shots] == ["image", "image", "black", "image"]
    short = [image(0, 20, "A"), black(20, 28.5), image(28.5, 33, "B"), black(33, 80), image(80, 90, "C")]
    PR.hold_pictures_between_gaps(short)
    done = PR.cover_short_gaps(short)
    assert [d["fix"] for d in done] == ["short_gap_covered"] and [s["kind"] for s in short].count("black") == 1, "the 4.2 s that is left is covered by a neighbour"


def test_a_picture_that_is_long_enough_or_not_between_two_gaps_or_a_clip_is_not_touched():
    long_enough = [image(0, 10, "A"), black(10, 30), image(30, 45, "B"), black(45, 70), image(70, 80, "C")]
    assert PR.hold_pictures_between_gaps(long_enough) == []
    next_to_card = [black(0, 20), image(20, 25, "B"), card(25, 40)]
    assert PR.hold_pictures_between_gaps(next_to_card) == []
    clip = [black(0, 20), {"kind": "video", "start": 20.0, "end": 25.0}, black(25, 70)]
    assert PR.hold_pictures_between_gaps(clip) == []
