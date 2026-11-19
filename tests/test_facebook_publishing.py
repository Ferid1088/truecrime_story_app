# ruff: noqa: F811  (the `world` fixture is imported from test_publication_registry and used by name)
"""The Farsi Facebook Page: connection (status, read-only test, Page token), the Reels and long-film uploader (UPLOAD_ONLY, SAVE_DRAFT, PUBLISH), duplicate protection, restart and the
Monitoring findings. Meta is a scripted httpx.MockTransport that follows the endpoints on Meta's own pages: no network and no real credentials. What the real Meta does with a draft, or with a
system-user token on the resumable upload, is verified separately and stays labelled unverified until then."""

from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from test_publication_registry import events, planned, pub, world  # noqa: F401
from test_publication_safeguards import approve_both, meet_every_film_requirement

from app.db.models import FacebookUploadSession, Publication, PublicationAccount
from app.meta_publishing import facebook_publisher as FP
from app.meta_publishing.config import meta_config
from app.meta_publishing.connection import ConnectError
from app.meta_publishing.facebook_client import FacebookClient
from app.meta_publishing.facebook_connection import FacebookConnection
from app.meta_publishing.facebook_publisher import (
    FacebookPublisher,
    Mode,
    Refused,
    confirmation_text,
)
from app.meta_publishing.token import TokenStore
from app.publishing import automatic
from app.publishing.config import publishing_config
from app.publishing.monitoring import facebook_connection_issues, publishing_issues

SYSTEM, PAGE_TOKEN = "SYSTEM_USER_TOKEN_aaaaaaaaaaaaaaaaaaaa", "PAGE_ACCESS_TOKEN_bbbbbbbbbbbbbbbbbbbbbbb"
PAGE = meta_config().facebook.page.page_id
IG = meta_config().facebook.page.linked_instagram_id
APP = meta_config().facebook.app_id


