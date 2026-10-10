"""Phase 4 gates: real case 6 visual reveal links and spoiler checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import OriginalMediaSegment, RevealAssetLink, VisualAsset
from app.longform.service import check_spoiler_visual_assets


def test_case6_real_visual_assets_are_linked_and_audio_is_explicitly_empty():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    real_engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    RealSession = sessionmaker(bind=real_engine)
    with RealSession() as db:
        assets = db.query(VisualAsset).filter_by(case_id=6).all()
        assert len(assets) == 32
        assert sum(asset.rights_status == "editorial_review_required" for asset in assets) == 23
        assert sum(asset.rights_status == "do_not_use" for asset in assets) == 6
        assert sum(asset.rights_status == "unknown" for asset in assets) == 2
        assert sum(asset.rights_status == "public_domain" for asset in assets) == 1
        assert sum(asset.verification_status == "verified" for asset in assets) == 23
        assert sum(asset.verification_status == "rejected" for asset in assets) == 9
        assert sum(asset.verification_status == "unverified" for asset in assets) == 0
        assert db.query(OriginalMediaSegment).filter_by(case_id=6).count() == 0

        links = db.query(RevealAssetLink).filter_by(graph_id=1).all()
        assert len(links) == 6
        assert any(link.visual_asset_id == 1 and link.node.node_key == "F005" for link in links)
        assert any(link.visual_asset_id == 13 and link.node.node_key == "F012" for link in links)

        before_knife = check_spoiler_visual_assets(db, 1, [1], beat_id="B10")
        assert before_knife["asset_nodes"] == {"1": ["F005"]}
        assert before_knife["forbidden_visual_asset_ids"] == [1]
        assert before_knife["allowed_visual_asset_ids"] == []

        after_knife = check_spoiler_visual_assets(db, 1, [1], beat_id="B15")
        assert after_knife["forbidden_visual_asset_ids"] == []
        assert after_knife["allowed_visual_asset_ids"] == [1]

        before_verdict = check_spoiler_visual_assets(db, 1, [13], beat_id="B15")
        assert before_verdict["asset_nodes"] == {"13": ["F011", "F012"]}
        assert before_verdict["forbidden_visual_asset_ids"] == [13]
