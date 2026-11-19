"""The case-level visual approval: one owner decision on the shared (English) picture package, required before any language's film is directed or rendered. Automated validation is not
that approval, a changed package closes the gate again, and the approval is the only thing that opens it."""

from __future__ import annotations

import json

import pytest
from conftest import _delete_case_tree
from test_mvp_reference import _candidate, _film

from app.db.models import Case
from app.documentary.mvp import visual_gate as VG
from app.documentary.pipeline.errors import AwaitingPictureReview


@pytest.fixture(autouse=True)
def _clean_cases(db_session):
    yield
    for stale in db_session.query(Case).filter(Case.slug.like("ref-%") | Case.slug.like("no-plan-gate%")).all():
        _delete_case_tree(db_session, stale.id)


def test_with_no_english_plan_there_is_nothing_to_approve(db_session):
    case = Case(canonical_title="No plan", slug="no-plan-gate", language="en")
    db_session.add(case)
    db_session.commit()
    assert VG.status(db_session, case.id) == {"state": "none", "open": False, "reason": "The English picture plan is not made yet."}
    with pytest.raises(VG.VisualApprovalError):
        VG.approve(db_session, case.id)
    with pytest.raises(AwaitingPictureReview, match="case's visual approval"):
        VG.guard(db_session, case.id, "de")


def test_automatic_validation_alone_never_opens_the_gate(db_session):
    case, english = _film(db_session, "en", [100, 100])
    _candidate(db_session, case, english, "B01", "AUTO_APPROVED", "approved")          # approved by the automated checks only
    assert VG.status(db_session, case.id)["state"] == "awaiting_owner" and not VG.status(db_session, case.id)["open"]
    for language in ("en", "fa", "de"):
        with pytest.raises(AwaitingPictureReview, match="case's visual approval"):
            VG.guard(db_session, case.id, language)


def test_the_owners_approval_opens_the_gate_for_every_language_and_counts_as_review_one(db_session):
    case, english = _film(db_session, "en", [100, 100])
    _candidate(db_session, case, english, "B01", "A1", "approved")
    state = VG.approve(db_session, case.id, "looks right")
    assert state["state"] == "approved" and state["open"] and state["note"] == "looks right"
    assert json.loads(english.validation_json)["mvp"]["reviews"] == 1
    for language in ("en", "fa", "de", "tr"):
        VG.guard(db_session, case.id, language)                                      # none of them stops


def test_a_changed_package_closes_the_gate_until_the_owner_approves_it_again(db_session):
    case, english = _film(db_session, "en", [100, 100])
    _candidate(db_session, case, english, "B01", "A1", "approved")
    VG.approve(db_session, case.id)
    _candidate(db_session, case, english, "B02", "A2", "approved")                 # a picture is added after the approval
    changed = VG.status(db_session, case.id)
    assert changed["state"] == "changed" and not changed["open"]
    with pytest.raises(AwaitingPictureReview):
        VG.guard(db_session, case.id, "de")
    assert VG.approve(db_session, case.id)["open"]


def test_a_picture_the_owner_rejects_after_approving_also_closes_the_gate(db_session):
    from app.db.models import MvpCandidate

    case, english = _film(db_session, "en", [100])
    _candidate(db_session, case, english, "B01", "A1", "approved")
    VG.approve(db_session, case.id)
    db_session.query(MvpCandidate).update({"final_status": "rejected"})
    db_session.commit()
    assert VG.status(db_session, case.id)["state"] == "changed"


def test_withdrawing_the_approval_closes_the_gate(db_session):
    case, english = _film(db_session, "en", [100])
    _candidate(db_session, case, english, "B01", "A1", "approved")
    VG.approve(db_session, case.id)
    assert VG.withdraw(db_session, case.id)["state"] == "awaiting_owner"


def test_the_approval_belongs_to_its_case(db_session):
    case, english = _film(db_session, "en", [100])
    _candidate(db_session, case, english, "B01", "A1", "approved")
    other, other_plan = _film(db_session, "en", [100])
    _candidate(db_session, other, other_plan, "B01", "A1", "approved")
    VG.approve(db_session, case.id)
    assert VG.status(db_session, other.id)["state"] == "awaiting_owner"


def test_the_api_reads_approves_and_withdraws(db_session, client):
    case, english = _film(db_session, "en", [100])
    _candidate(db_session, case, english, "B01", "A1", "approved")
    url = f"/api/cases/{case.id}/mvp/visual-approval"
    assert client.get(url).json()["state"] == "awaiting_owner"
    assert client.post(url, json={"note": "ok"}).json()["open"] is True
    assert client.get(url).json()["state"] == "approved"
    assert client.delete(url).json()["state"] == "awaiting_owner"
