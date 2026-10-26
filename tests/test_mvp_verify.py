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
    data = {"depicts": "a man", "matches": [{"entity": "caleb_flynn", "relation": relation}],
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


def test_watermark_blocks_rights_and_unsuitable_ethics_rejects(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch)
    run(V.verify_candidate(db_session, case, cand, req, ENTITIES, [],
                           judge=_judge(_verdict("exact", overlays={"watermark": True}))))
    assert cand.final_status == "rejected"
    cand2 = MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id) if False else None
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


def test_a_context_picture_serves_the_atmosphere_requirement_as_an_illustration(db_session, tmp_path, monkeypatch):
    case, req, asset, cand = _setup(db_session, tmp_path, monkeypatch, entity="atmosphere",
                                    accepted=("illustration",))
    verdict = _verdict("context")
    verdict.data["matches"][0]["entity"] = "atmosphere"
    run(V.verify_candidate(db_session, case, cand, req, [{"key": "atmosphere", "type": "place",
                                                          "name": "Atmosphere"}], [], judge=_judge(verdict)))
    assert cand.final_status == "approved" and cand.role == "illustration"
