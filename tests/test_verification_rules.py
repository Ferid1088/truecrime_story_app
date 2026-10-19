from app.documentary.visuals.verification import decide, normalize_other_entity_match

KNOWN = {"borden_house", "lizzie_borden"}


def v(**kw):
    return {"matches_claim": "yes", "period_ok": "yes", "confidence": 0.85, "subject_type": "building",
            "tone_ok": True, "watermark": False, **kw}


def test_a_modern_photo_of_the_real_house_is_kept_for_the_auditor():
    assert decide(v(period_ok="no")) == ("verified", 0.85, "modern_view")


def test_a_wrong_period_person_or_event_is_still_rejected():
    assert decide(v(period_ok="no", subject_type="person"))[0] == "rejected"
    assert decide(v(period_ok="no", subject_type="event"))[0] == "rejected"


def test_a_modern_view_with_low_confidence_needs_review():
    assert decide(v(period_ok="no", confidence=0.5))[0] == "needs_review"


def test_the_front_of_the_house_found_for_its_interior_is_a_house_picture():
    out = normalize_other_entity_match(v(matches_claim="no", entities=["borden_house"]), KNOWN)
    assert decide(out)[0] == "verified" and out["other_entity_match"] == ["borden_house"]


def test_a_different_person_is_never_reassigned():
    out = normalize_other_entity_match(
        v(matches_claim="no", subject_type="person", entities=["lizzie_borden"]), KNOWN)
    assert decide(out)[0] == "rejected"


def test_nothing_known_in_the_picture_stays_rejected():
    assert decide(normalize_other_entity_match(v(matches_claim="no", entities=["x"]), KNOWN))[0] == "rejected"
