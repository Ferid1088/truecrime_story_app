"""D4: archive (permanent, fingerprint-matched exclusion) and delete
(soft, restorable, never blocks a later Search) for discovered cases."""
from datetime import timedelta

import pytest

from app.db.models import Case, DiscoveryCandidate, Video
from app.lifecycle.identity import IdentityIndex
from app.services import research_jobs
from app.utils import utc_now


def _job(db):
    return research_jobs.create_job(
        db, job_type="discovery", input_data={"count": 1, "languages": ["en"]})


def _candidate(db, job_id, *, fingerprint, title="A case", **kw) -> DiscoveryCandidate:
    defaults = {"title": title, "query": "x", "rationale": "x",
                "state": "suggested", "discovery_job_id": job_id}
    defaults.update(kw)
    c = DiscoveryCandidate(fingerprint=fingerprint, **defaults)
    db.add(c)
    db.commit()
    return c


def test_archive_sets_state_and_timestamp_live(client, db_session):
    job = _job(db_session)
    c = _candidate(db_session, job.id, fingerprint="fp-archive")
    r = client.post(f"/api/discovery/{c.id}/archive")
    assert r.status_code == 200
    db_session.refresh(c)
    assert c.state == "archived"
    assert c.archived_at is not None


def test_archived_identity_blocks_the_same_case_under_a_different_title_in_a_later_search(db_session):
    job = _job(db_session)
    c = _candidate(db_session, job.id, fingerprint="fp-x", title="Murder of Jane Doe",
                  people_json='["Jane Doe", "John Smith"]', location="Berlin, Germany",
                  incident_date="2010-05-01")
    from app.lifecycle.archive import archive_candidate

    archive_candidate(db_session, c)
    db_session.commit()

    index = IdentityIndex.from_db(db_session)
    from app.lifecycle.identity import Identity

    same_case_new_title = Identity.build(
        kind="batch", id=None, title="The Jane Doe Killing", aliases=[],
        people=["Jane Doe", "John Smith"], places=["Berlin"], dates=["2010-05-01"], urls=[])
    verdict = index.check(same_case_new_title)
    assert verdict.duplicate


def test_restore_brings_an_archived_candidate_back_to_suggested(client, db_session):
    job = _job(db_session)
    c = _candidate(db_session, job.id, fingerprint="fp-restore", title="Archived Then Restored Case")
    client.post(f"/api/discovery/{c.id}/archive")
    r = client.post(f"/api/discovery/{c.id}/restore")
    assert r.status_code == 200
    db_session.refresh(c)
    assert c.state == "suggested"
    assert c.archived_at is None


def test_delete_soft_deletes_and_is_not_suggested_blocking(client, db_session):
    job = _job(db_session)
    c = _candidate(db_session, job.id, fingerprint="fp-delete", title="Soft Deleted Case",
                  people_json='["Soft Deleted"]')
    r = client.post(f"/api/discovery/{c.id}/delete")
    assert r.status_code == 200
    db_session.refresh(c)
    assert c.state == "deleted"
    assert c.deleted_at is not None

    # D4: deleting does not permanently block it — a later Search may
    # suggest it again (unlike archive).
    index = IdentityIndex.from_db(db_session)
    from app.lifecycle.identity import Identity

    same_case = Identity.build(kind="batch", id=None, title="Soft Deleted Case", aliases=[],
                               people=["Soft Deleted"], places=[], dates=[], urls=[])
    assert not index.check(same_case).duplicate


def test_delete_undo_restores_within_the_window(client, db_session):
    job = _job(db_session)
    c = _candidate(db_session, job.id, fingerprint="fp-undo", title="Deleted Then Undone Case")
    client.post(f"/api/discovery/{c.id}/delete")
    r = client.post(f"/api/discovery/{c.id}/restore")
    assert r.status_code == 200
    db_session.refresh(c)
    assert c.state == "suggested"
    assert c.deleted_at is None


def test_delete_restore_after_the_window_expired_is_rejected(client, db_session):
    job = _job(db_session)
    c = _candidate(db_session, job.id, fingerprint="fp-expired")
    client.post(f"/api/discovery/{c.id}/delete")
    db_session.refresh(c)
    c.deleted_at = utc_now() - timedelta(days=31)
    db_session.commit()

    r = client.post(f"/api/discovery/{c.id}/restore")
    assert r.status_code == 409
    db_session.refresh(c)
    assert c.state == "deleted"


def test_delete_is_rejected_once_a_case_was_created_from_the_candidate(client, db_session):
    job = _job(db_session)
    case = Case(canonical_title="Created Case", slug="created-case", language="en")
    db_session.add(case)
    db_session.commit()
    c = _candidate(db_session, job.id, fingerprint="fp-created", case_id=case.id)

    r = client.post(f"/api/discovery/{c.id}/delete")
    assert r.status_code == 409
    assert "archive" in r.json()["detail"].lower()
    db_session.refresh(c)
    assert c.state == "suggested"


