"""Part 5a: validation, enrichment and the 1:1 mapping check."""

from app.services import story_plans as SP

PACK = {"facts": [
    {"id": "F001", "people": ["Anna Weber"]}, {"id": "F002", "people": ["Anna Weber", "Karl"]},
    {"id": "F003", "people": []}], "timeline": [{"id": "T001"}], "contradictions": [{"id": "C001"}]}


def _plan(**over):
    plan = {"acts": [
        {"id": "act1", "title": "Absence", "purpose": "open", "target_words": 400,
         "evidence_ids": ["F001"], "do_not_reveal": ["F003"]},
        {"id": "act2", "title": "World", "purpose": "people", "target_words": 400,
         "evidence_ids": ["F001", "F002"]},
        {"id": "act3", "title": "Search", "purpose": "police", "target_words": 400,
         "evidence_ids": ["T001"]},
        {"id": "act4", "title": "Myth", "purpose": "claims", "target_words": 400,
         "evidence_ids": ["C001", "F003"]},
        {"id": "act5", "title": "Remains", "purpose": "end", "target_words": 400,
         "evidence_ids": []}]}
    plan.update(over)
    return SP.enrich(plan, PACK)


def test_enrich_marks_what_each_beat_reveals_and_whom_it_introduces():
    acts = _plan()["acts"]
    assert acts[0]["reveals"] == ["F001"] and acts[0]["introduces_people"] == ["Anna Weber"]
    assert acts[1]["reveals"] == ["F002"] and acts[1]["introduces_people"] == ["Karl"]


def test_a_good_plan_is_valid_and_bad_ones_are_named():
    assert SP.validate_plan(_plan(), PACK, 2000)["status"] == "valid"
    bad = _plan()
    bad["acts"][1]["evidence_ids"] = ["F999"]
    bad["acts"][0]["do_not_reveal"] = ["F001"]
    bad["acts"][2]["target_words"] = 0
    codes = {e["code"] for e in SP.validate_plan(bad, PACK, 2000)["errors"]}
    assert {"unknown_evidence_ids", "beat_reveals_what_it_reserves",
            "beat_without_target_length"} <= codes


def test_lengths_that_miss_the_film_length_make_the_plan_invalid():
    report = SP.validate_plan(_plan(), PACK, 4000)
    assert report["status"] == "invalid"
    assert report["errors"][0]["code"] == "beats_do_not_add_up_to_the_film_length"
