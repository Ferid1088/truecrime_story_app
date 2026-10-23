"""Phase 8 rights gate: an asset with unknown rights forces human sign-off
and a platform warning — built on a fixture asset, not a file snapshot."""

from __future__ import annotations

from app.db.models import VisualAsset
from app.shortform.gates import AssetUse, rights_checker


def test_unknown_rights_forces_human_signoff_and_platform_warning(db_session, monkeypatch):
    from app.core.ai_config import ai_config

    monkeypatch.setattr(ai_config.rights, "enforce", True)
    asset = VisualAsset(asset_code="VIS_000027", rights_status="unknown")
    result = rights_checker(
        [AssetUse(asset_id=asset.asset_code, rights_status=asset.rights_status)],
        platform="tiktok_video",
    )
    assert not result.passed
    assert result.offending_ids == ["VIS_000027"]
    assert result.metadata["platform_warnings"]
    assert "human sign-off is required" in result.metadata["platform_warnings"][0]