class FakeMeta:
    """A scripted Meta: records every request (method, host, path, headers, body) and answers by route."""

    def __init__(self):
        self.requests: list[tuple[str, str, str, dict, bytes]] = []
        self.granted = list(meta_config().facebook.required_permissions) + ["instagram_basic"]
        self.page_id, self.linked, self.tasks, self.listed = PAGE, IG, ["MANAGE", "CREATE_CONTENT"], True
        self.limits = {"length": 14460, "size": 28633115306}
        self.statuses = ["processing", "ready"]
        self.error: dict | None = None
        self.fail_next: list[Exception] = []
        self.upload_failures = 0
        self.descriptions: list[str] = []
        self.comments: list[str] = []
        self.fail_after_chunks: int | None = None          # the connection drops once this many film chunks are stored (and stays down until it is set to None)
        self.chunks: list[tuple[int, int]] = []
        self.finish_exception: Exception | None = None
        self.bytes_transferred = 0

    def calls(self, method: str, tail: str) -> list[tuple[str, str, str, dict, bytes]]:
        return [r for r in self.requests if r[0] == method and r[2].endswith(tail)]

    @staticmethod
    def form_of(request_or_body, content_type: str | None = None) -> dict[str, bytes]:
        """The parts of a multipart form: {name: bytes}."""
        if isinstance(request_or_body, tuple):
            content_type, request_or_body = request_or_body[3]["content-type"], request_or_body[4]
        boundary = content_type.split("boundary=")[1].encode()
        out = {}
        for part in request_or_body.split(b"--" + boundary)[1:-1]:
            head, _, value = part.partition(b"\r\n\r\n")
            name = re.search(rb'name="([^"]+)"', head).group(1).decode()
            out[name] = value[:-2] if value.endswith(b"\r\n") else value
        return out

    def film(self, headers: dict, body: bytes) -> httpx.Response:
        """Meta's chunked Page-video upload: start names a session and a video, transfer returns where the next chunk goes, finish makes the video."""
        form = self.form_of(body, headers["content-type"])
        phase = form["upload_phase"].decode()
        if phase == "start":
            return httpx.Response(200, json={"video_id": "VIDEO9", "upload_session_id": "S1", "start_offset": "0", "end_offset": str(min(4096, int(form["file_size"])))})
        if phase == "transfer":
            if self.fail_next:
                raise self.fail_next.pop(0)
            if self.fail_after_chunks is not None and len(self.chunks) >= self.fail_after_chunks:
                raise httpx.ConnectError("dropped")
            offset, size = int(form["start_offset"]), len(form["video_file_chunk"])
            self.chunks.append((offset, size))
            done = offset + size
            return httpx.Response(200, json={"start_offset": str(done), "end_offset": str(min(done + 4096, self.total))})
        if self.finish_exception:
            raise self.finish_exception
        return httpx.Response(200, json={"success": True})

    def json_of(self, request) -> dict:
        return json.loads(request[4]) if request[4] else {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        url, headers, body = request.url, dict(request.headers), request.content
        path = url.path
        self.requests.append((request.method, url.host, path, headers, body))
        if self.error:
            return httpx.Response(400, json={"error": self.error})
        query = dict(url.params)
        fields = query.get("fields", "")
        if path.endswith("/me/permissions"):
            return httpx.Response(200, json={"data": [{"permission": p, "status": "granted"} for p in self.granted]})
        if path.endswith("/me/accounts"):
            return httpx.Response(200, json={"data": [{"id": PAGE, "name": "Page", "tasks": self.tasks}] if self.listed else []})
        if path.endswith("/me"):
            if "video_upload_limits" in fields:
                return httpx.Response(200, json={"id": "1", "video_upload_limits": self.limits})
            return httpx.Response(200, json={"id": self.page_id if PAGE_TOKEN in headers.get("authorization", "") else "1", "name": "Page"})
        if request.method == "GET" and path.endswith(f"/{PAGE}"):
            if fields == "access_token":
                return httpx.Response(200, json={"id": PAGE, "access_token": PAGE_TOKEN})
            return httpx.Response(200, json={"id": self.page_id, "name": "ردِ خاموش", "instagram_business_account": {"id": self.linked, "username": "fe.rashidi.tc.fa"}})
        if request.method == "POST" and path.endswith(f"/{PAGE}/video_reels"):
            payload = json.loads(body)
            if payload["upload_phase"] == "start":
                return httpx.Response(200, json={"video_id": "VIDEO1", "upload_url": "https://rupload.facebook.com/video-upload/VIDEO1"})
            if self.finish_exception:
                raise self.finish_exception
            return httpx.Response(200, json={"success": True})
        if url.host == "rupload.facebook.com":
            if self.upload_failures:
                self.upload_failures -= 1
                raise httpx.ConnectError("connection dropped")
            return httpx.Response(200, json={"success": True})
        if request.method == "POST" and path.endswith("/VIDEO1/thumbnails"):
            return httpx.Response(200, json={"success": True})
        if request.method == "GET" and path.endswith("/VIDEO1/thumbnails"):
            return httpx.Response(200, json={"data": [{"id": "T1", "uri": "https://scontent.example/t1.jpg", "width": 1080, "height": 1920, "is_preferred": True},
                                                       {"id": "T2", "uri": "https://scontent.example/t2.jpg", "width": 1080, "height": 1920, "is_preferred": False}]})
        if request.method == "POST" and path.endswith("/VIDEO1/comments"):
            self.comments.append(json.loads(body)["message"])
            return httpx.Response(200, json={"id": f"COMMENT{len(self.comments)}"})
        if request.method == "POST" and path.endswith("/VIDEO1") and b"description" in body:
            self.descriptions.append(json.loads(body)["description"])
            return httpx.Response(200, json={"success": True})
        if path.endswith("/VIDEO1") or path.endswith("/VIDEO9"):
            if "permalink_url" in fields:
                return httpx.Response(200, json={"permalink_url": "/redsilence/videos/9/"})
            step = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
            return httpx.Response(200, json={"status": {"video_status": step, "uploading_phase": {"bytes_transferred": self.bytes_transferred}, "processing_phase": {"error": {"message": "bad"}}}})
        if request.method == "POST" and path.endswith(f"/{PAGE}/videos"):
            return self.film(headers, body)
        return httpx.Response(404, json={"error": {"message": "unexpected route", "code": 1}})

    total = 0


@pytest.fixture
def meta(tmp_path, monkeypatch):
    config = meta_config()
    monkeypatch.setenv(config.instagram.token_env, SYSTEM)
    fake = FakeMeta()
    facebook = config.facebook.model_copy(deep=True, update={"live_enabled": True, "state_file": str(tmp_path / "facebook_{language}.json"),
                                                  "film": config.facebook.film.model_copy(update={"chunk_bytes": 4096, "poll_interval_s": 1, "poll_max_s": 3}),
                                                  "reels": config.facebook.reels.model_copy(update={"poll_interval_s": 1, "poll_max_s": 3})})
    instagram = config.instagram.model_copy(deep=True, update={"token_file": str(tmp_path / "token"), "retry_backoff_s": 0})
    tokens = TokenStore(instagram)
    naps: list[float] = []

    async def nap(seconds):
        naps.append(seconds)

    transport = httpx.MockTransport(fake.handle)
    client = FacebookClient(instagram, facebook, tokens, transport=transport, sleep=nap)
    yield type("Meta", (), {"fake": fake, "facebook": facebook, "instagram": instagram, "tokens": tokens, "client": client, "transport": transport, "naps": naps, "tmp": tmp_path})


@pytest.fixture
def fb(world, meta, monkeypatch):
    """The publisher, over a world with the Facebook publications planned. Sessions are removed before the world's own clean-up (they point at its publications)."""
    monkeypatch.setattr(world.registry.cfg.approval, "default_approved", True)       # the owner's setting: approvals are given by default ...
    monkeypatch.setattr(world.registry.cfg.approval, "manual_languages", [])         # ... and these tests run in Persian, which the shipped configuration approves by hand (tested below)
    publisher = FacebookPublisher(world.db, meta.instagram, meta.facebook, client=meta.client, registry=world.registry, tokens=meta.tokens, sleep=meta.client.sleep)
    yield type("FB", (), {"publisher": publisher, "w": world, "m": meta})
    world.db.rollback()
    world.db.query(FacebookUploadSession).filter(FacebookUploadSession.publication_id.in_([p.id for p in world.db.query(Publication).filter_by(case_id=world.case.id).all()])).delete(
        synchronize_session=False)
    world.db.commit()


def approved(fb, kind: str = "short", key: str = "C1") -> Publication:
    planned(fb.w)
    asset = key if kind == "short" else f"V{fb.w.film.id}"
    p = pub(fb.w, "facebook", kind, asset)
    fb.w.registry.update(p.id, metadata={"title": "A Persian title", "hashtags": ["#truecrime"], "keywords": [], "cta": ""})
    approve_both(fb.w, pub(fb.w, "facebook", kind, asset))
    return pub(fb.w, "facebook", kind, asset)


def go(fb, p: Publication, mode: Mode, *, confirm: str | None = "right"):
    text = confirmation_text(mode, p.id, PAGE) if confirm == "right" else confirm
    return asyncio.run(fb.publisher.run(p.id, mode, text))


def session_of(fb, p: Publication) -> FacebookUploadSession:
    fb.w.db.expire_all()
    return FP.latest_session(fb.w.db, p.id)


# --- 1. the connection ---------------------------------------------------------------------------------------------------------------------------------------

def connection(meta) -> FacebookConnection:
    return FacebookConnection(meta.instagram, meta.facebook, tokens=meta.tokens, transport=meta.transport)


def test_the_cover_permissions_are_read_from_the_token_and_the_status_says_whether_the_cover_can_be_set(meta):
    assert connection(meta).status()["cover_ready"] is False
    asyncio.run(connection(meta).test())
    assert connection(meta).status()["cover_ready"] is False and "pages_manage_engagement" in connection(meta).status()["cover_missing_permissions"]       # the token lacks them
    meta.fake.granted += ["pages_manage_engagement", "pages_read_user_content"]
    result = asyncio.run(connection(meta).test())
    status = connection(meta).status()
    assert status["cover_ready"] is True and status["cover_missing_permissions"] == [] and set(result["cover_permissions"]) >= {"pages_manage_engagement", "pages_read_user_content"}


def test_each_language_has_its_own_page_and_a_language_without_one_is_refused_clearly(meta, client):
    from app.meta_publishing.config import FacebookPage

    settings = meta.facebook.model_copy(update={"pages": {**meta.facebook.pages, "de": FacebookPage(page_id="999000111", brand="Stille Spur", linked_instagram_id="17800000000000001")}})
    assert settings.language == "fa" and settings.page.page_id == PAGE                                                  # not chosen: the first configured Page
    german = settings.for_language("de")
    assert german.language == "de" and german.page.page_id == "999000111" and german.state_file == settings.state_file
    assert FacebookConnection(meta.instagram, settings, language="de", tokens=meta.tokens).state_path.name == "facebook_de.json"
    assert FacebookConnection(meta.instagram, settings, language="de", tokens=meta.tokens).status()["page"]["id"] == "999000111"
    with pytest.raises(ValueError, match="no Facebook Page is configured for 'tr'"):
        settings.for_language("tr")
    with pytest.raises(ValueError):
        FacebookConnection(meta.instagram, meta.facebook, language="tr", tokens=meta.tokens)
    refused = client.get("/api/facebook-publishing/connection", params={"language": "tr"})
    assert refused.status_code == 409 and refused.json()["detail"]["reason"] == "no_page"
    assert client.get("/api/facebook-publishing/cases/1/tr/uploads").json()["detail"]["reason"] == "no_page"


def test_the_connection_test_checks_permissions_page_instagram_link_task_page_token_and_limits_and_reveals_no_token(meta):
    result = asyncio.run(connection(meta).test())
    assert result["page"]["id"] == PAGE and result["linked_instagram"]["id"] == IG and result["page_token"] == "obtained and verified"
    assert result["permissions"] == sorted(meta.facebook.required_permissions) and "CREATE_CONTENT" in result["tasks"] and result["upload_limits"] == meta.fake.limits
    assert {request[0] for request in meta.fake.requests} == {"GET"}
    status = connection(meta).status()
    assert status["verified"] and status["last_test"]["ok"] and status["token_source"] == "environment" and status["token_shared_with"] == "Instagram"
    stored = (meta.tmp / "facebook_fa.json").read_text()
    for text in (json.dumps(result), json.dumps(status), stored):
        assert SYSTEM not in text and PAGE_TOKEN not in text


def test_the_page_access_token_is_used_for_reels_and_the_system_token_for_the_connection_reads(meta):
    asyncio.run(connection(meta).test())
    by_path = {r[2].rsplit("/", 1)[-1]: r[3].get("authorization", "") for r in meta.fake.requests}
    assert SYSTEM in by_path["permissions"] and SYSTEM in by_path["accounts"]
    identity = [r for r in meta.fake.requests if r[2].endswith("/me") and "video_upload_limits" not in str(r[3])]
    assert any(PAGE_TOKEN in r[3].get("authorization", "") for r in identity)


@pytest.mark.parametrize("change,reason", [
    (lambda f: setattr(f, "granted", ["pages_show_list"]), "permission_missing"),
    (lambda f: setattr(f, "page_id", "999"), "page_mismatch"),
    (lambda f: setattr(f, "linked", "555"), "instagram_link_mismatch"),
    (lambda f: setattr(f, "listed", False), "page_not_managed"),
    (lambda f: setattr(f, "tasks", ["MODERATE"]), "task_missing"),
])
def test_a_wrong_page_a_missing_permission_or_task_is_refused_with_its_reason_and_remembered(meta, change, reason):
    change(meta.fake)
    with pytest.raises(ConnectError) as caught:
        asyncio.run(connection(meta).test())
    assert caught.value.reason == reason
    last = connection(meta).status()["last_test"]
    assert last["ok"] is False and reason in last["error"] and connection(meta).status()["verified"] is False


def test_a_token_error_is_reported_with_its_kind_and_never_with_a_token(meta):
    meta.fake.error = {"message": f"Error validating access token: Session has expired ({SYSTEM})", "code": 190, "error_subcode": 463}
    with pytest.raises(ConnectError) as caught:
        asyncio.run(connection(meta).test())
    assert caught.value.reason == "token_expired" and SYSTEM not in str(caught.value) and SYSTEM not in connection(meta).status()["last_test"]["error"]


def test_without_a_token_or_with_connection_calls_off_nothing_is_asked(meta, monkeypatch):
    monkeypatch.delenv(meta.instagram.token_env)
    with pytest.raises(ConnectError) as caught:
        asyncio.run(connection(meta).test())
    assert caught.value.reason == "not_connected" and meta.fake.requests == []
    monkeypatch.setenv(meta.instagram.token_env, SYSTEM)
    meta.instagram.connection.live_enabled = False
    with pytest.raises(ConnectError) as caught:
        asyncio.run(connection(meta).test())
    assert caught.value.reason == "live_disabled" and meta.fake.requests == []


def test_the_status_says_what_blocks_an_upload_and_the_registry_row_mirrors_the_connection(meta, world):
    conn = connection(meta)
    assert conn.status()["upload_ready"] is False and any("never tested" in b for b in conn.status()["upload_blockers"])
    conn.sync(world.db)
    row = world.db.query(PublicationAccount).filter_by(platform="facebook", language="fa").one()
    try:
        assert row.status == "not_connected" and row.external_id == PAGE
        asyncio.run(conn.test())
        assert conn.status()["upload_ready"] is True
        conn.sync(world.db)
        world.db.expire_all()
        assert world.db.query(PublicationAccount).filter_by(platform="facebook", language="fa").one().status == "connected"
        assert world.db.query(PublicationAccount).filter_by(platform="facebook", language="fa").count() == 1
        meta.facebook.live_enabled = False
        assert any("switched off" in b for b in conn.status()["upload_blockers"])
    finally:
        row = world.db.query(PublicationAccount).filter_by(platform="facebook", language="fa").one()
        page = meta_config().facebook.pages["fa"]
        row.status, row.external_id, row.display_name, row.secret_ref = "not_connected", page.page_id, page.brand, ""      # the account row is shared by every test: leave it as the registry first makes it
        world.db.commit()


# --- 2. the checks before a byte ---------------------------------------------------------------------------------------------------------------------------

def test_a_dry_run_makes_no_request_and_lists_the_steps_and_what_blocks_when_something_does(fb):
    planned(fb.w)
    p = pub(fb.w, "facebook", "short", "C1")
    steps = fb.publisher.plan(p.id)
    assert steps[0].status == "failed" and any("metadata" in s.detail for s in steps[1:])          # a plan with no platform text yet cannot be uploaded
    p = approved(fb)
    names = [s.name for s in fb.publisher.plan(p.id)]
    assert names[:5] == ["Checks", "Start", "Upload", "Processing", "Finish"] and fb.m.fake.requests == []


@pytest.mark.parametrize("setup,reason", [
    (lambda fb, p: setattr(fb.m.facebook, "live_enabled", False), "live_disabled"),
    (lambda fb, p: setattr(fb.publisher.registry, "_stopped", lambda: True), "emergency_stop"),
])
def test_live_off_and_the_emergency_stop_stop_everything_before_any_request(fb, setup, reason):
    p = approved(fb)
    setup(fb, p)
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.UPLOAD_ONLY)
    assert caught.value.reason == reason and fb.m.fake.requests == []


