from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import AgentRun, DocumentaryJob, ProviderCall
from app.services.budget import BudgetService, StageTimeBudgetExceeded
from app.utils import ensure_utc, utc_now

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    # A plain top-level import pulls in app.providers' package __init__,
    # which imports app.research_engine.provider — and research_engine now
    # imports track_research_call/stamp_run from this module (below), so a
    # real import here is circular. GenerationResult is only used for a
    # type hint; `from __future__ import annotations` makes that safe to
    # defer to type-checking time only.
    from app.providers.generation.base import GenerationResult

_run_context: ContextVar[dict | None] = ContextVar("agent_run_context", default=None)
_AGENT_METADATA_KEYS = {"documentary_job_id", "language", "pipeline_stage"}


@contextmanager
def documentary_run_context(
    documentary_job_id: int | None = None,
    case_id: int | None = None,
    language: str | None = None,
    pipeline_stage: str | None = None,
    stage_budget_seconds: float | None = None,
    stage_started_monotonic: float | None = None,
):
    """Attach documentary job metadata to every nested AgentRun.

    Context variables are task-local, so parallel language branches do not
    leak attribution into one another.
    """
    token = _run_context.set({
        "documentary_job_id": documentary_job_id,
        "case_id": case_id,
        "language": language,
        "pipeline_stage": pipeline_stage,
        "stage_budget_seconds": stage_budget_seconds,
        "stage_started_monotonic": stage_started_monotonic,
    })
    try:
        yield
    finally:
        _run_context.reset(token)


def _documentary_metadata() -> dict:
    return _run_context.get() or {}


