"""The host window: the owner picks an outfit of the library and one of its photos, confirms, and the choice is saved
and approved as the host's look; the host videos of the language can be made again with it."""
import asyncio
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
    body = OF.choice_options(db_session, case.id, "en")
    options = {o["ref"]: o for o in body["options"]}
    assert body["selected"] is None and options["en/blazerJeans"]["key"] == "registry:en/blazerJeans"
    assert {p["situation"] for p in options["en/blazerJeans"]["photos"]} == {"sitHands", "standPockets"}
    assert [p["situation"] for p in options["en/brownSweater"]["photos"]] == ["sitHands"]


def test_choosing_an_outfit_and_a_photo_saves_selects_and_approves_exactly_that_photo(db_session, library):
    case = _case(db_session)
    photo = library[("blazerJeans", "sitHands")]
    done = OF.apply_choice(db_session, case.id, "en", "registry:en/blazerJeans", [photo.id], "owner_host_window")
    outfit = done["outfit"]
    assert outfit.status == "selected" and outfit.outfit_ref == "en/blazerJeans"
    assert OF.selected_library_item_ids(db_session, outfit) == [photo.id]
    looks = db_session.query(HostLook).filter_by(host_outfit_id=outfit.id, status="approved").all()
    assert looks and {x.stance for x in looks} == {"SEATED"}                  # the photo is a seated one
    assert {x.approved_by for x in looks} == {"owner_host_window"}
    chosen = next(o for o in OF.choice_options(db_session, case.id, "en")["options"] if o["selected"])
    assert chosen["key"] == f"existing:{outfit.id}"
    assert [p["id"] for p in chosen["photos"] if p["selected"]] == [photo.id]


def test_a_second_choice_replaces_the_first_as_the_videos_outfit(db_session, library):
    case = _case(db_session)
    first = _language_outfit(db_session, case, library)
    second = _language_outfit(db_session, case, library, "brownSweater")
    assert db_session.get(HostOutfit, first.id).status == "draft"
    assert OF.selected_outfit(db_session, case.id, "en").id == second.id


def test_a_photo_of_another_outfit_or_none_is_refused_and_nothing_is_saved(db_session, library):
    case = _case(db_session)
    foreign = library[("brownSweater", "sitHands")]
    with pytest.raises(OF.OutfitError, match="do not belong"):
        OF.apply_choice(db_session, case.id, "en", "registry:en/blazerJeans", [foreign.id], "owner_host_window")
    with pytest.raises(OF.OutfitError):
        OF.apply_choice(db_session, case.id, "en", "registry:en/nope", [foreign.id], "owner_host_window")
    with pytest.raises(OF.OutfitError, match="at least one"):
        OF.apply_choice(db_session, case.id, "en", "registry:en/blazerJeans", [], "owner_host_window")
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
    with pytest.raises(OF.OutfitError, match="not a sitting or standing pose"):
        OF.apply_choice(db_session, case.id, "en", "registry:en/blazerJeans", [front.id], "owner_host_window")
    assert db_session.query(HostOutfit).filter_by(case_id=case.id).count() == 0


# -- one outfit for one scene ----------------------------------------------------------------------------------------


def _language_outfit(db, case, library, slug="blazerJeans", situation="sitHands"):
    return OF.apply_choice(db, case.id, "en", f"registry:en/{slug}", [library[(slug, situation)].id],
                           "owner_host_window")["outfit"]


def test_a_scene_wears_the_outfit_chosen_for_it_and_every_other_scene_keeps_the_videos_outfit(db_session, library):
    from app.documentary.host_scenes import look_for_avatar

    case = _case(db_session)
    video_outfit = _language_outfit(db_session, case, library)
    s1, s2 = _scene(db_session, case, "voice_ready", video=None), _scene(db_session, case, "voice_ready", video=None)
    out = client.post(f"/api/host-scenes/{s2.id}/outfit", json={
        "option_key": "registry:en/brownSweater", "item_id": library[("brownSweater", "sitHands")].id}).json()
    assert out["outfit"]["name"] == "brown Sweater" and out["started"] is False
    db_session.refresh(s1)
    db_session.refresh(s2)
    assert OF.scene_outfit(db_session, s1).id == video_outfit.id                    # untouched
    assert OF.scene_outfit(db_session, s2).outfit_ref == "en/brownSweater"
    assert OF.selected_outfit(db_session, case.id, "en").id == video_outfit.id      # the language's outfit did not move
    assert look_for_avatar(db_session, s1).host_outfit_id == video_outfit.id
    assert look_for_avatar(db_session, s2).host_outfit_id == s2.chosen_outfit_id


