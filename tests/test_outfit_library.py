"""The outfit library: data/outfits is the source of truth — scan, classify,
validate, mark-missing. Nothing in data/outfits is ever deleted."""

import io
import uuid
from pathlib import Path

import pytest
from PIL import Image

from app.db.models import HostOutfit, OutfitItem
from app.outfits import library as lib


@pytest.fixture(autouse=True)
def _clean_items(db_session):
    """The shared session keeps rows between tests — each scan test starts empty."""
    db_session.query(OutfitItem).delete()
    db_session.commit()


def _png(width=400, height=600, color=(40, 90, 160)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _photo(root: Path, studio: str, outfit: str, situation: str, data=None) -> Path:
    d = root / studio / outfit
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{studio}_{outfit}_{situation}.png"
    path.write_bytes(data if data is not None else _png())
    return path


def test_scan_imports_the_folder(db_session, tmp_path):
    _photo(tmp_path, "en", "blazerJeans", "stand")
    _photo(tmp_path, "en", "blazerJeans", "sitCrossFeet")
    _photo(tmp_path, "de", "brownSweater", "sitHands")
    out = lib.scan_library(db_session, trigger="test", root=tmp_path)
    assert out["added"] == 3 and out["found"] == 3
    rows = db_session.query(OutfitItem).all()
    assert len(rows) == 3
    assert {r.stance for r in rows} == {"standing", "sitting"}


def test_new_outfit_appears_after_rescan(db_session, tmp_path):
    _photo(tmp_path, "en", "first", "stand")
    lib.scan_library(db_session, trigger="t", root=tmp_path)
    _photo(tmp_path, "de", "brandNew", "leanWall")
    lib.scan_library(db_session, trigger="t", root=tmp_path)
    assert db_session.query(OutfitItem).filter_by(
        studio="de", outfit_slug="brandNew").count() == 1


def test_new_situation_name_works_without_code(db_session, tmp_path):
    _photo(tmp_path, "en", "x", "floatMidAir")
    lib.scan_library(db_session, trigger="t", root=tmp_path)
    row = db_session.query(OutfitItem).one()
    assert row.situation == "floatMidAir"
    assert row.stance == "unknown"      # classified, not rejected
    assert row.status == "available"


def test_changed_file_updates_hash(db_session, tmp_path):
    path = _photo(tmp_path, "en", "x", "stand")
    lib.scan_library(db_session, trigger="t", root=tmp_path)
    first = db_session.query(OutfitItem).one().sha256
    path.write_bytes(_png(color=(200, 30, 30)))
    out = lib.scan_library(db_session, trigger="t", root=tmp_path)
    assert out["updated"] == 1
    assert db_session.query(OutfitItem).one().sha256 != first


def test_removed_file_is_marked_missing_never_deleted(db_session, tmp_path):
    path = _photo(tmp_path, "en", "x", "stand")
    lib.scan_library(db_session, trigger="t", root=tmp_path)
    path.unlink()
    out = lib.scan_library(db_session, trigger="t", root=tmp_path)
    assert out["missing"] == 1
    row = db_session.query(OutfitItem).one()
    assert row.status == "missing_on_disk"      # row kept
    _photo(tmp_path, "en", "x", "stand")        # file back
    lib.scan_library(db_session, trigger="t", root=tmp_path)
    db_session.refresh(row)
    assert row.status == "available"


def test_wrong_studio_folder_is_rejected_not_imported(db_session, tmp_path):
    _photo(tmp_path, "xx", "ghostOutfit", "stand")
    out = lib.scan_library(db_session, trigger="t", root=tmp_path)
    assert db_session.query(OutfitItem).count() == 0
    assert any("not a studio" in p["issue"] for p in out["problems"])


def test_misnamed_file_flagged_but_others_import(db_session, tmp_path):
    d = tmp_path / "en" / "x"
    d.mkdir(parents=True)
    (d / "de_x_stand.png").write_bytes(_png())   # wrong studio prefix for folder
    _photo(tmp_path, "en", "x", "sitHands")
    out = lib.scan_library(db_session, trigger="t", root=tmp_path)
    assert out["added"] == 1                     # the good one imports
    assert any("unexpected name" in p["issue"] for p in out["problems"])


def test_broken_photo_flagged_without_blocking(db_session, tmp_path):
    _photo(tmp_path, "en", "x", "stand", data=b"not a png at all")
    _photo(tmp_path, "en", "x", "sitHands")
    lib.scan_library(db_session, trigger="t", root=tmp_path)
    import json
    rows = {r.situation: r for r in db_session.query(OutfitItem).all()}
    assert "not a PNG" in " ".join(json.loads(rows["stand"].issues_json))
    assert rows["sitHands"].status == "available"


def test_classify_pose_tokens():
    assert lib.classify_pose("sitCrossFeetChin")["stance"] == "sitting"
    assert lib.classify_pose("standPockets")["stance"] == "standing"
    assert lib.classify_pose("standPockets")["hands"] is True
    assert lib.classify_pose("sitCrossFeet")["crossed_legs"] is True
    assert lib.classify_pose("leanWall")["stance"] == "standing"  # 'lean' is a stand token
    assert lib.classify_pose("floatMidAir")["stance"] == "unknown"


def test_outfit_in_use_is_reported(db_session):
    from app.db.models import Case

    case = Case(canonical_title="in use", slug=f"u-{uuid.uuid4().hex[:8]}")
    db_session.add(case)
    db_session.commit()
    outfit = HostOutfit(case_id=case.id, language="en", name="x",
                        outfit_ref="en/blazerJeans", status="selected")
    db_session.add(outfit)
    db_session.commit()
    assert "en/blazerJeans" in lib.in_use(db_session)


def test_old_cases_still_resolve_their_outfits(db_session, tmp_path):
    """The registry still resolves a stored outfit_ref — tolerance changes nothing
    for outfits already picked by a case."""
    _photo(tmp_path, "en", "blazerJeans", "stand")
    _photo(tmp_path, "en", "blazerJeans", "sitCrossFeet")
    from app.outfits.registry import OutfitRegistry

    outfit = OutfitRegistry(tmp_path).resolve("en/blazerJeans")
    assert len(outfit.poses) == 2
    assert outfit.reference_pose("STANDING").stance == "STANDING"


def test_library_view_groups_per_studio(db_session, tmp_path):
    _photo(tmp_path, "en", "x", "stand")
    lib.scan_library(db_session, trigger="t", root=tmp_path)
    view = lib.library_view(db_session)
    en = next(s for s in view["studios"] if s["studio"] == "en")
    assert en["outfits"][0]["slug"] == "x"
    assert en["outfits"][0]["items"][0]["situation"] == "stand"
    assert view["last_scan"]["found"] == 1
    assert view["counts"]["available"] >= 1
