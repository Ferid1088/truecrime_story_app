"""The Instagram Reels publisher with a mocked Meta: every request goes to an httpx.MockTransport, so no test can reach Meta. The token in these tests is a made-up string."""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
from conftest import _delete_case_tree
from PIL import Image
from sqlalchemy.exc import IntegrityError

from app.core.provider_guard import RealProviderUseError
from app.db.models import Case, CoverProposal, ProviderCall, Publication, RoutingLink, ShortClip
from app.documentary import storage
from app.meta_publishing.config import meta_config
from app.meta_publishing.graph import MetaGraphClient
from app.meta_publishing.publisher import InstagramReelsPublisher, Mode, Refused, confirmation_text
from app.meta_publishing.token import TokenError, TokenStore
from app.publishing.registry import PublicationRegistry
from app.youtube_publishing.uploader import compose_description

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is needed to make the test video")
TOKEN = "FAKE_TEST_TOKEN_aaaaaaaaaaaaaaaaaaaaaaaa"
TOKEN_ENV = meta_config().instagram.token_env
IG_ID = meta_config().instagram.account.ig_account_id
USER = meta_config().instagram.account.username


def _video(path: Path, *, editlist: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = [] if editlist else ["-use_editlist", "0"]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=540x960:rate=30:duration=5", "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "60", "-sc_threshold", "0", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-movflags", "+faststart", *flags, str(path)], check=True)
    return path


class FakeMeta:
    """A scripted Meta: records every request, answers by route; `statuses` is the container's status_code over time."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.account = {"id": IG_ID, "username": USER}
        self.limit_error: dict | None = None
        self.account_error: dict | None = None
        self.statuses = ["IN_PROGRESS", "FINISHED"]
        self.upload_replies: list[httpx.Response] = []
        self.publish_exception: Exception | None = None
        self.publish_error: dict | None = None
        self.on_status = None                       # called when Meta is asked for the container's status
        self.granted = list(meta_config().instagram.required_permissions)

    def calls(self, method: str, tail: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path.endswith(tail)]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if "ig-api-upload" in path:
            return self.upload_replies.pop(0) if self.upload_replies else httpx.Response(200, json={"success": True, "message": "Upload successful."})
        if path.endswith("/me/permissions"):
            return httpx.Response(200, json={"data": [{"permission": p, "status": "granted"} for p in self.granted]})
        if path.endswith("content_publishing_limit"):
            return httpx.Response(400, json={"error": self.limit_error}) if self.limit_error else httpx.Response(200, json={"data": [{"quota_usage": 2, "config": {}}]})
        if path.endswith(f"/{IG_ID}"):
            return httpx.Response(400, json={"error": self.account_error}) if self.account_error else httpx.Response(200, json=self.account)
        if request.method == "POST" and path.endswith(f"/{IG_ID}/media"):
            return httpx.Response(200, json={"id": "CONTAINER1"})
        if path.endswith("/CONTAINER1"):
            if self.on_status:
                self.on_status()
            return httpx.Response(200, json={"status_code": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]})
        if path.endswith("media_publish"):
            if self.publish_exception:
                raise self.publish_exception
            return httpx.Response(400, json={"error": self.publish_error}) if self.publish_error else httpx.Response(200, json={"id": "MEDIA1"})
        if path.endswith("/MEDIA1"):
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/TEST/", "media_product_type": "REELS"})
        return httpx.Response(404, json={"error": {"message": "unexpected route", "code": 1}})


@pytest.fixture
def world(db_session, tmp_path, monkeypatch):
    """A case with a Farsi clip C1 whose file is valid for Instagram, a token in the environment, live calls on, and no real waiting."""
    monkeypatch.setattr("app.core.ai_config.ai_config.documentary.storage_dir", str(tmp_path / "cases"))
    case = Case(canonical_title="Meta test", slug=f"meta-test-{uuid.uuid4().hex[:8]}", language="fa")
    db_session.add(case)
    db_session.commit()
    monkeypatch.setattr(meta_config().preflight, "case_id", case.id)
    video = _video(tmp_path / "cases" / str(case.id) / "clips" / "fa" / "clip_C1.mp4")
    db_session.add(ShortClip(case_id=case.id, language="fa", clip_key="C1", status="valid", media_json=json.dumps({"video": {"path": storage.rel(video), "seconds": 5}})))
    db_session.commit()
    from app.covers import service as covers_service

    db_session.add(CoverProposal(case_id=case.id, language="fa", cover_key="C1", kind="short", status="approved", version=1))
    db_session.commit()
    Image.new("RGB", (36, 64), (30, 60, 90)).save(covers_service.covers_dir(case.id, "fa") / "short_C1.jpg")
    registry = PublicationRegistry(db_session)
    monkeypatch.setattr(registry.cfg.plan, "make_delivery_copies", False)       # these tests upload the clip as it is; the delivery copy has its own test
    monkeypatch.setattr(registry.cfg.scheduling, "film_before_shorts", False)   # these tests upload a Reel with no film; the film-first rule has its own tests
    registry.plan(case.id, "fa")
    plan_row = db_session.query(Publication).filter_by(case_id=case.id, platform="instagram", asset_key="C1").one()
    registry.approve_content(plan_row.id)
    registry.approve_publication(plan_row.id)
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    fake = FakeMeta()
    settings = meta_config().instagram.model_copy(update={"live_enabled": True, "retry_backoff_s": 0, "poll_interval_s": 1, "poll_max_s": 3, "token_file": str(tmp_path / "token")})
    sleeps: list[float] = []

    async def no_sleep(seconds):
        sleeps.append(seconds)

    tokens = TokenStore(settings)
    client = MetaGraphClient(settings, tokens, transport=httpx.MockTransport(fake), sleep=no_sleep)
    publisher = InstagramReelsPublisher(settings, client=client, tokens=tokens, sleep=no_sleep)
    yield type("World", (), {"case": case, "video": video, "fake": fake, "publisher": publisher, "settings": settings, "db": db_session, "sleeps": sleeps, "tmp": tmp_path})
    db_session.rollback()
    ids = [p.id for p in db_session.query(Publication).filter_by(case_id=case.id).all()]
    db_session.query(RoutingLink).filter(RoutingLink.publication_id.in_(ids)).delete(synchronize_session=False)
    db_session.query(Publication).filter_by(case_id=case.id).delete()
    db_session.query(ProviderCall).filter(ProviderCall.host.in_(["graph.facebook.com", "rupload.facebook.com"])).delete(synchronize_session=False)      # the ledger records mocked calls too
    db_session.commit()
    _delete_case_tree(db_session, case.id)


async def run(w, mode, *, confirm=None, language="fa", clip="C1"):
    return await w.publisher.run(w.db, language=language, clip_key=clip, mode=mode, confirm=confirm)


def confirm_text(w):
    return confirmation_text("fa", "C1", IG_ID)


def op(w) -> Publication:
    w.db.expire_all()
    return w.db.query(Publication).filter_by(case_id=w.case.id, platform="instagram", asset_key="C1").first()


def everything_said(report) -> str:
    return json.dumps([report.steps, report.operation], default=lambda o: o.__dict__)


async def test_the_default_mode_is_dry_run_and_live_calls_start_off():
    assert meta_config().instagram.default_mode == "DRY_RUN" and meta_config().instagram.live_enabled is False


async def test_dry_run_sends_nothing_and_lists_the_requests(world):
    report = await run(world, Mode.DRY_RUN)
    assert world.fake.requests == []
    assert [s.status for s in report.steps][0] == "ok" and any(s.name == "Publish" and s.status == "planned" for s in report.steps)
    assert op(world).container_id is None and op(world).status == "publication_approved"
    assert any(m["action"] == "set_ai_disclosure_toggle" for m in report.manual_steps)


async def test_only_the_farsi_language_is_allowed(world):
    with pytest.raises(Refused) as e:
        await run(world, Mode.DRY_RUN, language="en")
    assert e.value.reason == "language_blocked" and world.fake.requests == []


async def test_live_calls_are_refused_while_live_is_disabled(world):
    off = InstagramReelsPublisher(world.settings.model_copy(update={"live_enabled": False}), client=world.publisher.client, tokens=world.publisher.tokens)
    with pytest.raises(Refused) as e:
        await off.run(world.db, language="fa", clip_key="C1", mode=Mode.UPLOAD_ONLY)
    assert e.value.reason == "live_disabled" and world.fake.requests == []


async def test_a_missing_token_is_refused_without_a_request(world, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV)
    with pytest.raises(Refused) as e:
        await run(world, Mode.UPLOAD_ONLY)
    assert e.value.reason == "token_missing" and world.fake.requests == []


async def test_invalid_media_is_refused_before_any_request(world):
    _video(world.video, editlist=True)
    with pytest.raises(Refused) as e:
        await run(world, Mode.UPLOAD_ONLY)
    assert e.value.reason == "video_invalid" and "no edit list" in str(e.value) and world.fake.requests == []


async def test_a_prepared_copy_beside_the_original_is_the_file_that_is_used(world):
    _video(world.video, editlist=True)
    prepared = _video(world.video.parent / "meta" / world.video.name)
    assert (await run(world, Mode.DRY_RUN)).steps[0].detail.startswith("Instagram preflight: PASS") and prepared.is_file()


async def test_the_delivery_copy_is_the_file_that_is_used_before_a_prepared_copy(world):
    from app.shortform.delivery import delivery_path

    _video(world.video, editlist=True)
    _video(world.video.parent / "meta" / world.video.name)
    delivery = _video(delivery_path(world.video))
    report = await run(world, Mode.DRY_RUN)
    assert report.steps[0].detail.startswith("Instagram preflight: PASS") and f"({delivery.name})" in report.steps[0].detail


async def test_the_reel_is_sent_with_the_publications_approved_text_and_hashtags(world):
    row = op(world)
    row.caption = "The approved text of this reel."
    row.metadata_json = json.dumps({"hashtags": ["#truecrime", "#case15"]})
    world.db.commit()
    registry = PublicationRegistry(world.db)
    registry.approve_content(row.id)
    registry.approve_publication(row.id)
    await run(world, Mode.UPLOAD_ONLY)
    sent = json.loads(world.fake.calls("POST", f"/{IG_ID}/media")[0].content)["caption"]
    assert sent.startswith("The approved text of this reel.") and "#truecrime" in sent and "#case15" in sent


async def test_upload_only_makes_the_container_uploads_and_waits_but_never_publishes(world):
    report = await run(world, Mode.UPLOAD_ONLY)
    row = op(world)
    assert row.status == "ready" and row.container_id == "CONTAINER1" and row.remote_id is None
    assert world.fake.calls("POST", "media_publish") == []
    media = world.fake.calls("POST", f"/{IG_ID}/media")[0]
    assert json.loads(media.content) == {"media_type": "REELS", "upload_type": "resumable", "caption": compose_description(row), "thumb_offset": 0}        # the text sent holds no reserved film row
    assert media.headers["authorization"] == f"Bearer {TOKEN}"
    upload = world.fake.calls("POST", "/CONTAINER1")[0]
    assert upload.url.host == "rupload.facebook.com" and upload.url.path == "/ig-api-upload/v27.0/CONTAINER1"
    assert upload.headers["authorization"] == f"OAuth {TOKEN}" and upload.headers["offset"] == "0" and upload.headers["file_size"] == str(world.video.stat().st_size)
    assert upload.content == world.video.read_bytes()
    assert TOKEN not in everything_said(report) and world.sleeps == [1]


async def test_a_second_upload_only_reuses_the_ready_container(world):
    await run(world, Mode.UPLOAD_ONLY)
    await run(world, Mode.UPLOAD_ONLY)
    assert len(world.fake.calls("POST", f"/{IG_ID}/media")) == 1 and len(world.fake.calls("POST", "/CONTAINER1")) == 1


async def test_publish_needs_a_prepared_reel(world):
    with pytest.raises(Refused) as e:
        await run(world, Mode.PUBLISH, confirm=confirm_text(world))
    assert e.value.reason == "not_prepared" and world.fake.calls("POST", "media_publish") == []


async def test_publish_needs_the_owners_confirmation_text(world):
    await run(world, Mode.UPLOAD_ONLY)
    for wrong in (None, "yes", "PUBLISH fa C2 " + IG_ID, "PUBLISH en C1 " + IG_ID):
        with pytest.raises(Refused) as e:
            await run(world, Mode.PUBLISH, confirm=wrong)
        assert e.value.reason == "confirmation_missing"
    assert world.fake.calls("POST", "media_publish") == []


async def test_publish_returns_the_media_id_and_permalink(world):
    await run(world, Mode.UPLOAD_ONLY)
    report = await run(world, Mode.PUBLISH, confirm=confirm_text(world))
    row = op(world)
    assert (row.status, row.remote_id, row.remote_url) == ("published", "MEDIA1", "https://www.instagram.com/reel/TEST/")
    assert json.loads(world.fake.calls("POST", "media_publish")[0].content) == {"creation_id": "CONTAINER1"}
    assert TOKEN not in everything_said(report)


async def test_the_same_file_is_never_published_twice(world):
    await run(world, Mode.UPLOAD_ONLY)
    await run(world, Mode.PUBLISH, confirm=confirm_text(world))
    for mode in (Mode.PUBLISH, Mode.UPLOAD_ONLY):
        with pytest.raises(Refused) as e:
            await run(world, mode, confirm=confirm_text(world))
        assert e.value.reason == "duplicate_publish"
    assert len(world.fake.calls("POST", "media_publish")) == 1


async def test_the_database_itself_refuses_a_second_published_row(world):
    await run(world, Mode.UPLOAD_ONLY)
    await run(world, Mode.PUBLISH, confirm=confirm_text(world))
    first = op(world)
    world.db.add(Publication(case_id=world.case.id, language="fa", platform="instagram", kind="short", asset_key="C9", account_id=IG_ID, content_hash=first.content_hash, video_path="x", status="published"))
    with pytest.raises(IntegrityError):
        world.db.commit()
    world.db.rollback()


async def test_a_lost_publish_answer_blocks_any_second_publish(world):
    await run(world, Mode.UPLOAD_ONLY)
    world.fake.publish_exception = httpx.ReadTimeout("no answer")
    report = await run(world, Mode.PUBLISH, confirm=confirm_text(world))
    assert op(world).status == "publish_uncertain" and any("may be public" in s.detail for s in report.steps)
    world.fake.publish_exception = None
    with pytest.raises(Refused) as e:
        await run(world, Mode.PUBLISH, confirm=confirm_text(world))
    assert e.value.reason == "duplicate_publish" and len(world.fake.calls("POST", "media_publish")) == 1


async def test_a_refused_publish_call_leaves_the_reel_ready(world):
    await run(world, Mode.UPLOAD_ONLY)
    world.fake.publish_error = {"message": "Media ID is not available", "code": 9007}
    await run(world, Mode.PUBLISH, confirm=confirm_text(world))
    assert op(world).status == "ready" and "Media ID" in op(world).error


async def test_publish_waits_for_a_finished_container(world):
    await run(world, Mode.UPLOAD_ONLY)
    world.fake.statuses = ["IN_PROGRESS"]
    await run(world, Mode.PUBLISH, confirm=confirm_text(world))
    assert world.fake.calls("POST", "media_publish") == [] and op(world).status == "ready" and "IN_PROGRESS" in op(world).error


@pytest.mark.parametrize("error, kind", [({"message": "Error validating access token: Session has expired", "code": 190, "error_subcode": 463}, "expired"),
                                          ({"message": "Invalid OAuth access token", "code": 190}, "Invalid OAuth")])
async def test_an_expired_or_invalid_token_stops_before_a_container_is_made(world, error, kind):
    world.fake.account_error = error
    report = await run(world, Mode.UPLOAD_ONLY)
    assert op(world).status == "failed" and kind in op(world).error
    assert world.fake.calls("POST", f"/{IG_ID}/media") == [] and TOKEN not in everything_said(report)


async def test_a_missing_publishing_permission_stops_before_a_container_is_made(world):
    world.fake.limit_error = {"message": "(#10) Application does not have permission for this action", "code": 10}
    await run(world, Mode.UPLOAD_ONLY)
    assert op(world).status == "failed" and "permission" in op(world).error and world.fake.calls("POST", f"/{IG_ID}/media") == []


@pytest.mark.parametrize("account", [{"id": "17841400000000000", "username": USER}, {"id": IG_ID, "username": "someone_else"}])
async def test_a_different_instagram_account_is_blocked(world, account):
    world.fake.account = account
    await run(world, Mode.UPLOAD_ONLY)
    assert op(world).status == "failed" and "different Instagram account" in op(world).error
    assert world.fake.calls("POST", f"/{IG_ID}/media") == [] and world.fake.calls("POST", "/CONTAINER1") == []


async def test_a_failed_upload_is_recorded_and_publishes_nothing(world):
    world.fake.upload_replies = [httpx.Response(400, json={"debug_info": {"retriable": False, "type": "ProcessingFailedError", "message": "unauthorized user request"}})]
    await run(world, Mode.UPLOAD_ONLY)
    assert op(world).status == "failed" and "unauthorized" in op(world).error and len(world.fake.calls("POST", "/CONTAINER1")) == 1


async def test_a_retriable_upload_failure_is_retried(world):
    flaky = httpx.Response(500, json={"debug_info": {"retriable": True, "type": "X", "message": "try again"}})
    world.fake.upload_replies = [flaky, flaky]
    await run(world, Mode.UPLOAD_ONLY)
    assert op(world).status == "ready" and len(world.fake.calls("POST", "/CONTAINER1")) == 3


async def test_retries_are_bounded(world):
    flaky = httpx.Response(500, json={"debug_info": {"retriable": True, "type": "X", "message": "try again"}})
    world.fake.upload_replies = [flaky] * 5
    await run(world, Mode.UPLOAD_ONLY)
    assert op(world).status == "failed" and len(world.fake.calls("POST", "/CONTAINER1")) == world.settings.retry_attempts


async def test_a_timeout_on_a_read_is_retried(world):
    original = world.fake.__call__
    seen = {"n": 0}

    def flaky(request):
        if request.url.path.endswith(f"/{IG_ID}") and seen["n"] == 0:
            seen["n"] += 1
            raise httpx.ConnectTimeout("slow")
        return original(request)

    world.publisher.client.transport = httpx.MockTransport(flaky)
    await run(world, Mode.UPLOAD_ONLY)
    assert seen["n"] == 1 and op(world).status == "ready"      # the first attempt timed out, the second answered


async def test_processing_that_outlasts_the_wait_can_be_resumed_without_a_second_upload(world):
    world.fake.statuses = ["IN_PROGRESS"]
    report = await run(world, Mode.UPLOAD_ONLY)
    assert op(world).status == "processing" and "still IN_PROGRESS" in report.steps[-2].detail
    world.fake.statuses = ["FINISHED"]
    await run(world, Mode.UPLOAD_ONLY)
    assert op(world).status == "ready" and len(world.fake.calls("POST", f"/{IG_ID}/media")) == 1 and len(world.fake.calls("POST", "/CONTAINER1")) == 1


async def test_a_container_error_is_recorded(world):
    world.fake.statuses = ["ERROR"]
    await run(world, Mode.UPLOAD_ONLY)
    assert op(world).status == "failed" and "ERROR" in op(world).error


async def test_the_client_cannot_reach_the_real_meta_in_a_test(world):
    real = MetaGraphClient(world.settings, world.publisher.tokens)
    with pytest.raises(RealProviderUseError):
        await real.account()


def test_the_token_store_keeps_the_token_owner_only_and_never_reveals_it(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    store = TokenStore(meta_config().instagram.model_copy(update={"token_file": str(tmp_path / "sub" / "token")}))
    assert store.source() is None
    with pytest.raises(TokenError):
        store.get()
    for bad in ("short", "has spaces in it and is long enough........"):
        with pytest.raises(TokenError):
            store.set(bad)
    store.set(TOKEN)
    assert store.path.stat().st_mode & 0o777 == 0o600 and store.source() == "file" and store.get() == TOKEN
    assert store.redact(f"boom {TOKEN} boom") == "boom [token] boom"
    assert store.clear() and store.source() is None


async def test_the_interface_never_returns_the_token(world, client, monkeypatch):
    monkeypatch.setattr("app.meta_publishing.api._publisher", lambda: world.publisher)
    status = client.get("/api/meta-publishing/status")
    assert status.status_code == 200 and TOKEN not in status.text
    body = status.json()
    assert body["token"] == {"configured": True, "source": "environment"} and body["clips"][0]["clip_key"] == "C1" and body["clips"][0]["verdict"] == "PASS"
    assert body["account"]["ig_account_id"] == IG_ID
    dry = client.post("/api/meta-publishing/operations", json={"clip_key": "C1"})
    assert dry.status_code == 200 and dry.json()["mode"] == "DRY_RUN" and world.fake.requests == []
    assert client.post("/api/meta-publishing/operations", json={"clip_key": "C1", "language": "en"}).status_code == 409
    publish = client.post("/api/meta-publishing/operations", json={"clip_key": "C1", "mode": "PUBLISH"})
    assert publish.status_code == 409 and publish.json()["detail"]["reason"] == "confirmation_missing" and world.fake.requests == []


def _spoil_media_check(w) -> None:
    """The stored Instagram media check now says FAIL: a requirement stopped holding without the file (and so any hash) changing."""
    w.db.expire_all()
    row = op(w)
    data = json.loads(row.hash_inputs_json)
    data["readiness"]["preflight"] = {"for": row.content_hash, "verdict": "FAIL", "failing": ["no edit list"], "error": None}
    row.hash_inputs_json = json.dumps(data, sort_keys=True)
    w.db.commit()


def _readiness_events(w):
    from app.db.models import PolicyEvent

    return w.db.query(PolicyEvent).filter_by(case_id=w.case.id, policy="publication", decision="readiness_blocked").all()


async def test_an_upload_is_refused_when_the_requirements_changed_after_approval(world):
    _spoil_media_check(world)
    with pytest.raises(Refused) as e:
        await run(world, Mode.UPLOAD_ONLY)
    assert e.value.reason == "readiness_changed" and "Instagram media check: no edit list" in str(e.value)
    assert world.fake.requests == []
    row = op(world)
    assert row.status == "voided" and row.publication_approved_hash is None and "no edit list" in row.error
    assert [json.loads(ev.context_json)["stage"] for ev in _readiness_events(world)] == ["upload"]


async def test_a_publish_is_refused_when_the_requirements_changed_after_the_upload(world):
    await run(world, Mode.UPLOAD_ONLY)
    requests_before = len(world.fake.requests)
    _spoil_media_check(world)
    with pytest.raises(Refused) as e:
        await run(world, Mode.PUBLISH, confirm=confirm_text(world))
    assert e.value.reason == "readiness_changed"
    assert world.fake.calls("POST", "media_publish") == [] and len(world.fake.requests) == requests_before
    assert op(world).status == "voided" and [json.loads(ev.context_json)["stage"] for ev in _readiness_events(world)] == ["publish"]


async def test_the_last_look_before_the_final_call_catches_a_change_during_the_publish(world):
    await run(world, Mode.UPLOAD_ONLY)
    world.fake.on_status = lambda: _spoil_media_check(world)               # the change happens while Meta is being asked about the container
    report = await run(world, Mode.PUBLISH, confirm=confirm_text(world))
    assert world.fake.calls("POST", "media_publish") == []
    assert any(step.name == "Stopped" and "no edit list" in step.detail for step in report.steps)
    assert op(world).status == "voided" and op(world).remote_id is None
    assert [json.loads(ev.context_json)["stage"] for ev in _readiness_events(world)] == ["publish"]


async def test_an_upload_that_finds_an_expired_approval_saves_the_expiry_at_once(world):
    from datetime import timedelta

    from app.publishing.config import publishing_config
    from app.utils import utc_now

    row = op(world)
    row.publication_approved_at = utc_now() - timedelta(days=publishing_config().approval.max_age_days + 1)
    world.db.commit()
    with pytest.raises(Refused) as e:
        await run(world, Mode.UPLOAD_ONLY)
    assert e.value.reason == "not_approved" and world.fake.requests == []
    from app.db.models import PolicyEvent

    assert op(world).status == "expired"
    assert world.db.query(PolicyEvent).filter_by(case_id=world.case.id, decision="approval_expired").count() == 1


def _sync(fn):
    @functools.wraps(fn)
    def runner(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return runner


for _name, _fn in list(globals().items()):      # the repository has no async test plugin: every async test runs in its own event loop
    if _name.startswith("test_") and inspect.iscoroutinefunction(_fn):
        globals()[_name] = _sync(_fn)


def test_publishing_stops_when_a_permission_meta_documents_for_publishing_is_missing(world):
    world.fake.granted = ["instagram_basic", "instagram_content_publish"]
    with pytest.raises(Refused) as caught:
        asyncio.run(world.publisher.authorize())
    assert caught.value.reason == "permission_missing" and "pages_read_engagement" in str(caught.value)
    assert not world.fake.calls("POST", f"/{IG_ID}/media")


def test_the_token_is_read_from_the_instagram_environment_variable_and_the_old_name_still_works(monkeypatch, tmp_path):
    settings = meta_config().instagram.model_copy(update={"token_file": str(tmp_path / "none")})
    assert settings.token_env == "TRUECRIME_INSTAGRAM_ACCESS_TOKEN"
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    monkeypatch.delenv("TRUECRIME_META_ACCESS_TOKEN", raising=False)
    assert TokenStore(settings).source() is None
    monkeypatch.setenv("TRUECRIME_META_ACCESS_TOKEN", "OLD_NAME_TOKEN_aaaaaaaaaaaaaaaaaaaa")
    assert TokenStore(settings).source() == "environment" and TokenStore(settings).get().startswith("OLD_NAME")
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    assert TokenStore(settings).get() == TOKEN                                     # the canonical name wins
