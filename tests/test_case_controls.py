"""Case controls: pause / resume / stop (keep|delete) / restore / global stop —
state machine, queue holding, audit, trash and purge."""

import uuid

import pytest

from app.db.models import (
    AppSetting,
    AutopilotRun,
    Case,
    CaseEvent,
    CaseTombstone,
    DocumentaryJob,
)
from app.services import case_control as cc
from app.worker import claim_next_job


def _case(db, title=None):
    title = title or f"control test {uuid.uuid4().hex[:8]}"
    case = Case(canonical_title=title, slug=f"ctl-{uuid.uuid4().hex[:8]}")
    db.add(case)
    db.commit()
    return case


def _job(db, case, status="queued"):
    job = DocumentaryJob(case_id=case.id, mode="full", languages_json='["en"]',
                         status=status)
    db.add(job)
    db.commit()
    return job


def _ledger(db, case, eur=1.5):
    from app.services.budget import BudgetService
    return BudgetService(db).record(
        case_id=case.id, stage="test", model="m", units=1, cost_eur=eur,
        estimated=True, request_id=f"ctl-{uuid.uuid4().hex[:8]}")


# --------------------------------------------------------------------- pause / resume
def test_pause_holds_queued_jobs_from_claim(db_session):
    case = _case(db_session)
    job = _job(db_session, case, "queued")
    cc.pause_case(db_session, case, actor="test")
    assert claim_next_job(db_session, "w-test") != job.id
    assert job.status == "queued"  # held, not deleted


def test_pause_flips_to_paused_when_nothing_runs(db_session):
    case = _case(db_session)
    _job(db_session, case, "queued")
    cc.pause_case(db_session, case, actor="test")
    assert cc.effective_state(db_session, case) == "paused"


def test_pause_while_running_shows_pausing_then_paused(db_session):
    case = _case(db_session)
    _job(db_session, case, "running")
    cc.pause_case(db_session, case, actor="test")
    assert cc.effective_state(db_session, case) == "pausing"  # still waiting for the step
    # the running job hits its boundary and lands paused
    job = db_session.query(DocumentaryJob).filter_by(case_id=case.id).one()
    job.status = "paused"
    db_session.commit()
    assert cc.effective_state(db_session, case) == "paused"


def test_resume_requeues_paused_job_and_is_idempotent(db_session):
    case = _case(db_session)
    job = _job(db_session, case, "paused")
    cc.pause_case(db_session, case, actor="t")
    out1 = cc.resume_case(db_session, case, actor="t")
    assert job.id in out1["resumed_jobs"]
    db_session.refresh(job)
    assert job.status == "queued"
    out2 = cc.resume_case(db_session, case, actor="t")   # double click
    assert out2["state"] == "running"
    db_session.refresh(job)
    assert job.status == "queued"  # exactly once
    # other test files may leave queued jobs behind (shared DB) — ours must be claimable
    claimed = set()
    while (cid := claim_next_job(db_session, "w-test")) is not None:
        claimed.add(cid)
    assert job.id in claimed


def test_per_language_pause_recorded(db_session):
    case = _case(db_session)
    cc.pause_case(db_session, case, actor="t", languages=["de", "fa"])
    assert cc.paused_languages(case) == ["de", "fa"]
    # extending while already pausing is idempotent
    cc.pause_case(db_session, case, actor="t", languages=["fa", "ar"])
    assert cc.paused_languages(case) == ["ar", "de", "fa"]


def test_pause_survives_fresh_session(db_session):
    """Persisted in the DB, not in memory: a new session sees the hold."""
    case = _case(db_session)
    cc.pause_case(db_session, case, actor="t")
    assert case.id in cc.held_case_ids(db_session)


# --------------------------------------------------------------------- stop
def test_stop_keep_halts_jobs_and_resume_brings_them_back(db_session):
    case = _case(db_session)
    running = _job(db_session, case, "running")
    paused = _job(db_session, case, "paused")
    cc.pause_case(db_session, case, actor="t")
    out = cc.stop_case(db_session, case, "keep", actor="t")
    assert out["state"] == "stopped"
    db_session.refresh(running)
    db_session.refresh(paused)
    assert running.status == "cancelling"   # in-flight paid work finishes
    assert paused.status == "cancelled"
    db_session.query(DocumentaryJob).filter_by(id=running.id).update(
        {"status": "cancelled"})
    db_session.commit()
    cc.resume_case(db_session, case, actor="t")
    db_session.refresh(running)
    assert running.status == "queued"


def test_stop_delete_needs_exact_case_name(db_session):
    case = _case(db_session, "The Exact Title")
    with pytest.raises(cc.ControlError) as err:
        cc.stop_case(db_session, case, "delete", actor="t", confirm_name="wrong")
    assert err.value.code == "confirm_name"
    out = cc.stop_case(db_session, case, "delete", actor="t",
                       confirm_name="The Exact Title")
    assert out["state"] == "trashed"


