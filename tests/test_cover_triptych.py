"""The three coordinated covers of a Short (9:16, 4:5, 1:1), drawn in code. The studio frame and the logo are synthetic here (a face cannot be made up for the detector, so the host's
face position is given); the checks are about the layout, the scripts, the key-word stroke, the banner and what the validator reports."""

from __future__ import annotations

import asyncio
import json

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.covers import triptych as T
from app.covers.compose import CoverError


@pytest.fixture(autouse=True)
def _renders_stay_proposals(monkeypatch):
    """These tests are about the renders and their approvals one by one: the app's automatic approval of the upload shapes has its own test at the end."""
    monkeypatch.setitem(T.settings()["approval"], "auto_approve_upload_shapes", False)


@pytest.fixture
def assets(tmp_path, monkeypatch):
    plate = Image.new("RGB", (1920, 1080), (60, 38, 20))
    ImageDraw.Draw(plate).ellipse((880, 300, 980, 420), fill=(200, 160, 130))          # a "face" for the layout; the detector is not asked to find it
    plate.save(tmp_path / "plate.png")
    logo = Image.new("RGB", (400, 400), (0, 0, 0))
    ImageDraw.Draw(logo).ellipse((100, 100, 300, 300), fill=(220, 190, 140))
    logo.save(tmp_path / "logo.png")
    photo = Image.new("RGB", (600, 700), (120, 120, 120))
    photo.save(tmp_path / "photo.png")
    monkeypatch.setattr(T.AV, "host_face", lambda image: (880.0, 300.0, 100.0, 120.0))
    return tmp_path


def make(assets, language="en", title="The Break-In That Left Nothing Behind At All", **kw):
    return T.compose_set(language, title, assets / "plate.png", assets / "logo.png", highlight=kw.pop("highlight", "Nothing"), **kw)


def test_the_three_formats_are_composed_at_their_own_sizes_for_a_latin_and_a_right_to_left_language(assets):
    for language, title, key in (("en", "The Break-In That Left Nothing Behind At All", "Nothing"), ("fa", "زنی که هیچ ردی از خود برنگذاشت و کسی ندید", "برنگذاشت"), ("ar", "اقتحامٌ لم يترك خلفه شيئًا ولم يره أحد قط", "شيئًا")):
        out = make(assets, language, title, highlight=key, subject="Name")
        assert {k: out[k]["image"].size for k in out} == {"reel": (1080, 1920), "feed": (1080, 1350), "grid": (1080, 1080)}
        for name, made in out.items():
            for element, (x, y, w, h) in made["elements"].items():
                assert x >= 0 and y >= 0 and x + w <= made["image"].width and y + h <= made["image"].height, (language, name, element)


def test_the_key_word_stands_on_a_red_brush_stroke_and_the_other_words_do_not(assets):
    image = make(assets)["reel"]["image"]
    pixels = np.asarray(image).astype(int)
    title = make(assets)["reel"]["elements"]["title"]
    box = pixels[title[1]:title[1] + title[3], title[0]:title[0] + title[2]]
    red = ((box[..., 0] > 140) & (box[..., 1] < 60) & (box[..., 2] < 60)).mean()
    assert 0.01 < red < 0.35                                                                      # a stroke behind part of the words, not the whole block


def test_the_banner_names_the_person_and_never_runs_off_the_picture_however_long_the_name(assets):
    out = make(assets, subject="A Person With A Very Very Very Long Double Barrelled Surname")
    for made in out.values():
        x, _, w, _ = made["elements"]["banner"]
        assert x >= 0 and x + w <= made["image"].width and "banner" not in " ".join(made["problems"])
    assert "banner" not in make(assets)["reel"]["elements"]                                       # no person named, no banner


def test_a_photo_appears_only_when_one_is_given_and_a_cover_text_of_seven_or_nineteen_words_is_refused(assets):
    assert "photo" not in make(assets)["reel"]["elements"]
    assert "photo" in make(assets, photo=assets / "photo.png")["reel"]["elements"] and make(assets, title="one two three four five six seven eight")["reel"]["elements"]["title"]      # eight words is the fewest
    for count in (7, 19):
        with pytest.raises(CoverError):
            make(assets, title=" ".join(["word"] * count))


def test_the_validator_reports_a_title_that_covers_the_host_and_a_picture_without_a_visible_face(assets):
    made = make(assets)["reel"]
    lay = T.settings()["layout"]["reel"]
    covering = {**made["elements"], "title": [0, round(lay["host"]["face_y"] * 1920) - 50, 1080, 120]}
    problems = T.problems_of(made["image"], covering, (1080, 1920), lay)
    assert "the title covers the host's face" in problems and any("face" in p for p in problems)


def test_every_language_has_a_banner_label_and_a_layout_for_each_format():
    cfg = T.settings()
    assert set(cfg["banner"]["labels"]) == {"en", "de", "fa", "ar", "es", "tr", "fr", "it", "pt"} and set(cfg["layout"]) == set(T.FORMATS) == set(cfg["formats"])


# --- the three shapes inside the cover review: storage, independent approval, rights of a photograph --------------------------------------------------------------

