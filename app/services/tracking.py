import asyncio
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import AgentRun, DocumentaryJob, ProviderCall
from app.providers.generation.base import GenerationResult
from app.services.budget import BudgetService, StageCostCeilingExceeded, StageTimeBudgetExceeded
from app.utils import ensure_utc, utc_now

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
        _set_call_progress(db, row, context, started)
        yield row
    except (Exception, asyncio.CancelledError) as e:
        row.status = "failed"
        row.error = str(e)[:2000]
        row.completed_at = utc_now()
        row.duration_ms = int((time.monotonic() - monotonic) * 1000)
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
    """
    if run.case_id is None or run.provider is None:
        return
    from app.services.cost_model import (
        configured_stage_cost_ceiling_eur,
        estimate_stage_eur,
        model_cost_eur,
    )

    stage = run.pipeline_stage or run.agent_name
    reported = run.estimated_cost_usd is not None or (
        run.input_tokens is not None and run.output_tokens is not None)
    cost_eur = None
    cost_usd = run.estimated_cost_usd
    if cost_usd is not None:
        cost_eur = float(cost_usd) * ai_config.budget.usd_to_eur
    elif reported:
        cost_eur = model_cost_eur(run.model, run.input_tokens, run.output_tokens)
    estimated = cost_eur is None
    if estimated:
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
        estimated=estimated,
        is_test_data=(run.model == "recorded-e2e"),
        documentary_job_id=run.documentary_job_id,
        duration_ms=run.duration_ms,
    )
    if run.documentary_job_id is None:
        return
    job = db.get(DocumentaryJob, run.documentary_job_id)
    ceiling = configured_stage_cost_ceiling_eur(
        job.mode if job else None,
        stage,
        float(job.target_minutes) if job and job.target_minutes else None,
    )
    if ceiling is None:
        return
    spent = db.query(func.coalesce(func.sum(ProviderCall.cost_eur), 0.0)).filter(
        ProviderCall.documentary_job_id == run.documentary_job_id,
        ProviderCall.stage == stage,
        ProviderCall.cost_eur.is_not(None),
        ProviderCall.is_test_data.is_(False),
    ).scalar() or 0.0
    if float(spent) > ceiling + 1e-9:
        reason = (
            f"Pilot {stage} stopped: stage cost ceiling €{ceiling:.2f} "
            f"exceeded (spent €{float(spent):.2f})."
        )
        raise StageCostCeilingExceeded(
            run.case_id, stage, reason,
            {
                "cost_ceiling_exceeded": True,
                "stage": stage,
                "ceiling_eur": ceiling,
                "spent_eur": round(float(spent), 6),
                "reason": reason,
            },
        )


def stamp_run(
    run: AgentRun, result: GenerationResult, role: str, text_hash: str | None = None
) -> None:
    """Record which provider/model/settings produced a generation, plus
    reported token usage/cost and the hash of the text this run scored or
    produced (so critic scores provably match stored story text)."""
    run.provider = result.provider
    run.model = result.model
    run.role = role
    run.temperature = ai_config.generation_for(role).temperature
    run.fallback_used = result.fallback_used
    run.input_tokens = result.input_tokens
    run.output_tokens = result.output_tokens
    run.total_tokens = result.total_tokens
    run.estimated_cost_usd = result.cost_usd
    run.generation_id = result.generation_id
    if text_hash is not None:
        run.text_hash = text_hash
