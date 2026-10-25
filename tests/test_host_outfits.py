"""Outfits per case and language, their looks, and the mapping of the director's plan to one
approved look of the video's own outfit."""
import asyncio
import hashlib
import json

import pytest

from app.core.ai_config import ai_config
from app.db.models import HostLook
from app.documentary import look_selection as LS
from app.documentary import outfits as OF
from app.documentary import studio as ST
from test_studio import FakeAvatar, FakeVoice, _nosleep, _renderer, _segments


def _case(db):
    from test_blueprint_performance import _story

    return _story(db)[0]


def _outfit(db, case, language="en", ref="en/navySuit", name="Navy suit", select=True):
    outfit = OF.create_outfit(db, case.id, language, name, outfit_ref=ref)
    return OF.select_outfit(db, outfit) if select else outfit


def _look(db, outfit, camera="CAM01_FRONT", framing="MEDIUM", stance="SEATED", status="approved",
          heygen="hg"):
    look = OF.request_look(db, outfit, camera, framing, stance, "P01_NEUTRAL_SEATED")
    look.status = status
    look.heygen_look_id = heygen
    db.commit()
    return look


# ---------------------------------------------------------------------------
# the outfit of a video
# ---------------------------------------------------------------------------


def test_an_outfit_belongs_to_one_case_and_language_and_one_is_selected(db_session):
    case = _case(db_session)
    a = _outfit(db_session, case, ref="en/navySuit")
    b = _outfit(db_session, case, ref="en/blazerJeans", name="Grey", select=False)
    de = _outfit(db_session, case, "de", ref="de/navySuit")
    assert OF.selected_outfit(db_session, case.id, "en").id == a.id
    OF.select_outfit(db_session, b)
    db_session.refresh(a)
    assert (a.status, b.status) == ("draft", "selected")
    assert OF.selected_outfit(db_session, case.id, "en").id == b.id
    assert OF.selected_outfit(db_session, case.id, "de").id == de.id      # another video
    assert OF.selected_outfit(db_session, case.id, "fa") is None
    OF.retire_outfit(db_session, a)
    with pytest.raises(OF.OutfitError, match="retired"):
        OF.select_outfit(db_session, a)


def test_an_outfit_needs_a_known_language_a_name_and_a_picture_source(db_session):
    case = _case(db_session)
    with pytest.raises(OF.OutfitError, match="channel"):
        OF.create_outfit(db_session, case.id, "xx", "n", outfit_ref="en/navySuit")
    with pytest.raises(OF.OutfitError, match="name"):
        OF.create_outfit(db_session, case.id, "en", " ", outfit_ref="en/navySuit")
    with pytest.raises(OF.OutfitError, match="registry or a reference"):
        OF.create_outfit(db_session, case.id, "en", "n")
    with pytest.raises(OF.OutfitError, match="unknown outfit"):
        OF.create_outfit(db_session, case.id, "en", "n", outfit_ref="no_such_outfit")


def test_the_outfits_picture_follows_the_stance(db_session):
    case = _case(db_session)
    outfit = _outfit(db_session, case)
    seated = OF.reference_png(outfit, "SEATED")
    standing = OF.reference_png(outfit, "STANDING")
    assert seated[:8] == standing[:8] == b"\x89PNG\r\n\x1a\n" and seated != standing


# ---------------------------------------------------------------------------
# looks and the pose set
# ---------------------------------------------------------------------------