import shutil  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from app.core import files  # noqa: E402
from app.covers import service as SV  # noqa: E402
from app.covers import variants as V  # noqa: E402
from app.db.models import (  # noqa: E402
    Case,
    CoverProposal,
    CoverVariant,
    HostLook,
    HostOutfit,
    ShortClip,
    VisualAsset,
)
from app.documentary import storage  # noqa: E402
from app.agents import shorts as SA  # noqa: E402
from app.covers import subject as SU  # noqa: E402
from app.outfits import registry  # noqa: E402

OUTFIT = Path(__file__).resolve().parents[1] / "data" / "outfits" / "en" / "navyBlazerBeigeChinos" / "en_navyBlazerBeigeChinos_front.png"
needs_assets = pytest.mark.skipif(not OUTFIT.exists(), reason="the host's outfit image is needed")
TITLE = "The Break-In That Left Nothing Behind At All"


@pytest.fixture
def review(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "case_dir", lambda case_id: tmp_path / "cases" / str(case_id))
    monkeypatch.setattr(files, "is_allowed", lambda path: True)
    for language, outfit in (("en", "navyBlazerBeigeChinos"), ("it", "navyFieldjacketTurtleneck")):                    # an outfit library of this test: the host's front picture under the registry's layout
        folder = tmp_path / "outfits" / language / outfit
        folder.mkdir(parents=True)
        shutil.copy(OUTFIT, folder / f"{language}_{outfit}_front.png")
    monkeypatch.setattr(registry, "_ROOT", tmp_path / "outfits")

    async def first_person(db, case, people, gen=None):                                                  # the model that reads the roles is not called here (it has its own tests)
        return (people[0]["entity_key"] if people else None), "test: the first person", []

    monkeypatch.setattr(SU, "choose", first_person)
    case = Case(canonical_title="Variants test", slug=f"variants-{uuid.uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    db_session.add(HostLook(studio_profile_id="STUDIO_EN", language="en", camera="CAM01_FRONT", framing="MEDIUM", outfit_id="en/navyBlazerBeigeChinos", pose="P01_NEUTRAL_SEATED", status="approved",
                            heygen_look_id=f"LOOK_{case.id}"))
    db_session.add(ShortClip(case_id=case.id, language="en", clip_key="C1", media_json=json.dumps({"avatars": {"hook": {"look_id": f"LOOK_{case.id}"}}})))
    db_session.add(CoverProposal(case_id=case.id, language="en", cover_key="C1", kind="short", status="proposed", title=TITLE, scene_prompt="a door", confidence=0.8))
    db_session.commit()
    yield type("Review", (), {"case": case, "db": db_session, "tmp": tmp_path})
    from conftest import _delete_case_tree

    db_session.rollback()
    for model in (CoverVariant, CoverProposal, VisualAsset, ShortClip, HostOutfit):
        db_session.query(model).filter_by(case_id=case.id).delete()
    db_session.query(HostLook).filter_by(heygen_look_id=f"LOOK_{case.id}").delete()
    db_session.commit()
    _delete_case_tree(db_session, case.id)


def asset(review, rights="editorial_review_required", approved_by=None) -> VisualAsset:
    path = review.tmp / f"person_{uuid.uuid4().hex[:6]}.jpg"
    Image.new("RGB", (900, 1100), (150, 140, 130)).save(path)
    row = VisualAsset(case_id=review.case.id, asset_code=f"VIS_T{uuid.uuid4().hex[:6]}", asset_type="image", entities_json="[]", asset_role="portrait", story_functions_json="[]", provider="test",
                      rights_status=rights, verification_status="verified", reveals_json="[]", has_original_audio=False, human_override=False, local_path=str(path), width=900, height=1100,
                      editorial_approved_by=approved_by)
    review.db.add(row)
    review.db.commit()
    return row


def candidates(monkeypatch, *people):
    monkeypatch.setattr(V, "subject_candidates", lambda db, case, language: list(people))


def person(row: VisualAsset, name="Ashley Flynn", key="ashley_flynn", extra=0) -> dict:
    ok, why = V.authorization(row)
    photos = [{"asset_id": row.id, "authorized": ok, "why": why}] + [{"asset_id": -i, "authorized": False, "why": "n"} for i in range(extra)]
    return {"entity_key": key, "name": name, "photos": photos}


@needs_assets
def test_a_render_stores_three_versions_with_their_files_and_never_pays_for_anything(review, monkeypatch):
    candidates(monkeypatch)
    rows = V.render(review.db, review.case, "en", "C1")
    assert {r.shape: (r.width, r.height) for r in rows} == {"reel": (1080, 1920), "feed": (1080, 1350), "grid": (1080, 1080)}
    assert all(r.status == "proposed" and r.version == 1 and r.title == TITLE and storage.resolve(r.file_path).is_file() for r in rows)
    assert all(json.loads(r.evidence_json)["paid_calls"] == 0 and json.loads(r.evidence_json)["title_status"] == "proposed" for r in rows)
    again = V.render(review.db, review.case, "en", "C1", shapes=["feed"])
    assert [(r.shape, r.version) for r in again] == [("feed", 2)] and review.db.query(CoverVariant).filter_by(cover_key="C1", shape="feed").count() == 2


