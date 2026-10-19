"""Part 7b: the look library workflow, with a fake image model."""

import asyncio

import pytest

from app.db.models import HostLook
from app.documentary import looks as LK


class FakeImage:
    model = "fake-image"

    def __init__(self):
        self.prompts = []

    async def generate(self, prompt, reference_png, outfit_png):
        self.prompts.append(prompt)
        return b"\x89PNG\r\n\x1a\nfake" + str(len(self.prompts)).encode()


@pytest.fixture
def profile(monkeypatch):
    import uuid

    pid = f"STUDIO_{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(LK.ST, "channel_profile", lambda language: {"studio_profile_id": pid})
    return pid


def _new(db, profile, camera="CAM01_FRONT", framing="WIDE", outfit="navy_knit", pose="P01_NEUTRAL_SEATED"):
    return LK.request_look(db, "en", camera, framing, outfit, pose)


def test_a_look_is_requested_generated_approved_and_only_then_may_go_to_heygen(db_session, profile, tmp_path, monkeypatch):
    monkeypatch.setattr(LK.storage, "ROOT", tmp_path)
    look = _new(db_session, profile)
    assert look.status == "requested" and look.studio_profile_id == profile
    with pytest.raises(LK.LookError, match="approved"):
        LK.mark_uploaded(db_session, look, "hg1")
    provider = FakeImage()
    asyncio.run(LK.generate(db_session, look, provider, b"studio", b"outfit"))
    assert look.status == "generated" and len(look.image_sha256) == 64 and look.attempts == 1
    assert (tmp_path / look.image_path).exists()
    with pytest.raises(LK.LookError, match="approved"):
        LK.mark_uploaded(db_session, look, "hg1")                  # generated is not approved
    LK.approve(db_session, look, "owner")
    LK.mark_uploaded(db_session, look, "hg1")
    assert (look.status, look.heygen_look_id, look.approved_by) == ("approved", "hg1", "owner")


def test_reject_and_regenerate_with_a_prompt(db_session, profile, tmp_path, monkeypatch):
    monkeypatch.setattr(LK.storage, "ROOT", tmp_path)
    look = _new(db_session, profile)
    provider = FakeImage()
    asyncio.run(LK.generate(db_session, look, provider, b"s", b"o"))
    LK.reject(db_session, look, "the sofa moved")
    assert look.status == "rejected" and LK.approved_looks(db_session, profile) == set()
    LK.regenerate_with_prompt(db_session, look, "keep the sofa where it is")
    asyncio.run(LK.generate(db_session, look, provider, b"s", b"o"))
    assert provider.prompts[-1] == "keep the sofa where it is" and look.attempts == 2
    assert look.status == "generated" and look.rejected_reason is None


def test_only_approved_looks_count_and_front_or_wide_pictures_give_their_crops(db_session, profile, tmp_path, monkeypatch):
    monkeypatch.setattr(LK.storage, "ROOT", tmp_path)
    wide = _new(db_session, profile, "CAM01_FRONT", "WIDE")
    side = _new(db_session, profile, "CAM04_LEFT_SIDE", "MEDIUM")
    for look in (wide, side):
        asyncio.run(LK.generate(db_session, look, FakeImage(), b"s", b"o"))
    assert LK.approved_looks(db_session, profile) == set()          # nothing approved yet
    LK.approve(db_session, wide, "owner")
    assert LK.approved_looks(db_session, profile) == {
        ("CAM01_FRONT", "WIDE"), ("CAM01_FRONT", "MEDIUM"),
        ("CAM01_FRONT", "MEDIUM_CLOSE"), ("CAM01_FRONT", "CLOSE")}
    LK.approve(db_session, side, "owner")
    assert ("CAM04_LEFT_SIDE", "MEDIUM") in LK.approved_looks(db_session, profile)
    assert ("CAM04_LEFT_SIDE", "CLOSE") not in LK.approved_looks(db_session, profile)   # no crop of a side look


def test_unknown_camera_and_duplicate_requests_are_refused(db_session, profile):
    with pytest.raises(LK.LookError, match="unknown camera"):
        LK.request_look(db_session, "en", "CAM99", "WIDE", "navy_knit", "P01_NEUTRAL_SEATED")
    _new(db_session, profile)
    with pytest.raises(LK.LookError, match="already exists"):
        _new(db_session, profile)


def test_the_approval_gate_cannot_be_skipped_for_a_requested_or_rejected_look(db_session, profile):
    look = _new(db_session, profile)
    with pytest.raises(LK.LookError):
        LK.approve(db_session, look, "owner")
    with pytest.raises(LK.LookError):
        LK.reject(db_session, look, "no")


def test_looks_api_request_approve_and_generation_refuses_without_a_studio_master(client, db_session, profile, monkeypatch):
    monkeypatch.setattr(LK.ST, "load_registry", lambda: type("R", (), {"assets_of": lambda self, l: []})())
    made = client.post("/api/host-looks", json={
        "language": "en", "camera": "CAM01_FRONT", "framing": "WIDE", "outfit_id": "navy_knit",
        "pose": "P01_NEUTRAL_SEATED"}).json()
    assert made["status"] == "requested"
    gen = client.post(f"/api/host-looks/{made['id']}/generate")
    assert gen.status_code == 409 and "studio master" in gen.json()["detail"]
    assert client.post(f"/api/host-looks/{made['id']}/approve", json={}).status_code == 409
    assert any(x["id"] == made["id"] for x in client.get("/api/host-looks?language=en").json())