def record_run(
    db: Session,
    case_id: int | None,
    agent_name: str,
    status: str,
    started_at: datetime | None = None,
    input_summary: str | None = None,
    output_summary: str | None = None,
    error: str | None = None,
) -> AgentRun:
    completed = utc_now() if status in ("completed", "failed") else None
    duration = None
    if started_at and completed:
        duration = int((completed - ensure_utc(started_at)).total_seconds() * 1000)
    metadata = {k: v for k, v in _documentary_metadata().items()
                if k in _AGENT_METADATA_KEYS}
    row = AgentRun(
        case_id=case_id,
        **metadata,
        agent_name=agent_name,
        status=status,
        started_at=ensure_utc(started_at) or utc_now(),
        completed_at=completed,
        duration_ms=duration,
        input_summary=input_summary,
        output_summary=output_summary,
        error=error,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@contextmanager
def track_run(db: Session, case_id: int | None, agent_name: str, input_summary: str | None = None):
    """Context manager that records an AgentRun around a block of work."""
    started = utc_now()
    monotonic = time.monotonic()
    context = _documentary_metadata()
    metadata = {k: v for k, v in context.items() if k in _AGENT_METADATA_KEYS}
    row = AgentRun(
        case_id=case_id,
        **metadata,
        agent_name=agent_name,
        status="running",
        started_at=started,
        input_summary=input_summary,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        _check_stage_time_budget(context, case_id)
        _check_case_budget(db, case_id, context.get("pipeline_stage"))
        _check_case_pause(db, case_id)
        _set_call_progress(db, row, context, started)
        yield row
    except (Exception, asyncio.CancelledError) as e:
        row.status = "failed"
        row.error = str(e)[:2000]
        row.completed_at = utc_now()
        row.duration_ms = int((time.monotonic() - monotonic) * 1000)
        if row.provider is None:
            # stamp_run never ran (the failure came from inside the
            # provider call itself, before a GenerationResult existed) —
            # but the provider may still have billed real usage before
            # raising (output truncated at max_tokens, or a repair call
            # whose JSON still failed to parse). GenerationError carries
            # that usage when it has it; a local import avoids a circular
            # import with app.providers at module load time (see the
            # TYPE_CHECKING note above).
            from app.providers.generation.base import GenerationError

            if isinstance(e, GenerationError) and e.provider is not None:
                row.provider = e.provider
                row.model = e.model
                row.input_tokens = e.input_tokens
                row.output_tokens = e.output_tokens
                row.total_tokens = e.total_tokens
                row.estimated_cost_usd = e.cost_usd
                row.cached_tokens = e.cached_tokens
                row.reasoning_tokens = e.reasoning_tokens
        try:
            # stamp_run may already have copied real provider/model/token
            # usage onto `row` before the failure (e.g. a successful,
            # billed generation whose JSON output then failed downstream
            # validation) — that spend must still reach the ledger.
            # _record_provider_call's own guard is a no-op when nothing was
            # ever stamped (a timeout/network failure with no usage at all).
            _record_provider_call(db, row)
        except Exception:
            log.exception(
                "Cost ledger: failed to record a provider call for a failed "
                "run (case_id=%s, stage=%s)", row.case_id, row.pipeline_stage,
            )
        _clear_call_progress(db, context)
        db.commit()
        raise
    else:
        row.status = "completed"
        row.completed_at = utc_now()
        row.duration_ms = int((time.monotonic() - monotonic) * 1000)
        try:
            _record_provider_call(db, row)
            _check_stage_time_budget(context, case_id)
        except Exception:
            _clear_call_progress(db, context)
            db.commit()
            raise
        _clear_call_progress(db, context)
        db.commit()


@contextmanager
def track_research_call(agent_name: str, input_summary: str | None = None,
                        case_id: int | None = None):
    """Like track_run, but for the research engine (app/research_engine),
    which is deliberately DB-free — it has no session to pass in. Opens
    its own short-lived session and reads case_id from the same context
    var app.research_engine.provider._launch already populates via
    documentary_run_context, so a research call gets exactly the same
    case-budget check and provider_calls ledger entry as every other
    paid call (previously it had neither: a case's real research spend
    was invisible and never checked against its EUR cap)."""
    from app.db.base import SessionLocal

    context = _documentary_metadata()
    case_id = case_id if case_id is not None else context.get("case_id")
    with SessionLocal() as db, track_run(db, case_id, agent_name, input_summary) as row:
        yield row


def _stage_time_error(context: dict, case_id: int | None) -> StageTimeBudgetExceeded | None:
    budget = context.get("stage_budget_seconds")
    started = context.get("stage_started_monotonic")
    stage = context.get("pipeline_stage")
    if case_id is None or not stage or budget is None or started is None:
        return None
    elapsed = max(time.monotonic() - float(started), 0.0)
    if elapsed <= float(budget) + 1e-9:
        return None
    reason = (
        f"Stage {stage} stopped: time budget {float(budget):.1f}s exceeded "
        f"(elapsed {elapsed:.1f}s)."
    )
    return StageTimeBudgetExceeded(
        case_id, stage, reason,
        {
            "stage_time_budget_exceeded": True,
            "stage": stage,
            "budget_seconds": float(budget),
            "elapsed_seconds": round(elapsed, 1),
            "reason": reason,
        },
    )


def _check_stage_time_budget(context: dict, case_id: int | None) -> None:
    error = _stage_time_error(context, case_id)
    if error is not None:
        raise error


def _check_case_budget(db: Session, case_id: int | None, stage: str | None) -> None:
    """The only cost limit: refuse the next paid provider call once the
    case's actual recorded spend has reached its EUR cap. No estimates —
    see app.services.budget.BudgetService.check."""
    if case_id is None:
        return
    BudgetService(db).check(case_id, stage)


def _check_case_pause(db: Session, case_id: int | None) -> None:
    """No new paid call may start while the case is held (pause/stop/trash) or the
    global emergency stop is active. A call already in flight finishes normally and
    is still booked; this check runs before the call, not after."""
    from app.documentary.pipeline.errors import JobPaused
    from app.services.case_control import case_held, global_stop_active

    if global_stop_active(db) or (case_id is not None and case_held(db, case_id)):
        raise JobPaused()


def _set_call_progress(db: Session, row: AgentRun, context: dict, started: datetime) -> None:
    job_id = row.documentary_job_id
    stage = row.pipeline_stage
    if job_id is None or not stage:
        return
    job = db.get(DocumentaryJob, job_id)
    if job is None:
        return
    call_number = db.query(func.count(AgentRun.id)).filter(
        AgentRun.documentary_job_id == job_id,
        AgentRun.pipeline_stage == stage,
        AgentRun.id <= row.id,
    ).scalar() or 1
    result = json.loads(job.result_json or "{}")
    result["call_progress"] = {
        "active": True,
        "stage": stage,
        "call_number": int(call_number),
        "provider": ai_config.providers.generation,
        "role": row.role,
        "started_at": started.isoformat(),
        "elapsed_seconds": 0.0,
        "stage_budget_seconds": context.get("stage_budget_seconds"),
    }
    job.result_json = json.dumps(result, ensure_ascii=False, default=str)
    job.updated_at = utc_now()
    db.commit()


def _clear_call_progress(db: Session, context: dict) -> None:
    job_id = context.get("documentary_job_id")
    if job_id is None:
        return
    job = db.get(DocumentaryJob, job_id)
    if job is None:
        return
    result = json.loads(job.result_json or "{}")
    if "call_progress" in result:
        result.pop("call_progress", None)
        job.result_json = json.dumps(result, ensure_ascii=False, default=str)
        job.updated_at = utc_now()


def _record_provider_call(db: Session, run: AgentRun) -> None:
    """Copy provider-reported usage into the EUR budget ledger.

    A provider that omits usage is still represented once for the stage with
    its configured estimate and ``estimated=true``. This keeps the budget
    gate conservative without inventing provider usage numbers.

    A provider that DOES report real token usage, for a model with no entry
    in config/parameters/pricing.json, is a configuration defect, not a
    "usage unknown" situation — it is recorded with its real tokens, a
    non-empty ``error``, and a deliberately pessimistic cost_eur (the real
    tokens priced at the most expensive configured model's rate), never
    folded into the usage-less-retry estimate (which would hide a real,
    priced call behind a stage-level guess, or zero it out entirely once one
    estimate for the stage already exists). The pessimistic price keeps the
    EUR-20/case actual-spend cap meaningful while the missing price gets
    fixed — a silent cost_eur=None here would let the cap go uncounted
    exactly where the ledger is least trustworthy.
    """
    if run.case_id is None or run.provider is None:
        return
    from app.services.cost_model import (
        conservative_unpriced_cost_eur,
        estimate_stage_eur,
        model_cost_eur,
    )

    stage = run.pipeline_stage or run.agent_name
    tokens_reported = run.input_tokens is not None and run.output_tokens is not None
    cost_eur = None
    cost_usd = run.estimated_cost_usd
    price_missing = False
    if cost_usd is not None:
        cost_eur = float(cost_usd) * ai_config.budget.usd_to_eur
    elif tokens_reported:
        cost_eur = model_cost_eur(run.model, run.input_tokens, run.output_tokens,
                                  cached_tokens=run.cached_tokens,
                                  cache_write_tokens=run.cache_write_tokens)
        price_missing = cost_eur is None
    estimated = cost_eur is None
    error = None
    if price_missing:
        error = f"no price entry for model {run.model!r} (stage {stage})"[:200]
        log.error(
            "Cost ledger: %s has real token usage (in=%s out=%s) but no "
            "configured price — booked at a conservative upper-bound "
            "cost, not the real (unknown) one.",
            error, run.input_tokens, run.output_tokens,
        )
        cost_eur = conservative_unpriced_cost_eur(run.input_tokens or 0, run.output_tokens or 0)
    elif estimated:
        # Do not multiply a stage estimate by every usage-less retry.
        already_estimated = db.query(ProviderCall.id).filter(
            ProviderCall.case_id == run.case_id,
            ProviderCall.stage == stage,
            ProviderCall.estimated.is_(True),
        ).first()
        cost_eur = 0.0 if already_estimated else estimate_stage_eur(db, run.case_id, stage)
    BudgetService(db).record(
        case_id=run.case_id, stage=stage, model=run.model,
        units=(run.total_tokens or ((run.input_tokens or 0) + (run.output_tokens or 0))),
        cost_usd=cost_usd if not estimated else None,
        cost_eur=cost_eur, tokens_in=run.input_tokens, tokens_out=run.output_tokens,
        estimated=estimated, error=error, ok=(run.status != "failed"),
        is_test_data=(run.model == "recorded-e2e"),
        documentary_job_id=run.documentary_job_id,
        duration_ms=run.duration_ms,
        tokens_cached=run.cached_tokens, tokens_reasoning=run.reasoning_tokens,
        reasoning_effort=run.reasoning_effort, cache_write_tokens=run.cache_write_tokens,
        prompt_prefix_sha256=run.prompt_prefix_sha256, request_id=run.gateway_request_id,
        cost_source=("estimate" if estimated else "provider_reported" if cost_usd is not None
                     else "computed_from_tokens"),
    )


def stamp_run(
    run: AgentRun, result: GenerationResult | None, role: str, text_hash: str | None = None
) -> None:
    """Record which provider/model/settings produced a generation, plus
    reported token usage/cost and the hash of the text this run scored or
    produced (so critic scores provably match stored story text).

    `result` is None for a handful of test doubles that short-circuit
    run_agent()'s (data, GenerationResult) contract; there is nothing to
    stamp in that case, not an error."""
    if result is None:
        if text_hash is not None:
            run.text_hash = text_hash
        return
    run.provider = result.provider
    run.model = result.model
    run.role = role
    run.temperature = ai_config.generation_for(role).temperature
    run.fallback_used = result.fallback_used
    run.input_tokens = result.input_tokens
    run.output_tokens = result.output_tokens
    run.total_tokens = result.total_tokens
    run.estimated_cost_usd = result.cost_usd
    run.cached_tokens = result.cached_tokens
    run.cache_write_tokens = result.cache_write_tokens
    run.prompt_prefix_sha256 = result.prefix_sha256
    run.gateway_request_id = result.gateway_request_id
    run.reasoning_tokens = result.reasoning_tokens
    run.reasoning_effort = result.reasoning_effort
    run.generation_id = result.generation_id
    if text_hash is not None:
        run.text_hash = text_hash
