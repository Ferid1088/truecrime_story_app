"""Phase 7 gate: real per-case rollout readiness reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Case
from app.longform.rollout import assess_all_cases


def test_real_case_readiness_reports_cases_1_to_5_as_not_candidates_and_case6_ready():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    real_engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    RealSession = sessionmaker(bind=real_engine)
    with RealSession() as db:
        rows = assess_all_cases(db)
    by_id = {row.case_id: row for row in rows}
    assert set(by_id) >= {1, 2, 3, 4, 5, 6}
    for case_id in range(1, 6):
        row = by_id[case_id]
        assert row.status == "NOT_CANDIDATE"
        assert row.blueprint_count == 0
        assert row.visual_asset_count == 0
        assert "no EditorialBlueprint exists" in row.reasons
        assert "no VisualAsset rows exist" in row.reasons
    case6 = by_id[6]
    assert case6.status == "READY"
    assert case6.usable_blueprint_ids == (1,)
    assert case6.visual_asset_count == 32
    assert case6.original_media_segment_count == 0
    assert case6.validated_graph_count == 1
    assert case6.contract_set_count == 1
    assert case6.approved_contract_set_count == 1