@needs_assets
def test_each_shape_is_approved_on_its_own_and_a_newer_version_never_replaces_an_approved_one_by_itself(review, monkeypatch):
    candidates(monkeypatch)
    monkeypatch.setattr(T, "problems_of", lambda *a, **k: [])
    rows = {r.shape: r for r in V.render(review.db, review.case, "en", "C1")}
    V.approve(review.db, rows["reel"].id)
    assert [r.status for r in (rows["reel"], rows["feed"], rows["grid"])] == ["approved", "proposed", "proposed"]
    newer = V.render(review.db, review.case, "en", "C1", shapes=["reel"], highlight="Left Nothing")[0]
    review.db.refresh(rows["reel"])
    assert newer.version == 2 and newer.status == "proposed" and rows["reel"].status == "approved" and newer.highlight == "Left Nothing"     # the approved one is untouched
    V.approve(review.db, newer.id)
    review.db.refresh(rows["reel"])
    assert rows["reel"].status == "superseded" and storage.resolve(rows["reel"].file_path).is_file() and newer.status == "approved" and newer.approved_by == "owner"
    assert V.reject(review.db, rows["grid"].id).status == "rejected"
    with pytest.raises(V.VariantError) as caught:
        V.approve(review.db, rows["grid"].id)
    assert caught.value.reason == "not_proposed"
    review.db.query(CoverProposal).filter_by(case_id=review.case.id).update({"status": "proposed"})
    SV.auto_approve(review.db, review.case, "en")                                                  # the default approval of covers never touches the three shapes
    latest = {r.shape: r.status for r in review.db.query(CoverVariant).filter_by(case_id=review.case.id, cover_key="C1").order_by(CoverVariant.version)}
    assert latest == {"reel": "approved", "feed": "proposed", "grid": "rejected"}                  # the default approval of covers did not approve the shapes that were waiting


@needs_assets
def test_a_version_with_problems_cannot_be_approved(review, monkeypatch):
    candidates(monkeypatch)
    monkeypatch.setattr(T, "problems_of", lambda *a, **k: ["a face is cut by the edge"])
    row = V.render(review.db, review.case, "en", "C1", shapes=["reel"])[0]
    with pytest.raises(V.VariantError) as caught:
        V.approve(review.db, row.id)
    assert caught.value.reason == "problems" and "face is cut" in str(caught.value) and row.status == "proposed"


@needs_assets
def test_a_photograph_is_used_only_when_authorized_and_its_approval_needs_the_copyright_acknowledgement(review, monkeypatch):
    monkeypatch.setitem(T.settings()["subject_photo"]["standing_approval"], "enabled", False)          # without the owner's standing instruction only an editorial approval clears a photograph
    monkeypatch.setattr(T, "problems_of", lambda *a, **k: [])
    uncleared, cleared = asset(review), asset(review, approved_by="owner")
    candidates(monkeypatch, person(uncleared))
    plain = V.render(review.db, review.case, "en", "C1", shapes=["reel"])[0]
    assert plain.photo_asset_id is None and "photo" not in json.loads(plain.elements_json) and plain.subject_name == "Ashley Flynn"           # the name is on the banner, the photo is not on the cover
    candidates(monkeypatch, person(cleared))
    with_photo = V.render(review.db, review.case, "en", "C1", shapes=["reel"])[0]
    note = json.loads(with_photo.evidence_json)["photo"]
    assert with_photo.photo_asset_id == cleared.id and "photo" in json.loads(with_photo.elements_json) and with_photo.cover_key == "C1"          # the cover's own key, never the person's
    assert note["authorized"] and "not copyright permission" in note["why"] and "not proof of copyright permission" in note["copyright_note"]
    monkeypatch.setitem(T.settings()["approval"], "rights_acknowledgement_required", True)         # the owner may switch the acknowledgement back on
    with pytest.raises(V.VariantError) as caught:
        V.approve(review.db, with_photo.id)
    assert caught.value.reason == "rights_not_acknowledged" and "not proof of copyright permission" in str(caught.value)
    monkeypatch.setitem(T.settings()["approval"], "rights_acknowledgement_required", False)
    cleared.editorial_approved_by = None                                                              # the approval was withdrawn after the render
    review.db.commit()
    with pytest.raises(V.VariantError) as caught:
        V.approve(review.db, with_photo.id, acknowledge_rights=True)
    assert caught.value.reason == "photo_not_authorized"
    cleared.editorial_approved_by = "owner"
    review.db.commit()
    assert V.approve(review.db, with_photo.id).status == "approved"                                 # by default no acknowledgement is asked
    no_photo = V.render(review.db, review.case, "en", "C1", shapes=["feed"], use_photo=False)[0]
    assert no_photo.photo_asset_id is None


def test_open_rights_and_editorial_approval_are_told_apart_and_nothing_else_is_authorized():
    row = lambda **k: SimpleNamespace(rights_status=k.get("rights", "unknown"), license=k.get("license"), editorial_approved_by=k.get("by"))  # noqa: E731
    assert V.authorization(row(rights="public_domain"))[0] and V.authorization(row(rights="creative_commons", license="CC BY 4.0"))[1].endswith("(CC BY 4.0)")
    assert V.authorization(row(rights="editorial_review_required", by="owner")) == (True, "approved editorially by owner: this is not copyright permission")
    for rights in ("editorial_review_required", "permission_required", "do_not_use", "unknown"):
        assert V.authorization(row(rights=rights))[0] is False
    assert V.authorization(row(rights="permission_required", by="owner"))[0] is False                # an editorial approval does not unlock a picture that needs permission