def test_each_mode_needs_its_own_typed_confirmation_for_this_publication_and_page(fb):
    p = approved(fb)
    for confirm in (None, "yes", confirmation_text(Mode.SAVE_DRAFT, p.id, PAGE), confirmation_text(Mode.UPLOAD_ONLY, p.id + 1, PAGE), confirmation_text(Mode.UPLOAD_ONLY, p.id, "999")):
        with pytest.raises(Refused) as caught:
            go(fb, p, Mode.UPLOAD_ONLY, confirm=confirm)
        assert caught.value.reason == "confirmation_missing"
    assert fb.m.fake.requests == []


def test_a_publication_that_is_not_approved_or_not_facebook_or_not_farsi_is_refused(fb):
    planned(fb.w)
    p = pub(fb.w, "facebook", "short", "C1")
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.UPLOAD_ONLY)
    assert caught.value.reason == "not_approved"
    other = pub(fb.w, "instagram", "short", "C1")
    with pytest.raises(Refused) as caught:
        asyncio.run(fb.publisher.run(other.id, Mode.UPLOAD_ONLY, confirmation_text(Mode.UPLOAD_ONLY, other.id, PAGE)))
    assert caught.value.reason == "wrong_destination" and fb.m.fake.requests == []


def test_a_file_that_changed_after_a_manual_approval_voids_it_and_nothing_is_sent(fb, monkeypatch):
    monkeypatch.setattr(fb.w.registry.cfg.approval, "default_approved", False)
    p = approved(fb)
    clip = fb.w.clips / "clip_C1.mp4"
    clip.write_bytes(clip.read_bytes() + b"\x00")
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.UPLOAD_ONLY)
    assert caught.value.reason in ("not_approved", "readiness_changed") and fb.m.fake.requests == []
    assert pub(fb.w, "facebook", "short", "C1").status == "voided"


def test_with_default_approvals_a_changed_file_is_approved_again_by_the_default_and_goes_up_with_its_new_hash(fb):
    p = approved(fb)
    old_hash = p.content_hash
    clip = fb.w.clips / "clip_C1.mp4"
    clip.write_bytes(clip.read_bytes() + b"\x00")
    session = go(fb, p, Mode.UPLOAD_ONLY)
    now = pub(fb.w, "facebook", "short", "C1")
    assert session.status == "ready" and now.content_hash != old_hash and session.content_hash == now.content_hash


def test_a_wrong_page_stops_the_upload_before_anything_is_created(fb):
    p = approved(fb)
    fb.m.fake.page_id = "999"
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.UPLOAD_ONLY)
    assert caught.value.reason == "page_mismatch" and not fb.m.fake.calls("POST", "/video_reels") and session_of(fb, p) is None


# --- 3. Reels ---------------------------------------------------------------------------------------------------------------------------------------------------

def test_upload_only_sends_the_file_with_the_page_token_waits_for_processing_and_finishes_nothing(fb):
    p = approved(fb)
    session = go(fb, p, Mode.UPLOAD_ONLY)
    assert session.status == "ready" and session.remote_video_id == "VIDEO1" and pub(fb.w, "facebook", "short", "C1").status == "ready"
    starts = [r for r in fb.m.fake.calls("POST", "/video_reels")]
    assert [fb.m.fake.json_of(r)["upload_phase"] for r in starts] == ["start"]
    upload = fb.m.fake.calls("POST", "/video-upload/VIDEO1")[0]
    assert upload[3]["authorization"] == f"OAuth {PAGE_TOKEN}" and upload[3]["offset"] == "0" and int(upload[3]["file_size"]) == len(upload[4]) and SYSTEM not in str(upload[3])
    assert events(fb.w, "facebook_upload_started") and events(fb.w, "facebook_upload_ready")


def test_save_draft_finishes_as_a_draft_never_public_and_a_draft_is_never_uploaded_again(fb):
    p = approved(fb)
    go(fb, p, Mode.UPLOAD_ONLY)
    session = go(fb, p, Mode.SAVE_DRAFT)
    finish = fb.m.fake.json_of(fb.m.fake.calls("POST", "/video_reels")[-1])
    assert finish["upload_phase"] == "finish" and finish["video_state"] == "DRAFT" and finish["video_id"] == "VIDEO1" and finish["title"] == "A Persian title"
    assert session.status == "drafted" and session.required_action == "publish_in_business_suite" and pub(fb.w, "facebook", "short", "C1").remote_id == "VIDEO1"
    assert "PUBLISHED" not in json.dumps([fb.m.fake.json_of(r) for r in fb.m.fake.calls("POST", "/video_reels")])
    before = len(fb.m.fake.requests)
    for mode in (Mode.UPLOAD_ONLY, Mode.SAVE_DRAFT):
        with pytest.raises(Refused) as caught:
            go(fb, p, mode)
        assert caught.value.reason == "already_drafted"
    assert len(fb.m.fake.requests) == before


