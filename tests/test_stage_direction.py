"""Part 7c: one test per deterministic guard-rail of the stage direction."""

import copy

import pytest

from app.documentary import stage_direction as SD

LOOKS = {("SEATED", "CAM01_FRONT", "MEDIUM"), ("SEATED", "CAM01_FRONT", "CLOSE"),
         ("SEATED", "CAM06_WIDE", "WIDE"), ("STANDING", "CAM01_FRONT", "WIDE")}
OUTFIT = "navy_knit"
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
        "outfit_id": "navy_knit", "stance": "SEATED", "posture": "P05_HANDS_LOOSELY_JOINED",
        "eye_contact": "MOSTLY_DIRECT",
        "performance_timeline": [
            {"start_sec": 0, "end_sec": 6.0, "action": "direct_eye_contact_still"},
            {"start_sec": 6.0, "end_sec": 8.0, "action": "G02_SMALL_OPEN_PALM", "hands": "both"},
            {"start_sec": 8.0, "end_sec": 18.5, "action": "hands_rest_direct_delivery"}],
        "lighting": {"variation": "L03_ANALYTICAL_NEUTRAL"},
    }


def check(raw=None, **over):
    kw = dict(voice_seconds=18.5, sentence_ends=ENDS, approved_looks=LOOKS, recent=[],
              sensitive=False, memory_refs={"M1"}, outfit_id=OUTFIT, language="en")
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
        {"start_sec": 0, "end_sec": 2.5, "action": "hands_rest"},
        {"start_sec": 2.5, "end_sec": 4.5, "action": "G02_SMALL_OPEN_PALM"},
        {"start_sec": 4.5, "end_sec": 6.5, "action": "G04_SMALL_HEAD_TILT"},
        {"start_sec": 6.5, "end_sec": 8.5, "action": "G06_SLIGHT_NOD"},
        {"start_sec": 8.5, "end_sec": 18.5, "action": "hands_rest"}]
    assert check(raw, sensitive=True) == {"sensitive_too_many_gestures"}


@pytest.mark.parametrize("emotion", ["SHOCKED", "TERRIFIED", "OUTRAGED"])
def test_forbidden_emotions_need_an_explicit_justification(emotion):
    raw = good()
    raw["emotion"]["primary"] = emotion
    assert "forbidden_emotion" in check(raw)
    raw["emotion"]["justification"] = "the court record states it"
    assert "forbidden_emotion" not in check(raw)


def test_the_outfit_is_the_videos_and_never_another():
    raw = good()
    raw["outfit_id"] = "black_knit"
    assert "wrong_outfit" in check(raw)
    assert "wrong_outfit" not in check(good())
    # the same outfit in the scenes of one video is the rule, not a repetition
    assert check(recent=[{"outfit_id": "navy_knit", "posture": "P03_RELAXED_BACK"}]) == set()


def test_one_stance_per_scene_that_fits_its_posture_and_its_looks():
    raw = good()
    raw["stance"] = "STANDING"                      # P02 is a seated posture, and no standing medium look
    codes = check(raw)
    assert {"posture_does_not_fit_the_stance", "look_not_approved"} <= codes
    raw["posture"] = "P06_STANDING_FRONT"
    raw["camera_plan"] = [{"start_sec": 0, "end_sec": 18.5, "camera": "CAM01_FRONT",
                           "framing": "WIDE", "movement": "STATIC"}]
    assert check(raw) == set()
    raw["stance"] = "LYING"
    assert "unknown_stance" in check(raw)


def test_body_language_uses_both_hands_never_repeats_back_to_back_and_respects_the_culture():
    def with_actions(*actions):
        raw = good()
        raw["performance_timeline"] = [
            {"start_sec": i * 6.0, "end_sec": (i + 1) * 6.0 if i < len(actions) - 1 else 18.5,
             "action": a, "hands": h} for i, (a, h) in enumerate(actions)]
        return raw

    one = with_actions(("G02_SMALL_OPEN_PALM", "right"), ("G06_SLIGHT_NOD", "both"),
                       ("G04_SMALL_HEAD_TILT", "right"))
    assert "one_hand_only" not in check(one)                 # a gesture with both hands is there
    only_right = with_actions(("G02_SMALL_OPEN_PALM", "right"), ("G04_SMALL_HEAD_TILT", "right"),
                              ("G06_SLIGHT_NOD", "right"))
    for a in only_right["performance_timeline"]:
        a["hands"] = "right"
    assert "one_hand_only" in check(only_right)
    left_and_right = with_actions(("G02_SMALL_OPEN_PALM", "left"), ("G04_SMALL_HEAD_TILT", "left"),
                                  ("G03_SUBTLE_TWO_HAND_EXPLANATION", "right"))
    assert "one_hand_only" not in check(left_and_right)
    twice = with_actions(("G02_SMALL_OPEN_PALM", "both"), ("G02_SMALL_OPEN_PALM", "both"),
                         ("G06_SLIGHT_NOD", "both"))
    assert "gesture_repeated_back_to_back" in check(twice)
    other_hand = with_actions(("G02_SMALL_OPEN_PALM", "left"), ("G02_SMALL_OPEN_PALM", "right"),
                              ("G06_SLIGHT_NOD", "both"))
    assert "gesture_repeated_back_to_back" not in check(other_hand)
    thumbs = with_actions(("G10_THUMBS_UP", "both"), ("G06_SLIGHT_NOD", "both"),
                          ("G04_SMALL_HEAD_TILT", "both"))
    assert "gesture_forbidden_in_language" not in check(thumbs, language="en")
    for language in ("fa", "ar"):
        assert "gesture_forbidden_in_language" in check(thumbs, language=language)
    pointing = with_actions(("G09_POINT_AT_VIEWER", "right"), ("G06_SLIGHT_NOD", "both"),
                            ("G04_SMALL_HEAD_TILT", "both"))
    for language in ("en", "de", "fa", "ar"):
        assert "gesture_forbidden_in_language" in check(pointing, language=language)
    odd = with_actions(("G02_SMALL_OPEN_PALM", "elbow"), ("G06_SLIGHT_NOD", "both"),
                       ("G04_SMALL_HEAD_TILT", "both"))
    assert "unknown_hands" in check(odd)


