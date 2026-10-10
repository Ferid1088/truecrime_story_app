"""Extract and persist proposed EpistemicContracts from a master story."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.db.base import SessionLocal
from app.db.models import EditorialBlueprint, EpistemicContractSet, StoryVersion
from app.longform.epistemic import extract_master_claims
from app.longform.service import create_contract_set


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id", type=int)
    parser.add_argument("story_version_id", type=int)
    parser.add_argument("blueprint_id", type=int)
    parser.add_argument("--replace", action="store_true",
                        help="replace the existing contract set for this version")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        existing = db.query(EpistemicContractSet).filter_by(
            case_id=args.case_id, story_version_id=args.story_version_id, version=1
        ).first()
        if existing and not args.replace:
            raise SystemExit("Epistemic contract set version 1 already exists")
        if existing:
            db.delete(existing)
            db.commit()
        story = db.get(StoryVersion, args.story_version_id)
        blueprint = db.get(EditorialBlueprint, args.blueprint_id)
        if not story or story.case_id != args.case_id or not blueprint or blueprint.case_id != args.case_id:
            raise SystemExit("Story version and blueprint must belong to the case")
        result = extract_master_claims(db, story, blueprint)
        contract = create_contract_set(db, args.case_id, {
            "blueprint_id": args.blueprint_id,
            "story_version_id": args.story_version_id,
            "version": 1,
            "status": "in_review",
            "coverage": result["coverage"],
            "validation": {"status": "proposed", "source": f"story_version:{story.id}"},
            "claims": result["claims"],
        })
        print(f"created contract_set_id={contract.id} claims={len(contract.claims)} "
              f"coverage={result['coverage']['coverage_percent']}%")
    finally:
        db.close()


if __name__ == "__main__":
    main()
