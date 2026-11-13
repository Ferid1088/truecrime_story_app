import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.models import (
    Case,
    CoverImage,
    ShortClip,
    Video,
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


def _case(db, title="Monitor proof"):
    case = Case(canonical_title=title, slug=f"mon-{uuid4().hex[:8]}", language="en")
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
    case = _case(db_session)
    snap = _snapshot(case)
    assert [j for j in snap["running"] if j["case_id"] == case.id] == [] and snap["latest_job"] is None
    assert {s["name"] for s in snap["services"]} == {"API", "Database", "Worker", "Brave Search"}
    assert snap["budget"]["cap_eur"] > 0
    assert not [i for i in snap["issues"] if i["area"] in ("job", "stage", "pictures")]


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


def test_the_app_wide_view_lists_a_stopped_case_with_buttons_that_open_it(db_session):
    title = f"Stopped case {uuid4().hex[:6]}"
    case = _case(db_session, title)
    job = _job(db_session, case, "failed", "voice:en", [{"name": "voice:en", "status": "failed", "attempts": 1,
                                                          "error": "provider down"}],
               error="RuntimeError: provider down",
               result_json=json.dumps({"failure": failure_details("provider down")}))
    snap = TestClient(app).get("/api/monitoring").json()
    assert snap["state"] == "problem"
    mine = [i for i in snap["issues"] if i["title"].startswith(title)]
    assert mine and any(a["kind"] == "href" and a["target"] == f"/documentary/{case.id}?tab=Voice"
                        for a in mine[0]["actions"])
    assert any(a["kind"] == "resume" and a["target"] == str(job.id) for a in mine[0]["actions"])
    assert any(c["id"] == case.id for c in snap["cases"])
    assert {s["name"] for s in snap["services"]} == {"API", "Database", "Worker", "Brave Search"}


def test_a_job_that_waits_for_a_portrait_choice_gets_a_button_to_the_review_with_the_portrait_view(db_session):
    case = _case(db_session)
    message = "Awaiting portrait choice: 2 gap(s) of the en film have no portrait that may be shown without a spoiler"
    job = _job(db_session, case, "failed", "mvp_portraits:en",
               [{"name": "mvp_portraits:en", "status": "failed", "attempts": 1}],
               error=f"AwaitingPortrait: {message}", result_json=json.dumps({"failure": failure_details(message)}))
    snap = _snapshot(case)
    kinds = {(a["label"], a["kind"], a["target"]) for i in snap["issues"] for a in i["actions"]}
    assert ("Choose another first portrait", "href", f"/documentary/{case.id}/review?portraits=1") in kinds
    assert ("Resume the job", "resume", str(job.id)) in kinds
    assert any("is waiting for you" in t for t in _titles(snap)) and snap["state"] == "attention"


def test_a_job_that_waits_for_the_reading_review_is_an_alarm_that_opens_the_reading_review_tab_and_resumes(db_session):
    case = _case(db_session)
    message = "RuntimeError: Awaiting reading review: 12 words of the fa narration are in doubt."
    job = _job(db_session, case, "failed", "reading:fa", [{"name": "reading:fa", "status": "failed", "attempts": 1}], error=message,
               result_json=json.dumps({"failure": failure_details(message)}))
    snap = _snapshot(case)
    kinds = {(a["kind"], a["target"]) for i in snap["issues"] for a in i["actions"]}
    assert ("tab", "Reading Review") in kinds and ("resume", str(job.id)) in kinds
    assert ("href", f"/documentary/{case.id}/review") not in kinds, "it is not a picture review"
    assert snap["state"] == "attention" and any("waiting for you" in t for t in _titles(snap))


def test_the_reading_stage_has_its_own_longer_limit_before_it_counts_as_stuck(db_session):
    case = _case(db_session)
    started = (utc_now() - timedelta(minutes=40)).isoformat()
    _job(db_session, case, "running", "reading:fa", [{"name": "reading:fa", "status": "running", "started_at": started}],
         worker_id="w1", heartbeat_at=utc_now())
    assert not any("has run for" in t for t in _titles(_snapshot(case))), "40 minutes is normal for the reading of a whole film"
    case = _case(db_session)
    started = (utc_now() - timedelta(minutes=120)).isoformat()
    _job(db_session, case, "running", "reading:fa", [{"name": "reading:fa", "status": "running", "started_at": started}],
         worker_id="w1", heartbeat_at=utc_now())
    assert any("Stage reading:fa has run for" in t for t in _titles(_snapshot(case)))


def _stage(name, status, seconds=5.0):
    return {"name": name, "status": status, "attempts": 1, "started_at": utc_now().isoformat(), "detail": {}}


def test_the_progress_bars_show_each_language_phase_and_where_it_is(db_session):
    case = _case(db_session)
    job = _job(db_session, case, "running", "voice:de", [
        _stage("blueprint", "done"), _stage("mvp_needs", "done"),
        _stage("spoken:en", "done"), _stage("voice:en", "done"), _stage("render:en", "done"),
        _stage("spoken:de", "done"), _stage("voice:de", "running"), _stage("render:de", "pending")])
    job.languages_json = '["en", "de"]'
    db_session.add_all([ShortClip(case_id=case.id, language="en", clip_key="C1", status="valid"),
                        CoverImage(case_id=case.id, language="en", cover_key="long", model="m", size="s", ok=True),
                        Video(case_id=case.id, language="fr", title="fr film")])
    db_session.commit()
    bars = next(j for j in _snapshot(case)["running"] if j["id"] == job.id)["phase_bars"]
    shared = {p["key"]: p["state"] for p in bars["shared"]["phases"]}
    assert list(shared) == ["research", "story", "blueprint", "music", "host_plan", "needs", "naming", "length", "library", "search"]
    assert shared["blueprint"] == "done" and shared["needs"] == "done" and shared["music"] == "pending"
    rows = {r["language"]: r for r in bars["languages"]}
    assert set(rows) == {"en", "de", "fr"}                      # a language the case has a film in shows without a job stage
    states = {k: {p["key"]: p["state"] for p in r["phases"]} for k, r in rows.items()}
    assert states["en"]["voice"] == "done" and states["en"]["render"] == "done" and states["en"]["clips"] == "waiting" and states["en"]["covers"] == "waiting"
    assert states["de"]["voice"] == "running" and states["de"]["render"] == "pending" and states["de"]["avatar"] == "pending"
    assert rows["de"]["current"] == "Voice" and not rows["de"]["finished"]
    assert [p["key"] for p in rows["fr"]["phases"]] == ["spoken", "opening", "host_text", "direction", "voice", "avatar", "plan", "fill", "portraits", "maps",
                                                       "production", "critique", "coverage", "render", "review", "clips", "covers"]


def test_the_bars_keep_what_an_earlier_job_of_the_case_finished(db_session):
    case = _case(db_session)
    _job(db_session, case, "completed", "render:de", [_stage("spoken:de", "done"), _stage("voice:de", "done")])
    job = _job(db_session, case, "running", "spoken:de", [_stage("spoken:de", "running"), _stage("voice:de", "pending")])
    job.languages_json = '["de"]'
    db_session.commit()
    bars = next(j for j in _snapshot(case)["running"] if j["id"] == job.id)["phase_bars"]
    states = {p["key"]: p["state"] for p in bars["languages"][0]["phases"]}
    assert states["spoken"] == "running" and states["voice"] == "done"


def test_research_counts_as_done_when_the_story_exists_but_this_job_did_not_research(db_session):
    case = _case(db_session)
    job = _job(db_session, case, "running", "blueprint", [_stage("final_approval", "done"), _stage("blueprint", "running")])
    shared = {p["key"]: p["state"] for p in next(j for j in _snapshot(case)["running"] if j["id"] == job.id)["phase_bars"]["shared"]["phases"]}
    assert shared["research"] == "done" and shared["story"] == "done" and shared["blueprint"] == "running"
