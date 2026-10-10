"""Verify Phase 8 prerequisites against real persisted media and rights data."""

from __future__ import annotations

import json
from pathlib import Path

from app.db.base import SessionLocal
from app.db.models import OriginalMediaSegment, VisualAsset
from app.shortform.gates import AssetUse, rights_checker


def main() -> None:
    with SessionLocal() as db:
        segments = db.query(OriginalMediaSegment).order_by(OriginalMediaSegment.id).all()
        audio_assets = db.query(VisualAsset).filter(
            VisualAsset.has_original_audio.is_(True)
        ).order_by(VisualAsset.id).all()
        unknown = db.query(VisualAsset).filter_by(
            case_id=6, asset_code="VIS_000027", rights_status="unknown"
        ).one()

    rights = rights_checker(
        [AssetUse(asset_id=unknown.asset_code, rights_status=unknown.rights_status)],
        platform="tiktok_video",
    )
    result = {
        "original_media_segments": len(segments),
        "assets_with_original_audio": [asset.asset_code for asset in audio_assets],
        "short_build": {
            "status": "BLOCKED_NO_REAL_ORIGINAL_AUDIO" if not segments and not audio_assets else "READY_TO_BUILD",
            "reason": "No persisted original media segment or asset with original audio exists; generated narration files are not substituted.",
        },
        "rights_checker_unknown_asset": {
            "asset_code": unknown.asset_code,
            "rights_status": unknown.rights_status,
            "passed": rights.passed,
            "offending_ids": rights.offending_ids,
            "reasons": rights.reasons,
            "metadata": rights.metadata,
        },
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
