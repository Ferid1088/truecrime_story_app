"""Pre-export checks for case visual-asset verification."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import VisualAsset


class UnverifiedVisualAssetsError(RuntimeError):
    """Export was requested while case visuals are not selectable."""


UNSELECTABLE_VERIFICATION_STATUSES = frozenset({"unverified", "rejected"})


def unverified_asset_codes(db: Session, case_id: int) -> list[str]:
    return [
        asset.asset_code
        for asset in (
            db.query(VisualAsset)
            .filter(
                VisualAsset.case_id == case_id,
                VisualAsset.verification_status == "unverified",
            )
            .order_by(VisualAsset.id)
            .all()
        )
    ]


def require_verified_case_assets(db: Session, case_id: int) -> None:
    codes = unverified_asset_codes(db, case_id)
    if codes:
        raise UnverifiedVisualAssetsError(
            f"case {case_id} has unverified visual assets; resolve "
            f"scripts/verify_case_assets.py {case_id} first: {codes}"
        )
