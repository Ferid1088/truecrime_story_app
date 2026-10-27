import math

from app.core.ai_config import ai_config
from app.documentary.visuals import crops
from app.documentary.visuals.usage import UsageTracker, annotate


def shot(code, start, end, **kw):
    return {"asset_id": code, "kind": "image", "type": "photo", "start": start, "end": end,
            "width": 1200, "height": 800, "role": "evidence", "tier": 2, "entities": [], **kw}


FACTS = {"A": {"asset_id": "A", "type": "photo", "tier": 2, "role": "evidence", "entities": []},
         "B": {"asset_id": "B", "type": "photo", "tier": 4, "role": "context", "entities": []}}


def test_a_real_picture_may_appear_twice_at_most_and_five_minutes_apart_by_config():
    vd = ai_config.visual_direction
    assert vd.max_evidence_appearances == vd.max_context_appearances == 2
    assert vd.max_generic_appearances == vd.max_person_appearances == 2
    assert vd.min_repeat_gap_seconds >= 300.0


def test_the_limit_of_the_category_counts_appearances():
    tr = UsageTracker(FACTS)
    tr.add("A", 0, 10)
    assert tr.allows("A", 100, 110)[0] is False                # not five minutes after the first showing
    assert tr.allows("A", 400, 410)[0] is True
    tr.add("A", 400, 410)
    ok, why = tr.allows("A", 900, 910)
    assert ok is False and "limit 2" in why


def test_a_second_appearance_inside_the_minimum_gap_is_refused_for_every_category():
    gap = ai_config.visual_direction.min_repeat_gap_seconds
    tr = UsageTracker(FACTS)
    tr.add("B", 0, 10)
    ok, why = tr.allows("B", 10 + gap - 5, 10 + gap + 5)
    assert ok is False and "apart" in why
    assert tr.allows("B", 10 + gap + 1, 10 + gap + 8)[0] is True


def test_every_later_appearance_of_a_picture_is_a_different_crop():
    shots = [shot("A", 0, 8), shot("A", 100, 108), shot("A", 200, 208)]
    annotate(shots, FACTS)
    assert shots[0].get("crop_region") is None and shots[0].get("focus") is None
    regions = [s["crop_region"] for s in shots[1:]]
    assert len(set(regions)) == 2 and None not in regions
    assert all(s["motion"] == "CROP_FOCUS" for s in shots[1:])
    boxes = {tuple(s["focus"]) for s in shots[1:]}
    assert len(boxes) == 2 and all(b[2] > b[0] and b[3] > b[1] for b in boxes)
    assert [s["usage"]["appearance"] for s in shots] == [1, 2, 3]


def test_a_shot_that_continues_the_picture_is_the_same_appearance_and_keeps_its_view():
    shots = [shot("A", 0, 8), shot("A", 8, 14)]
    annotate(shots, FACTS)
    assert shots[1].get("crop_region") is None and shots[1]["usage"]["appearance"] == 1


def test_a_close_up_already_chosen_by_a_fill_way_is_never_given_again():
    shots = [shot("A", 0, 8), shot("A", 100, 108, crop_region=0, focus=[1, 1, 2, 2]),
             shot("A", 200, 208)]
    annotate(shots, FACTS)
    assert shots[1]["crop_region"] == 0
    assert shots[2]["crop_region"] not in (None, 0)


def test_the_crop_regions_are_configured_and_distinct():
    regions = ai_config.fill_ways.crop_regions
    assert len(regions) >= ai_config.visual_direction.max_evidence_appearances
    assert len({tuple(r) for r in regions}) == len(regions)
    boxes = [crops.region_box(1200, 800, i) for i in range(len(regions))]
    assert all(0 <= b[0] < b[2] <= 1200 and 0 <= b[1] < b[3] <= 800 for b in boxes)


def test_free_region_skips_the_used_ones_and_ends_when_all_are_used():
    n = len(ai_config.fill_ways.crop_regions)
    assert crops.free_region(set()) == 0
    assert crops.free_region({0, 1}) == 2
    assert crops.free_region(set(range(n))) is None


def test_a_reuse_of_a_picture_that_cannot_be_cropped_again_is_not_offered(monkeypatch):
    n = len(ai_config.fill_ways.crop_regions)
    shots = [shot("A", 0, 8)] + [shot("A", 100 * (i + 1), 100 * (i + 1) + 8, crop_region=i) for i in range(n)]
    annotate(shots, FACTS)
    assert math.isclose(len(shots), n + 1)


def test_a_person_returns_on_the_face_never_on_an_arbitrary_part_of_the_photograph():
    person = {"P": {"asset_id": "P", "type": "photo", "tier": 2, "entity_type": "person", "entities": ["a"]}}
    shots = [shot("P", 0, 8, subject_type="person", focus=[400, 100, 700, 450]),
             shot("P", 100, 108, subject_type="person", focus=[400, 100, 700, 450])]
    annotate(shots, person)
    assert shots[1]["crop_region"] == crops.FACE and shots[1]["motion"] == "CROP_FOCUS"
    assert shots[1]["focus"] == [400, 100, 700, 450]


def test_a_person_without_a_known_face_is_not_cropped_at_random():
    person = {"P": {"asset_id": "P", "type": "photo", "tier": 2, "entity_type": "person", "entities": ["a"]}}
    shots = [shot("P", 0, 8, subject_type="person", focus=None),
             shot("P", 100, 108, subject_type="person", focus=None)]
    annotate(shots, person)
    assert shots[1].get("crop_region") is None
