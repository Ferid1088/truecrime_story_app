import pytest

from app.db.models import Case, DocumentaryJob, RenderedShort, ShortCandidate
from app.lifecycle import case_archive as A


@pytest.fixture
def case(db_session):
    row = Case(canonical_title="Archive Rules", slug="archive-rules-test", language="en")
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(RenderedShort).filter_by(case_id=row.id).delete()
    db_session.query(ShortCandidate).filter_by(case_id=row.id).delete()
    db_session.query(DocumentaryJob).filter_by(case_id=row.id).delete()
    db_session.delete(row)
    db_session.commit()


def add_shorts(db, case, *states):
    job = db.query(DocumentaryJob).filter_by(case_id=case.id).first()
    if job is None:
        job = DocumentaryJob(case_id=case.id, status="completed")
        db.add(job)
        db.flush()
    shorts = []
    for index, approval in enumerate(states):
        candidate = ShortCandidate(case_id=case.id, film_job_id=job.id, stable_key=f"c{index}")
        db.add(candidate)
        db.flush()
        short = RenderedShort(case_id=case.id, candidate_id=candidate.id, film_job_id=job.id,
                              language="en", approval_state=approval)
        db.add(short)
        shorts.append(short)
    db.commit()
    return shorts


def test_a_case_without_shorts_is_active(db_session, case):
    assert A.archive_reason(db_session, case) is None


def test_a_case_is_archived_when_every_short_is_published(db_session, case):
    first, second = add_shorts(db_session, case, "approved", "approved")
    A.set_published(db_session, first, True)
    assert A.archive_reason(db_session, case) is None
    A.set_published(db_session, second, True)
    assert A.archive_reason(db_session, case) == A.ALL_PUBLISHED
    A.set_published(db_session, second, False)
    assert A.archive_reason(db_session, case) is None


def test_a_rejected_short_does_not_hold_the_case_back(db_session, case):
    done, _rejected = add_shorts(db_session, case, "approved", "rejected")
    A.set_published(db_session, done, True)
    assert A.archive_reason(db_session, case) == A.ALL_PUBLISHED


def test_the_owner_can_archive_and_restore_a_case(db_session, case):
    A.archive(db_session, case)
    assert A.archive_dict(db_session, case) == {"archived": True, "archive_reason": A.MANUAL}
    A.unarchive(db_session, case)
    assert A.archive_dict(db_session, case) == {"archived": False, "archive_reason": None}


def test_the_case_list_carries_the_archive_state(client, db_session, case):
    row = next(r for r in client.get("/api/cases").json() if r["id"] == case.id)
    assert row["archived"] is False
    assert client.post(f"/api/cases/{case.id}/archive").json()["archive_reason"] == A.MANUAL
    row = next(r for r in client.get("/api/cases").json() if r["id"] == case.id)
    assert row["archived"] is True and row["archive_reason"] == A.MANUAL
    assert client.post(f"/api/cases/{case.id}/unarchive").json()["archived"] is False


def test_the_short_form_overview_archives_a_fully_published_case(client, db_session, case):
    first, second = add_shorts(db_session, case, "approved", "approved")

    def mine():
        return next(r for r in client.get("/api/short-form/cases").json() if r["id"] == case.id)

    assert mine()["archived"] is False and mine()["shorts"] == 2 and mine()["published"] == 0
    client.post(f"/api/short-form/renders/{first.id}/published", json={"published": True})
    answer = client.post(f"/api/short-form/renders/{second.id}/published", json={"published": True}).json()
    assert answer["case_archived"] is True
    assert mine()["archived"] is True and mine()["published"] == 2


def test_publishing_an_unknown_short_is_a_404(client):
    assert client.post("/api/short-form/renders/0/published", json={"published": True}).status_code == 404
