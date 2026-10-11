"""Download endpoints serve files from the data folders only, whatever a database row says."""
import uuid

from app.core import files
from app.db.models import Case, VisualAsset
from app.documentary import storage


def _asset(db, local_path):
    t = f"File {uuid.uuid4().hex[:6]}"
    case = Case(canonical_title=t, slug=t.lower().replace(" ", "-"), language="en")
    db.add(case)
    db.commit()
    a = VisualAsset(case_id=case.id, asset_type="photo", local_path=local_path, asset_code=uuid.uuid4().hex[:8])
    db.add(a)
    db.commit()
    return a


def test_a_stored_file_inside_the_data_folder_is_served(client, db_session):
    d = storage.ROOT / "data" / "cases" / "_test_serving"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "ok.jpg"
    f.write_bytes(b"\xff\xd8\xff fake")
    try:
        a = _asset(db_session, str(f.relative_to(storage.ROOT)))
        r = client.get(f"/api/visuals/{a.id}/file")
        assert r.status_code == 200 and r.content.startswith(b"\xff\xd8")
    finally:
        f.unlink()
        d.rmdir()


def test_a_row_pointing_outside_the_data_folder_gets_nothing(client, db_session, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    for evil in (str(secret), "../../../etc/passwd", "/etc/passwd", ".env"):
        a = _asset(db_session, evil)
        r = client.get(f"/api/visuals/{a.id}/file")
        assert r.status_code == 404, evil
        assert b"top secret" not in r.content and b"root:" not in r.content


def test_a_symlink_out_of_the_data_folder_is_refused(client, db_session, tmp_path):
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"x")
    d = storage.ROOT / "data" / "cases" / "_test_link"
    d.mkdir(parents=True, exist_ok=True)
    link = d / "link.jpg"
    link.symlink_to(outside)
    try:
        a = _asset(db_session, str(link.relative_to(storage.ROOT)))
        assert client.get(f"/api/visuals/{a.id}/file").status_code == 404
    finally:
        link.unlink()
        d.rmdir()


def test_is_allowed_unit(tmp_path):
    assert files.is_allowed(storage.ROOT / "data" / "x.png")
    assert not files.is_allowed(tmp_path / "x.png")
    assert not files.is_allowed(storage.ROOT / "data" / ".." / ".env")
