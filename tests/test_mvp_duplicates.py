"""The duplicate filter after research: the same picture found twice is kept once, the owner's decisions stand."""

from __future__ import annotations

import json
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.db.models import Case, EditorialBlueprint, MvpCandidate, MvpRequirement, StoryVersion, VisualAsset, VisualPlan
from app.documentary.mvp import duplicates as DUP
from app.documentary.mvp import verify as VF
from app.main import app

SAME = "f0f0f0f0f0f0f0f0"
NEAR = "f0f0f0f0f0f0f0f3"          # 2 bits away
OTHER = "0f0f0f0f0f0f0f0f"


@pytest.fixture
def pictures(tmp_path):
    """Image files: the same picture in two sizes, and a different picture."""
    gradient = np.linspace(0, 255, 256, dtype=np.uint8)
    base = Image.fromarray(np.tile(gradient, (256, 1)) * 1).convert("RGB")
    different = Image.fromarray(np.random.default_rng(3).integers(0, 255, (256, 256), dtype=np.uint8)).convert("RGB")
    paths = {}
    for name, img in (("large", base), ("small", base.resize((128, 128))), ("other", different)):
        paths[name] = str(tmp_path / f"{name}.png")
        img.save(paths[name])
    return paths


def _plan(db):
    case = Case(canonical_title="Dup proof", slug=f"dup-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, validation_json=json.dumps({"mvp": {"reveal_graph_id": 1}}))
    db.add(plan)
    db.commit()
    return case, plan


def _photo(db, case, phash, path, width=800, height=600):
    asset = VisualAsset(case_id=case.id, asset_code=f"V{uuid4().hex[:6]}", asset_type="photo", local_path=path,
                        phash=phash, width=width, height=height)
    db.add(asset)
    db.commit()
    return asset


def _offer(db, plan, case, asset, key, status="needs_review"):
    req = (db.query(MvpRequirement).filter_by(visual_plan_id=plan.id, requirement_key=key).first()
           or MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key=key, beat_id="B01", function="evidence",
                             entity_key="caleb", role_needed="evidence", priority="high", spec_json="{}"))
    db.add(req)
    db.commit()
    cand = MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, final_status=status)
    db.add(cand)
    db.commit()
    return cand


def test_the_larger_picture_of_a_group_is_kept_and_the_copy_is_marked_and_its_requirement_moved(db_session, pictures):
    case, plan = _plan(db_session)
    small, large, other = (_photo(db_session, case, SAME, pictures["small"], 400, 300),
                           _photo(db_session, case, NEAR, pictures["large"], 1600, 1200),
                           _photo(db_session, case, OTHER, pictures["other"]))
    copy = _offer(db_session, plan, case, small, "B01.1")
    _offer(db_session, plan, case, other, "B01.1")
    stats = DUP.filter_duplicates(db_session, plan)
    assert stats == {"groups": 1, "marked": 1, "moved": 1}
    db_session.refresh(copy)
    assert VF.is_duplicate(copy) and copy.final_status == "rejected"
    moved = db_session.query(MvpCandidate).filter_by(visual_asset_id=large.id).one()
    assert moved.final_status == "pending", "the kept picture is judged for the requirement the copy was offered for"
    assert not VF.is_duplicate(db_session.query(MvpCandidate).filter_by(visual_asset_id=other.id).one())


def test_a_picture_the_owner_decided_is_never_marked_and_the_owners_approved_one_is_the_kept_one(db_session, pictures):
    case, plan = _plan(db_session)
    owned = _photo(db_session, case, SAME, pictures["small"], 300, 200)
    copy = _photo(db_session, case, NEAR, pictures["large"], 2000, 1500)
    mine = _offer(db_session, plan, case, owned, "B01.1")
    mine.verifiers_json = json.dumps([{"verifier": "owner", "decision": "approve"}])
    mine.final_status = VF.final_status(mine)
    theirs = _offer(db_session, plan, case, copy, "B01.2")
    db_session.commit()
    DUP.filter_duplicates(db_session, plan)
    db_session.refresh(mine)
    db_session.refresh(theirs)
    assert mine.final_status == "approved" and not VF.is_duplicate(mine)
    assert VF.is_duplicate(theirs)