@needs_assets
def test_the_most_photographed_person_is_the_default_and_no_person_means_no_banner(review, monkeypatch):
    a, b = asset(review, approved_by="owner"), asset(review, approved_by="owner")
    candidates(monkeypatch, person(a, "Ashley Flynn", "ashley", extra=3), person(b, "Caleb Flynn", "caleb"))
    assert V.render(review.db, review.case, "en", "C1", shapes=["reel"])[0].subject_name == "Ashley Flynn"
    assert V.render(review.db, review.case, "en", "C1", shapes=["reel"], subject_entity="caleb")[0].subject_name == "Caleb Flynn"
    none = V.render(review.db, review.case, "en", "C1", shapes=["reel"], subject_entity="none")[0]
    assert none.subject_name == "" and none.photo_asset_id is None and "banner" not in json.loads(none.elements_json)


@needs_assets
def test_a_missing_studio_video_a_missing_title_and_the_film_cover_are_reported_and_nothing_is_invented(review, monkeypatch):
    candidates(monkeypatch)
    with pytest.raises(V.VariantError) as caught:
        V.render(review.db, review.case, "it", "C1")                                              # no Italian proposal, no Italian avatar video
    assert caught.value.reason == "no_title"
    review.db.add(CoverProposal(case_id=review.case.id, language="it", cover_key="C1", kind="short", status="proposed", title="Titolo approvato di prova per il caso di oggi", scene_prompt="x", confidence=0.7))
    review.db.commit()
    with pytest.raises(V.VariantError) as caught:
        V.render(review.db, review.case, "it", "C1")
    assert caught.value.reason == "plate_missing" and "no it avatar video for C1" in str(caught.value)
    state = V.overview(review.db, review.case, "it", "C1")
    assert state["plate"]["available"] is False and state["can_render"] is False and state["hook"]["title"] == "Titolo approvato di prova per il caso di oggi"
    with pytest.raises(V.VariantError) as caught:
        V.render(review.db, review.case, "en", "long", shapes=["reel"])                              # the film has its own 16:9 shape, not the Short's
    assert caught.value.reason == "unknown_shape" and V.shapes_of("long") == ("film",) and V.shapes_of("C1") == ("reel", "feed", "grid")
    with pytest.raises(V.VariantError) as caught:
        V.render(review.db, review.case, "en", "long")                                              # and it still needs a title from the planner
    assert caught.value.reason == "no_title"
    assert review.db.query(CoverVariant).filter_by(case_id=review.case.id).count() == 0


def test_the_platforms_get_one_cover_and_the_other_shapes_are_previews_only():
    matrix = {p["destination"]: p for p in V.platforms("C1")}
    assert set(matrix) == {"youtube/short", "facebook/short", "instagram/short", "tiktok/short"}
    for entry in matrix.values():
        assert entry["candidate_shape"] in ("reel", None) and "feed" in entry["preview_only"] and "grid" in entry["preview_only"]
    assert "short_C1.jpg" in matrix["youtube/short"]["uploaded_now"] and matrix["tiktok/short"]["candidate_shape"] is None and set(matrix["tiktok/short"]["preview_only"]) == {"reel", "feed", "grid"}
    assert all(not e["uploaded_now"].lower().startswith("the three shapes are uploaded") for e in matrix.values())


@needs_assets
def test_the_routes_render_show_approve_and_serve_the_versions(review, client, monkeypatch):
    candidates(monkeypatch)
    monkeypatch.setattr(T, "problems_of", lambda *a, **k: [])
    base = f"/api/cases/{review.case.id}/covers/en/C1/shapes"
    assert client.get(base).json()["shapes"]["reel"]["newest"] is None
    shown = client.post(base, json={"shapes": ["reel", "grid"]}).json()
    assert shown["shapes"]["reel"]["newest"]["status"] == "proposed" and shown["shapes"]["feed"]["newest"] is None and shown["can_render"] is True
    reel = shown["shapes"]["reel"]["newest"]
    picture = client.get(reel["image_url"])
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/jpeg" and Image.open(__import__("io").BytesIO(picture.content)).size == (1080, 1920)
    assert client.post(f"/api/cover-variants/{reel['id']}/approve", json={}).json()["status"] == "approved"
    assert client.post(f"/api/cover-variants/{reel['id']}/approve", json={}).status_code == 409
    assert client.post(f"/api/cover-variants/{shown['shapes']['grid']['newest']['id']}/reject").json()["status"] == "rejected"
    assert client.post(base, json={"shapes": ["sideways"]}).status_code == 409 and client.get("/api/cover-variants/999999/image").status_code == 404


# --- local improvements: the host frame chosen by score, the key phrase by rule, native names from the story, the edge of the picture -----------------------------------
from app.db.models import StoryVersion  # noqa: E402


def test_the_key_phrase_is_the_meaningful_words_by_rule_and_not_the_last_word():
    assert T.key_phrase("The Break-In That Left Nothing Behind At All", "en") == "LEFT NOTHING"
    assert T.key_phrase("Le cambriolage qui n'a rien laissé derrière lui", "fr")== "RIEN LAISSÉ"
    assert T.key_phrase("Ein Einbruch, der nichts hinterließ", "de").startswith("NICHTS")
    assert "هیچ" in T.key_phrase("دزدی که هیچ ردی از خودش نگذاشت", "fa")
    assert T.key_phrase("the of and", "en") == "AND"                                    # nothing meaningful at all: the last word, never an error


