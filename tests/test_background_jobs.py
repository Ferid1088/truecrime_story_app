import asyncio


def test_background_job_failure_is_durable_and_retryable(client, monkeypatch):
    case_id = client.post(
        "/api/cases", json={"canonical_title": "Job state case", "force": True}
    ).json()["id"]

    async def fail(_db, _job):
        raise RuntimeError("provider disconnected")

    monkeypatch.setattr("app.services.background_jobs._dispatch", fail)
    started = client.post(f"/api/cases/{case_id}/naming/generate", json={}).json()
    failed = client.get(f"/api/jobs/{started['job_id']}").json()
    assert failed["status"] == "failed"
    assert "provider disconnected" in failed["error"]

    async def succeed(_db, _job):
        await asyncio.sleep(0)
        return {"retried": True}

    monkeypatch.setattr("app.services.background_jobs._dispatch", succeed)
    retry = client.post(f"/api/jobs/{started['job_id']}/retry")
    assert retry.status_code == 200, retry.text
    done = client.get(f"/api/jobs/{started['job_id']}").json()
    assert done["status"] == "done"
    assert done["attempts"] == 2
    assert done["result"] == {"retried": True}