def test_every_studio_has_a_seat_and_free_floor_and_a_missing_one_blocks_the_request(
        db_session, monkeypatch):
    assert all(OF.studio_supports(lang, stance) for lang in ai_config.channels
               for stance in OF.STANCES)
    case = _case(db_session)
    outfit = _outfit(db_session, case)
    monkeypatch.setattr(OF, "studio_supports", lambda language, stance: stance == "SEATED")
    OF.request_look(db_session, outfit, "CAM01_FRONT", "WIDE", "SEATED", "P01_NEUTRAL_SEATED")
    with pytest.raises(OF.OutfitError, match="free floor for standing"):
        OF.request_look(db_session, outfit, "CAM01_FRONT", "WIDE", "STANDING", "P06_STANDING_FRONT")
    monkeypatch.setattr(OF, "studio_supports", lambda language, stance: stance == "STANDING")
    with pytest.raises(OF.OutfitError, match="seat"):
        OF.request_look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED", "P01_NEUTRAL_SEATED")


def test_the_pose_set_requests_the_standard_looks_in_one_click_and_only_once(db_session):
    case = _case(db_session)
    outfit = _outfit(db_session, case, "fa")
    done = OF.request_pose_set(db_session, outfit)
    labels = [e.label for e in ai_config.host_looks.pose_set]
    assert len(done["requested"]) == 5 == len(labels) and not done["skipped"]
    assert {(lk.camera, lk.framing, lk.stance) for lk in done["requested"]} == {
        (e.camera, e.framing, e.stance) for e in ai_config.host_looks.pose_set}
    assert {"SEATED", "STANDING"} == {lk.stance for lk in done["requested"]}
    assert all(lk.status == "requested" and lk.host_outfit_id == outfit.id and lk.language == "fa"
               and lk.studio_profile_id == ST.channel_profile("fa")["studio_profile_id"]
               for lk in done["requested"])
    again = OF.request_pose_set(db_session, outfit)
    assert again["requested"] == [] and len(again["already_there"]) == 5
    # a rejected look can be requested again
    done["requested"][0].status = "rejected"
    db_session.commit()
    assert len(OF.request_pose_set(db_session, outfit)["requested"]) == 1


def test_the_pose_set_skips_what_the_studio_cannot_hold_and_says_why(db_session, monkeypatch):
    case = _case(db_session)
    outfit = _outfit(db_session, case)
    monkeypatch.setattr(OF, "studio_supports", lambda language, stance: stance == "SEATED")
    done = OF.request_pose_set(db_session, outfit)
    assert len(done["requested"]) == 4
    assert done["skipped"] == [{"label": "standing, front, wide",
                                "why": "the en studio picture has no free floor for standing"}]


def test_existing_library_photos_can_be_reused_without_generation(db_session, monkeypatch, tmp_path):
    from app.db.models import OutfitItem
    from app.outfits import library as outfit_library

    case = _case(db_session)
    outfit = _outfit(db_session, case, "de", ref="de/brownSweater", name="brown Sweater")
    photo = tmp_path / "data" / "outfits" / "de" / "brownSweater" / "de_brownSweater_stand.png"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"\x89PNG\r\n\x1a\nexisting")
    monkeypatch.setattr(outfit_library, "ROOT", tmp_path)
    from app.documentary import looks as LK
    monkeypatch.setattr(LK.storage, "ROOT", tmp_path)
    db_session.add(OutfitItem(studio="de", outfit_slug="brownSweater", situation="stand",
                              file_path="data/outfits/de/brownSweater/de_brownSweater_stand.png",
                              sha256="x", status="available", stance="standing"))
    db_session.commit()

    OF.request_pose_set(db_session, outfit)
    done = OF.reuse_library_photos(db_session, outfit)
    assert len(done["attached"]) == 1
    assert len(done["skipped"]) == 4
    look = db_session.query(HostLook).filter_by(host_outfit_id=outfit.id, stance="STANDING").one()
    assert look.status == "generated" and look.image_model.startswith("owner_upload:library:")


