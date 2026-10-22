import json
import uuid
from collections import Counter

from app.db.models import Case, VisualAsset
from app.documentary.visuals import redecide


def _case(db):
    case = Case(canonical_title="Redecide", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


def _asset(db, case, code, verdict, status, **kw):
    db.add(VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", provider="wikimedia",
                       verification_status=status, local_path="x.jpg",
                       **{"asset_role": "evidence", "relevance_tier": 3, **kw},
                       verification_json=json.dumps(verdict)))
    db.commit()


def v(**kw):
    return {"matches_claim": "yes", "period_ok": "yes", "confidence": 0.65, "subject_type": "building",
            "tone_ok": True, "watermark": False, "depicts": "x", "reason": "uncertain", **kw}


def vid(**kw):
    """A video piece's stored describe() verdict — the same rules as a picture plus the
    auditor's own cut/description check."""
    return {"matches_claim": "yes", "period_ok": "yes", "confidence": 0.5, "subject_type": "building",
            "tone_ok": True, "text_or_logo_in_any_frame": False, "graphic_or_sensitive_in_any_frame": False,
            "cut_ok": True, "description_ok": True, "description": "x", "reason": "uncertain", **kw}


def test_stored_verdicts_are_judged_again_under_the_current_rules_without_a_model_call(db_session):
    case = _case(db_session)
    _asset(db_session, case, "A1", v(), "needs_review")                       # place at 0.65: now verified
    # person at 0.65: owner decision 2026-10-22 widened verified_min_confidence to 0.5, so a
    # person also moves now (no person-specific exception is in effect in this test)
    _asset(db_session, case, "A2", v(subject_type="person"), "needs_review")
    _asset(db_session, case, "A3", v(matches_claim="stand_in", subject_type="object", confidence=0.8,
                                     reason="stand_in"), "verified")
    _asset(db_session, case, "A4", {"entity": "x", "match": "likely"}, "rejected",
           verification_confidence=0.0)
    report = redecide.run(db_session, case.id, apply=False)
    assert report["before"] == {"needs_review": 2, "verified": 1, "rejected": 1}
    assert report["after"] == {"verified": 3, "unverified": 1}
    assert {c["asset"] for c in report["changed"]} == {"A1", "A2", "A4"}
    assert db_session.query(VisualAsset).filter_by(case_id=case.id, asset_code="A1").one().verification_status \
        == "needs_review"                                                      # a dry run changes nothing


def test_applying_moves_the_statuses_the_tier_and_the_role(db_session):
    case = _case(db_session)
    _asset(db_session, case, "A1", v(), "needs_review")
    _asset(db_session, case, "A3", v(matches_claim="stand_in", subject_type="object", confidence=0.8,
                                     role="context", reason="stand_in"), "verified",
           asset_role="illustration")
    redecide.run(db_session, case.id, apply=True)
    rows = {a.asset_code: a for a in db_session.query(VisualAsset).filter_by(case_id=case.id)}
    assert rows["A1"].verification_status == "verified"
    assert json.loads(rows["A1"].verification_json)["reason"] is None
    assert (rows["A3"].asset_role, rows["A3"].relevance_tier) == ("context", 4)
    assert json.loads(rows["A3"].verification_json)["reason"] == "context"


def test_a_picture_the_second_pass_verified_or_a_human_decided_is_left_alone(db_session):
    case = _case(db_session)
    _asset(db_session, case, "S1", v(reason="second_pass", confidence=0.5), "verified")
    _asset(db_session, case, "H1", v(confidence=0.4), "rejected", human_override=True)
    report = redecide.run(db_session, case.id, apply=True)
    assert report["changed"] == []
    assert Counter(a.verification_status for a in db_session.query(VisualAsset)) >= Counter(["verified"])


def test_generated_pictures_are_not_judged_again(db_session):
    case = _case(db_session)
    _asset(db_session, case, "G1", {}, "verified")
    db_session.query(VisualAsset).filter_by(asset_code="G1").update({"provider": "generated"})
    db_session.commit()
    assert redecide.run(db_session, case.id, apply=True)["changed"] == []


def test_video_pieces_are_judged_again_just_like_pictures(db_session):
    case = _case(db_session)
    _asset(db_session, case, "V1", vid(), "needs_review")   # 0.5: now verified
    db_session.query(VisualAsset).filter_by(asset_code="V1").update({"asset_type": "video"})
    db_session.commit()
    report = redecide.run(db_session, case.id, apply=True)
    assert {c["asset"] for c in report["changed"]} == {"V1"}
    assert db_session.query(VisualAsset).filter_by(asset_code="V1").one().verification_status == "verified"


def test_a_video_piece_with_a_bad_cut_or_description_stays_rejected_at_any_confidence(db_session):
    case = _case(db_session)
    _asset(db_session, case, "V2", vid(confidence=0.99, cut_ok=False), "rejected")
    db_session.query(VisualAsset).filter_by(asset_code="V2").update({"asset_type": "video"})
    db_session.commit()
    report = redecide.run(db_session, case.id, apply=True)
    assert report["changed"] == []
    row = db_session.query(VisualAsset).filter_by(asset_code="V2").one()
    assert row.verification_status == "rejected"
    assert json.loads(row.verification_json)["reason"] == "cut_not_meaningful"


def test_a_video_piece_whose_duration_probe_failed_is_left_alone(db_session):
    case = _case(db_session)
    _asset(db_session, case, "V3", {"reason": "duration_probe_failed", "error": "x",
                                    "auditor": "video"}, "rejected")
    db_session.query(VisualAsset).filter_by(asset_code="V3").update({"asset_type": "video"})
    db_session.commit()
    report = redecide.run(db_session, case.id, apply=True)
    assert report["changed"] == []
    assert db_session.query(VisualAsset).filter_by(asset_code="V3").one().verification_status == "rejected"


def test_a_video_piece_a_human_decided_is_left_alone(db_session):
    case = _case(db_session)
    _asset(db_session, case, "V4", vid(confidence=0.5), "rejected", human_override=True)
    db_session.query(VisualAsset).filter_by(asset_code="V4").update({"asset_type": "video"})
    db_session.commit()
    report = redecide.run(db_session, case.id, apply=True)
    assert report["changed"] == []
    assert db_session.query(VisualAsset).filter_by(asset_code="V4").one().verification_status == "rejected"
