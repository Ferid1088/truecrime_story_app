"""Phase 3 gates: spoiler horizons against the real case 6 graph."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.longform.service import check_spoiler_references, spoiler_horizon


def test_case6_spoiler_horizon_blocks_future_nodes_and_allows_late_nodes():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    real_engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    RealSession = sessionmaker(bind=real_engine)
    with RealSession() as db:
        early = spoiler_horizon(db, 1, beat_id="B10")
        assert early["allowed"] == [
            "C003", "F001", "F002", "F007", "F008", "F009", "F010",
            "T001", "T002", "T005", "T006", "T007", "T008",
        ]
        assert {"F011", "F012", "F015"} <= set(early["forbidden"])
        early_check = check_spoiler_references(
            db, 1, ["F011", "F012", "F015"], beat_id="B10"
        )
        assert early_check["forbidden_references"] == ["F011", "F012", "F015"]
        assert early_check["allowed_references"] == []

        mid = spoiler_horizon(db, 1, beat_id="B15")
        assert {"F011", "F012", "F015"} <= set(mid["forbidden"])

        late = spoiler_horizon(db, 1, beat_id="B45")
        assert late["forbidden"] == []
        late_check = check_spoiler_references(
            db, 1, ["F011", "F012", "F015"], beat_id="B45"
        )
        assert late_check["allowed_references"] == ["F011", "F012", "F015"]
        assert late_check["forbidden_references"] == []