def test_save_draft_alone_uploads_and_drafts_in_one_run(fb):
    p = approved(fb)
    assert go(fb, p, Mode.SAVE_DRAFT).status == "drafted"
    assert [fb.m.fake.json_of(r)["upload_phase"] for r in fb.m.fake.calls("POST", "/video_reels")] == ["start", "finish"]


def test_public_publishing_needs_its_own_switch_and_then_one_click_uploads_and_publishes_with_no_typed_text(fb):
    p = approved(fb)
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.PUBLISH, confirm=None)
    assert caught.value.reason == "public_publish_off" and fb.m.fake.requests == []
    fb.m.facebook.allow_public_publish = True
    session = go(fb, p, Mode.PUBLISH, confirm=None)
    assert [fb.m.fake.json_of(r)["upload_phase"] for r in fb.m.fake.calls("POST", "/video_reels")] == ["start", "finish"]
    assert fb.m.fake.json_of(fb.m.fake.calls("POST", "/video_reels")[-1])["video_state"] == "PUBLISHED"
    published = pub(fb.w, "facebook", "short", "C1")
    assert session.status == "published" and published.status == "published" and published.remote_url == "https://www.facebook.com/redsilence/videos/9/" and published.published_at is not None and published.visibility == "public"
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.PUBLISH, confirm=None)
    assert caught.value.reason == "already_uploaded"


def test_the_films_addresses_are_added_to_the_description_of_a_reel_that_is_public_and_to_nothing_else(fb):
    p = approved(fb)
    fb.m.facebook.allow_public_publish = True
    go(fb, p, Mode.PUBLISH, confirm=None)
    assert asyncio.run(fb.publisher.add_film_addresses(fb.w.case.id, "fa")) == []                          # no film has an address yet: nothing to add
    film = pub(fb.w, "youtube", "film", f"V{fb.w.film.id}")
    film.remote_url = "https://www.youtube.com/watch?v=FILM123"
    fb.w.db.commit()
    done = asyncio.run(fb.publisher.add_film_addresses(fb.w.case.id, "fa"))
    assert done == [{"publication_id": p.id, "updated": True, "video_id": "VIDEO1"}]
    assert "https://www.youtube.com/watch?v=FILM123" in fb.m.fake.descriptions[-1] and "#truecrime" in fb.m.fake.descriptions[-1]
    assert pub(fb.w, "facebook", "short", "C1").caption.startswith("https://www.youtube.com/watch?v=FILM123\n\n") and fb.m.fake.descriptions[-1].startswith("https://www.youtube.com/watch?v=FILM123\n")
    assert asyncio.run(fb.publisher.add_film_addresses(fb.w.case.id, "fa")) == [] and len(fb.m.fake.descriptions) == 1       # nothing is sent twice


def test_a_reel_that_goes_public_gets_its_approved_cover_and_the_film_comment_without_a_step_of_the_owner(fb):
    from PIL import Image

    from app.db.models import CoverVariant, PolicyEvent

    cover = fb.w.tmp / "tri_C1_reel_v1.jpg"
    Image.new("RGB", (1080, 1920), (200, 30, 30)).save(cover, quality=90)
    fb.w.db.add(CoverVariant(case_id=fb.w.case.id, language="fa", cover_key="C1", shape="reel", version=1, status="approved", title="t", highlight="h", subject_name="n", file_path=str(cover),
                             file_hash="abc", width=1080, height=1920, elements_json="{}", problems_json="[]", evidence_json="{}", approved_by="owner"))
    fb.w.db.commit()
    p = approved(fb)
    youtube = pub(fb.w, "youtube", "film", f"V{fb.w.film.id}")
    youtube.remote_url = "https://www.youtube.com/watch?v=FILM123"
    fb.w.db.commit()
    fb.m.fake.granted += ["pages_manage_engagement", "pages_read_user_content"]
    fb.m.facebook.allow_public_publish = True
    go(fb, p, Mode.PUBLISH, confirm=None)
    assert len(fb.m.fake.calls("POST", "/VIDEO1/thumbnails")) == 1 and fb.m.fake.comments == ["https://www.youtube.com/watch?v=FILM123"]
    events = [e.decision for e in fb.w.db.query(PolicyEvent).filter_by(case_id=fb.w.case.id).order_by(PolicyEvent.id).all()]
    assert "facebook_cover_set" in events and "facebook_film_comment" in events and not any(e.endswith("_failed") for e in events)
    asyncio.run(fb.publisher.follow_up_reel(pub(fb.w, "facebook", "short", "C1")))
    assert len(fb.m.fake.calls("POST", "/VIDEO1/thumbnails")) == 1 and len(fb.m.fake.comments) == 1                 # each is done once


def test_a_reel_whose_cover_or_comment_cannot_be_set_is_still_public_and_the_failure_is_recorded(fb):
    from app.db.models import PolicyEvent

    p = approved(fb)
    youtube = pub(fb.w, "youtube", "film", f"V{fb.w.film.id}")
    youtube.remote_url = "https://www.youtube.com/watch?v=FILM123"
    fb.w.db.commit()
    fb.m.facebook.allow_public_publish = True
    go(fb, p, Mode.PUBLISH, confirm=None)                                                                   # the token here lacks pages_manage_engagement
    assert pub(fb.w, "facebook", "short", "C1").status == "published"
    assert "facebook_film_comment_failed" in [e.decision for e in fb.w.db.query(PolicyEvent).filter_by(case_id=fb.w.case.id).all()]


def test_the_film_addresses_are_commented_under_a_public_reel_once_each_and_only_with_the_permission(fb):
    p = approved(fb)
    with pytest.raises(Refused) as caught:
        asyncio.run(fb.publisher.add_film_comment(p.id))
    assert caught.value.reason == "not_a_public_reel"                                                       # a Reel that is not public gets no comment
    fb.m.facebook.allow_public_publish = True
    go(fb, p, Mode.PUBLISH, confirm=None)
    assert asyncio.run(fb.publisher.add_film_comment(p.id))["posted"] is False                              # no film has an address yet
    youtube = pub(fb.w, "youtube", "film", f"V{fb.w.film.id}")
    youtube.remote_url = "https://www.youtube.com/watch?v=FILM123"
    fb.w.db.commit()
    with pytest.raises(Refused) as caught:
        asyncio.run(fb.publisher.add_film_comment(p.id))
    assert caught.value.reason == "comment_permission_missing" and fb.m.fake.comments == []
    fb.m.fake.granted += ["pages_manage_engagement"]
    first = asyncio.run(fb.publisher.add_film_comment(p.id))
    assert first == {"publication_id": p.id, "posted": True, "comment_id": "COMMENT1", "addresses": ["https://www.youtube.com/watch?v=FILM123"]}
    assert asyncio.run(fb.publisher.add_film_comment(p.id))["posted"] is False and len(fb.m.fake.comments) == 1       # nothing is posted twice
    facebook = pub(fb.w, "facebook", "film", f"V{fb.w.film.id}")
    facebook.remote_url = "https://www.facebook.com/reel/777/"
    fb.w.db.commit()
    second = asyncio.run(fb.publisher.add_film_comment(p.id))
    assert second["addresses"] == ["https://www.facebook.com/reel/777/"] and fb.m.fake.comments[-1] == "https://www.facebook.com/reel/777/"     # a later address is posted alone


def test_a_reel_is_refused_until_both_films_are_published_and_its_approval_stays(fb):
    p = approved(fb)
    fb.m.facebook.allow_public_publish = True
    fb.w.registry.cfg.scheduling.film_before_shorts = True
    try:
        with pytest.raises(Refused) as caught:
            go(fb, p, Mode.PUBLISH, confirm=None)
    finally:
        fb.w.registry.cfg.scheduling.film_before_shorts = False
    assert caught.value.reason == "film_first" and "youtube/film film is not published yet" in str(caught.value) and fb.m.fake.requests == []
    assert pub(fb.w, "facebook", "short", "C1").status == "publication_approved"                                  # only the order was wrong: the approval is untouched


