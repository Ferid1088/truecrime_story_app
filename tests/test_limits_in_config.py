"""Every limit that bounds a run's searches, checks, retries or spend is in config/, not in code."""

import json
from pathlib import Path

from app.core.ai_config import ai_config

ROOT = Path(__file__).resolve().parent.parent
MOVED = ("MAX_EXACT_QUERIES", "MAX_CONTEXT_QUERIES", "MAX_ALIASES", "VERIFY_PER_REQUEST", "MIN_ENTITY_SCORE",
         "DIRECT_CHUNK", "MAX_MISS_TAGS", "INSPECTOR_ATTEMPTS", "REVISION_ROUNDS", "RESEARCH_POLL_SECONDS",
         "RESEARCH_MAX_HOURS", "DISCOVERY_COST_ESTIMATE_SAMPLE_SIZE", "MAX_QUERY_WORDS")


def test_the_limits_that_used_to_be_constants_in_code_are_read_from_config():
    code = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in (ROOT / "app").rglob("*.py"))
    assert [name for name in MOVED if name in code] == []
    limits = ai_config.limits_settings()
    assert (limits.planner.max_exact_queries, limits.planner.max_context_queries, limits.planner.max_aliases) == (4, 3, 5)
    assert (limits.production_search.verify_per_request, limits.director.beats_per_call) == (3, 10)
    assert limits.research_job.max_hours == 6.0 and limits.language_pipeline.inspector_attempts == 2


def test_the_research_cost_cap_of_the_picture_supply_is_thirty_euro():
    assert ai_config.supply.research_cost_cap_eur == 30.0
    assert json.loads((ROOT / "config/parameters/visuals.json").read_text())["supply"]["research_cost_cap_eur"] == 30.0


def test_config_holds_no_limit_that_nothing_reads():
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in (ROOT / "config").rglob("*.json"))
    for dead in ("research_profiles", "stage_estimate_eur", "max_search_calls_per_language",
                 "max_followup_rounds", "minimum_result_novelty"):
        assert dead not in text, dead