def test_a_key_phrase_that_breaks_over_two_lines_gets_its_stroke_on_both_lines():
    assert T._key_run(["THAT", "LEFT"], ["LEFT", "NOTHING"]) == (1, 1)
    assert T._key_run(["NOTHING", "BEHIND"], ["LEFT", "NOTHING"]) == (0, 1)
    assert T._key_run(["LEFT", "NOTHING", "BEHIND"], ["LEFT", "NOTHING"]) == (0, 2)
    assert T._key_run(["A", "B"], ["LEFT"]) == (0, 0)


def test_the_side_strips_are_darkened_so_a_studio_sign_cut_by_the_edge_is_hidden():
    lay = T.settings()["layout"]["reel"]["veil"]
    veil = np.asarray(T._gradient((1080, 1920), 0, 0, 0.4, 0.2, lay["edge"], lay["edge_reach"]).getchannel("A")).astype(int)
    assert veil[960, :40].mean() > 150 and veil[960, 540] < 30                       # almost black at the very edge, clear in the middle


def test_the_validator_reports_the_hosts_face_too_close_to_the_edge_and_ignores_faces_painted_on_the_wall(monkeypatch):
    image = Image.new("RGB", (1080, 1920), (30, 20, 10))
    elements = {"title": [100, 200, 800, 200]}
    lay = {**T.settings()["layout"]["reel"], "host": {**T.settings()["layout"]["reel"]["host"], "face_x": 0.1}}
    host = [10.0, 900.0, 200.0, 240.0] + [0.0] * 10 + [0.95]
    wall = [900.0, 300.0, 120.0, 150.0] + [0.0] * 10 + [0.85]                               # confident, but not where the host is: a face on a studio picture
    uncertain = [5.0, 1500.0, 150.0, 200.0] + [0.0] * 10 + [0.55]

    def seen(*faces):
        fake = SimpleNamespace(detect=lambda frame: (1, np.array(faces, dtype=np.float32)))
        monkeypatch.setattr(cv2, "FaceDetectorYN", SimpleNamespace(create=lambda *a, **k: fake))
        return T.problems_of(image, elements, (1080, 1920), lay)

    assert "the host's face is too close to the edge" in seen(host, wall, uncertain)
    assert "the host's face is too close to the edge" not in seen(wall, uncertain, [300.0, 900.0, 200.0, 240.0] + [0.0] * 10 + [0.95])


def test_the_stroke_of_a_key_phrase_is_measured_without_a_trailing_space_so_it_sits_under_the_words_in_both_directions():
    from app.documentary.visuals import typography as TY

    font = TY.font("ar", "sans", "bold", 100)
    words = ["اقتحامٌ", "لم", "يترك"]
    reach, span = TY.text_width(" ".join(words), font, "ar"), TY.text_width("لم يترك", font, "ar")
    assert 0 < span < reach and reach == TY.text_width(" ".join(words[:3]), font, "ar")


def test_the_banner_keeps_a_latin_name_in_latin_letters_inside_arabic_and_persian(assets):
    for language in ("ar", "fa"):
        out = make(assets, language, "اقتحامٌ لم يترك خلفه شيئًا ولم يره أحد قط", highlight="شيئًا", subject="Ashley Flynn")
        assert "banner" in out["reel"]["elements"] and not [p for p in out["reel"]["problems"] if "banner" in p]


def test_a_native_spelling_is_used_only_when_the_languages_own_story_contains_it(review):
    spelling = T.settings()["native_names"]["Ashley Flynn"]["fa"]
    assert V.banner_name(review.db, review.case.id, "fa", "Ashley Flynn")[0] == "Ashley Flynn"                       # no story yet: the name as stored
    review.db.add(StoryVersion(case_id=review.case.id, version=1, kind="direct", narrative_angle="x", language="fa", story_text=f"او {spelling} بود و {spelling} آمد"))
    review.db.commit()
    name, check = V.banner_name(review.db, review.case.id, "fa", "Ashley Flynn")
    assert name == spelling and check["checked_in_story"] == 2
    assert V.banner_name(review.db, review.case.id, "de", "Ashley Flynn") == ("Ashley Flynn", {"source": "name as stored", "checked_in_story": None})
    review.db.query(StoryVersion).filter_by(case_id=review.case.id).delete()
    review.db.commit()


@needs_assets
def test_a_new_render_supersedes_the_older_proposals_of_that_shape_but_never_an_approved_one(review, monkeypatch):
    candidates(monkeypatch)
    monkeypatch.setattr(T, "problems_of", lambda *a, **k: [])
    first = {r.shape: r for r in V.render(review.db, review.case, "en", "C1")}
    V.approve(review.db, first["reel"].id)
    second = {r.shape: r for r in V.render(review.db, review.case, "en", "C1")}
    for row in first.values():
        review.db.refresh(row)
    assert first["reel"].status == "approved" and first["feed"].status == "superseded" and first["grid"].status == "superseded"
    assert all(r.status == "proposed" for r in second.values())
    evidence = json.loads(second["reel"].evidence_json)
    assert evidence["host_picture"]["outfit"] == "en/navyBlazerBeigeChinos" and evidence["host_picture"]["from"] == "the clip's avatar video" and "name_check" in evidence