def test_publish_after_an_upload_only_finishes_the_same_upload_without_a_second_one(fb):
    p = approved(fb)
    fb.m.facebook.allow_public_publish = True
    go(fb, p, Mode.UPLOAD_ONLY)
    assert go(fb, p, Mode.PUBLISH, confirm=None).status == "published"
    assert len(fb.m.fake.calls("POST", "/video-upload/VIDEO1")) == 1 and len(fb.m.fake.calls("POST", "/video_reels")) == 2


def test_a_draft_cannot_be_turned_public_by_this_code(fb):
    p = approved(fb)
    fb.m.facebook.allow_public_publish = True
    go(fb, p, Mode.SAVE_DRAFT)
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.PUBLISH, confirm=None)
    assert caught.value.reason == "already_drafted"


def test_a_lost_answer_to_the_public_finish_blocks_any_second_attempt(fb):
    p = approved(fb)
    fb.m.facebook.allow_public_publish = True
    go(fb, p, Mode.UPLOAD_ONLY)
    fb.m.fake.finish_exception = httpx.ReadTimeout("no answer")
    session = go(fb, p, Mode.PUBLISH, confirm=None)
    assert session.status == "uncertain" and session.required_action == "check_facebook" and pub(fb.w, "facebook", "short", "C1").status == "publish_uncertain"
    fb.m.fake.finish_exception = None
    for mode in (Mode.UPLOAD_ONLY, Mode.PUBLISH):
        with pytest.raises(Refused):
            go(fb, p, mode, confirm=None if mode is Mode.PUBLISH else "right")
    assert len(fb.m.fake.calls("POST", "/video_reels")) == 2                       # start and the one finish, never a retry


def test_the_same_file_is_never_started_twice_a_second_run_resumes_the_same_session(fb):
    p = approved(fb)
    go(fb, p, Mode.UPLOAD_ONLY)
    go(fb, p, Mode.UPLOAD_ONLY)
    assert len(fb.m.fake.calls("POST", "/video_reels")) == 1 and fb.w.db.query(FacebookUploadSession).filter_by(publication_id=p.id).count() == 1


def test_the_same_file_through_another_publication_is_refused_and_the_database_refuses_a_second_active_session(fb):
    p = approved(fb)
    go(fb, p, Mode.UPLOAD_ONLY)
    twin = pub(fb.w, "facebook", "short", "C2")
    twin.content_hash = p.content_hash
    fb.w.db.commit()
    with pytest.raises(Refused) as caught:
        fb.publisher._no_twin(twin)
    assert caught.value.reason == "duplicate_file"
    fb.w.db.add(FacebookUploadSession(publication_id=twin.id, kind="short", status="created", page_id=PAGE, content_hash=p.content_hash))
    with pytest.raises(IntegrityError):
        fb.w.db.commit()
    fb.w.db.rollback()
    fb.w.db.add(FacebookUploadSession(publication_id=p.id, kind="short", status="created", page_id=PAGE, content_hash="other"))
    with pytest.raises(IntegrityError):
        fb.w.db.commit()
    fb.w.db.rollback()


def test_meta_still_processing_leaves_the_session_waiting_and_checking_again_finishes_it(fb):
    p = approved(fb)
    fb.m.fake.statuses = ["processing"]
    session = go(fb, p, Mode.UPLOAD_ONLY)
    assert session.status == "processing" and "still processing" in session.error and pub(fb.w, "facebook", "short", "C1").status == "processing"
    fb.m.fake.statuses = ["ready"]
    done = asyncio.run(fb.publisher.check_processing(p.id))
    assert done.status == "ready"
    with pytest.raises(Refused):
        asyncio.run(fb.publisher.check_processing(p.id))


def test_a_video_meta_cannot_process_fails_the_session_and_the_file_can_be_tried_again(fb):
    p = approved(fb)
    fb.m.fake.statuses = ["error"]
    session = go(fb, p, Mode.UPLOAD_ONLY)
    assert session.status == "failed" and session.required_action == "restart_upload" and "bad" in session.error
    fb.m.fake.statuses = ["ready"]
    assert go(fb, p, Mode.UPLOAD_ONLY).status == "ready"
    assert fb.w.db.query(FacebookUploadSession).filter_by(publication_id=p.id).count() == 2


def test_an_interrupted_reel_upload_resumes_from_the_byte_meta_reports(fb):
    p = approved(fb)
    fb.m.fake.upload_failures = 3                                                  # every attempt of the bounded retry fails
    session = go(fb, p, Mode.UPLOAD_ONLY)
    assert session.status == "interrupted" and session.required_action == "retry" and pub(fb.w, "facebook", "short", "C1").status == "uploading"
    fb.m.fake.bytes_transferred = 0
    assert go(fb, p, Mode.UPLOAD_ONLY).status == "ready"
    assert len(fb.m.fake.calls("POST", "/video_reels")) == 1 and events(fb.w, "facebook_upload_resumed")


def test_a_restart_turns_a_running_upload_into_an_interrupted_one_and_sends_nothing(fb):
    p = approved(fb)
    fb.w.db.add(FacebookUploadSession(publication_id=p.id, kind="short", status="uploading", page_id=PAGE, content_hash="h", total_bytes=10, upload_ref="u"))
    fb.w.db.commit()
    assert FP.sweep_interrupted(fb.w.db) == 1
    session = session_of(fb, p)
    assert session.status == "interrupted" and session.required_action == "resume" and fb.m.fake.requests == []


def test_a_reel_limit_per_day_is_counted_and_stops_before_meta_does(fb):
    p = approved(fb)
    fb.m.facebook.reels.max_posts_per_24h = 0
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.UPLOAD_ONLY)
    assert caught.value.reason == "rate_limit" and fb.m.fake.requests == []


def test_cancelling_forgets_the_upload_and_says_a_video_left_on_facebook_must_be_deleted_there(fb):
    p = approved(fb)
    go(fb, p, Mode.SAVE_DRAFT)
    cancelled = fb.publisher.cancel(p.id)
    assert cancelled.status == "cancelled" and cancelled.required_action == "delete_on_facebook"
    assert pub(fb.w, "facebook", "short", "C1").remote_id is None and pub(fb.w, "facebook", "short", "C1").status == "publication_approved"
    with pytest.raises(Refused):
        fb.publisher.cancel(p.id)
    assert go(fb, p, Mode.UPLOAD_ONLY).status == "ready"                           # the file can go again once the first draft is forgotten


# --- 4. long films ---------------------------------------------------------------------------------------------------------------------------------------------

def film_world(fb, monkeypatch) -> Publication:
    meet_every_film_requirement(fb.w, monkeypatch)
    p = approved(fb, "film")
    fb.m.fake.total = fb.w.film_file.stat().st_size
    return p


def phases(fb) -> list[str]:
    """The upload phases sent to the Page's video endpoint, in order."""
    return [fb.m.fake.form_of(r)["upload_phase"].decode() for r in fb.m.fake.calls("POST", f"/{PAGE}/videos")]


def test_a_film_goes_up_in_chunks_of_the_size_meta_asks_for_and_nothing_is_created_on_the_page(fb, monkeypatch):
    p = film_world(fb, monkeypatch)
    session = go(fb, p, Mode.UPLOAD_ONLY)
    size = fb.w.film_file.stat().st_size
    assert session.status == "ready" and session.file_handle == "VIDEO9" and session.upload_ref == "S1" and session.bytes_sent == size
    offsets = fb.m.fake.chunks
    assert offsets[0][0] == 0 and sum(n for _, n in offsets) == size and all(o2 == o1 + n1 for (o1, n1), (o2, _) in zip(offsets, offsets[1:], strict=False)) and len(offsets) > 1
    assert phases(fb) == ["start", *["transfer"] * len(offsets)] and pub(fb.w, "facebook", "film", f"V{fb.w.film.id}").remote_id is None      # no video is made before the finish call


def test_a_film_draft_is_finished_hidden_and_followed_until_meta_is_ready(fb, monkeypatch):
    p = film_world(fb, monkeypatch)
    fb.m.fake.statuses = ["processing", "ready"]
    session = go(fb, p, Mode.SAVE_DRAFT)
    assert phases(fb)[0] == "start" and phases(fb)[-1] == "finish" and phases(fb).count("finish") == 1 and {r[1] for r in fb.m.fake.calls("POST", f"/{PAGE}/videos")} == {"graph-video.facebook.com"}
    assert session.status == "drafted" and session.remote_video_id == "VIDEO9" and pub(fb.w, "facebook", "film", f"V{fb.w.film.id}").remote_id == "VIDEO9"
    assert pub(fb.w, "facebook", "film", f"V{fb.w.film.id}").status == "ready"