def test_only_checked_library_photos_are_used_for_a_video(db_session, monkeypatch, tmp_path):
    from app.db.models import OutfitItem
    from app.outfits import library as outfit_library
    from app.documentary import looks as LK

    case = _case(db_session)
    outfit = _outfit(db_session, case, "de", ref="de/navySuit", name="Navy suit")
    root = tmp_path / "data" / "outfits" / "de" / "navySuit"
    root.mkdir(parents=True)
    monkeypatch.setattr(outfit_library, "ROOT", tmp_path)
    monkeypatch.setattr(LK.storage, "ROOT", tmp_path)
    rows = []
    for situation in ("stand", "standPockets"):
        path = root / f"de_navySuit_{situation}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + situation.encode())
        rows.append(OutfitItem(studio="de", outfit_slug="navySuit", situation=situation,
                               file_path=str(path.relative_to(tmp_path)), sha256=situation,
                               status="available", stance="standing"))
    db_session.add_all(rows)
    db_session.commit()

    OF.request_pose_set(db_session, outfit)
    done = OF.reuse_library_photos(db_session, outfit, selected_item_ids=[rows[1].id])
    assert done["selected_item_ids"] == [rows[1].id]
    assert {entry["item_id"] for entry in done["attached"]} == {rows[1].id}


def test_the_look_prompt_names_the_stance():
    from app.documentary import looks as LK

    standing = HostLook(camera="CAM01_FRONT", framing="WIDE", pose="P06_STANDING_FRONT",
                        stance="STANDING")
    seated = HostLook(camera="CAM01_FRONT", framing="WIDE", pose="P01_NEUTRAL_SEATED",
                      stance="SEATED")
    assert "host standing" in LK.default_prompt(standing)
    assert "host seated in the armchair" in LK.default_prompt(seated)


# ---------------------------------------------------------------------------
# from the director's plan to one approved look
# ---------------------------------------------------------------------------


def test_the_planned_look_is_used_when_it_is_approved(db_session):
    outfit = _outfit(db_session, _case(db_session))
    _look(db_session, outfit, "CAM01_FRONT", "WIDE", "SEATED", heygen="a")
    side = _look(db_session, outfit, "CAM04_LEFT_SIDE", "MEDIUM", "SEATED", heygen="b")
    got = LS.choose(db_session, outfit, LS.Wanted("SEATED", "CAM04_LEFT_SIDE", "MEDIUM"))
    assert got["look"].id == side.id and got["choice"]["exact"] is True
    assert got["choice"]["why"] == "the planned look" and got["choice"]["considered"]


def test_a_close_framing_is_a_crop_of_an_approved_front_or_wide_picture(db_session):
    outfit = _outfit(db_session, _case(db_session))
    wide = _look(db_session, outfit, "CAM01_FRONT", "WIDE", "SEATED")
    got = LS.choose(db_session, outfit, LS.Wanted("SEATED", "CAM01_FRONT", "CLOSE"))
    assert got["look"].id == wide.id and got["choice"]["cropped_from_framing"] == "WIDE"
    assert "crop" in got["choice"]["why"]


def test_without_the_exact_look_the_nearest_approved_one_is_used_and_the_reason_is_logged(db_session):
    outfit = _outfit(db_session, _case(db_session))
    front = _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED")
    _look(db_session, outfit, "CAM04_LEFT_SIDE", "MEDIUM", "SEATED", heygen="b")
    got = LS.choose(db_session, outfit, LS.Wanted("SEATED", "CAM02_LEFT_45", "MEDIUM"))
    assert got["look"].id == front.id and got["choice"]["exact"] is False
    assert "not approved" in got["choice"]["why"] and "nearest approved look" in got["choice"]["why"]
    # sitting or standing is the biggest difference: a seated look is not chosen for a standing plan
    # while the standing one exists
    stand = _look(db_session, outfit, "CAM01_FRONT", "WIDE", "STANDING", heygen="c")
    assert LS.choose(db_session, outfit,
                     LS.Wanted("STANDING", "CAM01_FRONT", "MEDIUM"))["look"].id == stand.id


