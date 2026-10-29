"""A found picture is saved even when a provider's title or credit is longer than its column."""
from __future__ import annotations

from uuid import uuid4

from app.db.models import Case, VisualAsset


def test_a_provider_text_longer_than_its_column_is_cut_to_fit_and_the_picture_is_saved(db_session):
    case = Case(canonical_title="Long title", slug=f"lt-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.flush()
    asset = VisualAsset(case_id=case.id, asset_code="V-LONG", local_path="x.jpg", title="t" * 900, credit="c" * 700,
                        source_name="s" * 400, location="l" * 301, found_for="f" * 301, license="L" * 201,
                        rights_reason="r" * 301, description="d" * 5000)
    db_session.add(asset)
    db_session.commit()
    db_session.refresh(asset)

    assert len(asset.title) == 500 and len(asset.credit) == 500 and len(asset.source_name) == 300
    assert len(asset.location) == 300 and len(asset.found_for) == 300 and len(asset.license) == 200
    assert len(asset.rights_reason) == 300 and len(asset.description) == 5000, "text columns have no limit"


def test_a_short_text_and_none_are_left_alone(db_session):
    asset = VisualAsset(case_id=1, asset_code="V-SHORT", title="Short", credit=None)
    assert asset.title == "Short" and asset.credit is None