def test_the_approved_film_cover_goes_along_with_the_film_as_its_thumbnail(fb, monkeypatch):
    from PIL import Image

    from app.db.models import CoverVariant

    cover = fb.w.tmp / "tri_long_film_v1.jpg"
    Image.new("RGB", (1920, 1080), (200, 30, 30)).save(cover, quality=90)
    fb.w.db.add(CoverVariant(case_id=fb.w.case.id, language="fa", cover_key="long", shape="film", version=1, status="approved", title="t", highlight="h", subject_name="n", file_path=str(cover),
                             file_hash="abc", width=1920, height=1080, elements_json="{}", problems_json="[]", evidence_json="{}", approved_by="owner"))
    fb.w.db.commit()
    p = film_world(fb, monkeypatch)
    sent: list[httpx.Request] = []
    original = fb.m.transport.handler

    def spy(request):
        sent.append(request)
        return original(request)

    fb.publisher.client.transport = httpx.MockTransport(spy)
    go(fb, p, Mode.SAVE_DRAFT)
    finish = next(r for r in sent if r.url.path.endswith(f"/{PAGE}/videos") and b'name="upload_phase"\r\n\r\nfinish' in r.content)
    assert finish.headers["content-type"].startswith("multipart/form-data") and b'name="thumb"' in finish.content and cover.read_bytes() in finish.content
    assert pub(fb.w, "facebook", "film", f"V{fb.w.film.id}").cover_ref.startswith("film:")


def test_the_film_draft_request_carries_published_false_and_the_draft_type_and_the_page_token(fb, monkeypatch):
    p = film_world(fb, monkeypatch)
    sent: list[httpx.Request] = []
    original = fb.m.transport.handler

    def spy(request):
        sent.append(request)
        return original(request)

    fb.publisher.client.transport = httpx.MockTransport(spy)
    go(fb, p, Mode.SAVE_DRAFT)
    finish = next(r for r in sent if r.url.path.endswith(f"/{PAGE}/videos") and b'name="upload_phase"\r\n\r\nfinish' in r.content)
    form = fb.m.fake.form_of(finish.content, finish.headers["content-type"])
    assert not dict(finish.url.params) and finish.headers["content-type"].startswith("multipart/form-data")                       # the fields travel in the form, not in the address
    assert {k: form[k].decode() for k in ("published", "unpublished_content_type", "upload_session_id", "title")} == {"published": "false", "unpublished_content_type": "DRAFT", "upload_session_id": "S1", "title": "A Persian title"}
    assert finish.headers["authorization"] == f"Bearer {PAGE_TOKEN}"


def test_a_film_larger_or_longer_than_metas_limit_is_refused_before_any_byte(fb, monkeypatch):
    p = film_world(fb, monkeypatch)
    fb.m.fake.limits = {"length": 14460, "size": 100}
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.UPLOAD_ONLY)
    assert caught.value.reason == "film_too_large" and not fb.m.fake.calls("POST", f"/{PAGE}/videos")
    fb.m.fake.limits = {"length": 1, "size": 28633115306}
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.UPLOAD_ONLY)
    assert caught.value.reason == "film_too_long" and not fb.m.fake.calls("POST", f"/{PAGE}/videos")


def test_an_interrupted_film_upload_resumes_from_the_last_confirmed_offset_and_never_resends_what_meta_has(fb, monkeypatch):
    p = film_world(fb, monkeypatch)
    fb.m.fake.fail_after_chunks = 1                                                 # the first chunk is stored, then the connection drops
    session = go(fb, p, Mode.UPLOAD_ONLY)
    assert session.status == "interrupted" and session.upload_ref == "S1" and session.bytes_sent == 4096 and len(fb.m.fake.chunks) == 1
    fb.m.fake.fail_after_chunks = None
    session = go(fb, p, Mode.UPLOAD_ONLY)
    assert session.status == "ready" and [o for o, _ in fb.m.fake.chunks][:2] == [0, 4096] and phases(fb).count("start") == 1      # the resume goes on at 4096 in the same upload


def test_the_emergency_stop_pauses_a_film_upload_between_chunks_and_it_can_be_resumed(fb, monkeypatch):
    p = film_world(fb, monkeypatch)
    stops = iter([False, False, True])
    monkeypatch.setattr(fb.publisher.registry, "_stopped", lambda: next(stops, True))
    session = go(fb, p, Mode.UPLOAD_ONLY)
    assert session.status == "interrupted" and session.required_action == "resume" and "Emergency Stop" in session.error and len(fb.m.fake.chunks) < 10
    monkeypatch.setattr(fb.publisher.registry, "_stopped", lambda: False)
    assert go(fb, p, Mode.UPLOAD_ONLY).status == "ready" and phases(fb).count("start") == 1


# --- 5. the interface and Monitoring -------------------------------------------------------------------------------------------------------------------------

def test_the_api_shows_the_connection_and_the_uploads_without_any_secret_and_refuses_what_is_not_allowed(fb, client, monkeypatch):
    p = approved(fb)
    monkeypatch.setattr("app.meta_publishing.facebook_api._publisher", lambda db, language=None: FacebookPublisher(db, fb.m.instagram, fb.m.facebook, client=fb.m.client, tokens=fb.m.tokens, sleep=fb.m.client.sleep))
    shown = client.get(f"/api/facebook-publishing/cases/{fb.w.case.id}/fa/uploads")
    assert shown.status_code == 200
    entry = shown.json()["uploads"][str(p.id)]
    assert entry["confirmations"]["UPLOAD_ONLY"] == f"UPLOAD FACEBOOK {p.id} {PAGE}" and entry["session"] is None and shown.json()["allow_public_publish"] is False
    refused = client.post(f"/api/facebook-publishing/publications/{p.id}/upload", json={"mode": "UPLOAD_ONLY", "confirm": "nope"})
    assert refused.status_code == 409 and refused.json()["detail"]["reason"] == "confirmation_missing"
    assert client.post(f"/api/facebook-publishing/publications/{p.id}/upload", json={"mode": "DRY_RUN"}).status_code == 422
    plan = client.get(f"/api/facebook-publishing/publications/{p.id}/plan")
    assert plan.status_code == 200 and plan.json()["steps"][0]["name"] == "Checks" and fb.m.fake.requests == []
    fb.w.db.add(FacebookUploadSession(publication_id=p.id, kind="film", status="ready", page_id=PAGE, content_hash="h", upload_ref="upload:SECRETREF", file_handle="SECRETHANDLE"))
    fb.w.db.commit()
    text = client.get(f"/api/facebook-publishing/cases/{fb.w.case.id}/fa/uploads").text
    assert "SECRETREF" not in text and "SECRETHANDLE" not in text and SYSTEM not in text and PAGE_TOKEN not in text


def test_monitoring_shows_a_draft_waiting_a_stopped_upload_an_unknown_finish_and_a_failed_connection_test(fb, meta, monkeypatch):
    p = approved(fb)
    go(fb, p, Mode.SAVE_DRAFT)
    titles = [i.title for i in publishing_issues(fb.w.db, fb.w.case.id)]
    assert any("A Facebook draft waits for you" in t for t in titles)
    row = session_of(fb, p)
    row.status, row.error = "uncertain", "no answer"
    fb.w.db.commit()
    assert any("finish outcome unknown" in i.title for i in publishing_issues(fb.w.db, fb.w.case.id))
    row.status, row.required_action = "interrupted", "resume"
    fb.w.db.commit()
    assert any("The Facebook upload needs you" in i.title for i in publishing_issues(fb.w.db, fb.w.case.id))
    meta.fake.granted = []
    with pytest.raises(ConnectError):
        asyncio.run(connection(meta).test())
    failed = connection(meta).status()
    original = FacebookConnection.status
    monkeypatch.setattr(FacebookConnection, "status", lambda self: failed if self.f.language == "fa" else original(self))
    problems = facebook_connection_issues()
    assert [i.severity for i in problems] == ["problem"] and "last connection test failed" in problems[0].title