def test_a_big_jump_from_the_previous_scene_needs_a_reason(db_session):
    outfit = _outfit(db_session, _case(db_session))
    seated = _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED")
    standing = _look(db_session, outfit, "CAM01_FRONT", "CLOSE", "STANDING", heygen="s")
    near = _look(db_session, outfit, "CAM02_LEFT_45", "MEDIUM", "SEATED", heygen="n")
    plan = LS.Wanted("STANDING", "CAM01_FRONT", "CLOSE")
    assert LS.distance(LS.of_look(seated), LS.of_look(standing)) > \
        ai_config.host_looks.continuity.max_change_per_cut
    kept = LS.choose(db_session, outfit, plan, previous=seated)
    assert kept["look"].id != standing.id and kept["choice"]["continuity"]
    assert kept["look"].id in (seated.id, near.id)
    allowed = LS.choose(db_session, outfit, plan, previous=seated,
                        change_reason="the opening ends and the story begins")
    assert allowed["look"].id == standing.id and allowed["choice"]["continuity"] is None
    small = LS.choose(db_session, outfit, LS.Wanted("SEATED", "CAM02_LEFT_45", "MEDIUM"),
                      previous=seated)
    assert small["look"].id == near.id


def test_the_continuity_limit_is_a_config_value(db_session, monkeypatch):
    outfit = _outfit(db_session, _case(db_session))
    seated = _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED")
    standing = _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "STANDING", heygen="s")
    plan = LS.Wanted("STANDING", "CAM01_FRONT", "MEDIUM")
    assert LS.choose(db_session, outfit, plan, previous=seated)["look"].id == standing.id   # 3 <= 4
    cont = ai_config.host_looks.continuity.model_copy(update={"max_change_per_cut": 1})
    monkeypatch.setattr(ai_config, "host_looks", ai_config.host_looks.model_copy(
        update={"continuity": cont}))
    assert LS.choose(db_session, outfit, plan, previous=seated)["look"].id == seated.id


def test_no_approved_look_stops_and_names_exactly_what_to_approve(db_session):
    outfit = _outfit(db_session, _case(db_session))
    with pytest.raises(LS.NoApprovedLook) as e:
        LS.choose(db_session, outfit, LS.Wanted("SEATED", "CAM01_FRONT", "MEDIUM"))
    assert "request front, wide, seated" in str(e.value) and len(e.value.to_approve) == 5
    one = OF.request_look(db_session, outfit, "CAM01_FRONT", "WIDE", "SEATED", "P01_NEUTRAL_SEATED")
    two = _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED", status="generated")
    msgs = LS.missing_looks(db_session, outfit)
    assert any(f"look #{one.id}" in m and "generate it, then approve it" in m for m in msgs)
    assert any(f"look #{two.id}" in m and m.endswith("approve it") for m in msgs)
    assert not any("front, medium, seated" in m and "request" in m for m in msgs)


def test_a_look_of_another_outfit_or_language_is_never_a_candidate(db_session):
    case = _case(db_session)
    mine = _outfit(db_session, case)
    other = _outfit(db_session, case, ref="en/blazerJeans", name="Grey", select=False)
    german = _outfit(db_session, case, "de")
    _look(db_session, other, "CAM04_LEFT_SIDE", "MEDIUM", "SEATED")
    _look(db_session, german, "CAM04_LEFT_SIDE", "MEDIUM", "SEATED")
    mine_front = _look(db_session, mine, "CAM01_FRONT", "MEDIUM", "SEATED")
    assert [lk.id for lk in LS.approved_looks(db_session, mine)] == [mine_front.id]
    got = LS.choose(db_session, mine, LS.Wanted("SEATED", "CAM04_LEFT_SIDE", "MEDIUM"))
    assert got["look"].id == mine_front.id
    # the language's studio profile counts too: a look filed under another studio is ignored
    mine_front.studio_profile_id = "STUDIO_DE"
    db_session.commit()
    with pytest.raises(LS.NoApprovedLook):
        LS.choose(db_session, mine, LS.Wanted("SEATED", "CAM01_FRONT", "MEDIUM"))


