"""Phase 9 adversarial checks against real case-6 persisted artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import EpistemicContractSet, RevealGraph
from app.longform.compression import check_compression_safety
from app.longform.service import check_spoiler_references, check_spoiler_visual_assets, spoiler_horizon


def test_case6_adversarial_reveal_and_epistemic_checks():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    with Session() as db:
        graph = db.query(RevealGraph).filter_by(case_id=6, blueprint_id=1, version=1).one()
        assert graph.status == "validated"
        assert len(graph.nodes) == 41 and len(graph.edges) == 69 and len(graph.exposures) == 41
        early = spoiler_horizon(db, graph.id, beat_id="B10")
        late = spoiler_horizon(db, graph.id, beat_id="B45")
        assert {"F011", "F012", "F015"} <= set(early["forbidden"])
        assert {"F011", "F012", "F015"} <= set(late["allowed"])
        assert check_spoiler_references(db, graph.id, ["F011", "F012", "F015"], beat_id="B10")["forbidden_references"] == ["F011", "F012", "F015"]
        assert check_spoiler_references(db, graph.id, ["F011", "F012", "F015"], beat_id="B45")["allowed_references"] == ["F011", "F012", "F015"]
        visual = check_spoiler_visual_assets(db, graph.id, [13], beat_id="B15")
        assert visual["forbidden_visual_asset_ids"] == [13]

        contract = db.query(EpistemicContractSet).filter_by(case_id=6, story_version_id=15, version=1).one()
        assert contract.status == "approved"
        assert len(contract.claims) == 330
        assert sum(claim.review_status == "approved" for claim in contract.claims) == 330
        quoted = next(claim for claim in contract.claims if claim.claim_key == "S15_C009_02")
        established = next(claim for claim in contract.claims if claim.claim_key == "S15_C080")
        violation = check_compression_safety(quoted.modality, "Anthony did it")
        safe = check_compression_safety(established.modality, "The record establishes that the strike caused a rapidly fatal two-inch chest wound.")
        assert not violation.passed and violation.rewritten_modality == "ESTABLISHED"
        assert safe.passed and safe.rewritten_modality == "ESTABLISHED"
