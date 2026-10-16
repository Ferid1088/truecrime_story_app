"""Part 7c: one test per deterministic guard-rail of the stage direction."""

import copy

import pytest

from app.documentary import stage_direction as SD

LOOKS = {("CAM01_FRONT", "MEDIUM"), ("CAM01_FRONT", "CLOSE"), ("CAM06_WIDE", "WIDE")}
OUTFITS = {"navy_knit", "black_knit", "stone_knit", "olive_overshirt"}
ENDS = [9.0, 18.5]


def good() -> dict:
    return {
        "appearance_id": "a1", "purpose": "highlight_contradiction", "studio_id": "STUDIO_X",
        "emotion": {"primary": "analytical", "intensity": 0.4},
        "camera_plan": [
            {"start_sec": 0, "end_sec": 9.0, "camera": "CAM06_WIDE", "framing": "WIDE",
             "movement": "STATIC"},
            {"start_sec": 9.0, "end_sec": 18.5, "camera": "CAM01_FRONT", "framing": "MEDIUM",
             "movement": "SUBTLE_PUSH_IN"}],
        "outfit_id": "navy_knit", "posture": "P02_SLIGHT_FORWARD", "eye_contact": "MOSTLY_DIRECT",
        "performance_timeline": [
            {"start_sec": 0, "end_sec": 9.0, "action": "direct_eye_contact_still"},
            {"start_sec": 9.0, "end_sec": 18.5, "action": "G02_SMALL_OPEN_PALM"}],
        "lighting": {"variation": "L03_ANALYTICAL_NEUTRAL"},
    }


def check(raw=None, **over):
    kw = dict(voice_seconds=18.5, sentence_ends=ENDS, approved_looks=LOOKS, recent=[],
              sensitive=False, memory_refs={"M1"}, outfit_ids=OUTFITS)
    kw.update(over)
    return {e["code"] for e in SD.validate_direction(SD.StageDirection(**(raw or good())), **kw)}


def test_a_good_direction_passes():
    assert check() == set()


def test_only_approved_looks_and_no_new_camera():
    raw = good()
    raw["camera_plan"][1]["camera"] = "CAM04_LEFT_SIDE"
    assert "look_not_approved" in check(raw)
    raw["camera_plan"][1]["camera"] = "CAM99_TOP"
    assert "unknown_camera" in check(raw)


def test_close_framing_at_most_once_and_only_for_a_reason():
    raw = good()
    raw["camera_plan"][1]["framing"] = "CLOSE"
    assert "close_without_reason" in check(raw)
    raw["emotion"]["primary"] = "reflective"
    assert "close_without_reason" not in check(raw)
    raw["camera_plan"][0].update(camera="CAM01_FRONT", framing="CLOSE")
    assert "too_many_close_shots" in check(raw)


def test_shot_lengths_and_cuts_at_sentence_ends():
    raw = good()
    raw["camera_plan"][0]["end_sec"] = raw["camera_plan"][1]["start_sec"] = 5.0
    raw["performance_timeline"][0]["end_sec"] = raw["performance_timeline"][1]["start_sec"] = 5.0
    codes = check(raw)
    assert {"shot_length", "cut_not_at_a_sentence_end"} <= codes


def test_the_timeline_covers_zero_to_the_voice_without_gaps_or_overlaps():
    raw = good()
    raw["performance_timeline"][1]["start_sec"] = 10.0
    assert "performance_timeline_gap_or_overlap" in check(raw)
    raw = good()
    raw["camera_plan"][1]["end_sec"] = 17.0
    assert "camera_plan_does_not_cover_the_voice" in check(raw)


def test_sensitive_material_gets_soft_lighting_few_gestures_and_a_static_camera():
    codes = check(sensitive=True)
    assert {"sensitive_needs_soft_lighting", "sensitive_needs_static_camera"} <= codes
    raw = good()
    raw["lighting"]["variation"] = "L06_VICTIM_SENSITIVE_SOFT"
    raw["camera_plan"][1]["movement"] = "STATIC"
    raw["performance_timeline"] = [
        {"start_sec": 0, "end_sec": 6, "action": "G02_SMALL_OPEN_PALM"},
        {"start_sec": 6, "end_sec": 12, "action": "G04_SMALL_HEAD_TILT"},
        {"start_sec": 12, "end_sec": 18.5, "action": "G06_SLIGHT_NOD"}]
    assert check(raw, sensitive=True) == {"sensitive_too_many_gestures"}


@pytest.mark.parametrize("emotion", ["SHOCKED", "TERRIFIED", "OUTRAGED"])
def test_forbidden_emotions_need_an_explicit_justification(emotion):
    raw = good()
    raw["emotion"]["primary"] = emotion
    assert "forbidden_emotion" in check(raw)
    raw["emotion"]["justification"] = "the court record states it"
    assert "forbidden_emotion" not in check(raw)


def test_the_outfit_is_not_repeated_within_the_recent_window_unless_continuity_requires_it():
    recent = [{"outfit_id": "navy_knit", "posture": "P03_RELAXED_BACK"}]
    assert "outfit_repeated" in check(recent=recent)
    raw = good()
    raw["continuity_reason"] = "same day as the previous appearance"
    assert "outfit_repeated" not in check(raw, recent=recent)
    assert "outfit_repeated" not in check(recent=[{"outfit_id": "x", "posture": "P03_RELAXED_BACK"}])


def test_the_posture_differs_from_the_previous_appearance():
    assert "posture_same_as_previous" in check(
        recent=[{"outfit_id": "stone_knit", "posture": "P02_SLIGHT_FORWARD"}])


def test_a_memory_reference_must_exist():
    raw = good()
    raw["memory_reference"] = "M99"
    assert "unknown_memory_reference" in check(raw)
    raw["memory_reference"] = "M1"
    assert "unknown_memory_reference" not in check(raw)


def test_sentence_ends_come_from_the_character_alignment():
    alignment = {"characters": list("Hi. Ok?"), "ends": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]}
    assert SD.sentence_ends(alignment) == [0.3, 0.7]
