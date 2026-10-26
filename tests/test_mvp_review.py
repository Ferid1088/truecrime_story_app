import pytest
from fastapi.testclient import TestClient
from uuid import uuid4

from app.db.models import Case
from app.documentary.mvp import review as RV
from app.main import app


def _case(db):
    case = Case(canonical_title="Review proof", slug=f"rv-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    return case


def _to_validating(db, case, tmp_path):
    review = RV.open_review(db, case.id, "en")
    RV.advance(db, review, "PRODUCING")
    RV.advance(db, review, "VALIDATING")
    film = tmp_path / "film.mp4"
    film.write_bytes(b"film-bytes")
    return review, film


def test_state_machine_matches_the_configured_states():
    assert set(RV.states()) == set(RV.TRANSITIONS)


def test_a_failed_validation_never_reaches_the_owner(db_session, tmp_path):
    review, film = _to_validating(db_session, _case(db_session), tmp_path)
    RV.submit_for_review(db_session, review, production_script_id=None, render_path=film,
                         validation={"review_ready": False, "blocking": ["black"]})
    assert review.state == "PRODUCING"
    with pytest.raises(RV.ReviewError):
        RV.advance(db_session, review, "AWAITING_OWNER_REVIEW")


def test_passed_validation_waits_for_the_owner_and_blocks_further_production(db_session, tmp_path):
    case = _case(db_session)
    review, film = _to_validating(db_session, case, tmp_path)
    RV.submit_for_review(db_session, review, production_script_id=None, render_path=film,
                         validation={"review_ready": True})
    assert review.state == "AWAITING_OWNER_REVIEW"
    with pytest.raises(RV.ReviewError, match="awaiting owner review"):
        RV.guard_no_progress_past_review(db_session, case.id, "en")
    with pytest.raises(RV.ReviewError):
        RV.advance(db_session, review, "APPROVED")        # only the owner decides
    assert not RV.english_approved(db_session, case.id)


def test_owner_decision_pins_the_checksum_and_a_changed_file_voids_it(db_session, tmp_path):
    case = _case(db_session)
    review, film = _to_validating(db_session, case, tmp_path)
    RV.submit_for_review(db_session, review, production_script_id=None, render_path=film,
                         validation={"review_ready": True})
    with pytest.raises(RV.ReviewError, match="different render"):
        RV.decide(db_session, review, "APPROVED", render_sha256="0" * 64)
    RV.decide(db_session, review, "APPROVED", "good", review.render_sha256)
    assert RV.english_approved(db_session, case.id)
    film.write_bytes(b"re-rendered")
    assert RV.invalidate_if_changed(db_session, review, film) is True
    assert not RV.english_approved(db_session, case.id)


def test_revision_required_returns_to_production(db_session, tmp_path):
    review, film = _to_validating(db_session, _case(db_session), tmp_path)
    RV.submit_for_review(db_session, review, production_script_id=None, render_path=film,
                         validation={"review_ready": True})
    RV.decide(db_session, review, "REVISION_REQUIRED", "pace too slow")
    RV.advance(db_session, review, "PRODUCING")
    assert review.state == "PRODUCING"


def test_decision_endpoint_conflicts_outside_the_review_state(db_session):
    case = _case(db_session)
    review = RV.open_review(db_session, case.id, "en")
    client = TestClient(app)
    r = client.post(f"/api/mvp/reviews/{review.id}/decision", json={"decision": "APPROVED"})
    assert r.status_code == 409
