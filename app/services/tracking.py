import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.models import AgentRun
from app.utils import ensure_utc, utc_now
from app.providers.generation.base import GenerationResult


_run_context: ContextVar[dict | None] = ContextVar("agent_run_context", default=None)


@contextmanager
def documentary_run_context(
    documentary_job_id: int | None = None,
    case_id: int | None = None,
    language: str | None = None,
    pipeline_stage: str | None = None,
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
    metadata = {k: v for k, v in _documentary_metadata().items() if k != "case_id"}
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
    metadata = {k: v for k, v in _documentary_metadata().items() if k != "case_id"}
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
        yield row
    except Exception as e:
        row.status = "failed"
        row.error = str(e)[:2000]
        row.completed_at = utc_now()
        row.duration_ms = int((time.monotonic() - monotonic) * 1000)
        db.commit()
        raise
    else:
        row.status = "completed"
        row.completed_at = utc_now()
        row.duration_ms = int((time.monotonic() - monotonic) * 1000)
        _record_provider_call(db, row)
        db.commit()


def _record_provider_call(db: Session, run: AgentRun) -> None:
    """Copy provider-reported usage into the EUR budget ledger.

    A provider that omits usage is still represented once for the stage with
    its configured estimate and ``estimated=true``. This keeps the budget
    gate conservative without inventing provider usage numbers.
    """
    if run.case_id is None or run.provider is None:
        return
    from app.db.models import ProviderCall
    from app.services.budget import BudgetService
    from app.services.cost_model import estimate_stage_eur, model_cost_eur

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
        if already_estimated:
            cost_eur = 0.0
        else:
            cost_eur = estimate_stage_eur(db, run.case_id, stage)
    BudgetService(db).record(
        case_id=run.case_id, stage=stage, model=run.model,
        units=(run.total_tokens or ((run.input_tokens or 0) + (run.output_tokens or 0))),
        cost_usd=cost_usd if not estimated else None,
        cost_eur=cost_eur, tokens_in=run.input_tokens, tokens_out=run.output_tokens,
        estimated=estimated,
        documentary_job_id=run.documentary_job_id,
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
