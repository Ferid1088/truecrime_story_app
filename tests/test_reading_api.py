"""The owner's list of doubts over the API: one list for a version, answered in one go (the editor's reading, the checker's, or the owner's own),
nothing saved when any answer cannot be taken, and a reading kept for every film when the owner says so."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.models import Case, ReadingApproval, ReadingDoubt, StoryVersion
from app.main import app


@pytest.fixture
def film(db_session):
    case = Case(canonical_title="Reading API", slug=f"ra-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.flush()
    version = StoryVersion(case_id=case.id, version=1, kind="spoken", language="zz", story_text="t", narrative_angle="a", status="ready")
    db_session.add(version)
    db_session.flush()
    rows = [ReadingDoubt(case_id=case.id, story_version_id=version.id, block_id="B1", word_n=n, surface=s, sentence="سارا بهم گفت", question="which?",
                         editor_latin=e, checker_latin=c) for n, s, e, c in [(1, "بهم", "beham", "baham"), (2, "ملک", "melk", "malek")]]
    db_session.add_all(rows)
    db_session.commit()
    ids = [r.id for r in rows]
    yield version, ids
    db_session.rollback()
    db_session.query(ReadingDoubt).filter_by(story_version_id=version.id).delete()
    db_session.query(ReadingApproval).filter_by(case_id=case.id).delete()
    db_session.query(StoryVersion).filter_by(case_id=case.id).delete()
    db_session.query(Case).filter_by(id=case.id).delete()
    db_session.commit()


def test_the_list_shows_every_doubt_with_both_readings_and_counts_the_open_ones(film):
    version, ids = film
    body = TestClient(app).get(f"/api/documentary/versions/{version.id}/reading-doubts").json()
    assert body["open"] == 2 and body["answered"] == 0 and [d["surface"] for d in body["doubts"]] == ["بهم", "ملک"]
    assert body["doubts"][0]["editor_latin"] == "beham" and body["doubts"][0]["checker_latin"] == "baham" and body["doubts"][0]["sentence"] == "سارا بهم گفت"


def test_all_the_answers_are_taken_in_one_go_and_a_word_can_be_kept_for_every_film(film, db_session):
    version, ids = film
    client = TestClient(app)
    body = client.post(f"/api/documentary/versions/{version.id}/reading-doubts", json={"answers": [
        {"id": ids[0], "choice": "checker", "everywhere": True}, {"id": ids[1], "choice": "custom", "latin": " malek "}]}).json()
    assert body["open"] == 0 and body["answered"] == 2
    assert [d["answer_latin"] for d in body["doubts"]] == ["baham", "malek"]
    kept = db_session.query(ReadingApproval).filter_by(language="zz").all()
    assert [(k.surface, k.latin, k.kind) for k in kept] == [("بهم", "baham", "spoken_form")]
    db_session.query(ReadingApproval).filter_by(language="zz").delete()
    db_session.commit()


def test_nothing_is_saved_when_one_answer_cannot_be_taken(film, db_session):
    version, ids = film
    client = TestClient(app)
    for answers in ([{"id": ids[0], "choice": "editor"}, {"id": ids[1], "choice": "custom"}],      # a custom answer without a reading
                    [{"id": ids[0], "choice": "editor"}, {"id": 99999999, "choice": "editor"}]):     # a doubt that is not this version's
        got = client.post(f"/api/documentary/versions/{version.id}/reading-doubts", json={"answers": answers})
        assert got.status_code == 409
    assert client.get(f"/api/documentary/versions/{version.id}/reading-doubts").json()["open"] == 2
    assert client.post(f"/api/documentary/versions/{version.id}/reading-doubts", json={"answers": [{"id": ids[0], "choice": "nonsense"}]}).status_code == 422
    assert client.get("/api/documentary/versions/99999999/reading-doubts").status_code == 404
