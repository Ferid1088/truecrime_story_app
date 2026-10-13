import uuid

from app.db.models import Case, DocumentaryJob, RenderedShort, ShortCandidate, Video


def _bundle_fixture(db, tmp_path):
    slug = f"bundle-case-{uuid.uuid4().hex[:8]}"
    case = Case(canonical_title="Bundle case", slug=slug, language="en")
    db.add(case)
    db.flush()
    job = DocumentaryJob(case_id=case.id, status="completed", languages_json='["en"]')
    db.add(job)
    db.flush()
    film = Video(case_id=case.id, job_id=job.id, language="en", title="Bundle film", state="rendered")
    db.add(film)
    db.flush()
    candidate = ShortCandidate(case_id=case.id, film_job_id=job.id, stable_key="bundle-short",
                               hook="A hook", cta="Watch the story", content_hash="candidate-hash")
    db.add(candidate)
    db.flush()
    short = RenderedShort(case_id=case.id, candidate_id=candidate.id, film_job_id=job.id,
                          language="en", status="done", content_hash="short-v1")
    db.add(short)
    db.commit()
    return case, film, short


def test_approval_is_per_item_and_hash_bound(client, db_session, tmp_path):
    case, film, short = _bundle_fixture(db_session, tmp_path)
    response = client.get(f"/api/cases/{case.id}/approval-bundles/en")
    assert response.status_code == 200
    payload = response.json()
    assert payload["visibility"] == "private"
    assert {item["item_type"] for item in payload["items"]} == {"film", "short"}

    for item in payload["items"]:
        assert client.post(
            f"/api/cases/{case.id}/approval-bundles/en/items/{item['item_type']}/{item['item_id']}/approve"
        ).status_code == 200
    approved = client.get(f"/api/cases/{case.id}/approval-bundles/en").json()
    assert approved["status"] == "approved"

    short.content_hash = "short-v2"
    db_session.commit()
    changed = client.get(f"/api/cases/{case.id}/approval-bundles/en").json()
    statuses = {item["item_type"]: item["status"] for item in changed["items"]}
    assert changed["status"] == "draft"
    assert statuses == {"film": "approved", "short": "needs_review"}

    assert client.post(
        f"/api/cases/{case.id}/approval-bundles/en/approve"
    ).status_code == 409


def test_exclude_and_re_review_are_item_scoped(client, db_session, tmp_path):
    case, _film, short = _bundle_fixture(db_session, tmp_path)
    response = client.get(f"/api/cases/{case.id}/approval-bundles/en").json()
    film_item = next(item for item in response["items"] if item["item_type"] == "film")
    assert client.post(
        f"/api/cases/{case.id}/approval-bundles/en/items/film/{film_item['item_id']}/exclude"
    ).status_code == 200
    assert client.post(
        f"/api/cases/{case.id}/approval-bundles/en/items/short/{short.id}/approve"
    ).status_code == 200
    payload = client.get(f"/api/cases/{case.id}/approval-bundles/en").json()
    assert payload["status"] == "approved"
    assert {item["status"] for item in payload["items"]} == {"excluded", "approved"}

    assert client.post(
        f"/api/cases/{case.id}/approval-bundles/en/items/short/{short.id}/re-review"
    ).status_code == 200
    payload = client.get(f"/api/cases/{case.id}/approval-bundles/en").json()
    assert payload["status"] == "draft"
    assert next(item for item in payload["items"] if item["item_type"] == "short")["status"] == "pending"
