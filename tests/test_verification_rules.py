from app.documentary.visuals.verification import (
    decide,
    normalize_fact_match,
    normalize_other_entity_match,
)

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


FACTS = {"F120", "F038"}


def scene(**kw):
    return v(**{"matches_claim": "no", "subject_type": "event", "role": "evidence",
                "reveals": ["F120"], **kw})


def test_a_scene_photo_found_for_a_portrait_is_kept_for_the_facts_it_supports():
    out = normalize_fact_match(scene(), FACTS)
    assert out["matches_claim"] == "yes" and out["fact_match"] == ["F120"]
    assert decide(out)[0] == "verified"


def test_a_photo_that_supports_no_known_fact_stays_rejected():
    assert normalize_fact_match(scene(reveals=["F999"]), FACTS)["matches_claim"] == "no"


def test_a_wrong_period_or_a_person_is_never_a_fact_match():
    assert normalize_fact_match(scene(period_ok="no"), FACTS)["matches_claim"] == "no"
    assert normalize_fact_match(scene(subject_type="person"), FACTS)["matches_claim"] == "no"


def test_a_graphic_archival_photo_of_the_case_goes_to_the_owner():
    out = normalize_fact_match(scene(graphic_or_sensitive=True), FACTS)
    assert decide(out) == ("needs_review", 0.85, "graphic_archival")


def test_graphic_material_without_case_facts_is_still_rejected():
    assert decide(v(graphic_or_sensitive=True, role="evidence", reveals=[]))[2] == "graphic_or_sensitive"
    assert decide(v(graphic_or_sensitive=True, role="context", reveals=["F1"]))[2] == "graphic_or_sensitive"


def test_every_close_unchecked_photo_is_vision_checked_not_only_the_ranked_ones(db_session, monkeypatch):
    import asyncio

    from app.db.models import Case, VisualAsset
    from app.documentary import jobs

    case = Case(canonical_title="Verify all", slug="verify-all-test", language="en")
    db_session.add(case)
    db_session.flush()

    def asset(code, tier, **kw):
        fields = {"asset_type": "photo", "local_path": f"x/{code}.jpg", "verification_status": "unverified",
                  "rights_status": "editorial_review_required", "relevance_tier": tier, **kw}
        row = VisualAsset(case_id=case.id, asset_code=code, provider="brave_images", **fields)
        db_session.add(row)
        return row

    asset("T1", 2)
    asset("T2", 3)
    asset("FAR", 5)
    asset("DONE", 2, verification_status="verified")
    asset("BLOCKED", 2, rights_status="do_not_use")
    asset("NOFILE", 2, local_path=None)
    asset("CLIP", 2, asset_type="video")
    db_session.flush()
    seen = []

    async def fake_verify(db, case, assets, entities):
        seen.extend(a.asset_code for a in assets)
        return {"checked": len(assets)}

    monkeypatch.setattr(jobs, "verify_assets", fake_verify)
    asyncio.run(jobs.verify_candidates(db_session, case, {"beats": []}, {"entities": [], "beats": []},
                                       None, None))
    db_session.rollback()
    assert sorted(seen) == ["T1", "T2"]