def test_a_second_run_marks_nothing_again(db_session, pictures):
    case, plan = _plan(db_session)
    a, b = _photo(db_session, case, SAME, pictures["large"]), _photo(db_session, case, SAME, pictures["small"])
    _offer(db_session, plan, case, a, "B01.1")
    _offer(db_session, plan, case, b, "B01.1")
    assert DUP.filter_duplicates(db_session, plan)["marked"] == 1
    assert DUP.filter_duplicates(db_session, plan)["marked"] == 0


def test_the_review_list_does_not_show_a_copy(db_session, pictures):
    case, plan = _plan(db_session)
    a = _photo(db_session, case, SAME, pictures["small"], 100, 100)
    b = _photo(db_session, case, SAME, pictures["large"], 900, 900)
    _offer(db_session, plan, case, a, "B01.1")
    _offer(db_session, plan, case, b, "B01.1")
    DUP.filter_duplicates(db_session, plan)
    shown = TestClient(app).get(f"/api/cases/{case.id}/mvp/candidates?status=all&group=true").json()
    assert [g["asset_id"] for g in shown] == [b.id]


def test_two_different_pictures_with_close_hashes_are_not_duplicates(db_session, pictures):
    case, plan = _plan(db_session)
    a, b = _photo(db_session, case, SAME, pictures["large"]), _photo(db_session, case, NEAR, pictures["other"])
    _offer(db_session, plan, case, a, "B01.1")
    _offer(db_session, plan, case, b, "B01.1")
    assert DUP.filter_duplicates(db_session, plan) == {"groups": 0, "marked": 0, "moved": 0}


def _approve(db, cand, by_owner=False):
    cand.final_status = "approved"
    if by_owner:
        cand.verifiers_json = json.dumps([{"verifier": "owner", "decision": "approve"}])
    db.commit()


def test_of_repeated_approved_pictures_one_stays_approved_and_the_others_go_to_the_repeated_folder(db_session, pictures):
    from pathlib import Path

    case, plan = _plan(db_session)
    kept = _photo(db_session, case, SAME, pictures["large"], 1600, 1200)
    copy = _photo(db_session, case, NEAR, pictures["small"], 400, 300)
    other = _photo(db_session, case, OTHER, pictures["other"])
    for asset, key in ((kept, "B01.1"), (copy, "B01.2"), (other, "B01.1")):
        _approve(db_session, _offer(db_session, plan, case, asset, key), by_owner=asset is not other)
    stats = DUP.repeat_approved(db_session, plan)
    assert (stats["groups"], stats["repeated"], stats["moved_files"], stats["approvals_moved"]) == (1, 1, 1, 1)
    assert stats["kept"] == [kept.asset_code]
    moved = db_session.query(MvpCandidate).filter_by(visual_asset_id=copy.id).one()
    assert VF.is_duplicate(moved) and moved.final_status == "rejected", "also the owner's approval: the owner ordered it"
    to_keeper = db_session.query(MvpCandidate).filter_by(visual_asset_id=kept.id).all()
    assert sorted(c.final_status for c in to_keeper) == ["approved", "approved"], "the repeated one's approval is moved"
    assert db_session.query(MvpCandidate).filter_by(visual_asset_id=other.id).one().final_status == "approved"
    db_session.refresh(copy)
    assert Path(copy.local_path).parent.name == "repeated" and Path(copy.local_path).is_file()
    assert json.loads(copy.spec_json)["repeated_of"] == kept.asset_code
    assert DUP.repeat_approved(db_session, plan)["repeated"] == 0
