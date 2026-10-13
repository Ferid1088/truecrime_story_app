from uuid import uuid4

from app.db.models import Case
from app.services.dry_run import documentary_dry_run


def test_dry_run_builds_english_prompts_and_sends_nothing(db_session):
    case = Case(canonical_title="Tiny dry run", slug=f"tiny-dry-{uuid4().hex[:8]}",
                language="en")
    db_session.add(case)
    db_session.commit()
    result = documentary_dry_run(case, ["en"])
    assert result["dry_run"] is True
    assert result["languages"] == ["en"]
    assert result["estimated_cost_eur"] == 6.0
    assert result["within_cap"] is True
    assert result["sent"] is False
    assert all(row["sends"] is False and row["prompt"] for row in result["stages"])
