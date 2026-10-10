"""Phase 8 rights gate against a real case-6 asset."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import VisualAsset
from app.shortform.gates import AssetUse, rights_checker


def test_case6_unknown_rights_forces_human_signoff_and_platform_warning():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    real_engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    RealSession = sessionmaker(bind=real_engine)
    with RealSession() as db:
        asset = db.query(VisualAsset).filter_by(
            case_id=6, asset_code="VIS_000027", rights_status="unknown"
        ).one()
    result = rights_checker(
        [AssetUse(asset_id=asset.asset_code, rights_status=asset.rights_status)],
        platform="tiktok_video",
    )
    assert not result.passed
    assert result.offending_ids == ["VIS_000027"]
    assert result.metadata["platform_warnings"]
    assert "human sign-off is required" in result.metadata["platform_warnings"][0]
