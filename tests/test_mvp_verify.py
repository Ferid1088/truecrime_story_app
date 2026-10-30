import asyncio
import json
from uuid import uuid4

import pytest

from app.db.models import Case, EditorialBlueprint, MvpCandidate, MvpRequirement, VisualAsset, VisualPlan
from app.documentary.mvp import verify as V

ENTITIES = [{"key": "caleb_flynn", "type": "person", "name": "Caleb Flynn"},
            {"key": "court", "type": "building", "name": "Miami County Courthouse"}]


def _setup(db, tmp_path, monkeypatch, *, entity="caleb_flynn", accepted=("evidence", "context"),
           rights="public_domain"):
    case = Case(canonical_title="Verify proof", slug=f"verify-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=None, blueprint_json="{}") \
        if False else None
    plan = VisualPlan(case_id=case.id, blueprint_id=_blueprint(db, case), version=1)
    db.add(plan)
    db.commit()
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                         function="human_connection", entity_key=entity, role_needed="evidence",
                         spec_json=json.dumps({"acceptable_roles": list(accepted)}))
    file = tmp_path / "a.jpg"
    file.write_bytes(b"x")
    asset = VisualAsset(case_id=case.id, asset_code=f"VIS_{uuid4().hex[:6]}", provider="wikimedia",
                        source_url="https://x/y.jpg", sha256="ab" * 32, local_path="a.jpg",
                        rights_status=rights)
    db.add_all([req, asset])
    db.commit()
    monkeypatch.setattr(V.storage, "resolve", lambda p: file)
    cand = MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id)
    db.add(cand)
    db.commit()
    return case, req, asset, cand


def _blueprint(db, case):
    from app.db.models import StoryVersion

    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t",
                      narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    return bp.id


def _verdict(relation, confidence=0.95, **extra):
    data = {"depicts": "a man", "matches": [{"entity": "caleb_flynn", "relation": relation, "signal": "caption names the person"}],
            "overlays": {}, "ethics": {"concern": "ok"}, "scene_fit": "good", "quality": 0.9,
            "confidence": confidence, **extra}
    return V.Verdict(True, data, "fake-model")


def _judge(*verdicts):
    queue = list(verdicts)

    async def judge(case, asset, entities, facts, agent):
        return queue.pop(0)
    return judge


def run(coro):
    return asyncio.run(coro)


def test_exact_high_confidence_person_is_approved(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [], judge=_judge(_verdict("exact"))))
    assert cand.final_status == "approved" and cand.role == "evidence"
    assert (cand.identity_status, cand.rights_status) == ("approved", "cleared")


def test_exact_person_at_low_confidence_waits_for_review(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [], judge=_judge(_verdict("exact", 0.6)),
                           second=_judge(_verdict("uncertain", 0.5))))
    assert cand.final_status == "needs_review"
    assert "verifiers disagree" in cand.reasons_json


def test_second_verifier_can_confirm_an_uncertain_identity(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [], judge=_judge(_verdict("exact", 0.6)),
                           second=_judge(_verdict("exact", 0.93))))
    assert cand.final_status == "approved"
    assert [v["verifier"] for v in json.loads(cand.verifiers_json)] == ["mvp.verify", "mvp.verify_second"]


def test_context_only_picture_cannot_serve_an_exact_only_requirement(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch, accepted=("evidence",))
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [], judge=_judge(_verdict("context"))))
    assert cand.final_status == "rejected"


def test_context_picture_serves_a_context_requirement(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch, entity="court",
                                    accepted=("context",))
    verdict = _verdict("context")
    verdict.data["matches"][0]["entity"] = "court"
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [], judge=_judge(verdict)))
    assert cand.final_status == "approved" and cand.role == "context"


def test_transient_failure_is_never_a_rejection(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    fail = V.Verdict(False, {"error": "ReadTimeout"})
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [],
                           judge=_judge(fail, fail, fail, fail, fail)))
    assert cand.final_status == "transient_failure"


def test_blocked_rights_reject_and_editorial_rights_are_preview_only(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch, rights="do_not_use")
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [], judge=_judge(_verdict("exact"))))
    assert cand.final_status == "rejected" and cand.rights_status == "blocked"
    asset.rights_status = "editorial_review_required"
    assert V.rights_decision(asset)[0] == "preview_only"
    assert V.rights_decision(asset, "publish")[0] == "blocked"


def test_a_watermark_is_left_to_the_overlay_scan_to_cut_away_and_unsuitable_ethics_rejects(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [],
                           judge=_judge(_verdict("exact", overlays={"watermark": True}))))
    assert cand.final_status == "approved" and cand.rights_status != "blocked", "the overlay scan cuts the mark away"
    out = V.interpret(_verdict("exact", ethics={"concern": "unsuitable"}).data, req, "person")
    assert out["ethics"] == "rejected"


