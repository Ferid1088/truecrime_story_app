"""Phase 11 publishing plan against real config and case-6 inventory."""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.ai_config import ai_config
from app.db.models import ShortFormConcept
from app.shortform.operations import build_publish_plan


def test_case6_plan_respects_configured_counts_without_mutating_persisted_inventory():
    database_path = Path(__file__).resolve().parents[1] / "truecrime.db"
    if not database_path.exists():
        pytest.skip("repository real database is unavailable")
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    with Session() as db:
        before_count = db.query(ShortFormConcept).filter_by(case_id=6).count()
    counts = {
        platform: cfg.count
        for platform, cfg in ai_config.short_form.distribution.items()
        if cfg.enabled and cfg.count
    }
    plan = build_publish_plan(date.today(), counts, days=14)
    assert Counter(slot.platform for slot in plan) == Counter(counts)
    assert len({slot.day for slot in plan}) > 1
    assert max(Counter(slot.day for slot in plan).values()) < len(plan)
    assert all(slot.approved_only and slot.status == "planned" for slot in plan)
    with Session() as db:
        assert db.query(ShortFormConcept).filter_by(case_id=6).count() == before_count
