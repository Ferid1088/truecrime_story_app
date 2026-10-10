"""Phase 13 persisted mock metrics and honest funnel reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import ShortFormMetric
from app.shortform.metrics import concept_type_comparison


def test_case6_mock_metrics_compare_concept_types_without_conversion_claims():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    with Session() as db:
        rows = db.query(ShortFormMetric).filter_by(model="phase13_mock").all()
        if not rows:
            pytest.skip("Phase 13 mock metrics have not been seeded")
        report = concept_type_comparison(db, 6)
        assert len(rows) == 15
        assert report["comparison_basis"] == "concept_type"
        assert len(report["comparisons"]) == 5
        assert all(item["attribution_status"] == "unavailable" for item in report["comparisons"])
        assert all(row.attribution_status == "unavailable" for row in rows)
        assert all(row.estimated_conversion_rate is None for row in rows)
        assert all("conversion" not in json.loads(row.detail_json) for row in rows)
