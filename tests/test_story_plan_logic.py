"""Part 5a: validation, host slots, enrichment and the 1:1 mapping check."""

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


def test_host_slots_are_one_opening_and_distinct_non_final_mid_slots():
    slots = _plan()["host_slots"]
    assert slots[0] == {"id": "H1", "position": "opening", "before_beat": "act1"}
    mids = [s["after_beat"] for s in slots if s["position"] == "mid"]
    assert 1 <= len(mids) <= 2 and len(set(mids)) == len(mids) and "act5" not in mids


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


def test_host_slot_rules_are_checked():
    plan = _plan()
    plan["host_slots"] = [{"id": "H1", "position": "opening", "before_beat": "act2"},
                          {"id": "H2", "position": "final", "after_beat": "act5"}]
    codes = {e["code"] for e in SP.validate_plan(plan, PACK, 2000)["errors"]}
    assert {"host_opening_must_be_one_slot_before_the_first_beat",
            "host_final_slot_is_off"} <= codes


def _sections(plan, words=None):
    return [{"id": a["id"], "text": " ".join(["w"] * (words or a["target_words"]))}
            for a in plan["acts"]]


def test_mapping_passes_when_ids_order_and_lengths_match():
    plan = _plan()
    assert SP.check_plan_mapping(_sections(plan), plan)["ok"]


def test_mapping_fails_on_wrong_order_lost_structure_and_length():
    plan = _plan()
    swapped = _sections(plan)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    assert SP.check_plan_mapping(swapped, plan)["issues"][0]["code"] == "beats_do_not_match"
    assert SP.check_plan_mapping([{"id": "full", "text": "x"}], plan)["issues"][0]["code"] == "structure_lost"
    long = _sections(plan)
    long[2]["text"] = " ".join(["w"] * 900)
    issue = SP.check_plan_mapping(long, plan)["issues"][0]
    assert (issue["code"], issue["beat"]) == ("beat_length", "act3")
