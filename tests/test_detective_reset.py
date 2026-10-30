"""Starting the toolkit again removes its own plan and nothing the Main Visual Planner found."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.ai_config import ai_config
from app.db.base import get_db
from app.db.models import (Case, DetectiveClaim, DetectiveScene, DetectiveToolAction, DocumentaryJob,
                           MvpCandidate, MvpRequirement, VisualAsset, VisualPlan)
from app.documentary.detective import cards as CD
from app.documentary.detective import toolkit as TK
from app.main import app
from tests.detective_film import film


def _planned_film(db_session, monkeypatch):
    graph, units = film(db_session)
    CD.store_card(db_session, graph.id, CD.Part("P01", ["B02-1", "B02-2"], "casings", ["fact_card"]), "fact_card",
                  {"facts": [{"text": "Two casings by the bed.", "fact_id": "B02-1"}]}, units, "en", 1)
    MEM = __import__("app.documentary.memory", fromlist=["add_claim"])
    MEM.ensure_entity(db_session, graph.id, "gun", "the 9mm", "object", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "gun", "origin", "A 9mm gun.", "established_in_text")
    case = db_session.get(Case, graph.case_id)
    plan = VisualPlan(case_id=case.id, blueprint_id=graph.blueprint_id, version=1,
                      validation_json=json.dumps({"mvp": {"reveal_graph_id": graph.id}}))
    asset = VisualAsset(case_id=case.id, asset_code="KEEP1", local_path="x.jpg", asset_type="photo")
    db_session.add_all([plan, asset])
    db_session.flush()
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                         function="evidence", role_needed="evidence", priority="high", status="met")
    db_session.add(req)
    db_session.flush()
    db_session.add(MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, final_status="approved"))
    db_session.commit()
    return graph, case


def test_the_toolkits_plan_is_removed_and_the_pictures_and_their_approvals_stay(db_session, monkeypatch):
    graph, case = _planned_film(db_session, monkeypatch)
    assert db_session.query(DetectiveScene).filter_by(graph_id=graph.id).count() > 0

    removed = TK.reset_plan(db_session, graph.id)

    assert removed["scenes"] > 0 and removed["actions"] > 0 and removed["claims"] > 0
    for model in (DetectiveScene, DetectiveClaim):
        assert db_session.query(model).filter_by(graph_id=graph.id).count() == 0
    assert db_session.query(DetectiveToolAction).join(DetectiveScene, isouter=True).filter(
        DetectiveScene.graph_id == graph.id).count() == 0
    assert db_session.query(VisualAsset).filter_by(case_id=case.id, asset_code="KEEP1").count() == 1
    assert db_session.query(MvpCandidate).filter_by(final_status="approved").count() >= 1, "the approval stays"


def test_the_owner_starts_the_toolkit_again_from_the_app_unless_a_job_is_running(db_session, monkeypatch):
    graph, case = _planned_film(db_session, monkeypatch)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        job = DocumentaryJob(case_id=case.id, status="running", mode="plan", languages_json='["en"]',
                             stages_json="[]")
        db_session.add(job)
        db_session.commit()
        assert client.post(f"/api/cases/{case.id}/detective/reset").status_code == 409
        assert db_session.query(DetectiveScene).filter_by(graph_id=graph.id).count() > 0, "nothing was removed"
        job.status = "failed"
        db_session.commit()
        answer = client.post(f"/api/cases/{case.id}/detective/reset")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert answer.status_code == 200 and answer.json()["graph_id"] == graph.id and answer.json()["removed"]["scenes"] > 0
    assert db_session.query(DetectiveScene).filter_by(graph_id=graph.id).count() == 0


def test_the_detective_view_uses_the_graph_the_film_is_planned_on_not_the_newest_graph(db_session, monkeypatch):
    from app.db.models import RevealGraph
    from app.documentary.detective import api as DA

    graph, case = _planned_film(db_session, monkeypatch)
    stray = RevealGraph(case_id=case.id, blueprint_id=graph.blueprint_id, version=9, status="validated")
    db_session.add(stray)
    db_session.commit()
    assert stray.id > graph.id and DA._latest_graph(db_session, case.id).id == graph.id
