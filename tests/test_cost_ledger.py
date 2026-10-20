"""Cost-ledger correctness (ROADMAP.md Part 0b).

Three real bugs, reproduced here against the real tracking/budget code
(never mocks of the ledger itself):

1. A failed call that had already consumed real, billed tokens before it
   failed (e.g. a successful generation whose JSON output then failed
   downstream validation) must still reach the provider_calls ledger —
   track_run's except branch previously never booked anything.
2. A model with real reported usage but no config/parameters/pricing.json
   entry must fail loudly (a distinct error row with its real tokens kept),
   never be silently folded into the usage-less-retry estimate (which
   would, after the first such call, zero every subsequent one to EUR 0 —
   the "~223 Haiku calls booked at EUR 0" bug).
3. A call that never got any provider response at all (timeout, before any
   usage could exist) must not fabricate a cost row.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.models import AgentRun, Case, ProviderCall
from app.providers.generation.base import GenerationError, GenerationResult
from app.services.budget import BudgetService
from app.services.cost_model import model_cost_eur
from app.services.tracking import documentary_run_context, stamp_run, track_run


def test_model_cost_eur_prices_a_dated_gateway_snapshot_like_its_base_model():
    """config/parameters/pricing.json only has the base alias ("cheap" ->
    gemini-3.8-flash); a gateway response that echoes a dated snapshot of
    it (gemini-3.8-flash-20251001, same shape as the one observed live on case 5) must price the
    same as the base model, not silently return no price."""
    base = model_cost_eur("gemini-3.8-flash", 1000, 500)
    dated = model_cost_eur("gemini-3.8-flash-20251001", 1000, 500)
    assert base is not None
    assert dated == base


def test_model_cost_eur_still_returns_none_for_a_genuinely_unknown_model():
    assert model_cost_eur("not-a-real-model-at-all", 1000, 500) is None
    assert model_cost_eur("not-a-real-model-20251001", 1000, 500) is None


def _case(db, cap_eur: float = 20.0) -> Case:
    case = Case(canonical_title=f"Cost ledger proof {uuid4().hex[:8]}",
                slug=f"cost-ledger-{uuid4().hex[:8]}", language="en",
                total_cost_cap_eur=cap_eur)
    db.add(case)
    db.commit()
    return case


def test_a_call_that_billed_real_tokens_before_failing_downstream_still_books_its_cost(db_session):
    """Mirrors app/services/research_jobs.py's evidence_normalizer shape:
    stamp_run already recorded a real, priced generation; a KeyError from
    processing the model's own (slightly malformed) JSON then raises
    inside the same track_run block."""
    case = _case(db_session)
    with (pytest.raises(KeyError),
          documentary_run_context(case_id=case.id, pipeline_stage="evidence_normalizer"),
          track_run(db_session, case.id, "evidence_normalizer") as row):
        result = GenerationResult(
            text="{}", model="claude-sonnet-5-5", provider="apimaster",
            input_tokens=1000, output_tokens=200, total_tokens=1200,
        )
        stamp_run(row, result, "evidence_normalizer")
        raise KeyError("malformed model output")

    agent_run = db_session.get(AgentRun, row.id)
    assert agent_run.status == "failed"
    assert agent_run.model == "claude-sonnet-5-5"

    call = (db_session.query(ProviderCall)
            .filter(ProviderCall.case_id == case.id, ProviderCall.stage == "evidence_normalizer")
            .one())
    assert call.model == "claude-sonnet-5-5"
    assert call.tokens_in == 1000
    assert call.tokens_out == 200
    assert call.estimated is False
    assert call.cost_eur and call.cost_eur > 0
    assert BudgetService(db_session).snapshot(case.id).spent_eur == pytest.approx(call.cost_eur, abs=1e-5)


def test_a_generation_error_with_billed_usage_books_its_cost_even_with_no_stamp_run(db_session):
    """Mirrors openai_compat.py raising GenerationError for a truncated
    completion or a JSON-repair-exhausted call: stamp_run never ran (no
    GenerationResult was ever returned), but the provider usage the error
    carries must still reach the ledger."""
    case = _case(db_session)
    error = GenerationError(
        "invalid_output", "completion truncated at max_tokens",
        provider="apimaster", model="claude-haiku-4-5",
        input_tokens=300, output_tokens=150, total_tokens=450, cost_usd=0.002,
    )
    with (pytest.raises(GenerationError),
          documentary_run_context(case_id=case.id, pipeline_stage="fact_extractor"),
          track_run(db_session, case.id, "fact_extractor")):
        raise error

    call = (db_session.query(ProviderCall)
            .filter(ProviderCall.case_id == case.id, ProviderCall.stage == "fact_extractor")
            .one())
    assert call.model == "claude-haiku-4-5"
    assert call.tokens_in == 300
    assert call.tokens_out == 150
    assert call.cost_usd == pytest.approx(0.002)
    assert call.cost_eur is not None and call.cost_eur > 0


def test_a_failure_with_no_provider_response_at_all_books_nothing(db_session):
    """A pure network/timeout failure (no response ever received) must
    not fabricate a ledger row — there is nothing to book."""
    case = _case(db_session)
    with (pytest.raises(GenerationError),
          documentary_run_context(case_id=case.id, pipeline_stage="fact_extractor"),
          track_run(db_session, case.id, "fact_extractor") as row):
        raise GenerationError("timeout", "request timed out")

    agent_run = db_session.get(AgentRun, row.id)
    assert agent_run.status == "failed"
    assert db_session.query(ProviderCall).filter(
        ProviderCall.case_id == case.id, ProviderCall.stage == "fact_extractor"
    ).count() == 0
    assert BudgetService(db_session).snapshot(case.id).spent_eur == 0.0


def test_a_model_with_no_price_entry_fails_loudly_and_still_counts_toward_the_cap(db_session):
    """config/parameters/pricing.json has no entry for this model string
    (e.g. a gateway alias never added to pricing) even though the call
    really happened and reported real tokens. This must never be folded
    into the generic usage-less-retry estimate (which would, after the
    first call for the stage, silently zero every further one) — but it
    must also never go uncounted against the EUR cap just because its
    exact price is unknown (a repeated unpriced model could otherwise
    spend real money forever without the cap ever firing)."""
    from app.services.cost_model import conservative_unpriced_cost_eur

    case = _case(db_session)
    for _ in range(2):
        with (documentary_run_context(case_id=case.id, pipeline_stage="fact_extractor"),
              track_run(db_session, case.id, "fact_extractor") as row):
            result = GenerationResult(
                text="{}", model="not-a-priced-model", provider="apimaster",
                input_tokens=400, output_tokens=100, total_tokens=500,
            )
            stamp_run(row, result, "fact_extractor")

    calls = (db_session.query(ProviderCall)
             .filter(ProviderCall.case_id == case.id, ProviderCall.stage == "fact_extractor")
             .order_by(ProviderCall.id)
             .all())
    assert len(calls) == 2
    expected = conservative_unpriced_cost_eur(400, 100)
    assert expected > 0
    for call in calls:
        # A conservative (never-under-counting), not a real, price — every
        # call gets its own, never deduped/zeroed like the usage-less-retry
        # estimate path.
        assert call.cost_eur == pytest.approx(expected)
        assert call.estimated is True
        assert call.error and "no price entry" in call.error
        assert call.tokens_in == 400
        assert call.tokens_out == 100
    assert BudgetService(db_session).snapshot(case.id).spent_eur == pytest.approx(2 * expected, abs=1e-5)


def test_missing_usage_retries_still_dedupe_to_zero_after_the_first_stage_estimate(db_session):
    """Unaffected regression: a provider that genuinely never reports
    usage still gets exactly one configured stage estimate, and every
    further usage-less retry for that stage is zeroed (never multiplied)."""
    case = _case(db_session)
    for _ in range(3):
        with (documentary_run_context(case_id=case.id, pipeline_stage="grounding_validator"),
              track_run(db_session, case.id, "grounding_validator") as row):
            result = GenerationResult(
                text="", model="claude-haiku-4-5", provider="apimaster",
            )
            stamp_run(row, result, "grounding_validator")

    calls = (db_session.query(ProviderCall)
             .filter(ProviderCall.case_id == case.id, ProviderCall.stage == "grounding_validator")
             .order_by(ProviderCall.id)
             .all())
    assert len(calls) == 3
    assert all(call.estimated for call in calls)
    assert all(call.error is None for call in calls)
    assert calls[1].cost_eur == 0.0
    assert calls[2].cost_eur == 0.0