def test_the_host_picture_is_the_outfit_of_the_clip_read_from_its_look_and_a_language_without_a_clip_uses_its_selected_outfit(review):
    still, source, why = V.plate_for(review.db, review.case.id, "en", "C1")
    assert why is None and still.name == OUTFIT.name
    assert source["outfit"] == "en/navyBlazerBeigeChinos" and source["pose"] == "front" and source["from"] == "the clip's avatar video"
    nothing = V.plate_for(review.db, review.case.id, "it", "C1")
    assert nothing[0] is None and "no it avatar video for C1" in nothing[2]
    review.db.add(HostOutfit(case_id=review.case.id, language="it", name="Fieldjacket", outfit_ref="it/navyFieldjacketTurtleneck", status="selected"))
    review.db.commit()
    chosen, source, why = V.plate_for(review.db, review.case.id, "it", "C1")
    assert why is None and chosen.name == "it_navyFieldjacketTurtleneck_front.png" and "selected for the language" in source["from"]
    review.db.query(ShortClip).filter_by(case_id=review.case.id).update({"media_json": json.dumps({"avatars": {"hook": {"look_id": "LOOK_UNKNOWN"}}})})
    review.db.commit()
    assert V.plate_for(review.db, review.case.id, "en", "C1")[2].startswith("The look LOOK_UNKNOWN")


def test_the_reel_keeps_its_logo_title_banner_and_face_inside_the_part_a_feed_crop_keeps(assets):
    lay = T.settings()["layout"]["reel"]
    band = lay["safe_band"]
    made = make(assets, subject="Name")["reel"]
    for name, (_, y, _, height) in made["elements"].items():
        assert band["top"] * 1920 <= y and y + height <= band["bottom"] * 1920, name
    assert not [p for p in made["problems"] if "middle part" in p]
    moved = {**made["elements"], "banner": [100, round(0.9 * 1920), 600, 115]}                                   # a banner at the old place, where Facebook's feed cuts it
    assert "the banner is outside the middle part that a feed crop keeps" in T.problems_of(made["image"], moved, (1080, 1920), lay)
    assert "safe_band" not in T.settings()["layout"]["feed"] and "safe_band" not in T.settings()["layout"]["grid"]


def test_the_film_cover_is_one_16_by_9_picture_mirrored_for_right_to_left_languages_and_checked_like_the_others(assets):
    for language, title, key, host_on_the_right in (("en", "The Love Song That Ended In Silence One Winter Night", "Night", True), ("fa", "عاشقانه‌ای که در سکوت یک شب زمستانی تمام شد", "تمام شد", False)):
        made = T.compose_set(language, title, assets / "plate.png", assets / "logo.png", highlight=key, subject="Name", shapes=T.FILM_SHAPES)
        assert list(made) == ["film"] and made["film"]["image"].size == (1920, 1080)
        title_box, logo = made["film"]["elements"]["title"], made["film"]["elements"]["logo"]
        assert (title_box[0] + title_box[2] / 2 < 960) == host_on_the_right and 0 <= logo[0] and logo[0] + logo[2] <= 1920 and logo[1] + logo[3] < title_box[1] + title_box[3]      # the words on the side the host is not on; the logo above the host's shoulder, inside the picture
        assert not [p for p in made["film"]["problems"] if "outside the picture" in p]
    lay = T.settings()["layout"]["film"]
    assert T._mirrored(lay)["host"]["face_x"] == pytest.approx(1 - lay["host"]["face_x"]) and "safe_band" not in lay


def test_an_approved_film_cover_made_here_becomes_the_films_cover_and_the_youtube_thumbnail_and_nothing_else_does(review, tmp_path):
    from types import SimpleNamespace

    from app.publishing.registry import PublicationRegistry
    from app.youtube_publishing.uploader import YouTubeUploader

    picture = tmp_path / "tri_long_film_v1.jpg"
    Image.new("RGB", (1920, 1080), (40, 20, 20)).save(picture)

    def film(version: int, **kw) -> CoverVariant:
        row = CoverVariant(case_id=review.case.id, language="en", cover_key="long", shape="film", version=version, status="proposed", title="t", highlight="h", subject_name="n", file_path=str(picture),
                           file_hash="abc", width=1920, height=1080, elements_json="{}", problems_json="[]", evidence_json="{}", **kw)
        review.db.add(row)
        review.db.commit()
        return row

    registry = PublicationRegistry(review.db)
    proposed = film(1)
    assert V.approved_cover(review.db, review.case.id, "en", "long") is None                          # a proposal is not the cover
    assert registry._cover(review.case.id, "en", "long")[0] != f"film:{proposed.id}"
    approved = film(2, approved_by="owner")
    approved.status = "approved"
    review.db.commit()
    ref, digest, state, problem = registry._cover(review.case.id, "en", "long")
    assert (ref, state, problem) == (f"film:{approved.id}", "approved", None) and digest
    assert registry._cover_url(review.case.id, "en", "long") == f"/api/cover-variants/{approved.id}/image"
    uploader = object.__new__(YouTubeUploader)
    uploader.db = review.db
    assert uploader._cover_file(SimpleNamespace(cover_ref=ref, kind="film", case_id=review.case.id, language="en", asset_key="long")) == picture
    approved.problems_json = json.dumps(["a face is cut by the edge"])
    review.db.commit()
    assert V.approved_cover(review.db, review.case.id, "en", "long") is None and not registry._cover(review.case.id, "en", "long")[0] == f"film:{approved.id}"     # a cover with problems is not used
    assert registry._cover(review.case.id, "en", "C1")[0] != f"film:{approved.id}"                  # a Short's cover is never a film cover


