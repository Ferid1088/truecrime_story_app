"""The host window: the owner picks an outfit of the library and one of its photos, confirms, and the choice is saved
and approved as the host's look; the host videos of the language can be made again with it."""
import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.models import Case, HostLook, HostOutfit, HostScene, OutfitItem, StoryVersion
from app.documentary import looks as LK
from app.documentary import outfits as OF
from app.documentary import studio_api
from app.main import app
from app.outfits import library as outfit_library

client = TestClient(app)


def _case(db):
    case = Case(canonical_title="Host window", slug=f"hw-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    return case


@pytest.fixture
def library(db_session, monkeypatch, tmp_path):
    """Photos of two library outfits of the English host (a seated and a standing one for the first)."""
    monkeypatch.setattr(outfit_library, "ROOT", tmp_path)
    monkeypatch.setattr(LK.storage, "ROOT", tmp_path)
    rows = {}
    for slug, situations in (("blazerJeans", ("sitHands", "standPockets")), ("brownSweater", ("sitHands",))):
        folder = tmp_path / "data" / "outfits" / "en" / slug
        folder.mkdir(parents=True)
        for situation in situations:
            path = folder / f"en_{slug}_{situation}.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + f"{slug}{situation}".encode())
            row = (db_session.query(OutfitItem).filter_by(studio="en", outfit_slug=slug, situation=situation).first()
                   or OutfitItem(studio="en", outfit_slug=slug, situation=situation))
            row.status, row.sha256 = "available", f"{slug}{situation}"
            row.file_path = str(path.relative_to(tmp_path))
            row.stance = "sitting" if situation.startswith("sit") else "standing"
            db_session.add(row)
            db_session.commit()
            rows[(slug, situation)] = row
    return rows


def test_the_options_list_each_library_outfit_with_its_photos_and_mark_the_chosen_one(db_session, library):
    case = _case(db_session)
    body = client.get(f"/api/cases/{case.id}/host-outfit", params={"language": "en"}).json()
    options = {o["ref"]: o for o in body["options"]}
    assert body["selected"] is None and options["en/blazerJeans"]["key"] == "registry:en/blazerJeans"
    assert {p["situation"] for p in options["en/blazerJeans"]["photos"]} == {"sitHands", "standPockets"}
    assert [p["situation"] for p in options["en/brownSweater"]["photos"]] == ["sitHands"]
    assert body["scenes"] == [] and body["avatar_enabled"] in (True, False)


def test_choosing_an_outfit_and_a_photo_saves_selects_and_approves_exactly_that_photo(db_session, library):
    case = _case(db_session)
    photo = library[("blazerJeans", "sitHands")]
    out = client.post(f"/api/cases/{case.id}/host-outfit", json={
        "language": "en", "option_key": "registry:en/blazerJeans", "item_id": photo.id}).json()
    assert out["outfit"]["status"] == "selected" and out["outfit"]["outfit_ref"] == "en/blazerJeans"
    assert out["started"] == [] and out["outfit"]["library_item_ids"] == [photo.id]
    looks = db_session.query(HostLook).filter_by(host_outfit_id=out["outfit"]["id"], status="approved").all()
    assert looks and {x.stance for x in looks} == {"SEATED"}                  # the photo is a seated one
    assert {x.approved_by for x in looks} == {"owner_host_window"}
    body = client.get(f"/api/cases/{case.id}/host-outfit", params={"language": "en"}).json()
    chosen = next(o for o in body["options"] if o["selected"])
    assert chosen["key"] == f"existing:{out['outfit']['id']}"
    assert [p["id"] for p in chosen["photos"] if p["selected"]] == [photo.id]


def test_a_second_choice_replaces_the_first_as_the_host_outfit(db_session, library):
    case = _case(db_session)
    first = client.post(f"/api/cases/{case.id}/host-outfit", json={
        "language": "en", "option_key": "registry:en/blazerJeans", "item_id": library[("blazerJeans", "sitHands")].id}).json()
    second = client.post(f"/api/cases/{case.id}/host-outfit", json={
        "language": "en", "option_key": "registry:en/brownSweater", "item_id": library[("brownSweater", "sitHands")].id}).json()
    assert db_session.get(HostOutfit, first["outfit"]["id"]).status == "draft"
    assert OF.selected_outfit(db_session, case.id, "en").id == second["outfit"]["id"]


def test_a_photo_of_another_outfit_or_none_is_refused_and_nothing_is_saved(db_session, library):
    case = _case(db_session)
    foreign = library[("brownSweater", "sitHands")]
    bad = client.post(f"/api/cases/{case.id}/host-outfit", json={
        "language": "en", "option_key": "registry:en/blazerJeans", "item_id": foreign.id})
    assert bad.status_code == 409 and "do not belong" in bad.json()["detail"]
    nothing = client.post(f"/api/cases/{case.id}/host-outfit", json={
        "language": "en", "option_key": "registry:en/nope", "item_id": foreign.id})
    assert nothing.status_code == 409
    assert OF.selected_outfit(db_session, case.id, "en") is None
    assert db_session.query(HostOutfit).filter_by(case_id=case.id).count() == 0


def _scene(db, case, status, voice="x.mp3", video="x.mp4"):
    version = StoryVersion(case_id=case.id, version=1, kind="spoken", language="en", narrative_angle="t",
                           story_text="x", narrative_structure="{}")
    db.add(version)
    db.commit()
    scene = HostScene(case_id=case.id, story_version_id=version.id, host_segments_id=-1, host_segment_id="S1",
                      language="en", channel="c", position="opening", text="t", text_sha256="h",
                      studio_profile_id="p", studio_asset_id="a", framing_preset="HOST_MEDIUM",
                      background_mode="x", status=status, voice_path=voice, avatar_video_path=video)
    db.add(scene)
    db.commit()
    return scene


def test_creating_the_host_makes_the_videos_again_with_the_new_outfit_and_starts_them(db_session, library, monkeypatch):
    case = _case(db_session)
    made, ran = _scene(db_session, case, "composited"), _scene(db_session, case, "voice_ready", video=None)
    started = []

    async def fake_run(scene_id, until):
        started.append((scene_id, until))

    monkeypatch.setattr(studio_api, "_run_task", fake_run)
    monkeypatch.setattr(studio_api, "_tasks", {})

    async def go():
        return await studio_api.choose_host_outfit(
            case.id, studio_api.HostOutfitChoice(language="en", option_key="registry:en/blazerJeans",
                                                 item_id=library[("blazerJeans", "sitHands")].id, create_host=True),
            db_session)

    out = asyncio.run(go())
    assert sorted(out["started"]) == sorted([made.id, ran.id])
    db_session.refresh(made)
    assert made.status == "voice_ready" and made.avatar_video_path is None            # redone: a new direction and video
    assert json.loads(made.history_json)[-1]["step"] == "redo"


def test_no_host_video_is_started_without_the_owners_request(db_session, library, monkeypatch):
    case = _case(db_session)
    _scene(db_session, case, "composited")
    monkeypatch.setattr(studio_api, "_tasks", {})
    out = client.post(f"/api/cases/{case.id}/host-outfit", json={
        "language": "en", "option_key": "registry:en/blazerJeans",
        "item_id": library[("blazerJeans", "sitHands")].id}).json()
    assert out["started"] == [] and not studio_api._tasks


def test_duplicate_draft_outfits_of_one_library_outfit_are_offered_once_and_the_selected_one_wins(db_session, library):
    case = _case(db_session)
    OF.create_outfit(db_session, case.id, "en", "brown Sweater", outfit_ref="en/brownSweater")
    newest = OF.create_outfit(db_session, case.id, "en", "brown Sweater", outfit_ref="en/brownSweater")
    chosen = OF.select_outfit(db_session, OF.create_outfit(db_session, case.id, "en", "blazer Jeans",
                                                           outfit_ref="en/blazerJeans"))
    OF.create_outfit(db_session, case.id, "en", "blazer Jeans", outfit_ref="en/blazerJeans")
    options = {o["ref"]: o for o in OF.choice_options(db_session, case.id, "en")["options"]}
    assert options["en/brownSweater"]["key"] == f"existing:{newest.id}"
    assert options["en/blazerJeans"]["key"] == f"existing:{chosen.id}" and options["en/blazerJeans"]["selected"]
    assert sum(1 for o in OF.choice_options(db_session, case.id, "en")["options"] if o["ref"] == "en/brownSweater") == 1


def test_a_photo_whose_pose_is_not_sitting_or_standing_is_refused_and_nothing_is_saved(db_session, library):
    case = _case(db_session)
    front = library[("blazerJeans", "sitHands")]
    front.stance = "unknown"
    db_session.commit()
    bad = client.post(f"/api/cases/{case.id}/host-outfit", json={
        "language": "en", "option_key": "registry:en/blazerJeans", "item_id": front.id})
    assert bad.status_code == 409 and "not a sitting or standing pose" in bad.json()["detail"]
    assert db_session.query(HostOutfit).filter_by(case_id=case.id).count() == 0