def test_sync_marks_the_library_asset_from_its_candidates(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [], judge=_judge(_verdict("exact"))))
    assert V.sync_asset_status(db_session, case.id) == {"verified": 1, "rejected": 0, "needs_review": 0}
    db_session.refresh(asset)
    assert asset.verification_status == "verified" and asset.asset_role == "evidence"


def test_a_second_verifier_settles_what_the_first_could_not_tell(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [],
                           judge=_judge(_verdict("uncertain", 0.5)), second=_judge(_verdict("exact", 0.93))))
    assert cand.final_status == "approved" and cand.role == "evidence"


def test_a_first_verifier_that_says_unrelated_is_not_overruled_into_approval(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [],
                           judge=_judge(_verdict("unrelated", 0.9)), second=_judge(_verdict("exact", 0.95))))
    assert cand.final_status == "rejected"


def test_a_block_that_only_the_old_watermark_rule_made_is_lifted_and_another_block_stays(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)             # public domain: usable
    cand.rights_status, cand.final_status = "blocked", "rejected"
    cand.provenance_status = cand.identity_status = cand.ethics_status = cand.scene_status = "approved"
    cand.reasons_json = json.dumps(["watermark visible", "something else"])
    other_asset = VisualAsset(case_id=case.id, asset_code="BLK1", local_path="x.jpg", rights_status="do_not_use")
    db_session.add(other_asset)
    db_session.commit()
    blocked = MvpCandidate(requirement_id=req.id, visual_asset_id=other_asset.id, rights_status="blocked",
                           final_status="rejected", reasons_json=json.dumps(["watermark visible"]))
    db_session.add(blocked)
    db_session.commit()

    assert V.release_watermark_blocks(db_session, case.id) == 1

    db_session.refresh(cand)
    db_session.refresh(blocked)
    assert cand.rights_status != "blocked" and cand.final_status == "approved"
    assert json.loads(cand.reasons_json) == ["something else"]
    assert blocked.rights_status == "blocked" and blocked.final_status == "rejected", "blocked for its own rights"
    assert V.release_watermark_blocks(db_session, case.id) == 0, "done once"


def test_an_asset_an_older_audit_gave_up_for_a_watermark_gets_its_rights_from_its_source_again(db_session, tmp_path, monkeypatch):
    from app.documentary.visuals import rights as RT

    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    marked = VisualAsset(case_id=case.id, asset_code="WM1", local_path="x.jpg", provider="wikimedia",
                         license="CC BY-SA 4.0", source_url="https://commons.wikimedia.org/x.jpg",
                         rights_status="do_not_use", rights_reason="watermark visible")
    stock = VisualAsset(case_id=case.id, asset_code="WM2", local_path="x.jpg", provider="web",
                        source_url="https://www.gettyimages.com/x.jpg", rights_status="do_not_use",
                        rights_reason="watermark visible")
    db_session.add_all([marked, stock])
    db_session.commit()
    for a in (marked, stock):
        db_session.add(MvpCandidate(requirement_id=req.id, visual_asset_id=a.id, rights_status="blocked",
                                    final_status="rejected", reasons_json=json.dumps(["do_not_use: watermark visible"])))
    db_session.commit()

    V.release_watermark_blocks(db_session, case.id)
    db_session.refresh(marked)
    db_session.refresh(stock)

    assert marked.rights_status != "do_not_use" and marked.rights_reason != "watermark visible"
    assert stock.rights_status == "do_not_use" and stock.rights_reason.startswith("stock library"), "a stock library stays out"
    assert RT.reclassify_watermarked(marked) is False, "only an asset still given up for the watermark is looked at"


def _owner(cand, decision):
    cand.verifiers_json = json.dumps(json.loads(cand.verifiers_json or "[]") + [{"verifier": "owner", "decision": decision}])
    cand.final_status = V.final_status(cand)


def test_an_owner_decision_stands_whatever_the_verifiers_say_afterwards(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [], judge=_judge(_verdict("exact"))))
    assert cand.final_status == "approved"
    _owner(cand, "reject")
    assert cand.final_status == "rejected"
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [], judge=_judge(_verdict("exact"))))
    assert cand.final_status == "rejected", "an automatic check approves it again: the owner's rejection stands"
    _owner(cand, "approve")
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [], judge=_judge(_verdict("unrelated", 0.9))))
    assert cand.final_status == "approved", "and an owner approval stands against a rejecting verifier"


def test_only_the_inspector_can_take_out_an_owner_approved_item_and_only_the_owner_brings_it_back(db_session, tmp_path,
                                                                                                    monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    _owner(cand, "approve")
    V.remove_unusable(cand, "the logo could not be cut away")
    assert cand.final_status == "rejected" and V.owner_settled(cand) == "rejected"
    assert json.loads(cand.verifiers_json)[-1]["note"] == "the logo could not be cut away"
    _owner(cand, "approve")
    assert cand.final_status == "approved", "the owner decides again"
