"""Phase 2 gates: the persisted RevealGraph for real case 6."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import EditorialBlueprint, RevealGraph
from app.longform.service import build_reveal_graph_payload, validate_reveal_graph_payload


def test_case6_real_blueprint_builds_and_validates_persisted_graph():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    real_engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    RealSession = sessionmaker(bind=real_engine)
    with RealSession() as db:
        blueprint = db.get(EditorialBlueprint, 1)
        assert blueprint and blueprint.case_id == 6 and blueprint.status == "valid"
        raw = json.loads(blueprint.blueprint_json)
        assert len(raw["beats"]) == 50
        assert sum(len(b.get("reveals") or []) for b in raw["beats"]) == 41
        assert sum(len(b.get("relies_on") or []) for b in raw["beats"]) == 97
        assert sum(len(b.get("viewer_knows") or []) for b in raw["beats"]) == 1396

        payload = build_reveal_graph_payload(db, 6, blueprint.id)
        report = validate_reveal_graph_payload(
            db, 6, blueprint.id, payload["nodes"], payload["edges"], payload["exposures"]
        )
        graph = db.query(RevealGraph).filter_by(case_id=6, blueprint_id=1, version=1).one()
        assert graph.status == "validated"
        assert len(graph.nodes) == 41
        assert len(graph.edges) == 69
        assert len(graph.exposures) == 41
        assert report == {"status": "valid", "nodes": 41, "edges": 69, "exposures": 41}
        assert {node.node_key for node in graph.nodes} == {
            str(key) for beat in raw["beats"] for key in (beat.get("reveals") or [])
        }
        assert all(edge.prerequisite_node.first_revealed_order < edge.dependent_node.first_revealed_order
                   for edge in graph.edges)
