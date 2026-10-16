"""Part 4: an editorial_review_required picture (news photo, case-source
image) is usable in a film only with a recorded human approval."""

import pytest

from app.db.models import Case, VisualAsset
from app.documentary.visuals import rights as R
from app.utils import slugify


def _asset(db, rights, code="VIS_T1"):
    case = Case(canonical_title=f"Rights {code}", slug=slugify(f"Rights {code}"), language="en")
    db.add(case)
    db.flush()
    a = VisualAsset(case_id=case.id, asset_code=code, asset_type="photo",
                    rights_status=rights, verification_status="verified")
    db.add(a)
    db.commit()
    return a


def test_editorial_review_picture_is_unusable_until_approved(db_session):
    a = _asset(db_session, "editorial_review_required")
    assert R.allowed(a.rights_status, "preview")      # the profile alone allows it
    assert not R.usable(a, "preview")                  # but no approval is recorded
    assert not R.usable(a, "publish")


def test_approved_editorial_picture_is_usable_in_preview_never_in_publish(client, db_session):
    a = _asset(db_session, "editorial_review_required", "VIS_T2")
    r = client.post(f"/api/visuals/{a.id}/approve-editorial", json={"approved_by": "owner"})
    assert r.status_code == 200
    body = r.json()
    assert body["editorial_approved_by"] == "owner" and body["editorial_approved_at"]
    assert body["usable_preview"] is True
    assert body["usable_publish"] is False             # owner gate: not in published films

    revoked = client.delete(f"/api/visuals/{a.id}/approve-editorial").json()
    assert revoked["usable_preview"] is False and revoked["editorial_approved_at"] is None


@pytest.mark.parametrize("rights", ["owned", "licensed", "public_domain", "creative_commons"])
def test_other_rights_statuses_need_no_approval(db_session, rights):
    a = _asset(db_session, rights, f"VIS_{rights}")
    assert R.usable(a, "preview")


@pytest.mark.parametrize("rights", ["permission_required", "unknown", "do_not_use"])
def test_blocked_rights_statuses_stay_blocked(db_session, rights):
    a = _asset(db_session, rights, f"VIS_{rights}")
    assert not R.usable(a, "preview")


def test_approval_only_applies_to_editorial_review_pictures(client, db_session):
    a = _asset(db_session, "public_domain", "VIS_T3")
    r = client.post(f"/api/visuals/{a.id}/approve-editorial", json={"approved_by": "owner"})
    assert r.status_code == 409