# ---------------------------------------------------------------------------
# the scene's avatar step
# ---------------------------------------------------------------------------


def _scene(db, tmp_path, monkeypatch, lang="en"):
    from app.documentary.host_scenes import plan_host_scenes

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    return plan_host_scenes(db, _segments(db, lang))[0]


def _run(db, scene, av, **kw):
    from app.documentary.host_scenes import run_scene

    return asyncio.run(run_scene(db, scene, "avatar", renderer=_renderer(FakeVoice()),
                                 avatar_provider=av, sleep=_nosleep, **kw))


def test_the_avatar_step_stops_before_any_upload_without_an_outfit_or_an_approved_look(
        db_session, tmp_path, monkeypatch):
    scene = _scene(db_session, tmp_path, monkeypatch)
    av = FakeAvatar()
    with pytest.raises(ST.StudioError, match="no outfit is selected"):
        _run(db_session, scene, av)
    assert scene.failed_step == "avatar_upload" and av.uploads == 0
    outfit = OF.create_outfit(db_session, scene.case_id, "en", "Navy", outfit_ref="en/navySuit")
    OF.select_outfit(db_session, outfit)
    with pytest.raises(LS.NoApprovedLook, match="request front, wide, seated"):
        _run(db_session, scene, av)
    assert av.uploads == 0 and av.requests == 0 and scene.status == "voice_ready"