def test_the_posture_differs_from_the_previous_appearance():
    assert "posture_same_as_previous" in check(
        recent=[{"outfit_id": "stone_knit", "posture": "P05_HANDS_LOOSELY_JOINED"}])


def test_a_memory_reference_must_exist():
    raw = good()
    raw["memory_reference"] = "M99"
    assert "unknown_memory_reference" in check(raw)
    raw["memory_reference"] = "M1"
    assert "unknown_memory_reference" not in check(raw)


def test_sentence_ends_come_from_the_character_alignment():
    alignment = {"characters": list("Hi. Ok?"), "ends": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]}
    assert SD.sentence_ends(alignment) == [0.3, 0.7]


def test_the_execution_view_marks_what_is_executed_and_what_is_planned_only():
    view = {v["field"]: v for v in SD.execution_view(good())}
    assert view["camera"]["shown_as"] == "EXECUTED_BY_LOOK_CHOICE"
    assert view["gestures"]["shown_as"] == "EXECUTED_BY_PROVIDER" and "G02" in view["gestures"]["value"]
    assert view["movement"]["shown_as"] == "PLANNED_ONLY" and view["lighting"]["executed"] is False
    assert view["performance_timeline"]["shown_as"] == "PLANNED_ONLY"
    assert SD.execution_view(None) == []


# ---------------------------------------------------------------------------
# a calm host: hands rest, a gesture only at real emphasis
# ---------------------------------------------------------------------------


def _calm(**timeline):
    raw = good()
    raw["performance_timeline"] = timeline["actions"]
    return raw


REST = {"action": "hands_rest"}


def test_a_calm_direction_with_one_small_gesture_passes():
    assert check() == set()


def test_a_busy_posture_needs_a_continuity_reason():
    raw = good()
    raw["posture"] = "P02_SLIGHT_FORWARD"
    assert "posture_not_calm" in check(raw)
    raw["continuity_reason"] = "the previous scene leaned forward"
    assert "posture_not_calm" not in check(raw)


def test_only_small_gestures_are_allowed():
    raw = _calm(actions=[{**REST, "start_sec": 0, "end_sec": 6},
                         {"action": "G03_SUBTLE_TWO_HAND_EXPLANATION", "hands": "both", "start_sec": 6, "end_sec": 8},
                         {**REST, "start_sec": 8, "end_sec": 18.5}])
    assert "gesture_not_calm" in check(raw)


def test_at_most_one_hand_gesture_per_ten_seconds_and_never_in_a_sentences_first_second():
    close = _calm(actions=[{**REST, "start_sec": 0, "end_sec": 3},
                           {"action": "G02_SMALL_OPEN_PALM", "hands": "both", "start_sec": 3, "end_sec": 5},
                           {**REST, "start_sec": 5, "end_sec": 8},
                           {"action": "G02_SMALL_OPEN_PALM", "hands": "both", "start_sec": 8, "end_sec": 10},
                           {**REST, "start_sec": 10, "end_sec": 18.5}])
    assert {"hand_gestures_too_close", "too_many_hand_gestures"} <= check(close) | {"too_many_hand_gestures"}
    assert "hand_gestures_too_close" in check(close)
    early = _calm(actions=[{"action": "G02_SMALL_OPEN_PALM", "hands": "both", "start_sec": 9.4, "end_sec": 11.0},
                           {**REST, "start_sec": 11.0, "end_sec": 18.5},
                           {**REST, "start_sec": 0, "end_sec": 9.4}])
    assert "gesture_in_a_sentences_first_second" in check(early)       # a sentence ends at 9.0


def test_a_long_hand_gesture_is_rejected():
    long = _calm(actions=[{**REST, "start_sec": 0, "end_sec": 5.0},
                          {"action": "G02_SMALL_OPEN_PALM", "hands": "both", "start_sec": 5.0, "end_sec": 9.5},
                          {**REST, "start_sec": 9.5, "end_sec": 18.5}])
    assert "hand_gesture_too_long" in check(long)


def test_the_count_limit_follows_the_length_of_the_speech():
    # 6 a minute: a 40 s appearance may have 4, a 10 s one still has one
    many = []
    for i in range(5):
        many += [{**REST, "start_sec": i * 8.0, "end_sec": i * 8.0 + 5.0},
                 {"action": "G02_SMALL_OPEN_PALM", "hands": "both", "start_sec": i * 8.0 + 5.0, "end_sec": i * 8.0 + 7.0},
                 {**REST, "start_sec": i * 8.0 + 7.0, "end_sec": i * 8.0 + 8.0}]
    codes = {e["code"] for e in SD.calm_errors(
        SD.StageDirection(**{**good(), "performance_timeline": many}), voice_seconds=40.0, sentence_ends=[])}
    assert "too_many_hand_gestures" in codes