def test_restore_of_a_candidate_with_an_existing_case_goes_back_to_accepted(client, db_session):
    job = _job(db_session)
    case = Case(canonical_title="Already Created Case", slug="already-created-case", language="en")
    db_session.add(case)
    db_session.commit()
    c = _candidate(db_session, job.id, fingerprint="fp-accepted", state="accepted", case_id=case.id)

    client.post(f"/api/discovery/{c.id}/archive")
    r = client.post(f"/api/discovery/{c.id}/restore")
    assert r.status_code == 200
    db_session.refresh(c)
    assert c.state == "accepted"


def test_archive_is_allowed_even_after_the_case_was_published(client, db_session):
    job = _job(db_session)
    case = Case(canonical_title="Published Case", slug="published-case", language="en")
    db_session.add(case)
    db_session.commit()
    db_session.add(Video(case_id=case.id, language="en", state="published"))
    c = _candidate(db_session, job.id, fingerprint="fp-published", case_id=case.id)
    db_session.commit()

    r = client.post(f"/api/discovery/{c.id}/archive")
    assert r.status_code == 200
    db_session.refresh(c)
    assert c.state == "archived"
    # the film itself is untouched
    video = db_session.query(Video).filter_by(case_id=case.id).one()
    assert video.state == "published"


def test_archive_unknown_candidate_is_404(client):
    assert client.post("/api/discovery/999999/archive").status_code == 404


def test_delete_unknown_candidate_is_404(client):
    assert client.post("/api/discovery/999999/delete").status_code == 404


@pytest.mark.parametrize("action", ["archive", "delete", "restore"])
def test_bulk_endpoint_applies_to_every_id(client, db_session, action):
    job = _job(db_session)
    ids = [_candidate(db_session, job.id, fingerprint=f"fp-bulk-{action}-{i}").id for i in range(3)]
    if action == "restore":
        for i in ids:
            client.post(f"/api/discovery/{i}/delete")
    r = client.post(f"/api/discovery/bulk/{action}", json={"candidate_ids": ids})
    assert r.status_code == 200
    body = r.json()
    assert set(body["succeeded"]) == set(ids)
    assert body["failed"] == []


def test_restoring_a_filtered_candidate_sends_it_back_through_the_filter_and_it_stays_filtered(
        client, db_session):
    job = _job(db_session)
    c = _candidate(db_session, job.id, fingerprint="fp-filtered", title="Unsolved Disappearance",
                  state="filtered", resolution_status="UNSOLVED",
                  suggestion_reason="UNSOLVED — this search requests solved cases only")
    client.post(f"/api/discovery/{c.id}/archive")
    r = client.post(f"/api/discovery/{c.id}/restore")
    assert r.status_code == 200
    assert r.json()["state"] == "filtered"
    db_session.refresh(c)
    assert c.state == "filtered"
    assert c.suggestion_reason.startswith("UNSOLVED")
    assert c.archived_at is None


def test_restoring_a_duplicate_candidate_stays_a_duplicate_with_its_reason(client, db_session):
    job = _job(db_session)
    original = _candidate(db_session, job.id, fingerprint="fp-orig", title="Murder of Mara Veld",
                          people_json='["Mara Veld", "Tom Brandt"]', location="Hamburg, Germany",
                          incident_date="1998-03-02")
    twin = _candidate(db_session, job.id, fingerprint="fp-twin", title="The Mara Veld Killing",
                      people_json='["Mara Veld", "Tom Brandt"]', location="Hamburg, Germany",
                      incident_date="1998-03-02", state="duplicate")
    client.post(f"/api/discovery/{twin.id}/delete")
    r = client.post(f"/api/discovery/{twin.id}/restore")
    assert r.json()["state"] == "duplicate"
    db_session.refresh(twin)
    assert twin.duplicate_of_candidate_id == original.id
    assert twin.duplicate_reason
    assert twin.deleted_at is None


def test_restoring_a_candidate_outside_the_searchs_country_stays_filtered(client, db_session):
    job = research_jobs.create_job(
        db_session, job_type="discovery",
        input_data={"count": 1, "languages": ["en"], "country_code": "DE"})
    c = _candidate(db_session, job.id, fingerprint="fp-geo", title="Murder in Lyon",
                  location="Lyon, France", state="filtered")
    client.post(f"/api/discovery/{c.id}/archive")
    r = client.post(f"/api/discovery/{c.id}/restore")
    assert r.json()["state"] == "filtered"
    db_session.refresh(c)
    assert "outside the requested country" in c.suggestion_reason
