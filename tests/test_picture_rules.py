"""The audience can follow the pictures: a named person is shown, a repeat fits its text, no picture is held too long."""

from __future__ import annotations

from types import SimpleNamespace

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


def test_a_black_stretch_under_four_seconds_is_covered_by_the_picture_before_it():
    shots = [image(0, 10, "A"), black(10, 12.5), image(12.5, 30, "B")]
    done = PR.cover_short_gaps(shots)
    assert [s["kind"] for s in shots] == ["image", "image"] and shots[0]["end"] == 12.5 and done[0]["seconds"] == 2.5


def test_when_the_picture_before_is_at_its_limit_the_picture_after_starts_earlier_and_else_the_two_share_the_gap():
    after = [image(0, 27, "A"), black(27, 29), image(29, 40, "B")]
    PR.cover_short_gaps(after)
    assert after[1]["start"] == 27
    shared = [image(0, 25.5, "A"), black(25.5, 28.5), image(28.5, 54, "B")]
    PR.cover_short_gaps(shared)
    assert shared[0]["end"] == shared[1]["start"] == 27.0 and len(shared) == 2


def test_two_black_stretches_next_to_each_other_are_one_gap_and_a_long_gap_or_the_films_fade_is_left_alone():
    shots = [image(0, 10, "A"), black(10, 14), black(14, 19), image(19, 30, "B")]       # 9 s together: a drawing gap, not covered
    assert PR.cover_short_gaps(shots) == [] and [s["kind"] for s in shots] == ["image", "black", "image"]
    fade = [image(0, 10, "A"), black(10, 12)]
    assert PR.cover_short_gaps(fade, 12.0) == [] and fade[-1]["kind"] == "black"