def test_an_approved_reel_cover_becomes_a_shorts_cover_and_its_youtube_thumbnail_while_feed_and_grid_never_do(review, tmp_path):
    from types import SimpleNamespace

    from app.publishing.registry import PublicationRegistry
    from app.youtube_publishing.uploader import YouTubeUploader

    picture = tmp_path / "tri_C1_reel_v1.jpg"
    Image.new("RGB", (1080, 1920), (40, 20, 20)).save(picture)

    def variant(shape: str, version: int) -> CoverVariant:
        row = CoverVariant(case_id=review.case.id, language="en", cover_key="C1", shape=shape, version=version, status="approved", title="t", highlight="h", subject_name="n", file_path=str(picture),
                           file_hash="abc", width=1080, height=1920, elements_json="{}", problems_json="[]", evidence_json="{}", approved_by="owner")
        review.db.add(row)
        review.db.commit()
        return row

    registry = PublicationRegistry(review.db)
    variant("feed", 1), variant("grid", 1)
    assert V.approved_cover(review.db, review.case.id, "en", "C1") is None                            # only the reel shape is a platform cover
    reel = variant("reel", 1)
    ref, digest, state, problem = registry._cover(review.case.id, "en", "C1")
    assert (ref, state, problem) == (f"reel:{reel.id}", "approved", None) and digest
    assert registry._cover_url(review.case.id, "en", "C1") == f"/api/cover-variants/{reel.id}/image"
    uploader = object.__new__(YouTubeUploader)
    uploader.db = review.db
    assert uploader._cover_file(SimpleNamespace(cover_ref=ref, kind="short", case_id=review.case.id, language="en", asset_key="C1")) == picture
    assert registry._cover(review.case.id, "en", "C2")[0] != ref                                      # another Short is not affected


# --- whose photograph the covers show ---------------------------------------------------------------------------------------------------------------------------

def role(key, kind, gender):
    return {"entity_key": key, "role": kind, "gender": gender}


def test_the_woman_among_victim_and_culprit_is_on_every_cover_the_victim_first_else_the_victim():
    rule = SU.apply_rule
    assert rule([role("a", "victim", "female"), role("b", "culprit", "male")], ["a", "b"])[0] == "a"
    assert rule([role("a", "victim", "male"), role("b", "culprit", "female")], ["a", "b"])[0] == "b"
    assert rule([role("a", "victim", "female"), role("b", "culprit", "female")], ["a", "b"])[0] == "a"
    assert rule([role("a", "victim", "male"), role("b", "culprit", "male")], ["a", "b"])[0] == "a"
    assert rule([role("a", "victim", "unknown"), role("b", "culprit", "unknown")], ["a", "b"])[0] == "a"
    assert rule([role("a", "victim", "female"), role("b", "culprit", "male")], ["b"])[0] is None               # the victim has no usable photograph: the rule names nobody, the caller falls back
    assert rule([role("a", "other", "female")], ["a"])[0] is None
    assert rule([role("a", "victim", "female")], ["a"])[1] == "the victim is a woman: her photograph is on every cover"


