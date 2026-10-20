"""D2: the Searches list — one row per discovery job, newest first, with
its generated/display name, criteria, cost and D3's live counts; and the
expanded per-Search view (its discovered cases with status badges)."""
from app.db.models import Case, DiscoveryCandidate, Video
from app.services import research_jobs


def _candidate(db, job_id, *, fingerprint, **kw) -> DiscoveryCandidate:
    defaults = {"title": "x", "query": "x", "rationale": "x",
                "state": "suggested", "discovery_job_id": job_id}
    defaults.update(kw)
    c = DiscoveryCandidate(fingerprint=fingerprint, **defaults)
    db.add(c)
    db.commit()
    return c


def _case(db, title) -> Case:
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-"), language="en")
    db.add(case)
    db.commit()
    return case


def test_searches_list_returns_one_row_per_discovery_job_newest_first(client, db_session):
    older = research_jobs.create_job(
        db_session, job_type="discovery",
        input_data={"count": 1, "languages": ["en"], "theme": "true crime"})
    newer = research_jobs.create_job(
        db_session, job_type="discovery",
        input_data={"count": 2, "languages": ["de"], "theme": "true crime",
                    "country_code": "FR", "resolution_filter": "both"})
    newer.status = "completed"
    newer.total_cost_usd = 0.05
    db_session.commit()

    r = client.get("/api/discovery/searches")
    assert r.status_code == 200
    rows = r.json()
    ids = [row["id"] for row in rows]
    assert ids.index(newer.id) < ids.index(older.id)
    row = next(row for row in rows if row["id"] == newer.id)
    assert row["name"] == row["generated_name"]
    assert "France" in row["generated_name"]
    assert row["status"] == "completed"
    assert row["total_cost_usd"] == 0.05
    from app.core.ai_config import ai_config

    assert row["total_cost_eur"] == round(0.05 * ai_config.budget.usd_to_eur, 4)
    assert row["counts"] == {"total": 0, "free": 0, "created_not_published": 0,
                             "created_published": 0, "archived": 0}


def test_searches_list_counts_reflect_real_discovered_cases(client, db_session):
    job = research_jobs.create_job(
        db_session, job_type="discovery", input_data={"count": 2, "languages": ["en"]})
    _candidate(db_session, job.id, fingerprint="fp-a")
    case = _case(db_session, "Searches List Case")
    _candidate(db_session, job.id, fingerprint="fp-b", case_id=case.id)
    db_session.add(Video(case_id=case.id, language="en", state="published"))
    db_session.commit()

    rows = client.get("/api/discovery/searches").json()
    row = next(r for r in rows if r["id"] == job.id)
    assert row["counts"] == {"total": 2, "free": 1, "created_not_published": 0,
                             "created_published": 1, "archived": 0}


def test_searches_list_only_includes_discovery_jobs(client, db_session):
    research_job = research_jobs.create_job(db_session, job_type="research", input_data={})
    rows = client.get("/api/discovery/searches").json()
    assert all(row["id"] != research_job.id for row in rows)


def test_discovery_history_filters_by_job_and_reports_live_case_status(client, db_session):
    job = research_jobs.create_job(
        db_session, job_type="discovery", input_data={"count": 1, "languages": ["en"]})
    other_job = research_jobs.create_job(
        db_session, job_type="discovery", input_data={"count": 1, "languages": ["en"]})
    _candidate(db_session, job.id, fingerprint="fp-this-job")
    _candidate(db_session, other_job.id, fingerprint="fp-other-job")

    rows = client.get(f"/api/discovery/history?discovery_job_id={job.id}").json()
    assert len(rows) == 1
    assert rows[0]["case_status"] == "free"
    assert rows[0]["language_badges"] == {
        "en": "not_created", "de": "not_created", "fa": "not_created", "ar": "not_created"}
