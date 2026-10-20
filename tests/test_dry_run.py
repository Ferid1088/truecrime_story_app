from uuid import uuid4

from app.db.models import Case
from app.services.cost_model import estimate_lines, load_pricing
from app.services.dry_run import documentary_dry_run


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
        "research", "language_draft", "language_rewrite:en", "language_inspect:en",
        "language_final:en", "spoken:en", "performance:en", "tts:en",
        "thumbnail", "short_selection", "short_render:en", "host:en",
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
    db_session.add(StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                                narrative_angle="angle", story_text="word " * 20000,
                                status="approved"))
    db_session.commit()
    long = documentary_dry_run(db_session, case, ["en"])
    assert long["estimated_cost_eur"] > short["estimated_cost_eur"]
    assert next(row for row in long["stages"] if row["stage"] == "tts:en")["units"]["characters"] > \
        next(row for row in short["stages"] if row["stage"] == "tts:en")["units"]["characters"]


def test_120_second_pilot_scales_the_editorial_token_target(db_session):
    case = Case(canonical_title="Pilot length", slug=f"pilot-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    from app.db.models import StoryVersion

    # A pre-existing long master must not make the pilot estimate a full film.
    db_session.add(StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                                narrative_angle="angle", story_text="word " * 12000,
                                status="approved"))
    db_session.commit()
    result = documentary_dry_run(db_session, case, ["en"], pilot_seconds=120)
    assert result["metrics"]["target_minutes"]["expected"] == 2.0
    assert result["metrics"]["story_words"]["expected"] == 300.0
    assert result["token_assumptions"]["story_tokens"]["expected"] == 390.0
    assert result["metrics"]["source_words_basis"] == "assumed"


def test_price_change_changes_estimate_and_redo_uses_same_stage_model(db_session):
    case = Case(canonical_title="Price sensitivity", slug=f"price-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    pricing = load_pricing()
    before = sum(line.expected for line in estimate_lines(db_session, case, ["en"], pricing=pricing)[0])
    pricing["tts"]["eleven_v4"]["eur_per_1m_characters"] = 900.0
    after = sum(line.expected for line in estimate_lines(db_session, case, ["en"], pricing=pricing)[0])
    assert after > before
