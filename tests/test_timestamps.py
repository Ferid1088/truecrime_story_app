from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import AgentRun, Case, ResearchJob, Source
from app.services import research_jobs
from app.services.tracking import record_run, track_run
from app.utils import ensure_utc, slugify, utc_now


def test_utc_now_is_aware_utc():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_ensure_utc_normalises_naive_and_aware():
    naive = datetime(2024, 1, 1, 12, 0, 0)
    assert ensure_utc(naive) == datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    aware = utc_now()
    assert ensure_utc(aware) == aware
    assert ensure_utc(None) is None


def test_new_rows_have_aware_utc_timestamps(db_session):
    case = Case(canonical_title="X", slug=slugify("X"))
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    assert case.created_at.tzinfo is not None
    assert abs((case.created_at - utc_now()).total_seconds()) < 10


def test_record_run_duration_with_naive_legacy_started_at(db_session):
    """Rows written before the refactor read back naive — duration must still work."""
    run = record_run(
        db_session,
        case_id=None,
        agent_name="T",
        status="completed",
        started_at=utc_now().replace(tzinfo=None) - timedelta(seconds=2),  # legacy naive value
    )
    assert run.duration_ms is not None and run.duration_ms >= 1500
    assert run.started_at.tzinfo is not None


def test_track_run_records_aware_timestamps(db_session):
    with track_run(db_session, None, "T") as run:
        pass
    assert run.started_at.tzinfo is not None
    assert run.completed_at.tzinfo is not None
    assert run.duration_ms is not None and run.duration_ms >= 0


def test_source_timestamps_via_api(client, db_session):
    resp = client.post("/api/cases", json={"canonical_title": "TZ Case", "language": "en"})
    assert resp.status_code == 200
    case_id = resp.json()["id"]
    src = client.post(
        f"/api/cases/{case_id}/sources",
        json={"title": "S", "url": "https://ex.com", "source_type": "article"},
    )
    assert src.status_code == 200
    body = client.get(f"/api/cases/{case_id}/sources").json()
    created = datetime.fromisoformat(body[0]["created_at"])
    assert created.tzinfo is not None and created.utcoffset() == timedelta(0)


def test_legacy_naive_rows_readable_and_job_timeout_works(db_session):
    """A job with a naive legacy created_at must not crash poll_job."""
    job = ResearchJob(
        provider="openrouter",
        job_type="research",
        status="running",
        external_job_id="x",
    )
    # Simulate a pre-refactor naive timestamp as stored by old rows.
    job.created_at = utc_now().replace(tzinfo=None) - timedelta(days=2)  # naive, past timeout
    db_session.add(job)
    db_session.commit()

    fake_provider = AsyncMock()
    fake_provider.poll.return_value = SimpleNamespace(
        status="running", result=None, error=None, meta={}
    )
    with patch.object(research_jobs, "_provider_for", return_value=fake_provider):
        import asyncio

        out = asyncio.run(research_jobs.poll_job(db_session, job))
    assert out.status == "failed"
    assert "timed out" in out.error
    assert out.completed_at.tzinfo is not None
    # Provider is always polled first — a result that finished between
    # polls is ingested rather than discarded by the timeout guard.
    fake_provider.poll.assert_called_once_with("x")


def test_job_dict_serializes_aware_timestamps(db_session):
    job = ResearchJob(provider="openrouter", job_type="discovery", status="queued")
    db_session.add(job)
    db_session.commit()
    d = research_jobs.job_dict(job)
    assert d["created_at"].tzinfo is not None
    import json

    json.dumps(d, default=str)  # serialisable shape intact
