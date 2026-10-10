"""Phase 10 repetition and automation-feel checks on real case-6 data."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import ShortFormConcept
from app.shortform.operations import repetition_check
from scripts.phase10_repetition_verify import _candidates


def test_case6_candidate_batch_passes_and_repetitive_batch_fails():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    with Session() as db:
        before_count = db.query(ShortFormConcept).count()
    candidates = _candidates()
    passed, reasons = repetition_check(candidates, max_repeats=1)
    assert passed and reasons == []
    rejected, reasons = repetition_check([candidates[0], candidates[0]], max_repeats=1)
    assert not rejected
    assert {"hook_structure", "cta", "opening_visual", "host_pose"} <= {
        reason.split(" repeats:", 1)[0] for reason in reasons
    }
    with Session() as db:
        assert db.query(ShortFormConcept).count() == before_count
