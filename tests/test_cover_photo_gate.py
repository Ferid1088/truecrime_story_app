"""The owner's "I'm finished" with the cover photographs: refused while no photograph is approved, recorded with the approved photographs, then every cover that has a text is made with them in turn
(a cover that cannot be made is reported, never hidden), and it can be opened again."""

import uuid

from test_host import _plan_story

from app.covers import photo_gate as PG
from app.covers import variants as V
from app.db.models import CoverPhotoApproval, CoverProposal


def _cover(db, case, language, key):
    db.add(CoverProposal(case_id=case.id, language=language, cover_key=key, kind="long" if key == "long" else "short", version=1, source="agent", status="open", title="a cover text of eight words or more here",
                         scene_prompt="", validation_json="{}"))


def test_finishing_needs_an_approved_photograph_and_records_the_approved_ones(db_session, client, monkeypatch):
    case, _, _ = _plan_story(db_session)
    db_session.query(CoverPhotoApproval).delete()
    db_session.commit()
    monkeypatch.setattr(PG, "approved_photos", lambda db, c, language: ("victim", []))
    refused = client.post(f"/api/cases/{case.id}/cover-photos/finished", json={})
    assert refused.status_code == 409 and "approve at least one photograph" in refused.json()["detail"] and client.get(f"/api/cases/{case.id}/cover-photos/finished").json()["finished"] is False


def test_finishing_makes_every_cover_with_the_approved_photographs_in_turn_and_reports_what_it_could_not(db_session, client, monkeypatch):
    case, _, _ = _plan_story(db_session)
    db_session.query(CoverPhotoApproval).delete()
    for key in ("long", "C1", "C2"):
        _cover(db_session, case, "fa", key)
    db_session.commit()
    used = []

    def render(db, c, language, key, **kw):
        if key == "C2":
            raise V.VariantError("plate_missing", "no host picture")
        used.append((language, key, kw["photo_asset_id"], kw["subject_entity"]))
        return [object()]

    monkeypatch.setattr(PG, "approved_photos", lambda db, c, language: ("victim", [11, 22]))
    monkeypatch.setattr(V, "render", render)
    done = client.post(f"/api/cases/{case.id}/cover-photos/finished", json={"approved_by": "owner"})
    body = done.json()
    assert done.status_code == 200 and body["finished"] is True and body["photo_ids"] == [11, 22] and body["made"] == 2
    assert used == [("fa", "long", 11, "victim"), ("fa", "C1", 22, "victim")] and body["skipped"] == [{"language": "fa", "key": "C2", "reason": "plate_missing"}]
    assert client.delete(f"/api/cases/{case.id}/cover-photos/finished").json()["finished"] is False
    assert client.get("/api/cases/999999/cover-photos/finished").status_code == 404 and uuid.uuid4()
