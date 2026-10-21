import json

import pytest
from conftest import _delete_case_tree

from app import worker as W
from app.autopilot import service as SV
from app.autopilot import state as S
from app.autopilot.publish import PlannedRequest, store_requests
from app.db.models import AutopilotRun, DiscoveryCandidate
from app.services import research_jobs
from app.utils import utc_now

LANGS = ["en", "de", "fa", "ar"]


@pytest.fixture(autouse=True)
def no_launch(monkeypatch):
    monkeypatch.setattr(SV, "launch", lambda run_id: None)


@pytest.fixture
def candidate(db_session):
    job = research_jobs.create_job(db_session, job_type="discovery",
                                   input_data={"count": 1, "languages": ["en"]})
    row = DiscoveryCandidate(title="Autopilot api case", query="q", rationale="why",
                             fingerprint="fp-autopilot-api", state="suggested",
                             discovery_job_id=job.id, resolution_status="SOLVED")
    db_session.add(row)
    db_session.commit()
    candidate_id = row.id
    yield candidate_id
    db_session.expire_all()
    case_id = db_session.query(DiscoveryCandidate.case_id).filter_by(id=candidate_id).scalar()
    if case_id:
        _delete_case_tree(db_session, case_id)
    db_session.query(DiscoveryCandidate).filter_by(id=candidate_id).delete()
    db_session.commit()


def test_the_estimate_endpoint_prints_cap_and_expected_cost(client, candidate):
    body = client.get(f"/api/autopilot/estimate?candidate_id={candidate}").json()
    assert body["languages"] == LANGS and body["estimated_eur"] > 0 and body["cap_eur"] > 0


def test_start_returns_the_run_and_lists_it(client, candidate):
    r = client.post("/api/autopilot/runs", json={"candidate_id": candidate, "languages": LANGS})
    assert r.status_code == 201
    run = r.json()
    assert run["status"] == "running" and run["case"]["title"] == "Autopilot api case"
    assert [s["name"] for s in run["steps"]][0] == "case"
    assert client.get(f"/api/autopilot/runs/{run['id']}").json()["id"] == run["id"]
    assert run["id"] in [x["id"] for x in client.get("/api/autopilot/runs").json()["runs"]]


def test_a_refused_start_says_why_and_a_missing_candidate_is_404(client, candidate):
    r = client.post("/api/autopilot/runs", json={"candidate_id": candidate, "languages": ["de"]})
    assert r.status_code == 409 and "English" in r.json()["detail"]
    assert client.post("/api/autopilot/runs",
                       json={"candidate_id": 99999999, "languages": LANGS}).status_code == 409
    assert client.get("/api/autopilot/runs/99999999").status_code == 404


def test_cancel_marks_the_run_cancelled(client, candidate):
    run = client.post("/api/autopilot/runs",
                      json={"candidate_id": candidate, "languages": LANGS}).json()
    assert client.post(f"/api/autopilot/runs/{run['id']}/cancel").json()["status"] == "cancelled"


def test_the_publish_requests_are_listed_with_their_schedule(client, db_session, candidate):
    run = client.post("/api/autopilot/runs",
                      json={"candidate_id": candidate, "languages": LANGS}).json()
    case_id = run["case"]["id"]
    store_requests(db_session, run["id"], case_id, [PlannedRequest(
        "film", "en", "youtube", None, utc_now(), {"title": "T", "sent": False})])
    body = client.get(f"/api/autopilot/runs/{run['id']}/publish-requests").json()
    assert body["sent"] == 0 and len(body["requests"]) == 1
    row = body["requests"][0]
    assert row["status"] == "dry_run" and row["request"]["title"] == "T" and row["scheduled_for"]


def test_a_worker_tick_drives_every_active_run_once(db_session, candidate):
    run = SV.start_run(db_session, candidate, LANGS)
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
