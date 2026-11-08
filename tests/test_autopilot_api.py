import json

import pytest
from conftest import _delete_case_tree

from app import worker as W
from app.autopilot import service as SV
from app.autopilot import state as S
from app.autopilot.publish import PlannedRequest, store_requests
from app.db.models import AutopilotRun, Case
from app.utils import utc_now

LANGS = ["en", "de", "fa", "ar"]


@pytest.fixture(autouse=True)
def no_launch(monkeypatch):
    monkeypatch.setattr(SV, "launch", lambda run_id: None)


@pytest.fixture
def case(db_session):
    for stale in db_session.query(Case).filter_by(slug="autopilot-api-case").all():
        _delete_case_tree(db_session, stale.id)
    row = Case(canonical_title="Autopilot api case", slug="autopilot-api-case", language="en")
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.expire_all()
    _delete_case_tree(db_session, row.id)


def make_run(db, case) -> AutopilotRun:
    run = AutopilotRun(case_id=case.id, languages_json=json.dumps(LANGS), steps_json=json.dumps(S.initial_steps()))
    db.add(run)
    db.commit()
    return run


def test_a_run_is_shown_by_id_and_listed(client, db_session, case):
    run = make_run(db_session, case)
    body = client.get(f"/api/autopilot/runs/{run.id}").json()
    assert body["status"] == "running" and body["case"]["title"] == "Autopilot api case"
    assert [s["name"] for s in body["steps"]][0] == "case"
    assert run.id in [x["id"] for x in client.get("/api/autopilot/runs").json()["runs"]]
    assert client.get("/api/autopilot/runs/99999999").status_code == 404


def test_the_old_suggestion_start_routes_are_gone(client):
    assert client.post("/api/autopilot/runs", json={"candidate_id": 1, "languages": LANGS}).status_code == 405
    assert client.get("/api/autopilot/estimate?candidate_id=1").status_code == 404
    assert client.get("/api/autopilot/outfit-options?candidate_id=1").status_code == 404


def test_cancel_marks_the_run_cancelled(client, db_session, case):
    run = make_run(db_session, case)
    assert client.post(f"/api/autopilot/runs/{run.id}/cancel").json()["status"] == "cancelled"


def test_the_publish_requests_are_listed_with_their_schedule(client, db_session, case):
    run = make_run(db_session, case)
    store_requests(db_session, run.id, case.id, [PlannedRequest(
        "film", "en", "youtube", None, utc_now(), {"title": "T", "sent": False})])
    body = client.get(f"/api/autopilot/runs/{run.id}/publish-requests").json()
    assert body["sent"] == 0 and len(body["requests"]) == 1
    row = body["requests"][0]
    assert row["status"] == "dry_run" and row["request"]["title"] == "T" and row["scheduled_for"]


def test_a_worker_tick_drives_every_active_run_once(db_session, case):
    run = make_run(db_session, case)
    seen = []

    async def fake(run_id):
        seen.append(run_id)

    worker = W.Worker(run_autopilot=fake, max_parallel=0)

    import asyncio

    async def go():
        await worker.tick()
        await worker.tick()
        await asyncio.gather(*worker.autopilot_tasks.values())

    asyncio.run(go())
    assert seen.count(run.id) == 1
    assert json.loads(run.languages_json) == LANGS and S.steps(run)
    assert db_session.get(AutopilotRun, run.id).status == "running"