def test_the_facebook_settings_reuse_the_instagram_token_and_graph_settings_and_hold_no_credential():
    config = meta_config()
    assert config.facebook.page.page_id == "1524968800689127" and config.facebook.page.linked_instagram_id == config.instagram.account.ig_account_id
    assert config.facebook.allow_public_publish is False                              # public publishing stays off; live uploads are the owner's switch for the controlled tests
    assert not any("token" in name or "secret" in name for name in type(config.facebook).model_fields)


# --- 6. no approval to press: approvals are given by default, publishing follows the plan or one click ------------------------------------------------------

def test_a_publication_with_no_blocker_is_approved_by_default_when_it_is_uploaded_and_the_events_say_so(fb):
    planned(fb.w)
    p = pub(fb.w, "facebook", "short", "C1")
    fb.w.registry.update(p.id, metadata={"title": "A Persian title", "hashtags": [], "keywords": [], "cta": ""})
    assert pub(fb.w, "facebook", "short", "C1").status == "draft"
    go(fb, p, Mode.UPLOAD_ONLY)
    row = pub(fb.w, "facebook", "short", "C1")
    assert fb.w.registry.approval_current(row)
    assert {json.loads(e.context_json)["actor"] for e in events(fb.w, "content_approved") + events(fb.w, "publication_approved")} == {"default"}


def test_the_default_approval_never_goes_around_a_blocker(fb):
    planned(fb.w)
    p = pub(fb.w, "facebook", "short", "C1")                                          # no platform text yet
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.UPLOAD_ONLY)
    assert caught.value.reason == "not_approved" and "metadata" in str(caught.value) and fb.m.fake.requests == []
    assert pub(fb.w, "facebook", "short", "C1").status == "draft"


def test_the_owner_can_still_require_a_manual_approval(fb, monkeypatch):
    monkeypatch.setattr(fb.w.registry.cfg.approval, "default_approved", False)
    planned(fb.w)
    p = pub(fb.w, "facebook", "short", "C1")
    fb.w.registry.update(p.id, metadata={"title": "A Persian title", "hashtags": [], "keywords": [], "cta": ""})
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.UPLOAD_ONLY)
    assert caught.value.reason == "not_approved" and "approve" in str(caught.value).lower()


def make_due(fb, p: Publication, *, minutes_ago: int = 5, window: int = 55) -> None:
    from datetime import timedelta

    from app.utils import utc_now

    row = fb.w.db.get(Publication, p.id)
    row.scheduled_at, row.window_ends_at = utc_now() - timedelta(minutes=minutes_ago), utc_now() + timedelta(minutes=window)
    fb.w.db.commit()


def run_due(fb):
    return asyncio.run(automatic.publish_due(fb.w.db, publisher_factory=lambda db, language=None: fb.publisher))


def test_the_automatic_publisher_does_nothing_while_it_is_switched_off(fb):
    p = approved(fb)
    make_due(fb, p)
    fb.m.facebook.allow_public_publish = True
    assert publishing_config().automatic_publishing.enabled is False and run_due(fb) == [] and fb.m.fake.requests == []


def test_a_due_publication_is_published_at_its_time_by_the_automatic_publisher_when_it_is_on(fb, monkeypatch):
    monkeypatch.setattr(publishing_config().automatic_publishing, "enabled", True)
    fb.m.facebook.allow_public_publish = True
    due, not_yet = approved(fb), pub(fb.w, "facebook", "short", "C2")
    fb.w.registry.update(not_yet.id, metadata={"title": "Later", "hashtags": [], "keywords": [], "cta": ""})
    make_due(fb, due)
    assert [p.id for p in automatic.due(fb.w.db)] == [due.id]
    assert run_due(fb) == [due.id]
    assert pub(fb.w, "facebook", "short", "C1").status == "published" and session_of(fb, due).status == "published"
    assert pub(fb.w, "facebook", "short", "C2").status != "published" and run_due(fb) == []                 # one publication, once


def test_the_automatic_publisher_leaves_alone_what_is_outside_its_window_or_not_facebook_or_stopped(fb, monkeypatch):
    monkeypatch.setattr(publishing_config().automatic_publishing, "enabled", True)
    fb.m.facebook.allow_public_publish = True
    p = approved(fb)
    make_due(fb, p, minutes_ago=120, window=-60)                                      # the window closed an hour ago
    assert automatic.due(fb.w.db) == [] and run_due(fb) == []
    make_due(fb, p)
    other = pub(fb.w, "instagram", "short", "C1")
    make_due(fb, other)
    assert [x.platform for x in automatic.due(fb.w.db)] == ["facebook"]
    monkeypatch.setattr(fb.publisher.registry, "_stopped", lambda: True)
    monkeypatch.setattr(automatic.PublicationRegistry, "_stopped", lambda self: True)
    assert run_due(fb) == [] and fb.m.fake.requests == []


def test_a_refused_automatic_publication_is_recorded_once_and_never_retried_at_every_poll(fb, monkeypatch):
    monkeypatch.setattr(publishing_config().automatic_publishing, "enabled", True)
    automatic._reported.clear()
    p = approved(fb)
    make_due(fb, p)
    assert fb.m.facebook.allow_public_publish is False
    for _ in range(3):
        assert run_due(fb) == []
    assert len(events(fb.w, "automatic_blocked")) == 1 and fb.m.fake.requests == []


def test_the_automatic_publisher_is_off_in_the_shipped_configuration():
    assert publishing_config().automatic_publishing.enabled is False and meta_config().facebook.allow_public_publish is False and publishing_config().approval.default_approved is True


def test_meta_starts_processing_only_at_the_finish_step_so_a_complete_upload_is_ready_before_it_and_the_draft_waits_for_processing_after_it(fb):
    """What the first real upload showed: after the upload Meta says upload_complete with processing not started, and processing starts when the finish step is called."""
    p = approved(fb)
    fb.m.fake.statuses = ["upload_complete"]
    session = go(fb, p, Mode.UPLOAD_ONLY)
    assert session.status == "ready" and session.video_status == "upload_complete" and not fb.m.fake.calls("POST", "/video_reels")[1:]
    session = go(fb, p, Mode.SAVE_DRAFT)
    assert session.status == "processing" and "still processing" in session.error                    # finished, and Meta has not processed it yet
    assert fb.m.fake.json_of(fb.m.fake.calls("POST", "/video_reels")[-1])["video_state"] == "DRAFT"
    fb.m.fake.statuses = ["ready"]
    done = asyncio.run(fb.publisher.check_processing(p.id))
    assert done.status == "drafted" and pub(fb.w, "facebook", "short", "C1").remote_id == "VIDEO1"


def test_a_persian_publication_waits_for_the_owners_own_approval_but_another_language_does_not(fb, monkeypatch):
    monkeypatch.setattr(fb.w.registry.cfg.approval, "manual_languages", ["fa"])
    planned(fb.w)
    p = pub(fb.w, "facebook", "short", "C1")
    fb.w.registry.update(p.id, metadata={"title": "A Persian title", "hashtags": [], "keywords": [], "cta": ""})
    planned(fb.w)
    assert pub(fb.w, "facebook", "short", "C1").status == "draft" and not events(fb.w, "publication_approved")          # the plan did not approve it
    assert any("needs your approval" in b for b in fb.publisher.readiness(p.id))
    with pytest.raises(Refused) as caught:
        go(fb, p, Mode.UPLOAD_ONLY)
    assert caught.value.reason == "not_approved" and "needs your approval" in str(caught.value) and fb.m.fake.requests == []
    approve_both(fb.w, pub(fb.w, "facebook", "short", "C1"))                                                           # the owner's click
    assert go(fb, p, Mode.UPLOAD_ONLY).status == "ready"
    assert {json.loads(e.context_json)["actor"] for e in events(fb.w, "publication_approved")} == {"owner"}
    assert fb.w.registry.approved_by_default(pub(fb.w, "facebook", "short", "C1")) is False


