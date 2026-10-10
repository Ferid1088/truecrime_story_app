"""Phase 6 gates: compression safety on real case 6 claims."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import EpistemicContractSet
from app.longform.compression import check_compression_safety, evaluate_compression_cases
from scripts.evaluate_compression_safety import _cases


def test_case6_compression_safety_real_claim_set():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    with Session() as db:
        contract = db.query(EpistemicContractSet).filter_by(
            case_id=6, story_version_id=15, version=1
        ).one()
        result = evaluate_compression_cases(_cases(contract.claims))
        assert result["total"] == 660
        assert result["expected_violations"] == 61
        assert result["expected_safe"] == 599
        assert result["false_positive"] == 0
        assert result["false_negative"] == 0
        assert result["false_positive_rate"] == 0.0
        assert result["false_negative_rate"] == 0.0

        quoted = next(c for c in contract.claims if c.claim_key == "S15_C009_02")
        check = check_compression_safety(quoted.modality, "Anthony did it")
        assert not check.passed
        assert check.rewritten_modality == "ESTABLISHED"

        established = next(c for c in contract.claims if c.claim_key == "S15_C080")
        safe = check_compression_safety(
            established.modality,
            "The record establishes that the strike caused a rapidly fatal two-inch chest wound.",
        )
        assert safe.passed