def test_each_of_four_scenes_can_have_its_own_outfit(db_session, library):
    case = _case(db_session)
    _language_outfit(db_session, case, library)
    scenes = [_scene(db_session, case, "voice_ready", video=None) for _ in range(4)]
    for scene, slug in zip(scenes[1:3], ("brownSweater", "blazerJeans"), strict=True):
        assert client.post(f"/api/host-scenes/{scene.id}/outfit", json={
            "option_key": f"registry:en/{slug}", "item_id": library[(slug, "sitHands")].id}).status_code == 200
    for scene in scenes:
        db_session.refresh(scene)
    refs = [OF.scene_outfit(db_session, s).outfit_ref for s in scenes]
    assert refs == ["en/blazerJeans", "en/brownSweater", "en/blazerJeans", "en/blazerJeans"]
    assert [s.chosen_outfit_id is not None for s in scenes] == [False, True, True, False]


def test_a_scene_whose_video_is_made_keeps_its_look_until_the_owner_has_it_made_again(db_session, library):
    case = _case(db_session)
    _language_outfit(db_session, case, library)
    made = _scene(db_session, case, "composited")
    made.look_id, made.direction_json = 7, "{}"
    db_session.commit()
    client.post(f"/api/host-scenes/{made.id}/outfit", json={
        "option_key": "registry:en/brownSweater", "item_id": library[("brownSweater", "sitHands")].id})
    db_session.refresh(made)
    assert made.chosen_outfit_id is not None and made.look_id == 7 and made.direction_json == "{}"
    assert made.avatar_video_path == "x.mp4"
    body = client.get(f"/api/host-scenes/{made.id}/outfit").json()
    assert body["outfit"]["pinned"] and body["video_made"] and body["outfit"]["name"] == "brown Sweater"
    assert next(o for o in body["options"] if o["selected"])["ref"] == "en/brownSweater"


def test_creating_the_host_of_one_scene_makes_only_that_video_again(db_session, library, monkeypatch):
    case = _case(db_session)
    _language_outfit(db_session, case, library)
    one, other = _scene(db_session, case, "composited"), _scene(db_session, case, "composited")
    started = []

    async def fake_run(scene_id, until):
        started.append((scene_id, until))

    monkeypatch.setattr(studio_api, "_run_task", fake_run)
    monkeypatch.setattr(studio_api, "_tasks", {})

    async def go():
        return await studio_api.choose_scene_outfit(one.id, studio_api.SceneOutfitChoice(
            option_key="registry:en/brownSweater", item_id=library[("brownSweater", "sitHands")].id,
            create_host=True), db_session)

    out = asyncio.run(go())
    assert out["started"] is True and list(studio_api._tasks) == [one.id]
    db_session.refresh(one)
    db_session.refresh(other)
    assert one.status == "voice_ready" and one.avatar_video_path is None and one.look_id is None
    assert other.status == "composited" and other.chosen_outfit_id is None            # the other video is not touched


def test_a_scene_can_go_back_to_the_videos_outfit(db_session, library):
    case = _case(db_session)
    video_outfit = _language_outfit(db_session, case, library)
    scene = _scene(db_session, case, "voice_ready", video=None)
    client.post(f"/api/host-scenes/{scene.id}/outfit", json={
        "option_key": "registry:en/brownSweater", "item_id": library[("brownSweater", "sitHands")].id})
    client.post(f"/api/host-scenes/{scene.id}/outfit", json={"option_key": None})
    db_session.refresh(scene)
    assert scene.chosen_outfit_id is None and OF.scene_outfit(db_session, scene).id == video_outfit.id


def test_a_retired_scene_outfit_falls_back_to_the_videos_outfit_and_a_photo_is_required(db_session, library):
    case = _case(db_session)
    video_outfit = _language_outfit(db_session, case, library)
    scene = _scene(db_session, case, "voice_ready", video=None)
    client.post(f"/api/host-scenes/{scene.id}/outfit", json={
        "option_key": "registry:en/brownSweater", "item_id": library[("brownSweater", "sitHands")].id})
    db_session.refresh(scene)
    OF.retire_outfit(db_session, db_session.get(HostOutfit, scene.chosen_outfit_id))
    assert OF.scene_outfit(db_session, scene).id == video_outfit.id
    missing = client.post(f"/api/host-scenes/{scene.id}/outfit", json={"option_key": "registry:en/brownSweater"})
    assert missing.status_code == 422
