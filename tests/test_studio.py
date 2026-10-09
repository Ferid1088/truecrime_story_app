"""Channel studios (registry, mapping, validation) and host scenes
(text → voice → avatar, each step saved and resumable)."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.core.ai_config import AIConfig, ai_config
from app.documentary import studio as ST

CHANNELS = {"en": "ClueVera", "de": "Fallspur", "ar": "أثر خفي", "fa": "رد خاموش"}
REG = ST.load_registry()


def _images_present() -> bool:
    return all(ST.asset_path(REG, a).exists() for a in REG.assets)


# ---------------------------------------------------------------------------
# 1–4: each language resolves to its own channel's studio
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "de", "ar", "fa"])
def test_language_resolves_to_its_channel_studio(lang):
    r = ST.resolve(lang)
    assert r["channel_name"] == CHANNELS[lang]
    assert r["studio_profile_id"] == f"STUDIO_{lang.upper()}"
    assert r["primary"].language == lang and r["primary"].id.startswith(f"STUDIO_{lang.upper()}_")
    assert all(a.language == lang for _, a in r["presets"].values())
    assert set(r["presets"]) == set(ST.PRESETS)


# ---------------------------------------------------------------------------
# 5–8: primary, safe zones, presets
# ---------------------------------------------------------------------------


def test_primary_background_exists_and_is_host_ready():
    for lang in CHANNELS:
        prof = REG.profile_for(lang)
        a = REG.asset(prof.primary_background)
        assert a is not None and a.approved_for_host and a.safe_zones.host
        if _images_present():
            assert ST.asset_path(REG, a).exists()
            facts = ST.file_facts(ST.asset_path(REG, a))
            assert facts["sha256"] == a.sha256 and facts["width"] == a.width


def test_exactly_one_primary_per_channel_and_unique_ids():
    ids = [a.id for a in REG.assets]
    assert len(ids) == len(set(ids))
    for lang in CHANNELS:
        prof = REG.profile_for(lang)
        assert sum(1 for a in REG.assets_of(lang) if a.id == prof.primary_background) == 1
        assert [p.language for p in REG.profiles.values()].count(lang) == 1


def test_safe_zones_are_normalized_and_consistent():
    for a in REG.assets:
        for z in (a.safe_zones.host, a.safe_zones.head, a.safe_zones.logo,
                  a.safe_zones.lower_third):
            if z is None:
                continue
            assert 0 <= z.x <= 1 and 0 <= z.y <= 1 and z.x + z.width <= 1.0001
            assert z.y + z.height <= 1.0001
        if a.safe_zones.head:
            assert a.safe_zones.host.contains(a.safe_zones.head)
            assert not a.safe_zones.head.overlaps(a.safe_zones.logo)  # the logo stays visible
    with pytest.raises(ValueError):
        ST.Zone(x=0.8, y=0.1, width=0.3, height=0.2)   # leaves the image
    with pytest.raises(ValueError):
        ST.Zone(x=-0.1, y=0.1, width=0.3, height=0.2)


def test_framing_presets_reference_valid_approved_assets_of_the_channel():
    for lang in CHANNELS:
        for name, pr in REG.profile_for(lang).presets.items():
            a = REG.asset(pr.asset_id)
            assert a is not None and a.language == lang and a.approved_for_host, name
            up = ST.background_upscale(a, pr)
            assert up <= ai_config.studio.max_background_upscale, (lang, name, up)


def test_the_real_registry_validates_with_warnings_only():
    checks = ST.validate_all(REG, check_files=_images_present())
    for lang in CHANNELS:
        assert checks[lang]["ok"], checks[lang]
        assert any("not yet confirmed" in w for w in checks[lang]["warnings"]) or \
            REG.profile_for(lang).review.get("confirmed")


# ---------------------------------------------------------------------------
# 9–10: missing studio, wrong language
# ---------------------------------------------------------------------------


def _mini(tmp_path, lang="en", **asset_kw) -> ST.StudioRegistry:
    from PIL import Image

    (tmp_path / lang).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), (40, 30, 20)).save(tmp_path / lang / "front.png")
    zones = ST.SafeZones(host=ST.Zone(x=.3, y=.2, width=.4, height=.8),
                         head=ST.Zone(x=.42, y=.22, width=.16, height=.2))
    a = ST.StudioAsset(id=f"STUDIO_{lang.upper()}_FRONT", language=lang, file="front.png",
                       shot="front_medium", camera_angle="front_medium", shot_size="medium",
                       width=160, height=90, approved_for_host=True, approved_for_avatar=True,
                       safe_zones=zones, **asset_kw)
    pr = ST.FramingPreset(asset_id=a.id, host_height_ratio=.7, host_center_x=.5, host_bottom=1)
    prof = ST.StudioProfile(id=f"STUDIO_{lang.upper()}", language=lang, primary_background=a.id,
                            presets={"HOST_MEDIUM": pr})
    return ST.StudioRegistry(root=str(tmp_path), assets=[a], profiles={lang: prof})


def test_missing_studio_reports_errors_and_warnings(tmp_path):
    reg = _mini(tmp_path)
    v = ST.validate_language("en", reg)
    assert v["ok"]
    assert "no HOST_CLOSE preset" in v["warnings"] and "no HOST_WIDE preset" in v["warnings"]
    assert "no approved wide shot" in v["warnings"]
    (tmp_path / "en" / "front.png").unlink()
    v = ST.validate_language("en", reg)
    assert not v["ok"] and any("file en/front.png is missing" in e for e in v["errors"])
    reg2 = _mini(tmp_path)
    reg2.assets[0].approved_for_host = False
    v = ST.validate_language("en", reg2)
    assert "no usable host background" in v["errors"]
    empty = ST.StudioRegistry(root=str(tmp_path))
    v = ST.validate_language("de", empty)
    assert not v["ok"] and "no studio assets" in v["errors"]
    with pytest.raises(ST.StudioError):
        ST.resolve("de", empty)


def test_a_channel_never_resolves_another_channels_studio(tmp_path):
    reg = _mini(tmp_path, "en")
    de = _mini(tmp_path, "de")
    reg.assets += de.assets
    reg.profiles["de"] = de.profiles["de"]
    # the English profile pointed at the German picture
    reg.profiles["en"].presets["HOST_MEDIUM"].asset_id = "STUDIO_DE_FRONT"
    with pytest.raises(ST.StudioError, match="belongs to de"):
        ST.resolve("en", reg)
    assert any("not a en asset" in e for e in ST.validate_language("en", reg)["errors"])
    # and the settings endpoint refuses it
    with pytest.raises(ST.StudioError):
        ST.update_profile("en", primary_background="STUDIO_DE_02_FRONT_MEDIUM")


# ---------------------------------------------------------------------------
# 11–12: mapping in one place, old settings
# ---------------------------------------------------------------------------


def test_voice_and_channel_mapping_stay_consistent():
    expected = {"de": "02KhC7wycOLwuF6sc5Qu", "ar": "EFlRMcr2Nd9ah6iW85Z4",
                "en": "UF84IGrTBtegPkgbbrS2", "fa": "I3gMKh0nwZ8NQXKqUg6F"}
    for lang, voice in expected.items():
        cp = ST.channel_profile(lang)
        assert cp["elevenlabs_voice_id"] == voice == ai_config.voice.languages[lang].voice_id
        assert cp["channel_name"] == CHANNELS[lang]
        assert cp["studio_profile_id"] == f"STUDIO_{lang.upper()}"
        assert cp["heygen_avatar_env"] == ai_config.avatar.avatar_id_env
        assert cp["heygen_key_env"] == ai_config.avatar.secret_env
    with pytest.raises(ST.StudioError):
        ST.channel_profile("xx")


def test_historical_settings_still_load():
    raw = json.loads(Path("config/ai_config.json").read_text(encoding="utf-8"))
    # the first channel config (a design board image) and no studio section
    raw["channels"] = {"en": {"name": "ClueVera", "studio_design": "data/Studio/ClueVera.png"},
                       "de": {"name": "Fallspur", "studio_dir": "data/studio/de"}}
    raw.pop("studio", None)
    for k in ("enabled", "base_url", "output_format", "resolution"):
        raw["avatar"].pop(k, None)
    cfg = AIConfig.model_validate(raw)
    assert cfg.channels["en"].profile_id("en") == "STUDIO_EN"
    assert cfg.avatar.enabled is False and cfg.studio.registry_path.endswith(".json")
    # and a missing registry is an empty one, reported — not a crash
    assert ST.load_registry(Path("/nonexistent/registry.json")).assets == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_studios_api_shows_channels_without_paths(client):
    d = client.get("/api/studios").json()
    langs = {c["language"]: c for c in d["channels"]}
    assert {l: c["channel_name"] for l, c in langs.items()} == CHANNELS
    body = json.dumps(d)
    assert "data/studio" not in body and "/Users/" not in body
    en = langs["en"]
    assert en["profile"]["primary_background"] == "STUDIO_EN_02_FRONT_MEDIUM"
    assert len(en["assets"]) == 7 and en["validation"]["ok"]
    assert {a["shot"] for a in en["assets"]} >= {"front_medium", "overview_wide", "floor_plan"}
    r = client.patch("/api/studios/en", json={"primary_background": "STUDIO_DE_02_FRONT_MEDIUM"})
    assert r.status_code == 422
    if _images_present():
        img = client.get("/api/studios/assets/STUDIO_EN_02_FRONT_MEDIUM/image")
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert client.get("/api/studios/assets/NOPE/image").status_code == 404


def test_studio_profile_edit_persists_and_is_validated(client, tmp_path, monkeypatch):
    import shutil

    reg_copy = tmp_path / "studio_registry.json"
    shutil.copy(ST.registry_path(), reg_copy)
    monkeypatch.setattr(ai_config.studio, "registry_path", str(reg_copy))
    r = client.patch("/api/studios/de", json={"confirm": True,
                                              "presets": {"HOST_WIDE": "STUDIO_DE_02_FRONT_MEDIUM"}})
    assert r.status_code == 200, r.text
    reg = ST.load_registry(reg_copy)
    assert reg.profiles["de"].review["confirmed"] is True
    assert reg.profiles["de"].presets["HOST_WIDE"].asset_id == "STUDIO_DE_02_FRONT_MEDIUM"
    # a picture not approved for the host is refused
    r = client.patch("/api/studios/de", json={"presets": {"HOST_MEDIUM": "STUDIO_DE_07_FLOOR_PLAN"}})
    assert r.status_code == 422 and "not approved" in r.json()["detail"]
    assert ST.load_registry(reg_copy).profiles["de"].presets["HOST_MEDIUM"].asset_id == \
        "STUDIO_DE_02_FRONT_MEDIUM"


# ---------------------------------------------------------------------------
# host scenes: every step saved, retried from where it stopped
# ---------------------------------------------------------------------------


def _segments(db, lang="de"):
    from app.db.models import Case, HostSegments, StoryVersion
    from app.utils import slugify
    import uuid

    t = f"Scene {uuid.uuid4().hex[:6]}"
    case = Case(canonical_title=t, slug=slugify(t), language=lang)
    db.add(case)
    db.commit()
    v = StoryVersion(case_id=case.id, version=1, kind="spoken", language=lang,
                     narrative_angle="test", story_text="x", narrative_structure="{}")
    db.add(v)
    db.commit()
    segs = [{"segment_id": "S1", "position": "opening", "beat_id": None,
             "avatar_dialogue": "Guten Abend. Heute geht es um eine Tür.", "estimated_seconds": 4.0},
            {"segment_id": "S2", "position": "mid", "beat_id": "B03",
             "avatar_dialogue": "Und hier wird es seltsam.", "estimated_seconds": 2.5},
            {"segment_id": "S3", "position": "final", "beat_id": None, "avatar_dialogue": ""}]
    row = HostSegments(case_id=case.id, story_version_id=v.id, host_plan_id=0, language=lang,
                       segments_json=json.dumps(segs, ensure_ascii=False), status="valid")
    db.add(row)
    db.commit()
    return row


class FakeVoice:
    def __init__(self):
        self.calls = 0

    async def synthesize(self, req):
        from app.providers.voice.base import VoiceRenderResult

        self.calls += 1
        n = len(req.text)
        return VoiceRenderResult(audio=b"ID3" + req.text.encode("utf-8"), audio_format="mp3",
                                 characters=list(req.text), char_starts=[i * .05 for i in range(n)],
                                 char_ends=[(i + 1) * .05 for i in range(n)], provider="fake",
                                 model_id=req.model_id, voice_id=req.voice_id, request_id="req-1")


class FakeAvatar:
    def __init__(self, fail_request=0, job_states=None):
        self.uploads, self.requests, self.keys, self.polls = 0, 0, [], 0
        self.fail_request = fail_request
        self.job_states = list(job_states or ["processing", "completed"])

    async def resolve_look(self, avatar_id):
        return "look-1"

    async def upload_asset(self, path, mime):
        self.uploads += 1
        return f"asset-{self.uploads}"

    async def create_video(self, look_id, audio_asset_id, idempotency_key, output_format,
                           resolution, background_asset_id=None, title=None):
        from app.providers.avatar.base import AvatarJob, AvatarProviderError

        self.requests += 1
        self.keys.append(idempotency_key)
        if self.fail_request:
            self.fail_request -= 1
            raise AvatarProviderError("unreachable", "network down")
        return AvatarJob(job_id=f"job-{self.requests}", status="waiting")

    async def get_video(self, job_id):
        from app.providers.avatar.base import AvatarJob

        self.polls += 1
        st = self.job_states.pop(0) if self.job_states else "completed"
        return AvatarJob(job_id=job_id, status=st, video_url="https://x/v.webm" if st == "completed"
                         else None, failure_message="no matting" if st == "failed" else None,
                         duration=4.0)

    async def download(self, url, dest):
        dest.write_bytes(b"webm" + url.encode())
        return 10


async def _nosleep(_):
    return None


def test_host_scenes_are_planned_with_the_channel_studio(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import plan_host_scenes

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    row = _segments(db_session, "de")
    scenes = plan_host_scenes(db_session, row)
    assert [s.host_segment_id for s in scenes] == ["S1", "S2"]   # no text → no scene
    s1, s2 = scenes
    assert s1.channel == "Fallspur" and s1.studio_profile_id == "STUDIO_DE"
    assert s1.framing_preset == "HOST_MEDIUM" and s1.studio_asset_id == "STUDIO_DE_02_FRONT_MEDIUM"
    assert s2.framing_preset == "HOST_CLOSE"
    assert s1.voice_id == "02KhC7wycOLwuF6sc5Qu" and len(s1.text_sha256) == 64
    assert s1.status == "planned" and json.loads(s1.history_json)[0]["step"] == "plan"
    again = plan_host_scenes(db_session, row)
    assert [s.id for s in again] == [s1.id, s2.id]   # idempotent, text stays frozen


def test_voice_is_saved_before_the_avatar_and_never_made_twice(db_session, tmp_path, monkeypatch):
    from app.documentary import storage
    from app.documentary.host_scenes import plan_host_scenes, run_scene

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setenv("TrueCrime_Avatar_ID_Heygen", "group-1")
    scene = plan_host_scenes(db_session, _segments(db_session, "de"))[0]
    voice = FakeVoice()
    asyncio.run(run_scene(db_session, scene, "voice", voice_provider=voice))
    assert scene.status == "voice_ready" and voice.calls == 1
    p = storage.resolve(scene.voice_path)
    side = json.loads(p.with_name(p.name + ".json").read_text(encoding="utf-8"))
    assert side["text"] == scene.text and side["text_sha256"] == scene.text_sha256
    assert side["voice_id"] == "02KhC7wycOLwuF6sc5Qu" and side["request_id"] == "req-1"
    # the avatar provider is down for the first request
    av = FakeAvatar(fail_request=1)
    with pytest.raises(Exception, match="network down"):
        asyncio.run(run_scene(db_session, scene, "avatar", voice_provider=voice,
                              avatar_provider=av, sleep=_nosleep))
    assert scene.status == "avatar_uploaded" and scene.failed_step == "avatar_request"
    assert voice.calls == 1 and av.uploads == 1
    # retry: voice and upload are reused, the request carries the same key
    asyncio.run(run_scene(db_session, scene, "avatar", voice_provider=voice, avatar_provider=av,
                          sleep=_nosleep))
    assert scene.status == "avatar_ready" and scene.failed_step is None
    assert voice.calls == 1 and av.uploads == 1 and av.requests == 2
    assert av.keys[0] == av.keys[1]                      # idempotent retry
    assert storage.resolve(scene.avatar_video_path).exists() and scene.avatar_id == "look-1"
    hist = json.loads(scene.history_json)
    assert [h["outcome"] for h in hist if h["step"] == "voice"] == ["ok", "skipped", "skipped"]
    assert [h["outcome"] for h in hist if h["step"] == "avatar_request"] == ["failed", "ok"]
    # done is done: nothing is paid again
    asyncio.run(run_scene(db_session, scene, "avatar", voice_provider=voice, avatar_provider=av,
                          sleep=_nosleep))
    assert (voice.calls, av.uploads, av.requests) == (1, 1, 2)


def test_a_failed_provider_job_is_requested_anew_a_slow_one_is_not(db_session, tmp_path,
                                                                     monkeypatch):
    from app.documentary.host_scenes import plan_host_scenes, run_scene

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.avatar, "max_poll_minutes", 0)   # stop waiting at once
    monkeypatch.setenv("TrueCrime_Avatar_ID_Heygen", "group-1")
    scene = plan_host_scenes(db_session, _segments(db_session, "en"))[0]
    voice = FakeVoice()
    av = FakeAvatar(job_states=["processing", "failed", "completed"])
    # slow: the wait ends, the job is kept
    with pytest.raises(Exception, match="still processing"):
        asyncio.run(run_scene(db_session, scene, "avatar", voice_provider=voice,
                              avatar_provider=av, sleep=_nosleep))
    assert scene.status == "avatar_requested" and av.requests == 1
    # the provider fails the job: the next run asks again with a new key
    with pytest.raises(Exception, match="no matting"):
        asyncio.run(run_scene(db_session, scene, "avatar", voice_provider=voice,
                              avatar_provider=av, sleep=_nosleep))
    assert av.requests == 1 and scene.provider_generation == 1 and scene.provider_job_id is None
    asyncio.run(run_scene(db_session, scene, "avatar", voice_provider=voice, avatar_provider=av,
                          sleep=_nosleep))
    assert scene.status == "avatar_ready" and av.requests == 2 and av.keys[0] != av.keys[1]
    assert scene.channel == "ClueVera" and scene.studio_asset_id.startswith("STUDIO_EN_")


def test_avatar_stays_off_unless_enabled(db_session, tmp_path, monkeypatch, client):
    from app.documentary.host_scenes import plan_host_scenes, run_scene

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    scene = plan_host_scenes(db_session, _segments(db_session, "fa"))[0]
    with pytest.raises(ST.StudioError, match="disabled"):
        asyncio.run(run_scene(db_session, scene, "avatar", voice_provider=FakeVoice()))
    assert scene.status == "voice_ready"     # the voice is kept
    r = client.post(f"/api/host-scenes/{scene.id}/run", json={"until": "avatar"})
    assert r.status_code == 409
    d = client.get(f"/api/host-scenes/{scene.id}").json()
    assert d["channel"] == "رد خاموش" and d["failed_step"] == "avatar_upload"
    assert d["voice_ready"] and d["history"]


# ---------------------------------------------------------------------------
# HeyGen client (network replaced)
# ---------------------------------------------------------------------------


def test_heygen_client_uses_own_audio_transparent_output_and_idempotency(tmp_path, monkeypatch):
    from app.providers.avatar.heygen import HeyGenAvatarProvider

    monkeypatch.setenv("HEYGEN_API_KEY", "k")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/v3/avatars/looks/group-1":
            return httpx.Response(404, json={"error": {"code": "avatar_not_found",
                                                       "message": "avatar look not found"}})
        if path == "/v3/avatars/looks":
            return httpx.Response(200, json={"data": [
                {"id": "look-x", "group_id": "other", "status": "completed"},
                {"id": "look-1", "group_id": "group-1", "status": "completed"}],
                "has_more": False})
        if path == "/v3/assets":
            return httpx.Response(200, json={"data": {"asset_id": "a-1"}})
        if path == "/v3/videos" and request.method == "POST":
            return httpx.Response(200, json={"data": {"video_id": "v-1", "status": "waiting"}})
        if path == "/v3/videos/v-1":
            return httpx.Response(200, json={"data": {"status": "completed", "duration": 3.8,
                                                      "video_url": "https://files.example/v.webm"}})
        if request.url.host == "files.example":
            return httpx.Response(200, content=b"WEBM-DATA")
        return httpx.Response(500, json={"error": "unexpected"})

    p = HeyGenAvatarProvider(transport=httpx.MockTransport(handler))
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3")

    async def flow():
        look = await p.resolve_look("group-1")
        aid = await p.upload_asset(audio, "audio/mpeg")
        job = await p.create_video(look, aid, "key-1", "webm", "1080p")
        st = await p.get_video(job.job_id)
        n = await p.download(st.video_url, tmp_path / "v.webm")
        return look, aid, job, st, n

    look, aid, job, st, n = asyncio.run(flow())
    assert (look, aid, job.job_id, st.status, n) == ("look-1", "a-1", "v-1", "completed", 9)
    post = next(r for r in seen if r.url.path == "/v3/videos")
    body = json.loads(post.content)
    assert post.headers["Idempotency-Key"] == "key-1" and post.headers["X-Api-Key"] == "k"
    assert body["output_format"] == "webm" and body["audio_asset_id"] == "a-1"
    assert "background" not in body and "script" not in body
    assert (tmp_path / "v.webm").read_bytes() == b"WEBM-DATA"
    assert not (tmp_path / "v.webm.part").exists()
    with pytest.raises(Exception, match="cannot take a background"):
        asyncio.run(p.create_video("look-1", "a-1", "k2", "webm", "1080p", background_asset_id="b"))
    monkeypatch.delenv("HEYGEN_API_KEY")
    from app.providers.avatar.base import AvatarProviderError

    with pytest.raises(AvatarProviderError) as e:
        asyncio.run(HeyGenAvatarProvider().upload_asset(audio, "audio/mpeg"))
    assert e.value.kind == "missing_credentials"
