from uuid import uuid4

from app.db.models import Case
from app.services.dry_run import documentary_dry_run
from app.services.cost_model import estimate_lines, load_pricing


def test_dry_run_builds_english_prompts_and_sends_nothing(db_session):
    case = Case(canonical_title="Tiny dry run", slug=f"tiny-dry-{uuid4().hex[:8]}",
                language="en")
    db_session.add(case)
    db_session.commit()
    result = documentary_dry_run(db_session, case, ["en"])
    assert result["dry_run"] is True
    assert result["languages"] == ["en"]
    assert result["estimated_cost_eur"] == result["estimate_range_eur"]["expected"]
    assert result["estimate_range_eur"]["low"] < result["estimate_range_eur"]["expected"] < result["estimate_range_eur"]["high"]
    assert {row["stage"] for row in result["stages"]} >= {
        "research", "master_story", "spoken:en", "performance:en", "tts:en",
        "thumbnail", "short_selection", "short_render:en", "translation", "host:en",
    }
    assert any(row["status"] == "skipped" for row in result["stages"])
    assert result["within_cap"] is True
    assert result["sent"] is False
    assert all(row["sends"] is False and row["prompt"] for row in result["stages"])


def test_dry_run_changes_when_story_length_changes(db_session):
    case = Case(canonical_title="Narration length", slug=f"narration-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    short = documentary_dry_run(db_session, case, ["en"])
    from app.db.models import StoryVersion
    db_session.add(StoryVersion(case_id=case.id, version=1, kind="master", language="en",
                                narrative_angle="angle", story_text="word " * 20000,
                                status="approved"))
    db_session.commit()
    long = documentary_dry_run(db_session, case, ["en"])
    assert long["estimated_cost_eur"] > short["estimated_cost_eur"]
    assert next(row for row in long["stages"] if row["stage"] == "tts:en")["units"]["characters"] > \
        next(row for row in short["stages"] if row["stage"] == "tts:en")["units"]["characters"]


def test_price_change_changes_estimate_and_redo_uses_same_stage_model(db_session):
    case = Case(canonical_title="Price sensitivity", slug=f"price-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    pricing = load_pricing()
    before = sum(line.expected for line in estimate_lines(db_session, case, ["en"], pricing=pricing)[0])
    pricing["image"]["thumbnail"]["eur_per_image"] = 9.0
    after = sum(line.expected for line in estimate_lines(db_session, case, ["en"], pricing=pricing)[0])
    assert after > before
