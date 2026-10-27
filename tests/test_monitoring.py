import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.models import (
    Case,
    DocumentaryJob,
    EditorialBlueprint,
    ProviderCall,
    StoryVersion,
    VisualPlan,
)
from app.documentary.jobs import failure_details
from app.main import app
from app.utils import utc_now


@pytest.fixture(autouse=True)
def _search_key(monkeypatch):
    monkeypatch.setattr("app.documentary.monitoring.env_value", lambda name, default=None: "key")


def _case(db):
    case = Case(canonical_title="Monitor proof", slug=f"mon-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    return case


def _job(db, case, status, stage, stages, **kw):
    job = DocumentaryJob(case_id=case.id, languages_json='["en"]', status=status, stage=stage,
                         stages_json=json.dumps(stages), **kw)
    db.add(job)
    db.commit()
    return job


def _snapshot(case):
    answer = TestClient(app).get(f"/api/cases/{case.id}/monitoring")
    assert answer.status_code == 200
    return answer.json()


def _titles(snap):
    return [i["title"] for i in snap["issues"]]


def test_an_unknown_case_is_404():
    assert TestClient(app).get("/api/cases/99999999/monitoring").status_code == 404


def test_a_case_without_jobs_is_idle_with_services_and_budget(db_session):
    snap = _snapshot(_case(db_session))
    assert snap["running"] == [] and snap["latest_job"] is None
    assert {s["name"] for s in snap["services"]} == {"API", "Database", "Worker", "Brave Search"}
    assert snap["budget"]["cap_eur"] > 0
    assert snap["state"] == "idle"


def test_a_job_that_waits_for_picture_review_points_to_the_review_page_and_resume(db_session):
    case = _case(db_session)
    job = _job(db_session, case, "failed", "mvp_complete:en",
               [{"name": "mvp_complete:en", "status": "failed", "attempts": 1}],
               error="RuntimeError: Awaiting picture review: 3 moment(s)",
               result_json=json.dumps({"failure": failure_details("Awaiting picture review: 3 moment(s)")}))
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db_session.add(sv)
    db_session.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db_session.add(bp)
    db_session.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, validation_json=json.dumps({
        "mvp": {"language": "en"}, "plan": {"gaps": [{"beat": "B01", "start": 1.0, "end": 4.0, "said": "x"}]}}))
    db_session.add(plan)
    db_session.commit()
    snap = _snapshot(case)
    kinds = {(a["kind"], a["target"]) for i in snap["issues"] for a in i["actions"]}
    assert ("href", f"/documentary/{case.id}/review") in kinds
    assert ("resume", str(job.id)) in kinds
    assert any("nothing to show" in t for t in _titles(snap))
    assert snap["state"] == "attention"


def test_a_running_job_with_a_silent_worker_is_a_problem(db_session):
    case = _case(db_session)
    _job(db_session, case, "running", "voice:en", [{"name": "voice:en", "status": "running"}],
         worker_id="w1", heartbeat_at=utc_now() - timedelta(seconds=settings.job_lease_seconds + 60))
    snap = _snapshot(case)
    assert snap["state"] == "problem"
    assert any("has not reported" in t for t in _titles(snap))
    worker = next(s for s in snap["services"] if s["name"] == "Worker")
    assert worker["state"] == "problem"


def test_a_healthy_running_job_is_listed_with_its_stages_and_tab(db_session):
    case = _case(db_session)
    job = _job(db_session, case, "running", "voice:en",
         [{"name": "blueprint", "status": "done", "attempts": 1},
          {"name": "voice:en", "status": "running", "attempts": 1, "started_at": utc_now().isoformat()}],
         worker_id="w1", heartbeat_at=utc_now())
    snap = _snapshot(case)
    assert snap["state"] == "running"
    mine = next(j for j in snap["running"] if j["id"] == job.id)
    stages = {s["name"]: s["tab"] for s in mine["stages"]}
    assert stages == {"blueprint": "Blueprint", "voice:en": "Voice"}


def test_a_stage_running_too_long_and_a_queued_job_are_flagged(db_session):
    case = _case(db_session)
    long_ago = (utc_now() - timedelta(hours=2)).isoformat()
    _job(db_session, case, "running", "render:en", [{"name": "render:en", "status": "running",
                                                       "started_at": long_ago}],
         worker_id="w1", heartbeat_at=utc_now())
    queued = _job(db_session, case, "queued", None, [])
    queued.created_at = utc_now() - timedelta(minutes=10)
    db_session.commit()
    titles = _titles(_snapshot(case))
    assert any("has run for" in t for t in titles)
    assert any("in the queue" in t for t in titles)


def test_a_failing_provider_is_reported(db_session):
    case = _case(db_session)
    host = f"flaky-{uuid4().hex[:6]}.example"
    for ok in (False, False, False, True, True, False):
        db_session.add(ProviderCall(host=host, method="POST", path="/v1/x", status=None if ok else 500, ok=ok,
                                    duration_ms=10))
    db_session.commit()
    snap = _snapshot(case)
    assert any(host in t for t in _titles(snap))
    assert any(h["host"] == host and h["errors"] == 4 for h in snap["providers"]["hosts"])


def test_a_missing_search_key_is_a_problem_with_a_settings_link(db_session, monkeypatch):
    monkeypatch.setattr("app.documentary.monitoring.env_value", lambda name, default=None: None)
    snap = _snapshot(_case(db_session))
    assert snap["state"] == "problem"
    assert any(a["target"] == "/settings" for i in snap["issues"] for a in i["actions"])