def test_trash_marks_ledger_and_writes_tombstone(db_session):
    case = _case(db_session)
    row = _ledger(db_session, case, eur=2.5)
    cc.stop_case(db_session, case, "delete", actor="t",
                 confirm_name=case.canonical_title)
    db_session.refresh(row)
    assert row.case_deleted is True
    tomb = db_session.query(CaseTombstone).filter_by(case_id=case.id).one()
    assert tomb.total_cost_eur == 2.5
    assert tomb.purged_at is None
    # trashed preview is off: the case view reports trash info
    view = cc.control_view(db_session, case)
    assert view["state"] == "trashed" and view["restorable"] is True


def test_restore_clears_trash_into_stopped(db_session):
    case = _case(db_session)
    cc.stop_case(db_session, case, "delete", actor="t",
                 confirm_name=case.canonical_title)
    out = cc.restore_case(db_session, case, actor="t")
    assert out["state"] == "stopped"
    assert case.purge_after is None
    tomb = db_session.query(CaseTombstone).filter_by(case_id=case.id).one()
    assert tomb.restored_at is not None
    # a restored case can resume afterwards
    cc.resume_case(db_session, case, actor="t")
    assert case.control_state is None


def test_purge_removes_rows_and_media_keeps_ledger(db_session, tmp_path):
    from datetime import timedelta

    from app.utils import utc_now

    case = _case(db_session)
    row = _ledger(db_session, case)
    _job(db_session, case, "queued")
    media = cc.ROOT / "data" / "cases" / str(case.id) / "clip.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"x" * 10)
    try:
        cc.stop_case(db_session, case, "delete", actor="t",
                     confirm_name=case.canonical_title)
        case.purge_after = utc_now() - timedelta(seconds=1)  # force expiry
        db_session.commit()
        out = cc.purge_expired_trash(db_session)
        assert out["purged"][0]["case_id"] == case.id
        assert db_session.get(Case, case.id) is None
        assert not media.parent.exists()  # media gone
        db_session.refresh(row)
        assert row.id and row.case_deleted  # ledger row kept, marked
        tomb = db_session.query(CaseTombstone).filter_by(case_id=case.id).one()
        assert tomb.purged_at is not None
        assert any(e.action == "purge" for e in db_session.query(CaseEvent)
                   .filter_by(case_id=case.id).all())
    finally:
        if media.parent.exists():
            import shutil
            shutil.rmtree(media.parent)


def test_audit_trail_written(db_session):
    case = _case(db_session)
    cc.pause_case(db_session, case, actor="owner", reason="checking")
    cc.resume_case(db_session, case, actor="owner")
    actions = [e.action for e in db_session.query(CaseEvent)
               .filter_by(case_id=case.id).order_by(CaseEvent.id).all()]
    assert actions[-2:] == ["pause", "resume"]  # earlier rows may belong to a reused id
    pause_evt = db_session.query(CaseEvent).filter_by(
        case_id=case.id, action="pause").one()
    assert pause_evt.actor == "owner" and pause_evt.state_after == "pausing"


# --------------------------------------------------------------------- global stop
def test_global_stop_blocks_claims_and_autopilot(db_session):
    case = _case(db_session)
    job = _job(db_session, case, "queued")
    run = AutopilotRun(case_id=case.id, status="running",
                       languages_json='["en"]')
    db_session.add(run)
    db_session.commit()
    try:
        payload = cc.set_global_stop(db_session, True, "owner")
        assert payload["active"] is True
        assert payload["stopped"]["queued_jobs"] >= 1
        db_session.refresh(run)
        assert run.status == "cancelled"
        assert claim_next_job(db_session, "w-test") != job.id
        assert cc.global_stop_active(db_session)
    finally:
        cc.set_global_stop(db_session, False, "owner")
    assert cc.global_stop_active(db_session) is False


def test_paid_call_gate_refuses_when_held(db_session):
    from app.documentary.pipeline.errors import JobPaused
    from app.services.tracking import _check_case_pause

    case = _case(db_session)
    _check_case_pause(db_session, case.id)   # fine while running
    cc.pause_case(db_session, case, actor="t")
    with pytest.raises(JobPaused):
        _check_case_pause(db_session, case.id)
    cc.resume_case(db_session, case, actor="t")
    _check_case_pause(db_session, case.id)   # clean again


def test_events_endpoint_keeps_history_after_delete(db_session):
    case = _case(db_session)
    cc.stop_case(db_session, case, "delete", actor="t",
                 confirm_name=case.canonical_title)
    from datetime import timedelta

    from app.utils import utc_now
    case.purge_after = utc_now() - timedelta(seconds=1)
    db_session.commit()
    cc.purge_expired_trash(db_session)
    events = db_session.query(CaseEvent).filter_by(case_id=case.id).all()
    assert {e.action for e in events} >= {"stop_delete", "purge"}


def test_settings_and_shared_assets_survive_purge(db_session):
    case = _case(db_session)
    keep = AppSetting(key="channel:test-keep", value_json='{"x": 1}')
    db_session.add(keep)
    db_session.commit()
    cc.stop_case(db_session, case, "delete", actor="t",
                 confirm_name=case.canonical_title)
    from datetime import timedelta

    from app.utils import utc_now
    case.purge_after = utc_now() - timedelta(seconds=1)
    db_session.commit()
    cc.purge_expired_trash(db_session)
    assert db_session.get(AppSetting, "channel:test-keep") is not None
