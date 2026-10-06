import time
from contextlib import contextmanager
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.models import AgentRun
from app.utils import ensure_utc, utc_now
from app.providers.generation.base import GenerationResult


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
    row = AgentRun(
        case_id=case_id,
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
    row = AgentRun(
        case_id=case_id,
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
        db.commit()


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