def test_the_roles_are_read_by_the_model_and_the_rule_picks_the_person(db_session, monkeypatch):
    case = Case(canonical_title="Subject test", slug=f"subject-{uuid.uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    asked = {}

    async def roles(self, *, case, people):
        asked.update(case=case, people=people)
        return [role("husband", "culprit", "male"), role("wife", "victim", "female")]

    monkeypatch.setattr(SA.CoverSubjectPicker, "run", roles)
    asset_row = VisualAsset(case_id=case.id, asset_code=f"VIS_{uuid.uuid4().hex[:6]}", asset_type="photo", description="A smiling woman", title="t")
    db_session.add(asset_row)
    db_session.commit()
    people = [{"entity_key": "husband", "name": "Caleb", "photos": [{"asset_id": asset_row.id}]}, {"entity_key": "wife", "name": "Ashley", "photos": [{"asset_id": asset_row.id}]}]
    key, why, found = asyncio.run(SU.choose(db_session, case, people))
    assert key == "wife" and "woman" in why and len(found) == 2 and asked["people"][1]["photo_descriptions"] == ["A smiling woman"]
    assert asyncio.run(SU.choose(db_session, case, []))[0] is None
    db_session.query(VisualAsset).filter_by(case_id=case.id).delete()
    db_session.delete(case)
    db_session.commit()


def test_the_owners_standing_instruction_clears_a_photograph_of_the_person_but_never_one_marked_unusable(db_session):
    from app.db.models import VisualAsset as Asset

    def row(status):
        return Asset(case_id=1, asset_code="x", asset_type="photo", rights_status=status)

    ok, why = V.cleared(row("editorial_review_required"))
    assert ok and "standing instruction" in why and "not copyright permission" in why
    assert V.cleared(row("owned"))[0] and V.cleared(row("permission_required"))[0] is False and V.cleared(row("do_not_use"))[0] is False


def test_the_standing_instruction_can_be_switched_off_in_the_config(monkeypatch):
    from app.db.models import VisualAsset as Asset

    monkeypatch.setitem(T.settings()["subject_photo"]["standing_approval"], "enabled", False)
    assert V.cleared(Asset(case_id=1, asset_code="x", asset_type="photo", rights_status="editorial_review_required"))[0] is False


@needs_assets
def test_a_photo_that_covers_the_title_the_banner_or_the_logo_is_reported(assets):
    made = make(assets, photo=assets / "photo.png")["reel"]
    lay = T.settings()["layout"]["reel"]
    assert not [p for p in made["problems"] if "photo" in p]
    on_title = {**made["elements"], "photo": [made["elements"]["title"][0] - 20, made["elements"]["title"][1] - 20, 300, 300]}
    assert "the photo covers the title" in T.problems_of(made["image"], on_title, (1080, 1920), lay)


def test_the_shapes_that_get_uploaded_are_approved_by_the_app_itself_when_they_have_no_problem_and_the_previews_are_not(review, monkeypatch):
    monkeypatch.setitem(T.settings()["approval"], "auto_approve_upload_shapes", True)
    candidates(monkeypatch, person(asset(review)))
    made = V.render(review.db, review.case, "en", "C1", shapes=["reel", "feed", "grid"], use_photo=False)
    status = {row.shape: (row.status, row.approved_by) for row in made}
    assert status["reel"] == ("approved", "automatic") and status["feed"][0] == "proposed" and status["grid"][0] == "proposed"
    monkeypatch.setitem(T.settings()["approval"], "auto_approve_upload_shapes", False)
    assert V.render(review.db, review.case, "en", "C1", shapes=["reel"], use_photo=False)[0].status == "proposed"


def test_a_photo_the_owner_chose_for_the_covers_is_cleared_unless_its_rights_forbid_it(review, monkeypatch):
    monkeypatch.setitem(T.settings()["subject_photo"]["standing_approval"], "enabled", False)
    chosen, plain, forbidden = asset(review), asset(review), asset(review, rights="do_not_use")
    chosen.cover_photo = forbidden.cover_photo = True
    review.db.commit()
    ok, why = V.cleared(chosen)
    assert ok and "not copyright permission" in why
    assert not V.cleared(plain)[0] and not V.cleared(forbidden)[0]


@needs_assets
def test_the_cover_uses_the_photo_the_owner_chose_even_when_another_one_is_larger(review, monkeypatch):
    monkeypatch.setattr(T, "problems_of", lambda *a, **k: [])
    large, chosen = asset(review, approved_by="owner"), asset(review, approved_by="owner")
    chosen.cover_photo, chosen.width, chosen.height = True, 400, 500
    review.db.commit()
    both = person(large)
    both["photos"].append({"asset_id": chosen.id, "authorized": True, "why": "owner"})
    candidates(monkeypatch, both)
    made = V.render(review.db, review.case, "en", "C1", shapes=["reel"])[0]
    assert made.photo_asset_id == chosen.id


def test_the_cover_subject_includes_a_person_photo_only_the_owner_chose(review):
    row = asset(review, rights="editorial_review_required")
    row.cover_photo, row.entity_key, row.asset_type = True, "navjeet_singh", "photo"
    review.db.commit()
    people = V.subject_candidates(review.db, review.case, "en")
    assert [p["entity_key"] for p in people] == ["navjeet_singh"] and people[0]["photos"][0]["asset_id"] == row.id and V.cleared(row)[0]


def test_an_outfit_without_a_front_picture_uses_its_standing_picture_for_the_cover(monkeypatch):
    class Pose:
        def __init__(self, path):
            self.path = path

    class Outfit:
        poses = {"stand": Pose("stand.png"), "sitHands": Pose("sit.png")}

    monkeypatch.setattr(V, "_film_outfit", lambda db, case_id, language: ("en/brownSweater", {"from": "test"}, None))
    monkeypatch.setattr(V, "OutfitRegistry", lambda: type("R", (), {"resolve": staticmethod(lambda outfit_id: Outfit())})())
    path, source, why = V.plate_for(None, 1, "en", "long")
    assert path == "stand.png" and source["pose"] == "stand" and why is None


def test_a_host_pushed_against_the_side_is_reported_and_one_inside_the_picture_is_not():
    size = (1000, 1000)
    host = {"face_width": 0.15, "face_y": 0.5}
    inside, outside = Image.new("L", size, 0), Image.new("L", size, 0)
    ImageDraw.Draw(inside).rectangle((300, 550, 700, 999), fill=255)
    ImageDraw.Draw(outside).rectangle((600, 550, 999, 999), fill=255)
    assert T.host_cut_by_edge(inside, host, size) is None
    assert T.host_cut_by_edge(outside, host, size) == "right shoulder"


def test_the_standard_layouts_keep_the_standing_host_inside_the_square_and_the_feed(assets):
    out = make(assets, subject="Name")
    assert not any("cut by the edge" in p for name in ("feed", "grid") for p in out[name]["problems"])
