"""Run adversarial RevealGraph and EpistemicContract checks on case 6."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import EpistemicContractSet, RevealGraph, VisualAsset
from app.longform.compression import check_compression_safety
from app.longform.service import (
    LongformValidationError,
    check_spoiler_references,
    check_spoiler_visual_assets,
    spoiler_horizon,
)


def main() -> None:
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    with Session() as db:
        graph = db.query(RevealGraph).filter_by(case_id=6, blueprint_id=1, version=1).one()
        contract = db.query(EpistemicContractSet).filter_by(
            case_id=6, story_version_id=15, version=1
        ).one()
        visual = db.query(VisualAsset).filter_by(case_id=6, asset_code="VIS_000013").one()

        early = spoiler_horizon(db, graph.id, beat_id="B10")
        late = spoiler_horizon(db, graph.id, beat_id="B45")
        early_refs = check_spoiler_references(
            db, graph.id, ["F011", "F012", "F015"], beat_id="B10"
        )
        late_refs = check_spoiler_references(
            db, graph.id, ["F011", "F012", "F015"], beat_id="B45"
        )
        visual_check = check_spoiler_visual_assets(db, graph.id, [visual.id], beat_id="B15")
        try:
            check_spoiler_references(db, graph.id, ["NOT_A_NODE"], beat_id="B10")
        except LongformValidationError as exc:
            unknown_reference = {"rejected": True, "error": str(exc)}
        else:
            unknown_reference = {"rejected": False}

        quoted = next(claim for claim in contract.claims if claim.claim_key == "S15_C009_02")
        established = next(claim for claim in contract.claims if claim.claim_key == "S15_C080")
        strengthened = check_compression_safety(quoted.modality, "Anthony did it")
        preserved = check_compression_safety(
            established.modality,
            "The record establishes that the strike caused a rapidly fatal two-inch chest wound.",
        )
        result = {
            "graph": {
                "id": graph.id,
                "status": graph.status,
                "nodes": len(graph.nodes),
                "edges": len(graph.edges),
                "exposures": len(graph.exposures),
            },
            "early_B10": {
                "allowed": early["allowed"],
                "forbidden": early["forbidden"],
                "verdict_refs": early_refs,
            },
            "late_B45": {
                "allowed": late["allowed"],
                "forbidden": late["forbidden"],
                "verdict_refs": late_refs,
            },
            "visual_adversary": {
                "asset_code": visual.asset_code,
                "asset_id": visual.id,
                "at_B15": visual_check,
            },
            "unknown_reference": unknown_reference,
            "contracts": {
                "status": contract.status,
                "claims": len(contract.claims),
                "approved_claims": sum(c.review_status == "approved" for c in contract.claims),
                "strengthening": {
                    "claim_key": quoted.claim_key,
                    "source_modality": quoted.modality,
                    "rewrite": "Anthony did it",
                    "passed": strengthened.passed,
                    "rewritten_modality": strengthened.rewritten_modality,
                },
                "safe_paraphrase": {
                    "claim_key": established.claim_key,
                    "source_modality": established.modality,
                    "passed": preserved.passed,
                    "rewritten_modality": preserved.rewritten_modality,
                },
            },
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
