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
