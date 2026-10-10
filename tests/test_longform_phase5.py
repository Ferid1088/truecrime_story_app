"""Phase 5 gates: real master-story epistemic extraction for case 6."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import EpistemicClaim, EpistemicContractSet
from app.longform.epistemic import extract_master_claims


def test_case6_master_story_claim_coverage_and_persisted_review_set():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    real_engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    RealSession = sessionmaker(bind=real_engine)
    with RealSession() as db:
        contract = db.query(EpistemicContractSet).filter_by(
            case_id=6, story_version_id=15, version=1
        ).one()
        coverage = json.loads(contract.coverage_json)
        assert contract.status == "approved"
        assert coverage["total_sentence_spans"] == 304
        assert coverage["question_spans_excluded"] == 6
        assert coverage["denominator_declarative_sentences"] == 298
        assert coverage["atomic_claims_extracted"] > 298
        assert coverage["atomic_claims_extracted"] == 330
        assert coverage["declarative_spans_covered"] == 298
        assert coverage["coverage_percent"] == 100.0
        assert coverage["split_source_sentences"] == 30
        assert coverage["split_percent"] == 10.07
        assert coverage["evidence_linked_claims"] == 310
        assert db.query(EpistemicClaim).filter_by(contract_set_id=contract.id).count() == coverage["atomic_claims_extracted"]
        assert all(claim.review_status == "approved" for claim in contract.claims)
        assert all(claim.span_start is not None and claim.span_end > claim.span_start
                   for claim in contract.claims)
        assert all(claim.beat_id for claim in contract.claims)
        assert all(claim.source_sentence_text for claim in contract.claims)
        assert any(claim.assertion_role == "REPORTING_ACT" for claim in contract.claims)
        assert any(claim.assertion_role == "QUOTED_PROPOSITION" and claim.modality == "ALLEGED"
                   for claim in contract.claims)
        for source_key in ("S15_C016", "S15_C067", "S15_C070", "S15_C074",
                           "S15_C101", "S15_C164", "S15_C165", "S15_C166"):
            assert sum(claim.claim_key.startswith(source_key + "_") for claim in contract.claims) >= 2