def test_the_shipped_configuration_approves_persian_by_hand_and_every_other_language_by_default():
    assert publishing_config().approval.manual_languages == ["fa"] and publishing_config().approval.default_approved is True


def test_the_api_reads_which_thumbnail_meta_uses_without_changing_anything_and_without_a_secret(fb, client, monkeypatch):
    monkeypatch.setattr("app.meta_publishing.facebook_api._publisher", lambda db, language=None: FacebookPublisher(db, fb.m.instagram, fb.m.facebook, client=fb.m.client, tokens=fb.m.tokens, sleep=fb.m.client.sleep))
    p = approved(fb)
    none_yet = client.get(f"/api/facebook-publishing/publications/{p.id}/thumbnails")
    assert none_yet.status_code == 409 and none_yet.json()["detail"]["reason"] == "no_upload" and fb.m.fake.requests == []
    go(fb, p, Mode.UPLOAD_ONLY)
    before = len(fb.m.fake.requests)
    shown = client.get(f"/api/facebook-publishing/publications/{p.id}/thumbnails")
    assert shown.status_code == 200
    body = shown.json()
    assert body["video_id"] == "VIDEO1" and body["preferred_id"] == "T1" and [t["id"] for t in body["thumbnails"]] == ["T1", "T2"] and body["checked_at"]
    assert {r[0] for r in fb.m.fake.requests[before:]} == {"GET"} and not fb.m.fake.calls("POST", "/thumbnails")
    assert SYSTEM not in shown.text and PAGE_TOKEN not in shown.text
    fb.m.fake.error = {"message": f"Permissions error ({PAGE_TOKEN})", "code": 200}
    failed = client.get(f"/api/facebook-publishing/publications/{p.id}/thumbnails")
    assert failed.status_code == 502 and failed.json()["detail"]["reason"] == "permission_missing" and PAGE_TOKEN not in failed.text


def test_a_text_corrected_after_the_video_was_finished_on_facebook_is_flagged_because_the_draft_keeps_the_old_one(fb, client, monkeypatch):
    monkeypatch.setattr("app.meta_publishing.facebook_api._publisher", lambda db, language=None: FacebookPublisher(db, fb.m.instagram, fb.m.facebook, client=fb.m.client, tokens=fb.m.tokens, sleep=fb.m.client.sleep))
    p = approved(fb)
    url = f"/api/facebook-publishing/cases/{fb.w.case.id}/fa/uploads"
    go(fb, p, Mode.UPLOAD_ONLY)
    fb.w.registry.update(p.id, caption="متن تازه")                                           # corrected before the finish: the finish will carry it
    assert client.get(url).json()["uploads"][str(p.id)]["text_stale"] is False
    go(fb, p, Mode.SAVE_DRAFT)
    assert session_of(fb, p).metadata_hash and client.get(url).json()["uploads"][str(p.id)]["text_stale"] is False
    assert pub(fb.w, "facebook", "short", "C1").status == "ready"
    fb.w.registry.update(p.id, caption="متن اصلاح‌شده بعد از ساخت پیش‌نویس")                    # corrected after: allowed, and the draft keeps the old text
    assert pub(fb.w, "facebook", "short", "C1").caption.startswith("متن اصلاح") and client.get(url).json()["uploads"][str(p.id)]["text_stale"] is True


# --- the cover of an uploaded Reel -------------------------------------------------------------------------------------------------------------------------------
def test_the_cover_call_sends_the_picture_as_a_multipart_source_marked_preferred_and_refuses_a_picture_that_is_too_big(tmp_path):
    import asyncio

    import httpx

    from app.meta_publishing.config import meta_config
    from app.meta_publishing.facebook_client import FacebookClient
    from app.meta_publishing.graph import MetaError

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/thumbnails") and request.method == "POST":
            seen["type"], seen["body"] = request.headers["content-type"], request.content
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"access_token": "PAGE-TOKEN", "id": "1"})

    class Tokens:
        def get(self):
            return "T"

        def redact(self, text):
            return text.replace("PAGE-TOKEN", "***")

    cfg = meta_config()
    client = FacebookClient(cfg.instagram, cfg.facebook, Tokens(), transport=httpx.MockTransport(handler))
    client._page_token = "PAGE-TOKEN"
    picture = tmp_path / "tri_C1_reel_v1.jpg"
    picture.write_bytes(b"\xff\xd8\xff-picture-bytes")
    asyncio.run(client.set_thumbnail("555", picture))
    assert seen["type"].startswith("multipart/form-data; boundary=") and b'name="is_preferred"' in seen["body"] and b"true" in seen["body"]
    assert b'name="source"; filename="tri_C1_reel_v1.jpg"' in seen["body"] and b"-picture-bytes" in seen["body"]
    big = tmp_path / "big.jpg"
    big.write_bytes(b"x" * (cfg.facebook.cover.max_mb * 1024 * 1024 + 1))
    with pytest.raises(MetaError, match="at most"):
        asyncio.run(client.set_thumbnail("555", big))


def test_the_cover_needs_the_two_extra_permissions_and_the_config_names_them_and_their_source():
    from app.meta_publishing.config import meta_config

    cover = meta_config().facebook.cover
    assert {"pages_read_user_content", "pages_manage_engagement"} <= set(cover.required_permissions) and cover.max_mb == 10 and "thumbnails" in cover.source
    assert not {"pages_read_user_content", "pages_manage_engagement"} & set(meta_config().facebook.required_permissions)       # the upload itself never waits for them


def test_a_reel_cover_is_set_only_on_an_uploaded_video_with_the_extra_permissions_and_only_for_a_usable_reel_picture(fb, tmp_path):
    from app.db.models import CoverVariant, PolicyEvent

    p = approved(fb)
    picture = tmp_path / "tri_C1_reel_v1.jpg"
    picture.write_bytes(b"\xff\xd8\xff-cover")

    numbers = iter(range(1, 20))

    def variant(**kw) -> CoverVariant:
        row = CoverVariant(case_id=fb.w.case.id, language="fa", cover_key="C1", shape="reel", version=next(numbers), status="proposed", title="t", highlight="h", subject_name="n", file_path=str(picture), file_hash="abc",
                           width=1080, height=1920, elements_json="{}", problems_json="[]", evidence_json="{}", **kw)
        fb.w.db.add(row)
        fb.w.db.commit()
        return row

    good = variant()
    with pytest.raises(Refused) as caught:
        asyncio.run(fb.publisher.set_cover(p.id, good.id))
    assert caught.value.reason == "no_upload"
    go(fb, p, Mode.UPLOAD_ONLY)
    with pytest.raises(Refused) as caught:
        asyncio.run(fb.publisher.set_cover(p.id, good.id))                                           # the token has only the upload permissions
    assert caught.value.reason == "cover_permission_missing" and "pages_read_user_content" in str(caught.value) and not fb.m.fake.calls("POST", "/thumbnails")
    fb.m.fake.granted += list(fb.m.facebook.cover.required_permissions)
    feed = variant()
    feed.shape = "feed"
    wrong_language = variant()
    wrong_language.language = "en"
    flawed = variant()
    flawed.problems_json = json.dumps(["a face is cut by the edge"])
    fb.w.db.commit()
    for row, reason in ((feed, "not_a_reel_cover"), (wrong_language, "unknown_cover"), (flawed, "cover_has_problems")):
        with pytest.raises(Refused) as caught:
            asyncio.run(fb.publisher.set_cover(p.id, row.id))
        assert caught.value.reason == reason
    assert not fb.m.fake.calls("POST", "/thumbnails")                                                  # nothing was sent for any refused cover
    done = asyncio.run(fb.publisher.set_cover(p.id, good.id))
    assert done["video_id"] == "VIDEO1" and done["preferred_id"] == "T1" and len(fb.m.fake.calls("POST", "/thumbnails")) == 1
    events = fb.w.db.query(PolicyEvent).filter_by(case_id=fb.w.case.id, decision="facebook_cover_set").all()
    assert len(events) == 1 and json.loads(events[0].context_json)["publication_id"] == p.id and json.loads(events[0].context_json)["cover_variant_id"] == good.id
    assert SYSTEM not in str(done) and PAGE_TOKEN not in str(done)
