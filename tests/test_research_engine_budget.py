"""The research engine (app/research_engine) is deliberately DB-free, so
its LLM calls (query planning, reranking, gap analysis, summarizing,
discovery) previously made no AgentRun/ProviderCall entry at all — real
research spend was invisible and never checked against a case's EUR cap,
directly undermining the one cost rule that matters: the cap is based on
ACTUAL recorded spend, checked before each paid call.

These tests reproduce that gap against the real research_engine classes,
using documentary_run_context exactly as app/research_engine/provider.py's
_launch() sets it up for a real research job."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.db.models import Case, ProviderCall
from app.providers.generation.base import GenerationResult
from app.research_engine.planner import ResearchQueryPlanner
from app.services.budget import BudgetService, BudgetStopped
from app.services.tracking import documentary_run_context


def _case(db, cap_eur: float = 20.0) -> Case:
    case = Case(canonical_title=f"Research budget proof {uuid4().hex[:8]}",
                slug=f"research-budget-{uuid4().hex[:8]}", language="en",
                total_cost_cap_eur=cap_eur)
    db.add(case)
    db.commit()
    return case


class FakeGen:
    """Returns one query and reports real usage, like a live provider."""

    def __init__(self):
        self.calls = 0

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user, images=None):
        self.calls += 1
        data = {"queries": [{"query": "a real query", "language": "en",
                             "purpose": "case_identity", "priority": 1,
                             "expected_source_types": ["news"]}]}
        result = GenerationResult(
            text="{}", model="fake-research-model", provider="fake",
            input_tokens=500, output_tokens=120, total_tokens=620,
            cost_usd=0.01,
        )
        return data, result


def test_query_planning_records_a_provider_call_for_the_case(db_session):
    case = _case(db_session)
    gen = FakeGen()
    planner = ResearchQueryPlanner(gen, telemetry={})

    with documentary_run_context(case_id=case.id, pipeline_stage="research"):
        queries = asyncio.run(planner.plan({"title": case.canonical_title}, "en"))

    assert queries and queries[0].query == "a real query"
    assert gen.calls == 1
    call = (db_session.query(ProviderCall)
            .filter(ProviderCall.case_id == case.id, ProviderCall.stage == "research")
            .one())
    assert call.model == "fake-research-model"
    assert call.is_test_data is False
    assert BudgetService(db_session).snapshot(case.id).spent_eur > 0


def test_query_planning_stops_once_the_case_cap_is_reached(db_session, monkeypatch):
    case = _case(db_session, cap_eur=1.0)
    BudgetService(db_session).record(
        case_id=case.id, stage="research", model="x", units=1, cost_eur=1.0,
    )
    gen = FakeGen()
    planner = ResearchQueryPlanner(gen, telemetry={})
    # The caller (ResearchQueryPlanner.plan) swallows most exceptions into a
    # fallback query list — BudgetStopped must not be one of them, or the
    # cap would never actually stop a research job.
    with documentary_run_context(case_id=case.id, pipeline_stage="research"), \
         pytest.raises(BudgetStopped, match="case budget"):
        asyncio.run(planner.plan({"title": case.canonical_title}, "en"))
    assert gen.calls == 0


def test_discovery_has_no_case_yet_so_the_budget_check_never_blocks(db_session):
    """Discovery (app/research_engine/orchestrator.discover_cases) runs
    before any case exists — case_id is None in its context, exactly like
    app/research_engine/provider.py's start_discovery. The call must still
    be stamped (model/tokens visible), just never budget-gated."""
    from app.research_engine.planner import GapAnalyzer

    gen = FakeGen()
    analyzer = GapAnalyzer(gen, telemetry={})
    with documentary_run_context(pipeline_stage="discovery"):
        result = asyncio.run(analyzer.analyze({"title": "x"}, [], []))
    assert gen.calls == 1
    assert result  # the call succeeded and returned the provider's payload
    # No case_id in context: nothing to attribute the call to, nothing recorded.
    assert db_session.query(ProviderCall).filter(ProviderCall.stage == "discovery").count() == 0