def _heygen(handler, monkeypatch):
    import httpx

    from app.providers.avatar.heygen import HeyGenAvatarProvider

    monkeypatch.setenv("TRUECRIME_HEYGEN_API_KEY", "k")
    return HeyGenAvatarProvider(key_env="TRUECRIME_HEYGEN_API_KEY",
                                transport=httpx.MockTransport(handler))


def test_heygen_create_look_adds_a_photo_look_to_the_group_and_reads_its_status(monkeypatch):
    import httpx
    import json

    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/v3/avatars":
            return httpx.Response(200, json={"data": {"avatar_item": {"id": "look9"}}})
        return httpx.Response(200, json={"data": {"status": "completed"}})

    prov = _heygen(handler, monkeypatch)
    assert asyncio.run(prov.create_look("grp1", "studio front", "asset7")) == "look9"
    body = json.loads(seen[0][2])
    assert body == {"type": "photo", "name": "studio front", "avatar_group_id": "grp1",
                    "file": {"type": "asset_id", "asset_id": "asset7"}}
    assert asyncio.run(prov.look_status("look9")) == ("completed", None)


def test_upload_to_heygen_needs_an_approved_look_with_its_picture_unchanged(db_session, profile, tmp_path, monkeypatch):
    monkeypatch.setattr(LK.storage, "ROOT", tmp_path)
    look = _new(db_session, profile)
    asyncio.run(LK.generate(db_session, look, FakeImage(), b"s", b"o"))

    class Prov:
        calls = []

        async def upload_asset(self, path, mime):
            self.calls.append("upload")
            return "a1"

        async def create_look(self, group, name, asset):
            self.calls.append("create")
            return "hl1"

        async def look_status(self, look_id):
            return "completed", None

    prov = Prov()
    with pytest.raises(LK.LookError, match="approved"):
        asyncio.run(LK.upload_to_heygen(db_session, look, prov, "grp"))
    assert prov.calls == []                              # nothing reached HeyGen
    LK.approve(db_session, look, "owner")
    (tmp_path / look.image_path).write_bytes(b"tampered")
    with pytest.raises(LK.LookError, match="changed on disk"):
        asyncio.run(LK.upload_to_heygen(db_session, look, prov, "grp"))
    (tmp_path / look.image_path).write_bytes(b"\x89PNG\r\n\x1a\nfake1")
    asyncio.run(LK.upload_to_heygen(db_session, look, prov, "grp"))
    assert look.heygen_look_id == "hl1" and prov.calls == ["upload", "create"]


def test_an_owner_picture_becomes_a_generated_look_that_still_needs_approval(
        db_session, profile, tmp_path, monkeypatch):
    monkeypatch.setattr(LK.storage, "ROOT", tmp_path)
    look = _new(db_session, profile)
    with pytest.raises(LK.LookError, match="PNG"):
        LK.attach_picture(db_session, look, b"not a png", "x.png")
    LK.attach_picture(db_session, look, b"\x89PNG\r\n\x1a\nowner", "en_sitting.png")
    assert look.status == "generated" and look.image_model == "owner_upload:en_sitting.png"
    assert (tmp_path / look.image_path).read_bytes().endswith(b"owner") and look.attempts == 1
    with pytest.raises(LK.LookError, match="approved"):
        LK.mark_uploaded(db_session, look, "hg1")
    LK.approve(db_session, look, "owner (chat instruction)")
    assert (look.approved_by, look.approved_at is not None) == ("owner (chat instruction)", True)


def test_owner_picture_api_outfit_and_look_upload(client, db_session, profile, tmp_path, monkeypatch):
    from test_host_outfits import _case

    monkeypatch.setattr(LK.storage, "ROOT", tmp_path)
    monkeypatch.setattr("app.documentary.outfits.storage.ROOT", tmp_path)
    case = _case(db_session)
    png = b"\x89PNG\r\n\x1a\nowner-photo"
    bad = client.post(f"/api/cases/{case.id}/outfits/upload",
                      data={"language": "en", "name": "x"}, files={"file": ("a.png", b"nope")})
    assert bad.status_code == 409
    made = client.post(f"/api/cases/{case.id}/outfits/upload", data={"language": "en", "name": "Brown sweater"},
                       files={"file": ("a.png", png, "image/png")})
    assert made.status_code == 200 and made.json()["name"] == "Brown sweater"
    look = _new(db_session, profile)
    got = client.post(f"/api/host-looks/{look.id}/picture", files={"file": ("a.png", png, "image/png")})
    assert got.status_code == 200 and got.json()["status"] == "generated"
    assert client.post(f"/api/host-looks/{look.id}/picture",
                       files={"file": ("a.png", b"nope")}).status_code == 409
