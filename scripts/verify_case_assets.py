"""Run the production visual verifier for every unverified case asset."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.db.base import SessionLocal
from app.db.models import Case, VisualAsset, VisualPlan
from app.documentary.jobs import verify_assets


def _json(value, default):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _result(asset: VisualAsset, known_entity_keys: set[str]) -> dict:
    verification = _json(asset.verification_json, {})
    proposed_entities = [str(value) for value in verification.get("entities") or []]
    rejected_unknown = sorted(set(proposed_entities) - known_entity_keys)
    return {
        "asset_code": asset.asset_code,
        "verification_status": asset.verification_status,
        "verification_confidence": asset.verification_confidence,
        "depicts": verification.get("depicts"),
        "entities": proposed_entities,
        "rejected_unknown_entities": rejected_unknown,
        "identity_mismatch_signal": bool(rejected_unknown),
        "reason": verification.get("reason"),
        "verification_payload": verification,
    }


async def _run(case_id: int, force: bool) -> None:
    with SessionLocal() as db:
        case = db.get(Case, case_id)
        if case is None:
            raise SystemExit(f"case not found: {case_id}")
        plan = (db.query(VisualPlan)
                .filter_by(case_id=case_id)
                .order_by(VisualPlan.version.desc(), VisualPlan.id.desc())
                .first())
        if plan is None:
            raise SystemExit(f"no visual plan exists for case {case_id}; cannot load verifier entities")
        requirements = _json(plan.requirements_json, {})
        entities = requirements.get("entities") or []
        known_entity_keys = {str(entity.get("key")) for entity in entities if entity.get("key")}
        query = db.query(VisualAsset).filter(VisualAsset.case_id == case_id)
        if not force:
            query = query.filter(VisualAsset.verification_status == "unverified")
        assets = query.order_by(VisualAsset.id).all()
        before = {asset.asset_code: _result(asset, known_entity_keys) for asset in assets}
        summary = await verify_assets(db, case, assets, entities)
        # Refresh the rows after verify_assets commits each result.
        db.expire_all()
        refreshed = {asset.asset_code: asset for asset in assets}
        output = {
            "case_id": case_id,
            "case_title": case.canonical_title,
            "force": force,
            "known_entity_count": len(known_entity_keys),
            "assets_requested": len(assets),
            "verify_assets_result": summary,
            "assets": [
                _result(refreshed[code], known_entity_keys)
                for code in sorted(refreshed, key=lambda value: int(value.rsplit("_", 1)[-1]))
            ],
            "preexisting_results": before,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id", type=int)
    parser.add_argument("--force", action="store_true", help="re-verify all assets, not only unverified rows")
    args = parser.parse_args()
    asyncio.run(_run(args.case_id, args.force))


if __name__ == "__main__":
    main()
