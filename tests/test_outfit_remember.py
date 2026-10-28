"""The owner's outfit and photo choice per language is kept for the next films, and a new outfit can be added to a
language from the app."""
import io
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.db.models import Case, OutfitDefault, OutfitItem
from app.documentary import outfits as OF
from app.main import app
from app.outfits import library as lib

client = TestClient(app)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 600), (40, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _case(db):
    case = Case(canonical_title="Remember", slug=f"rm-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    return case


@pytest.fixture
def library(db_session, monkeypatch, tmp_path):
    """Two outfits of the English studio in a scratch library, mirrored into outfit_items."""
    monkeypatch.setattr(lib, "ROOT", tmp_path)
    monkeypatch.setattr(lib, "OUTFIT_ROOT", tmp_path / "data" / "outfits")
    db_session.query(OutfitDefault).delete()
    db_session.query(OutfitItem).delete()
    db_session.commit()
    rows = {}
    for slug, poses in (("scratchBlazer", ("sitHands", "sitCalm", "stand")), ("scratchSweater", ("sitHands",))):
        for pose in poses:
            rows[(slug, pose)] = lib.add_photo(db_session, "en", slug, pose, _png())
    return rows


def _choose(db, case, ref, items):
    return OF.apply_choice(db, case.id, "en", f"registry:{ref}", [i.id for i in items], "owner")


def test_the_next_film_starts_with_the_outfit_and_photos_chosen_last(db_session, library, monkeypatch):
    monkeypatch.setattr(OF, "OutfitRegistry", lambda: __import__("app.outfits.registry", fromlist=["x"]).OutfitRegistry(
        lib.OUTFIT_ROOT))
    first = _case(db_session)
    kept = [library[("scratchBlazer", "sitHands")], library[("scratchBlazer", "stand")]]
    _choose(db_session, first, "en/scratchBlazer", kept)

    fresh = OF.choice_options(db_session, None, "en")                  # a film with no case yet
    option = next(o for o in fresh["options"] if o["ref"] == "en/scratchBlazer")
    assert fresh["selected"] == option["key"]
    assert sorted(p["id"] for p in option["photos"] if p["selected"]) == sorted(i.id for i in kept)
    other = next(o for o in fresh["options"] if o["ref"] == "en/scratchSweater")
    assert not other["selected"] and not any(p["selected"] for p in other["photos"])


def test_a_later_choice_replaces_what_is_remembered(db_session, library, monkeypatch):
    monkeypatch.setattr(OF, "OutfitRegistry", lambda: __import__("app.outfits.registry", fromlist=["x"]).OutfitRegistry(
        lib.OUTFIT_ROOT))
    _choose(db_session, _case(db_session), "en/scratchBlazer", [library[("scratchBlazer", "sitHands")]])
    _choose(db_session, _case(db_session), "en/scratchSweater", [library[("scratchSweater", "sitHands")]])
    fresh = OF.choice_options(db_session, None, "en")
    assert fresh["selected"] == "registry:en/scratchSweater"
    assert db_session.get(OutfitDefault, "en").outfit_ref == "en/scratchSweater"


def test_a_remembered_photo_that_left_the_library_is_not_pre_selected(db_session, library, monkeypatch):
    monkeypatch.setattr(OF, "OutfitRegistry", lambda: __import__("app.outfits.registry", fromlist=["x"]).OutfitRegistry(
        lib.OUTFIT_ROOT))
    gone = library[("scratchBlazer", "sitCalm")]
    _choose(db_session, _case(db_session), "en/scratchBlazer", [library[("scratchBlazer", "sitHands")], gone])
    gone.status = "missing_on_disk"
    db_session.commit()
    option = next(o for o in OF.choice_options(db_session, None, "en")["options"] if o["ref"] == "en/scratchBlazer")
    assert [p["situation"] for p in option["photos"] if p["selected"]] == ["sitHands"]


def test_a_language_with_nothing_remembered_selects_nothing(db_session, library):
    assert OF.choice_options(db_session, None, "en")["selected"] is None


def test_adding_a_photo_makes_a_new_outfit_in_that_languages_folder(db_session, library):
    row = lib.add_photo(db_session, "en", "Grey wool coat", "sitHands", _png())
    assert (row.studio, row.outfit_slug, row.stance, row.status) == ("en", "greyWoolCoat", "sitting", "available")
    assert (lib.OUTFIT_ROOT / "en" / "greyWoolCoat" / "en_greyWoolCoat_sitHands.png").is_file()


@pytest.mark.parametrize("studio, name, pose, data, why", [
    ("zz", "Coat", "sit", None, "not a studio"),
    ("en", "!!!", "sit", None, "needs a name"),
    ("en", "Coat", "wave", None, "sitting or standing"),
    ("en", "Coat", "sit hands", None, "letters and digits"),
    ("en", "Coat", "sit", b"not a png", "must be a PNG"),
    ("en", "scratchBlazer", "sitHands", None, "already exists"),
])
def test_a_photo_that_cannot_be_used_is_refused_and_nothing_is_written(db_session, library, studio, name, pose, data,
                                                                       why):
    before = sorted(p.name for p in lib.OUTFIT_ROOT.rglob("*.png"))
    with pytest.raises(lib.AddPhotoError, match=why):
        lib.add_photo(db_session, studio, name, pose, _png() if data is None else data)
    assert sorted(p.name for p in lib.OUTFIT_ROOT.rglob("*.png")) == before


def test_the_upload_endpoint_adds_the_photo_and_reports_a_refusal(db_session, library):
    ok = client.post("/api/outfit-library/en/photos", data={"outfit": "Black suit", "situation": "stand"},
                     files={"photo": ("x.png", _png(), "image/png")})
    assert ok.status_code == 200 and ok.json()["ref"] == "en/blackSuit" and ok.json()["stance"] == "standing"
    bad = client.post("/api/outfit-library/en/photos", data={"outfit": "Black suit", "situation": "stand"},
                      files={"photo": ("x.png", _png(), "image/png")})
    assert bad.status_code == 400 and "already exists" in bad.json()["detail"]
