"""Phase 1 gates for persisted long-form integrity artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.base import Base, engine
from app.db.models import (
    Case,
    EditorialBlueprint,
    EpistemicClaim,
    EpistemicContractSet,
    OriginalMediaSegment,
    RevealGraph,
    StoryVersion,
    VisualAsset,
)
from app.longform.service import (
    LongformValidationError,
    create_contract_set,
    create_reveal_graph,
    delete_claim,
    delete_contract_set,
    delete_reveal_graph,
    get_contract_set,
    get_reveal_graph,
    update_claim,
    update_contract_set_status,
    approve_contract_set,
    update_reveal_graph_status,
)
from app.utils import utc_now


def _blueprint(db_session):
    slug = f"phase-1-case-{uuid4().hex[:10]}"
    case = Case(canonical_title="Phase 1 case", slug=slug, language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(
        case_id=case.id, version=1, kind="master", language="en",
        narrative_angle="test", story_text="First paragraph.\n\nSecond paragraph.",
        engagement_score=0.0,
    )
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(
        case_id=case.id, story_version_id=story.id, version=1, status="valid",
        blueprint_json=json.dumps({
            "central_question": "q", "editorial_thesis": "t", "human_thread": "h",
            "arcs": {}, "questions": [], "beats": [
                {"id": "B01", "reveals": ["A"], "relies_on": [], "viewer_knows": ["A"]},
                {"id": "B02", "reveals": ["B"], "relies_on": ["A"], "viewer_knows": ["A", "B"]},
            ],
        }),
        validation_json=json.dumps({"status": "valid"}),
    )
    db_session.add(blueprint)
    db_session.commit()
    return case, story, blueprint


def _graph_payload():
    return {
        "version": 1,
        "nodes": [
            {"node_key": "A", "label": "First fact", "category": "fact",
             "first_revealed_beat_id": "B01", "first_revealed_order": 0},
            {"node_key": "B", "label": "Dependent fact", "category": "fact",
             "first_revealed_beat_id": "B02", "first_revealed_order": 1},
        ],
        "edges": [{"prerequisite": "A", "dependent": "B"}],
        "exposures": [
            {"beat_id": "B01", "node_key": "A", "exposure_type": "exposed", "source_field": "reveals"},
            {"beat_id": "B02", "node_key": "B", "exposure_type": "exposed", "source_field": "reveals"},
        ],
    }


def test_phase1_tables_and_case_uid_migration_exist():
    tables = set(inspect(engine).get_table_names())
    assert {
        "original_media_segments", "reveal_graphs", "reveal_nodes", "reveal_edges",
        "reveal_exposures", "reveal_asset_links", "epistemic_contract_sets", "epistemic_claims",
    } <= tables
    assert "case_uid" in {column["name"] for column in inspect(engine).get_columns("cases")}


def test_original_media_segment_model_matches_table_and_round_trips_fk(db_session):
    columns = {column["name"] for column in inspect(engine).get_columns("original_media_segments")}
    assert columns == {
        "id", "case_id", "asset_id", "beat_id", "source_start", "source_end", "language",
        "transcript", "story_use", "translation_strategy_json", "selected", "created_at",
    }
    fk_targets = {fk.target_fullname for fk in OriginalMediaSegment.__table__.foreign_keys}
    assert fk_targets == {"cases.id", "visual_assets.id"}

    case = Case(canonical_title="Media FK case", slug=f"media-fk-case-{uuid4().hex[:10]}", language="en")
    db_session.add(case)
    db_session.flush()
    asset = VisualAsset(case_id=case.id, asset_code="MEDIA-FK", asset_type="photo")
    db_session.add(asset)
    db_session.flush()
    segment = OriginalMediaSegment(
        case_id=case.id, asset_id=asset.id, beat_id="B01", source_start=1.25,
        source_end=3.5, language="en", transcript="clip", story_use="evidence",
        translation_strategy_json="{}", selected=True, created_at=utc_now(),
    )
    db_session.add(segment)
    db_session.commit()
    row = db_session.get(OriginalMediaSegment, segment.id)
    assert row.case.id == case.id
    assert row.asset.id == asset.id
    assert row in case.original_media_segments
    assert row in asset.original_media_segments


def test_real_database_case6_original_media_segment_count_and_fk_resolution():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    real_engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    RealSession = sessionmaker(bind=real_engine)
    with RealSession() as real_db:
        rows = (real_db.query(OriginalMediaSegment)
                .filter(OriginalMediaSegment.case_id == 6)
                .order_by(OriginalMediaSegment.id).all())
        assert len(rows) == 0
        for row in rows:
            assert row.case.id == 6
            assert row.asset.case_id == 6


def test_reveal_graph_crud_and_constraint_validation(db_session):
    case, _, blueprint = _blueprint(db_session)
    graph = create_reveal_graph(db_session, case.id, blueprint.id, _graph_payload())
    assert get_reveal_graph(db_session, graph.id).nodes[1].node_key == "B"
    assert graph.edges[0].prerequisite_node.node_key == "A"
    assert graph.exposures[0].node.node_key == "A"
    assert update_reveal_graph_status(db_session, graph.id, "draft").status == "draft"

    cycle = _graph_payload()
    cycle["edges"] = [{"prerequisite": "A", "dependent": "B"},
                       {"prerequisite": "B", "dependent": "A"}]
    with pytest.raises(LongformValidationError, match="acyclic"):
        create_reveal_graph(db_session, case.id, blueprint.id, {**cycle, "version": 2})

    orphan = _graph_payload()
    orphan["nodes"][0]["node_key"] = "X"
    with pytest.raises(LongformValidationError, match="unknown reveal nodes"):
        create_reveal_graph(db_session, case.id, blueprint.id, {**orphan, "version": 2})

    future = _graph_payload()
    future["edges"] = [{"prerequisite": "B", "dependent": "A"}]
    with pytest.raises(LongformValidationError, match="revealed too late"):
        create_reveal_graph(db_session, case.id, blueprint.id, {**future, "version": 2})

    delete_reveal_graph(db_session, graph.id)
    assert get_reveal_graph(db_session, graph.id) is None


def test_epistemic_contract_crud_and_schema_constraints(db_session):
    case, story, blueprint = _blueprint(db_session)
    contract = create_contract_set(db_session, case.id, {
        "blueprint_id": blueprint.id, "story_version_id": story.id,
        "claims": [{"claim_key": "C1", "claim_text": "Investigators believed X.",
                     "beat_id": "B01", "modality": "BELIEVED_BY_INVESTIGATORS",
                     "source_refs": ["S1"], "evidence_refs": ["A"]}],
    })
    assert get_contract_set(db_session, contract.id).claims[0].modality == "BELIEVED_BY_INVESTIGATORS"
    claim = contract.claims[0]
    assert update_claim(db_session, claim.id, {"review_status": "approved"}).review_status == "approved"
    assert update_contract_set_status(db_session, contract.id, "in_review").status == "in_review"
    approved = approve_contract_set(db_session, contract.id)
    assert approved.status == "approved"
    assert all(item.review_status == "approved" for item in approved.claims)

    bad = EpistemicClaim(contract_set_id=contract.id, claim_key="bad", claim_text="x",
                         beat_id="B01", modality="INVENTED", review_status="proposed")
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    delete_claim(db_session, claim.id)
    delete_contract_set(db_session, contract.id)
    assert get_contract_set(db_session, contract.id) is None


def test_longform_crud_api(client, db_session):
    case, story, blueprint = _blueprint(db_session)
    graph_payload = {"blueprint_id": blueprint.id, **_graph_payload()}
    response = client.post(f"/api/cases/{case.id}/reveal-graphs", json=graph_payload)
    assert response.status_code == 201, response.text
    graph_id = response.json()["id"]
    assert client.get(f"/api/reveal-graphs/{graph_id}").json()["status"] == "validated"
    assert client.patch(f"/api/reveal-graphs/{graph_id}", json={"status": "draft"}).status_code == 200

    contract_response = client.post(
        f"/api/cases/{case.id}/epistemic-contracts",
        json={"blueprint_id": blueprint.id, "story_version_id": story.id, "claims": [{
            "claim_key": "C1", "claim_text": "X", "beat_id": "B01",
            "modality": "ESTABLISHED",
        }]},
    )
    assert contract_response.status_code == 201, contract_response.text
    contract = contract_response.json()
    claim_id = contract["claims"][0]["id"]
    assert client.patch(f"/api/epistemic-claims/{claim_id}",
                        json={"review_status": "approved"}).status_code == 200
    assert client.delete(f"/api/epistemic-claims/{claim_id}").status_code == 204
    second_claim = client.post(
        f"/api/epistemic-contracts/{contract['id']}/claims",
        json={"claim_key": "C2", "claim_text": "Y", "beat_id": "B02",
              "modality": "DISPUTED"},
    )
    assert second_claim.status_code == 201, second_claim.text
    assert client.delete(f"/api/epistemic-claims/{second_claim.json()['id']}").status_code == 204
    assert client.delete(f"/api/epistemic-contracts/{contract['id']}").status_code == 204
    assert client.delete(f"/api/reveal-graphs/{graph_id}").status_code == 204