def test_an_approved_library_look_is_uploaded_automatically_before_avatar_step(
        db_session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.documentary.looks.storage.ROOT", tmp_path)
    scene = _scene(db_session, tmp_path, monkeypatch)
    outfit = OF.select_outfit(db_session, OF.create_outfit(
        db_session, scene.case_id, "en", "Navy", outfit_ref="en/navySuit"))
    look = _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED", heygen=None)
    picture = b"\x89PNG\r\n\x1a\nselected-library-photo"
    picture_path = tmp_path / "selected.png"
    picture_path.write_bytes(picture)
    look.image_path = "selected.png"
    look.image_sha256 = hashlib.sha256(picture).hexdigest()
    db_session.commit()
    av = FakeAvatar()
    _run(db_session, scene, av)
    assert scene.status == "avatar_ready" and scene.avatar_id == "look-1"
    assert look.heygen_look_id == "look-1"
    assert av.uploads >= 2  # selected look picture and the scene audio
    assert scene.look_id == look.id and scene.host_outfit_id == outfit.id
    choice = json.loads(scene.look_choice_json)
    assert choice["look_id"] == look.id and choice["considered"] == [look.id]
    assert any(h["step"] == "look" and h["outcome"] == "chosen"
               for h in json.loads(scene.history_json))


def test_every_scene_of_a_video_gets_its_look_from_the_same_outfit(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import plan_host_scenes

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    row = _segments(db_session, "en")
    first, second = plan_host_scenes(db_session, row)
    outfit = OF.select_outfit(db_session, OF.create_outfit(
        db_session, first.case_id, "en", "Navy", outfit_ref="en/navySuit"))
    _look(db_session, outfit, "CAM01_FRONT", "WIDE", "SEATED")
    av = FakeAvatar()
    _run(db_session, first, av)
    _run(db_session, second, av)
    assert first.host_outfit_id == second.host_outfit_id == outfit.id
    # the mid scene is framed closer than the opening: a crop of the approved wide picture
    assert json.loads(second.look_choice_json)["cropped_from_framing"] == "WIDE"


def test_a_scene_that_is_already_made_does_not_need_its_look_again(db_session, tmp_path, monkeypatch):
    scene = _scene(db_session, tmp_path, monkeypatch)
    outfit = OF.select_outfit(db_session, OF.create_outfit(
        db_session, scene.case_id, "en", "Navy", outfit_ref="en/navySuit"))
    look = _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED")
    av = FakeAvatar()
    _run(db_session, scene, av)
    look.status = "rejected"                      # rejected later: the finished video stays valid
    db_session.commit()
    _run(db_session, scene, av)
    assert scene.status == "avatar_ready" and av.requests == 1


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_outfit_api_create_select_pose_set_and_list(client, db_session):
    case = _case(db_session)
    r = client.post(f"/api/cases/{case.id}/outfits", json={
        "language": "fa", "name": "Navy suit", "outfit_ref": "fa/navySuit"})
    assert r.status_code == 200, r.text
    outfit = r.json()
    assert outfit["status"] == "draft" and outfit["studio"] == {"SEATED": True, "STANDING": True}
    assert client.post(f"/api/cases/{case.id}/outfits", json={
        "language": "fa", "name": "x", "outfit_ref": "nope"}).status_code == 409
    assert client.post(f"/api/outfits/{outfit['id']}/select").json()["status"] == "selected"
    done = client.post(f"/api/outfits/{outfit['id']}/pose-set").json()
    assert len(done["requested"]) == 5 and len(done["outfit"]["looks"]) == 5
    assert len(done["outfit"]["to_approve"]) == 5
    listed = client.get(f"/api/cases/{case.id}/outfits?language=fa").json()
    assert [o["id"] for o in listed] == [outfit["id"]] and listed[0]["approved"] == 0
    assert client.get(f"/api/cases/{case.id}/outfits?language=de").json() == []
    one = client.post(f"/api/outfits/{outfit['id']}/looks", json={
        "camera": "CAM03_RIGHT_45", "framing": "MEDIUM", "stance": "SEATED"}).json()
    assert one["host_outfit_id"] == outfit["id"] and one["stance"] == "SEATED"
    assert client.post(f"/api/outfits/{outfit['id']}/looks", json={
        "camera": "CAM03_RIGHT_45", "framing": "MEDIUM", "stance": "SEATED"}).status_code == 409
    assert client.post(f"/api/outfits/{outfit['id']}/retire").json()["status"] == "retired"
    assert client.get(f"/api/cases/{case.id}/outfits?language=fa").json() == []


def test_outfit_registry_api_lists_the_outfits_a_video_can_use(client):
    body = client.get("/api/outfit-registry").json()
    assert len(body) == 12 and {"id", "label", "languages"} <= set(body[0])
    assert all(o["languages"] == [o["id"].split("/")[0]] for o in body)


def test_the_owner_can_override_a_scene_only_among_the_outfits_approved_looks(
        client, db_session, tmp_path, monkeypatch):
    scene = _scene(db_session, tmp_path, monkeypatch)
    outfit = OF.select_outfit(db_session, OF.create_outfit(
        db_session, scene.case_id, "en", "Navy", outfit_ref="en/navySuit"))
    ok = _look(db_session, outfit, "CAM02_LEFT_45", "MEDIUM", "SEATED", heygen="hg-45")
    pending = _look(db_session, outfit, "CAM01_FRONT", "WIDE", "SEATED", status="generated")
    other = OF.create_outfit(db_session, scene.case_id, "en", "Grey", outfit_ref="en/blazerJeans")
    foreign = _look(db_session, other, "CAM01_FRONT", "MEDIUM", "SEATED")
    for bad in (pending.id, foreign.id, 99999):
        assert client.post(f"/api/host-scenes/{scene.id}/look", json={"look_id": bad}).status_code == 409
    r = client.post(f"/api/host-scenes/{scene.id}/look", json={"look_id": ok.id})
    assert r.status_code == 200 and r.json()["look_id"] == ok.id
    assert r.json()["look_choice"]["override"] is True
    db_session.refresh(scene)
    assert scene.avatar_id == "hg-45" and any(
        h["outcome"] == "override" for h in json.loads(scene.history_json))
    scene.status = "avatar_ready"
    db_session.commit()
    assert client.post(f"/api/host-scenes/{scene.id}/look", json={"look_id": ok.id}).status_code == 409


def test_the_avatar_video_and_a_looks_picture_are_served(client, db_session, tmp_path, monkeypatch):
    scene = _scene(db_session, tmp_path, monkeypatch)
    assert client.get(f"/api/host-scenes/{scene.id}/video").status_code == 404
    video, png = tmp_path / "v.webm", tmp_path / "l.png"
    video.write_bytes(b"webm-bytes")
    monkeypatch.setattr("app.documentary.studio_api.storage.resolve",
                        lambda p: video if str(p).endswith("webm") else png)
    scene.avatar_video_path = "x/v.webm"
    db_session.commit()
    r = client.get(f"/api/host-scenes/{scene.id}/video")
    assert r.status_code == 200 and r.headers["content-type"] == "video/webm"
    assert r.content == b"webm-bytes"
    outfit = _outfit(db_session, _case(db_session))
    look = _look(db_session, outfit)
    assert client.get(f"/api/host-looks/{look.id}/image").status_code == 404
    png.write_bytes(b"\x89PNG\r\n\x1a\nx")
    look.image_path = "l.png"
    db_session.commit()
    assert client.get(f"/api/host-looks/{look.id}/image").content.startswith(b"\x89PNG")


def test_a_look_of_an_outfit_is_generated_from_the_picture_that_fits_its_stance(
        client, db_session, monkeypatch, tmp_path):
    from app.documentary import looks as LK

    monkeypatch.setattr(LK.storage, "ROOT", tmp_path)       # the picture goes to the test folder

    seen = {}

    class Image:
        model = "fake"

        def __init__(self, *a, **k):
            pass

        async def generate(self, prompt, reference_png, outfit_png):
            seen["prompt"], seen["outfit"] = prompt, outfit_png
            return b"\x89PNG\r\n\x1a\nnew"

    monkeypatch.setattr(LK, "ApimasterImageProvider", Image)
    case = _case(db_session)
    outfit = _outfit(db_session, case)
    standing = OF.request_look(db_session, outfit, "CAM01_FRONT", "WIDE", "STANDING",
                               "P06_STANDING_FRONT")
    r = client.post(f"/api/host-looks/{standing.id}/generate")
    assert r.status_code == 200, r.text
    assert seen["outfit"] == OF.reference_png(outfit, "STANDING") != OF.reference_png(outfit, "SEATED")
    assert "host standing" in seen["prompt"] and r.json()["status"] == "generated"


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------


def test_the_outfit_migration_keeps_the_looks_that_were_there(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, text

    from app.db.migrate import alembic_config

    url = f"sqlite:///{tmp_path / 'm.db'}"
    command.upgrade(alembic_config(url), "0043")
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text(
            "insert into host_looks (studio_profile_id, language, camera, framing, outfit_id, pose,"
            " status, attempts, created_at) values ('STUDIO_EN','en','CAM01_FRONT','WIDE','navy_suit',"
            "'P01_NEUTRAL_SEATED','approved',1,'2026-10-19')"))
    command.upgrade(alembic_config(url), "0044")
    with engine.begin() as c:
        row = c.execute(text("select outfit_id, host_outfit_id, stance, status from host_looks")).one()
        assert tuple(row) == ("navy_suit", None, "SEATED", "approved")
        c.execute(text("insert into host_outfits (case_id, language, name, status, created_at) "
                       "values (1, 'en', 'x', 'draft', '2026-10-19')"))
        scene_cols = {r[1] for r in c.execute(text("pragma table_info(host_scenes)"))}
    assert {"host_outfit_id", "look_id", "look_choice_json"} <= scene_cols
    command.downgrade(alembic_config(url), "0043")
    with engine.begin() as c:
        assert c.execute(text("select count(*) from host_looks")).scalar() == 1
        tables = {r[0] for r in c.execute(text("select name from sqlite_master where type='table'"))}
    assert "host_outfits" not in tables
    engine.dispose()
