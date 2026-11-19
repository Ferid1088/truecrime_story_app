# ruff: noqa: F811  (the `world` fixture is imported from test_publication_registry and used by name)
"""The real YouTube integration, against a fake Google (an httpx MockTransport that speaks the documented OAuth, resumable-upload, videos.list, thumbnails and captions protocols).
No network and no real credentials: these tests prove the application's behaviour (protocol, safety controls, persistence, recovery); the live behaviour of Google itself is
verified separately and only with the owner's credentials."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from test_publication_registry import events, planned, pub, world  # noqa: F401
from test_publication_safeguards import approve_both

from app.db.models import PolicyEvent, Publication, PublicationAccount, YouTubeUploadSession
from app.publishing.monitoring import publishing_issues
from app.services.case_control import set_global_stop
from app.youtube_publishing import uploader as U
from app.youtube_publishing.channels import ChannelError, YouTubeChannels
from app.youtube_publishing.client import SessionExpired, YouTubeClient, YouTubeError
from app.youtube_publishing.config import youtube_config
from app.youtube_publishing.credentials import ClientFile
from app.youtube_publishing.oauth import OAuthError, YouTubeOAuth
from app.youtube_publishing.uploader import Refused, YouTubeUploader, confirmation_text

CHANNEL = "UC3bIVrg7dD6B9_JlSHduPLw"
SECRET_TOKENS = ("ya29.ACCESS-SECRET", "1//REFRESH-SECRET", "CLIENT-SECRET-VALUE")


class FakeGoogle:
    """Just enough of Google: the token endpoint, channels.list, the resumable upload session, videos.list, thumbnails.set and captions.insert."""

    def __init__(self, *, channel: str = CHANNEL, scopes: list[str] | None = None, refresh_token: str | None = SECRET_TOKENS[1]):
        self.channel, self.refresh_token = channel, refresh_token
        self.scopes = scopes if scopes is not None else youtube_config().oauth.scopes
        self.requests: list[tuple[str, str]] = []
        self.sessions: dict[str, bytearray] = {}
        self.resources: list[dict] = []
        self.session_headers: list[dict] = []
        self.thumbnails, self.captions, self.revoked = [], [], []
        self.processing = [{"processingStatus": "processing", "processingProgress": {"partsTotal": 10, "partsProcessed": 5}}, {"processingStatus": "succeeded"}]
        self.privacy = "private"
        self.fail_put_after: int | None = None            # a dropped connection once this many bytes are stored
        self.queue: list[int] = []                         # status codes answered to the next PUTs
        self.invalid_grant = False
        self.expired_sessions: set[str] = set()
        self.video_id = "VID123abc"
        self.gone = False
        self.updates: list[dict] = []
        self.snippet_updates: list[dict] = []
        self.category = "25"
        self.refuse_public = False

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        url, path = request.url, request.url.path
        self.requests.append((request.method, f"{url.host}{path}"))
        if url.host == "oauth2.googleapis.com" and path == "/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["refresh_token"]:
                if self.invalid_grant:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": "ya29.REFRESHED-ACCESS", "expires_in": 3600})
            body = {"access_token": SECRET_TOKENS[0], "expires_in": 3600, "scope": " ".join(self.scopes)}
            if self.refresh_token:
                body["refresh_token"] = self.refresh_token
            return httpx.Response(200, json=body)
        if path == "/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        if path == "/youtube/v3/channels":
            return httpx.Response(200, json={"items": [{"id": self.channel, "snippet": {"title": "Red Silence"}}]})
        if path == "/upload/youtube/v3/videos" and request.method == "POST":
            assert parse_qs(url.query.decode())["uploadType"] == ["resumable"]
            self.resources.append(json.loads(request.content))
            self.session_headers.append(dict(request.headers))
            location = f"https://upload.example/session/{len(self.sessions) + 1}?upload_id=SECRETSESSION{len(self.sessions) + 1}"
            self.sessions[location] = bytearray()
            return httpx.Response(200, headers={"Location": location})
        if url.host == "upload.example":
            return self._session(request, str(url))
        if path == "/youtube/v3/videos" and request.method == "PUT":
            body = json.loads(request.content)
            if parse_qs(url.query.decode()).get("part") == ["snippet"]:
                self.category = body["snippet"]["categoryId"]
                self.snippet_updates.append(body)
                return httpx.Response(200, json={"id": body["id"], "snippet": body["snippet"]})
            if self.refuse_public:
                return httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}], "message": "no"}})
            self.privacy = body["status"]["privacyStatus"]
            self.updates.append(body)
            return httpx.Response(200, json={"id": body["id"], "status": body["status"]})
        if path == "/youtube/v3/videos":
            step = self.processing[0] if len(self.processing) == 1 else self.processing.pop(0)
            if self.gone:
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"items": [{"id": self.video_id, "snippet": {"title": "T", "description": "D\nhttps://x", "tags": ["a"], "defaultLanguage": "fa", "categoryId": self.category},
                                                        "status": {"privacyStatus": self.privacy, "uploadStatus": "processed" if step["processingStatus"] == "succeeded" else "uploaded"},
                                                        "processingDetails": step}]})
        if path == "/upload/youtube/v3/thumbnails/set":
            self.thumbnails.append(parse_qs(url.query.decode())["videoId"][0])
            return httpx.Response(200, json={})
        if path == "/upload/youtube/v3/captions":
            self.captions.append(request.content)
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": {"message": "unexpected"}})

    def _session(self, request: httpx.Request, key: str) -> httpx.Response:
        if request.method != "PUT":
            return httpx.Response(405)
        if key in self.expired_sessions:
            return httpx.Response(404)
        if self.queue:
            return httpx.Response(self.queue.pop(0), json={"error": {"errors": [{"reason": "quotaExceeded"}], "message": "quota"}})
        stored = self.sessions[key]
        header = request.headers["content-range"]
        spec, total = header.split(" ")[1].split("/")
        total = int(total)
        if spec == "*":
            if len(stored) == total:
                return httpx.Response(201, json=self._video())
            return httpx.Response(308, headers={"Range": f"bytes=0-{len(stored) - 1}"} if stored else {})
        first, last = (int(x) for x in spec.split("-"))
        if self.fail_put_after is not None and len(stored) >= self.fail_put_after:
            self.fail_put_after = None
            raise httpx.ConnectError("connection dropped")
        assert first == len(stored), "chunks must continue exactly where Google stopped (overlap or a gap loses the upload)"
        stored.extend(request.content)
        assert len(request.content) == last - first + 1
        if len(stored) == total:
            return httpx.Response(201, json=self._video())
        return httpx.Response(308, headers={"Range": f"bytes=0-{len(stored) - 1}"})

    def _video(self) -> dict:
        return {"id": self.video_id, "status": {"privacyStatus": self.privacy, "uploadStatus": "uploaded"}}


async def _nap(_seconds):
    return None


@pytest.fixture
def google(tmp_path, monkeypatch, db_session):
    """A fake Google, an OAuth client file and token file in tmp, live calls on, small chunks (so that an upload takes several)."""
    cfg = youtube_config().model_copy(deep=True)
    client_file = tmp_path / "client_secret.json"
    client_file.write_text(json.dumps({"web": {"client_id": "client-id-1", "client_secret": SECRET_TOKENS[2], "redirect_uris": [cfg.redirect_uri]}}))
    cfg.oauth.client_secret_file, cfg.oauth.token_file = str(client_file), str(tmp_path / "token_{language}.json")
    cfg.live_enabled, cfg.upload.chunk_bytes, cfg.upload.processing_poll_seconds, cfg.upload.retry_base_seconds = True, 2048, 0, 0
    cfg.upload.public_after_processing = False                 # most tests look at the private end state; the public step has its own tests
    monkeypatch.setattr("app.youtube_publishing.client.CHUNK_STEP", 1024)
    fake = FakeGoogle()
    oauth = YouTubeOAuth("fa", CHANNEL, cfg, http=fake.client)
    yield type("Google", (), {"cfg": cfg, "fake": fake, "oauth": oauth, "client": YouTubeClient(oauth, cfg, http=fake.client, sleep=_nap), "tokens": oauth.tokens})
    db_session.rollback()
    db_session.query(YouTubeUploadSession).delete()
    db_session.commit()


def connect(g) -> None:
    url = g.oauth.authorization_url("/cases/15?tab=Publishing")
    state = parse_qs(urlparse(url).query)["state"][0]
    asyncio.run(g.oauth.complete("auth-code", state))


def uploader(w, g) -> YouTubeUploader:
    return YouTubeUploader(w.db, g.oauth, g.cfg, client=g.client, registry=w.registry, sleep=_nap)


def approved_short(w, key: str = "C1") -> Publication:
    planned(w)
    p = pub(w, "youtube", "short", key)
    w.registry.update(p.id, metadata={"title": "A Persian title", "hashtags": ["#truecrime"], "keywords": ["case", "film"], "cta": ""})
    approve_both(w, pub(w, "youtube", "short", key))
    return pub(w, "youtube", "short", key)


def run(w, g, p: Publication, confirm: str | None = "right"):
    text = confirmation_text(p.id, CHANNEL) if confirm == "right" else confirm
    return asyncio.run(uploader(w, g).run(p.id, text))


# --- 1. the account connection -----------------------------------------------------------------------------------------------------------------------------

def test_the_authorization_address_asks_for_offline_access_with_pkce_and_exactly_the_configured_scopes(google):
    query = parse_qs(urlparse(google.oauth.authorization_url("/x")).query)
    assert query["access_type"] == ["offline"] and query["prompt"] == ["select_account consent"] and query["code_challenge_method"] == ["S256"]
    assert query["scope"] == [" ".join(google.cfg.oauth.scopes)] and query["redirect_uri"] == [google.cfg.redirect_uri] and query["client_id"] == ["client-id-1"]
    assert "client_secret" not in query


def test_without_a_client_file_connecting_says_what_to_set_up(google):
    ClientFile(google.cfg.oauth).path.unlink()
    with pytest.raises(OAuthError) as caught:
        google.oauth.authorization_url()
    assert caught.value.reason == "not_configured" and google.oauth.status()["client_configured"] is False


def test_a_client_that_does_not_list_the_redirect_address_is_refused_before_google_is_asked(google):
    ClientFile(google.cfg.oauth).path.write_text(json.dumps({"web": {"client_id": "a", "client_secret": "b", "redirect_uris": ["http://elsewhere/cb"]}}))
    with pytest.raises(OAuthError, match="authorized redirect URI"):
        google.oauth.authorization_url()


def test_connecting_verifies_the_channel_stores_the_tokens_owner_only_and_never_shows_them(google):
    connect(google)
    status = google.oauth.status()
    assert status["connected"] and status["channel_matches"] and status["scopes_ok"] and status["channel_id"] == CHANNEL and status["channel_title"] == "Red Silence"
    assert not any(secret in json.dumps(status) for secret in SECRET_TOKENS)
    path = google.tokens.path
    assert path.stat().st_mode & 0o777 == 0o600 and json.loads(path.read_text())["refresh_token"] == SECRET_TOKENS[1]
    assert ("GET", "www.googleapis.com/youtube/v3/channels") in google.fake.requests


def test_the_wrong_channel_stores_nothing_and_revokes_nothing(google):
    google.fake.channel = "UCsomeoneElse"
    state = parse_qs(urlparse(google.oauth.authorization_url()).query)["state"][0]
    with pytest.raises(OAuthError) as caught:
        asyncio.run(google.oauth.complete("code", state))
    assert caught.value.reason == "channel_mismatch" and "UCsomeoneElse" in str(caught.value)
    assert not google.tokens.exists() and google.fake.revoked == []        # a revoke would also end the grant of every other channel of the same account


def test_missing_scopes_or_no_refresh_token_store_nothing(google):
    google.fake.scopes = google.cfg.oauth.scopes[:1]
    state = parse_qs(urlparse(google.oauth.authorization_url()).query)["state"][0]
    with pytest.raises(OAuthError) as caught:
        asyncio.run(google.oauth.complete("code", state))
    assert caught.value.reason == "scopes_missing" and not google.tokens.exists()
    google.fake.scopes, google.fake.refresh_token = youtube_config().oauth.scopes, None
    state = parse_qs(urlparse(google.oauth.authorization_url()).query)["state"][0]
    with pytest.raises(OAuthError, match="no refresh token"):
        asyncio.run(google.oauth.complete("code", state))
    assert not google.tokens.exists()


def test_an_unknown_or_used_state_is_refused(google):
    with pytest.raises(OAuthError) as caught:
        asyncio.run(google.oauth.complete("code", "never-issued"))
    assert caught.value.reason == "state_invalid"
    state = parse_qs(urlparse(google.oauth.authorization_url()).query)["state"][0]
    asyncio.run(google.oauth.complete("code", state))
    with pytest.raises(OAuthError):
        asyncio.run(google.oauth.complete("code", state))


def test_an_expired_access_token_is_refreshed_and_a_rejected_refresh_token_asks_for_a_reconnection(google):
    connect(google)
    tokens = google.tokens.load()
    tokens.expires_at = tokens.expires_at.replace(year=2000)
    google.tokens.save(tokens)
    assert asyncio.run(google.oauth.access_token()) == "ya29.REFRESHED-ACCESS" and google.tokens.load().access_token == "ya29.REFRESHED-ACCESS"
    tokens = google.tokens.load()
    tokens.expires_at = tokens.expires_at.replace(year=2000)
    google.tokens.save(tokens)
    google.fake.invalid_grant = True
    with pytest.raises(OAuthError) as caught:
        asyncio.run(google.oauth.access_token())
    assert caught.value.reason == "reconnect_required"


def test_disconnecting_revokes_and_removes_the_stored_tokens(google):
    connect(google)
    assert asyncio.run(google.oauth.disconnect(revoke=True)) is True
    assert not google.tokens.exists() and google.fake.revoked == [SECRET_TOKENS[1]] and google.oauth.status()["connected"] is False


def test_the_connection_test_is_a_read_only_channel_check(google):
    connect(google)
    google.fake.requests.clear()
    result = asyncio.run(google.oauth.test())
    assert result["channel_id"] == CHANNEL and set(result["scopes"]) == {"youtube.upload", "youtube.readonly", "youtube.force-ssl"} and result["checked_at"]
    assert google.fake.requests == [("POST", "oauth2.googleapis.com/token"), ("GET", "www.googleapis.com/youtube/v3/channels")]       # a refresh and one read: nothing is written
    google.fake.channel = "UCother"
    with pytest.raises(OAuthError, match="not UC3bIVrg7dD6B9_JlSHduPLw"):
        asyncio.run(google.oauth.test())


def test_the_connection_test_fails_clearly_when_a_required_scope_is_missing_and_asks_google_nothing(google):
    connect(google)
    tokens = google.tokens.load()
    tokens.scopes = tokens.scopes[:1]
    google.tokens.save(tokens)
    google.fake.requests.clear()
    with pytest.raises(OAuthError) as caught:
        asyncio.run(google.oauth.test())
    assert caught.value.reason == "scopes_missing" and "connect again" in str(caught.value) and google.fake.requests == []


def test_secrets_are_redacted_from_error_text(google):
    connect(google)
    leaked = f"boom {SECRET_TOKENS[1]} and {SECRET_TOKENS[0]} and {SECRET_TOKENS[2]}"
    assert not any(secret in google.tokens.redact(leaked) for secret in SECRET_TOKENS)


# --- 2. the upload ------------------------------------------------------------------------------------------------------------------------------------------

def test_a_private_upload_sends_the_approved_metadata_with_an_explicit_private_status_and_follows_processing(world, google):
    connect(google)
    p = approved_short(world)
    session = run(world, google, p)
    resource = google.fake.resources[0]
    assert resource["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}
    assert resource["snippet"]["title"] == "A Persian title" and resource["snippet"]["tags"] == ["case", "film"] and "#truecrime" in resource["snippet"]["description"]
    assert bytes(google.fake.sessions[next(iter(google.fake.sessions))]) == (world.clips / "clip_C1.mp4").read_bytes()
    assert google.fake.session_headers[0]["x-upload-content-length"] == str(session.total_bytes)
    p = pub(world, "youtube", "short", "C1")
    assert session.status == "processed" and session.processing_percent == 100 and session.thumbnail_status == "done"
    assert (p.status, p.remote_id, p.remote_url) == ("ready", "VID123abc", "https://www.youtube.com/shorts/VID123abc")
    assert google.fake.thumbnails == ["VID123abc"]
    assert [e.decision for e in world.db.query(PolicyEvent).filter_by(case_id=world.case.id).order_by(PolicyEvent.id).all() if e.decision.startswith("youtube_")] == [
        "youtube_upload_started", "youtube_uploaded_private", "youtube_processing_done"]


def test_uploading_privately_is_not_publishing(world, google):
    connect(google)
    p = approved_short(world)
    run(world, google, p)
    p = pub(world, "youtube", "short", "C1")
    assert p.status == "ready" and p.status != "published" and p.published_at is None
    assert google.fake.resources[0]["status"]["privacyStatus"] == "private"
    assert not [r for r in google.fake.requests if r[1].endswith("/videos") and r[0] in ("PUT", "PATCH")]       # no videos.update: nothing was made public


def test_the_client_refuses_any_privacy_but_private():
    for privacy in ("public", "unlisted", None):
        resource = YouTubeClient.video_resource(title="t", description="d", tags=[], category_id="25", language="fa", privacy=privacy, made_for_kids=False, synthetic=True)
        with pytest.raises(YouTubeError) as caught:
            asyncio.run(YouTubeClient(YouTubeOAuth("fa", CHANNEL)).start_session(resource, 10))
        assert caught.value.reason == "privacy_refused"


def test_the_privacy_setting_cannot_be_configured_away_from_private():
    from pydantic import ValidationError

    from app.youtube_publishing.config import UploadSettings

    body = youtube_config().upload.model_dump() | {"initial_privacy": "public"}
    with pytest.raises(ValidationError):
        UploadSettings.model_validate(body)


def test_the_film_gets_its_chapters_and_its_subtitle_file(world, google, monkeypatch):
    from test_publication_safeguards import meet_every_film_requirement

    meet_every_film_requirement(world, monkeypatch)
    connect(google)
    planned(world)
    film = pub(world, "youtube", "film", f"V{world.film.id}")
    world.registry.update(film.id, metadata={"title": "The film", "hashtags": [], "keywords": ["k"], "chapters": [{"time": "00:00", "title": "Start"}, {"time": "01:00", "title": "Next"}]})
    approve_both(world, pub(world, "youtube", "film", f"V{world.film.id}"))
    world.film_file.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nسلام\n", encoding="utf-8")
    session = run(world, google, pub(world, "youtube", "film", f"V{world.film.id}"))
    assert "00:00 Start\n01:00 Next" in google.fake.resources[0]["snippet"]["description"] and session.captions_status == "done" and "سلام".encode() in google.fake.captions[0]
    assert pub(world, "youtube", "film", f"V{world.film.id}").remote_url == "https://www.youtube.com/watch?v=VID123abc"


def test_a_public_film_is_recorded_as_published_and_its_shorts_carry_its_address_from_the_upload_on(world, google, monkeypatch):
    from test_publication_safeguards import meet_every_film_requirement

    meet_every_film_requirement(world, monkeypatch)
    connect(google)
    google.cfg.upload.public_after_processing = True
    planned(world)
    film = pub(world, "youtube", "film", f"V{world.film.id}")
    world.registry.update(film.id, metadata={"title": "The film", "hashtags": [], "keywords": ["k"], "chapters": [{"time": "00:00", "title": "Start"}, {"time": "01:00", "title": "Next"}]})
    approve_both(world, pub(world, "youtube", "film", f"V{world.film.id}"))
    run(world, google, pub(world, "youtube", "film", f"V{world.film.id}"))
    film = pub(world, "youtube", "film", f"V{world.film.id}")
    assert (film.status, film.visibility) == ("published", "public") and google.fake.updates and film.published_at is not None
    assert film.remote_url in pub(world, "youtube", "short", "C1").caption                                  # the Short found the film's address when the film was uploaded


def test_a_missing_cover_or_subtitle_is_reported_and_never_undoes_the_upload(world, google):
    connect(google)
    p = approved_short(world)
    google.fake.processing = [{"processingStatus": "succeeded"}]
    session = run(world, google, p)
    assert session.status == "processed" and session.captions_status == "no subtitle file"


def test_a_cover_approved_after_the_upload_can_be_put_on_the_video_that_is_already_there(world, google):
    connect(google)
    p = approved_short(world)
    run(world, google, p)
    assert google.fake.thumbnails == ["VID123abc"]
    session = asyncio.run(uploader(world, google).replace_thumbnail(p.id))
    assert google.fake.thumbnails == ["VID123abc", "VID123abc"] and session.thumbnail_status == "done" and len(google.fake.resources) == 1          # the video was not uploaded again
    assert any(e.decision == "youtube_thumbnail_replaced" for e in world.db.query(PolicyEvent).filter_by(case_id=world.case.id).all())
    with pytest.raises(Refused) as refused:
        asyncio.run(uploader(world, google).replace_thumbnail(pub(world, "youtube", "short", "C2").id))
    assert refused.value.reason == "no_video"


def test_a_processed_video_is_made_public_at_once_when_the_config_says_so_and_a_refusal_leaves_it_private(world, google):
    connect(google)
    google.cfg.upload.public_after_processing = True
    p = approved_short(world)
    session = run(world, google, p)
    p = pub(world, "youtube", "short", "C1")
    assert google.fake.updates == [{"id": "VID123abc", "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}]
    assert (session.privacy, p.status, p.visibility) == ("public", "published", "public") and p.published_at is not None
    assert session.thumbnail_status == "done" and session.status == "processed"                      # the thumbnail came first
    assert [e.decision for e in world.db.query(PolicyEvent).filter_by(case_id=world.case.id).order_by(PolicyEvent.id).all() if e.decision.startswith("youtube_")][-1] == "youtube_made_public"
    google.fake.refuse_public, google.fake.privacy, google.fake.updates = True, "private", []
    other = approved_short(world, "C2")
    session = run(world, google, other)
    other = pub(world, "youtube", "short", "C2")
    assert other.status == "ready" and session.privacy == "private" and session.required_action == "make_public" and "could not be made public" in session.error


def test_the_category_of_an_uploaded_video_is_changed_alone_and_only_when_it_differs(world, google):
    connect(google)
    google.cfg.upload.category_id = "1"
    p = approved_short(world)
    run(world, google, p)
    assert asyncio.run(uploader(world, google).set_category(p.id)) is True
    assert google.fake.snippet_updates == [{"id": "VID123abc", "snippet": {"title": "T", "description": "D\nhttps://x", "tags": ["a"], "defaultLanguage": "fa", "categoryId": "1"}}]    # nothing else was lost
    assert asyncio.run(uploader(world, google).set_category(p.id)) is False and len(google.fake.snippet_updates) == 1                  # it has the category: no second call
    with pytest.raises(Refused) as refused:
        asyncio.run(uploader(world, google).set_category(pub(world, "youtube", "short", "C2").id))
    assert refused.value.reason == "no_video"


def test_a_video_made_public_in_studio_is_found_public_and_recorded_so(world, google):
    connect(google)
    p = approved_short(world)
    run(world, google, p)
    assert asyncio.run(uploader(world, google).sync_visibility(p.id)) == "private" and pub(world, "youtube", "short", "C1").status == "ready"
    google.fake.privacy = "public"                                                                      # the owner made it public in YouTube Studio
    assert asyncio.run(uploader(world, google).sync_visibility(p.id)) == "public"
    found = pub(world, "youtube", "short", "C1")
    assert (found.status, found.visibility) == ("published", "public") and found.published_at is not None
    assert [e.decision for e in world.db.query(PolicyEvent).filter_by(case_id=world.case.id).order_by(PolicyEvent.id).all()][-1] == "youtube_found_public"


# --- 3. interruption, resume, restart ---------------------------------------------------------------------------------------------------------------------

def test_a_dropped_connection_leaves_a_resumable_session_and_a_resume_sends_only_the_rest(world, google):
    connect(google)
    p = approved_short(world)
    size = (world.clips / "clip_C1.mp4").stat().st_size
    assert size > 2048 * 2, "the test needs several chunks"
    google.fake.fail_put_after = 2048
    google.cfg.upload.max_retries = 0
    session = run(world, google, p)
    assert session.status == "interrupted" and 0 < session.bytes_sent <= size and session.required_action == "retry" and pub(world, "youtube", "short", "C1").status == "uploading"
    starts = len(google.fake.resources)
    google.cfg.upload.max_retries = 3
    session = run(world, google, pub(world, "youtube", "short", "C1"))
    assert len(google.fake.resources) == starts == 1, "the session is resumed, not started again"
    assert session.status == "processed" and bytes(google.fake.sessions[next(iter(google.fake.sessions))]) == (world.clips / "clip_C1.mp4").read_bytes()
    assert events(world, "youtube_upload_interrupted") and events(world, "youtube_upload_resumed")


def test_the_session_survives_an_application_restart_and_is_resumed_from_the_database(world, google):
    connect(google)
    p = approved_short(world)
    google.fake.fail_put_after = 2048
    google.cfg.upload.max_retries = 0
    run(world, google, p)
    row = world.db.query(YouTubeUploadSession).one()
    row.status = "uploading"                                          # as if the process had died mid-upload
    world.db.commit()
    assert U.sweep_interrupted(world.db) == 1
    world.db.expire_all()
    row = world.db.query(YouTubeUploadSession).one()
    assert row.status == "interrupted" and row.required_action == "resume" and row.session_uri
    google.cfg.upload.max_retries = 3
    session = YouTubeUploader(world.db, google.oauth, google.cfg, client=google.client, sleep=_nap)
    done = asyncio.run(session.run(p.id, confirmation_text(p.id, CHANNEL)))
    assert done.status == "processed" and len(google.fake.resources) == 1


def test_an_expired_session_starts_again_from_the_beginning_with_a_new_session(world, google):
    connect(google)
    p = approved_short(world)
    google.fake.fail_put_after = 2048
    google.cfg.upload.max_retries = 0
    run(world, google, p)
    google.fake.expired_sessions = set(google.fake.sessions)
    google.cfg.upload.max_retries = 3
    expired = run(world, google, pub(world, "youtube", "short", "C1"))
    assert expired.status == "expired" and expired.required_action == "restart_upload" and expired.session_uri is None
    again = run(world, google, pub(world, "youtube", "short", "C1"))
    assert again.id != expired.id and again.status == "processed" and len(google.fake.resources) == 2


def test_a_file_changed_after_the_upload_started_cancels_that_session(world, google):
    connect(google)
    p = approved_short(world)
    google.fake.fail_put_after = 2048
    google.cfg.upload.max_retries = 0
    first = run(world, google, p)
    (world.clips / "clip_C1.mp4").write_bytes((world.clips / "clip_C1.mp4").read_bytes() + b"\0")
    resources = len(google.fake.resources)
    with pytest.raises(Refused) as caught:
        run(world, google, pub(world, "youtube", "short", "C1"))
    assert caught.value.reason == "readiness_changed" and len(google.fake.resources) == resources
    assert world.db.get(YouTubeUploadSession, first.id).status == "cancelled" and pub(world, "youtube", "short", "C1").status == "voided"


def test_a_quota_answer_is_a_required_action_not_a_crash(world, google):
    connect(google)
    p = approved_short(world)
    google.fake.queue = [403]
    session = run(world, google, p)
    assert session.status in ("interrupted", "failed") and session.required_action == "wait_for_quota" and "quota" in session.error.lower()


def test_a_rejected_refresh_token_during_an_upload_asks_for_a_reconnection_and_keeps_the_session(world, google):
    connect(google)
    p = approved_short(world)
    tokens = google.tokens.load()
    tokens.expires_at = tokens.expires_at.replace(year=2000)
    google.tokens.save(tokens)
    google.fake.invalid_grant = True
    with pytest.raises(Refused) as caught:
        run(world, google, p)
    assert caught.value.reason == "reconnect_required"
    assert not google.fake.resources


# --- 4. duplicates --------------------------------------------------------------------------------------------------------------------------------------------

def test_the_same_publication_cannot_be_uploaded_twice(world, google):
    connect(google)
    p = approved_short(world)
    run(world, google, p)
    with pytest.raises(Refused) as caught:
        run(world, google, pub(world, "youtube", "short", "C1"))
    assert caught.value.reason == "already_uploaded" and len(google.fake.resources) == 1


def test_the_same_file_behind_another_publication_is_refused(world, google):
    import shutil

    shutil.copy(world.clips / "clip_C1.mp4", world.clips / "clip_C2.mp4")                       # two publications, one file
    connect(google)
    first = approved_short(world, "C1")
    run(world, google, first)
    other = pub(world, "youtube", "short", "C2")
    world.registry.update(other.id, metadata={"title": "Other", "hashtags": [], "keywords": []})
    approve_both(world, pub(world, "youtube", "short", "C2"))
    other = pub(world, "youtube", "short", "C2")
    assert other.content_hash == pub(world, "youtube", "short", "C1").content_hash
    with pytest.raises(Refused) as caught:
        run(world, google, other)
    assert caught.value.reason == "duplicate_file" and len(google.fake.resources) == 1


def test_the_database_allows_one_active_session_per_publication(world, google):
    p = approved_short(world)
    world.db.add(YouTubeUploadSession(publication_id=p.id, status="uploading"))
    world.db.commit()
    world.db.add(YouTubeUploadSession(publication_id=p.id, status="interrupted"))
    with pytest.raises(IntegrityError):
        world.db.commit()
    world.db.rollback()
    world.db.query(YouTubeUploadSession).filter_by(publication_id=p.id).update({"status": "processed"})
    world.db.add(YouTubeUploadSession(publication_id=p.id, status="uploading"))
    world.db.commit()                                                  # a finished session does not block a new one


def test_a_second_run_of_the_same_publication_in_this_process_is_refused(world, google):
    connect(google)
    p = approved_short(world)
    U._RUNNING.add(p.id)
    try:
        with pytest.raises(Refused) as caught:
            run(world, google, p)
    finally:
        U._RUNNING.discard(p.id)
    assert caught.value.reason == "already_running" and not google.fake.resources


# --- 5. the controls before any byte -------------------------------------------------------------------------------------------------------------------------

def test_live_calls_off_means_no_request_at_all(world, google):
    connect(google)
    p = approved_short(world)
    google.cfg.live_enabled = False
    google.fake.requests.clear()
    with pytest.raises(Refused) as caught:
        run(world, google, p)
    assert caught.value.reason == "live_disabled" and google.fake.requests == []


def test_the_upload_needs_the_owners_confirmation_for_this_publication_and_channel(world, google):
    connect(google)
    p = approved_short(world)
    for wrong in (None, "", "yes", confirmation_text(p.id + 1, CHANNEL), confirmation_text(p.id, "UCother")):
        with pytest.raises(Refused) as caught:
            run(world, google, p, wrong)
        assert caught.value.reason == "confirmation_missing"
    assert not google.fake.resources


def test_an_unapproved_publication_is_never_uploaded(world, google):
    connect(google)
    planned(world)
    p = pub(world, "youtube", "short", "C1")
    with pytest.raises(Refused) as caught:
        run(world, google, p)
    assert caught.value.reason == "not_approved" and not google.fake.resources


def test_the_content_approval_alone_is_not_enough(world, google, monkeypatch):
    monkeypatch.setattr(world.registry.cfg.approval, "default_approved", False)          # the owner asks for manual approvals
    connect(google)
    planned(world)
    p = pub(world, "youtube", "short", "C1")
    world.registry.update(p.id, metadata={"title": "t", "hashtags": [], "keywords": []})
    world.registry.approve_content(p.id)
    with pytest.raises(Refused) as caught:
        run(world, google, pub(world, "youtube", "short", "C1"))
    assert caught.value.reason == "not_approved" and not google.fake.resources


def test_the_emergency_stop_blocks_the_upload(world, google):
    connect(google)
    p = approved_short(world)
    set_global_stop(world.db, True, "test")
    try:
        with pytest.raises(Refused) as caught:
            run(world, google, p)
    finally:
        set_global_stop(world.db, False, "test")
    assert caught.value.reason == "emergency_stop" and not google.fake.resources


def test_a_video_file_that_changed_after_the_approval_voids_it_and_nothing_is_uploaded(world, google, monkeypatch):
    monkeypatch.setattr(world.registry.cfg.approval, "default_approved", False)          # with the default approval a changed file is simply approved again
    connect(google)
    p = approved_short(world)
    (world.clips / "clip_C1.mp4").write_bytes((world.clips / "clip_C1.mp4").read_bytes() + b"changed")
    with pytest.raises(Refused) as caught:
        run(world, google, p)
    assert caught.value.reason in ("readiness_changed", "not_approved") and not google.fake.resources
    assert pub(world, "youtube", "short", "C1").status in ("voided", "content_approved", "draft")


def test_only_the_configured_language_and_platform_go_to_the_channel(world, google):
    connect(google)
    planned(world)
    facebook = pub(world, "facebook", "short", "C1")
    google.fake.requests.clear()
    with pytest.raises(Refused) as caught:
        run(world, google, facebook)
    assert caught.value.reason == "wrong_destination" and google.fake.requests == []


def test_a_wrong_channel_at_upload_time_stops_before_the_first_byte(world, google):
    connect(google)
    p = approved_short(world)
    google.fake.channel = "UCsomeoneElse"
    with pytest.raises(Refused) as caught:
        run(world, google, p)
    assert caught.value.reason == "channel_mismatch" and not google.fake.resources


def test_metadata_youtube_refuses_is_caught_before_the_upload(world, google):
    connect(google)
    p = approved_short(world)
    stored = json.loads(p.metadata_json)
    stored["title"] = "x" * 101
    p.metadata_json = json.dumps(stored)
    world.db.commit()
    with pytest.raises(Refused) as caught:
        uploader(world, google).prepare(p.id, confirmation_text(p.id, CHANNEL))
    assert caught.value.reason in ("metadata_invalid", "not_approved", "readiness_changed")


def test_the_description_limit_is_in_bytes_and_the_angle_brackets_are_refused():
    from app.publishing.config import publishing_config
    from app.publishing.intelligence import validate as V

    limit = publishing_config().intelligence.metadata_limits["youtube/short"]
    item = {"title": "t", "text": "ا" * 2600, "hashtags": [], "keywords": [], "chapters": [], "cta": "", "routing_note": "", "facts_used": []}
    kinds = {f["type"] for f in V.metadata_findings(item, "short", limit)}
    assert "text_too_long_bytes" in kinds and "text_too_long" not in kinds                  # 2600 characters, 5200 bytes
    assert "forbidden_characters" in {f["type"] for f in V.metadata_findings({**item, "text": "a < b"}, "short", limit)}
    assert "text_too_long_bytes" not in {f["type"] for f in V.metadata_findings({**item, "text": "ا" * 2400}, "short", limit)}


def test_the_tag_length_counts_commas_and_quoted_spaces():
    assert U.tag_length(["Foo-Baz"]) == 7 and U.tag_length(["Foo Baz"]) == 9 and U.tag_length(["a", "b"]) == 3


# --- 6. the interface's backend ------------------------------------------------------------------------------------------------------------------------

def test_opening_the_studio_makes_no_google_call_and_no_upload(world, google, client, monkeypatch):
    planned(world)
    calls = []
    monkeypatch.setattr("app.youtube_publishing.oauth.httpx.AsyncClient", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(AssertionError("no network on open")))
    monkeypatch.setattr("app.youtube_publishing.client.httpx.AsyncClient", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(AssertionError("no network on open")))
    assert client.get(f"/api/cases/{world.case.id}/publishing/fa").status_code == 200
    assert client.get(f"/api/cases/{world.case.id}/publishing/fa/ai").status_code == 200
    body = client.get(f"/api/youtube-publishing/cases/{world.case.id}/fa/uploads").json()
    assert client.get("/api/youtube-publishing/channels").status_code == 200 and calls == [] and all(u["session"] is None for u in body["uploads"].values())


def test_the_api_shows_progress_without_the_session_address_and_refuses_an_unconfirmed_upload(world, google, client, monkeypatch):
    monkeypatch.setattr("app.youtube_publishing.api.youtube_config", lambda: google.cfg)
    monkeypatch.setattr("app.youtube_publishing.api.uploader_for", lambda db, publication_id: YouTubeUploader(db, google.oauth, google.cfg, client=google.client, sleep=_nap))
    connect(google)
    p = approved_short(world)
    refused = client.post(f"/api/youtube-publishing/publications/{p.id}/upload", json={"confirm": "no"})
    assert refused.status_code == 409 and refused.json()["detail"]["reason"] == "confirmation_missing"
    world.db.add(YouTubeUploadSession(publication_id=p.id, status="interrupted", session_uri="https://upload.example/session/9?upload_id=SECRETSESSION9", total_bytes=100, bytes_sent=40,
                                      required_action="resume", error="stopped"))
    world.db.commit()
    body = client.get(f"/api/youtube-publishing/cases/{world.case.id}/fa/uploads").json()
    shown = body["uploads"][str(p.id)]
    assert shown["session"]["percent"] == 40 and shown["session"]["required_action"] == "resume" and "SECRETSESSION9" not in json.dumps(body) and "session_uri" not in json.dumps(body)
    assert shown["confirmation"] == confirmation_text(p.id, CHANNEL)


def use_fake_channels(monkeypatch, google) -> None:
    monkeypatch.setattr("app.youtube_publishing.api._channels", lambda db: YouTubeChannels(db, google.cfg, http=google.fake.client))


def test_the_channels_endpoint_and_connect_never_return_a_token(world, google, client, monkeypatch):
    connect(google)
    use_fake_channels(monkeypatch, google)
    status = client.get("/api/youtube-publishing/channels").text
    assert not any(secret in status for secret in SECRET_TOKENS) and CHANNEL in status
    connected = client.post("/api/youtube-publishing/channels/fa/connect", json={"return_to": "//evil.example"})
    assert connected.status_code == 200 and "accounts.google.com" in connected.json()["authorization_url"]
    assert client.post("/api/youtube-publishing/channels/fa/test").json()["channel_id"] == CHANNEL


def test_the_callback_returns_to_the_studio_and_an_error_never_carries_a_secret(world, google, client, monkeypatch):
    use_fake_channels(monkeypatch, google)
    state = parse_qs(urlparse(google.oauth.authorization_url("/cases/15?tab=Publishing")).query)["state"][0]
    done = client.get("/api/youtube-publishing/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert done.status_code in (302, 307) and done.headers["location"].endswith("/cases/15?tab=Publishing&youtube=connected&language=fa")
    denied = client.get("/api/youtube-publishing/oauth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert "youtube=error" in denied.headers["location"]
    bad = client.get("/api/youtube-publishing/oauth/callback", params={"code": "c", "state": "forged"}, follow_redirects=False)
    assert "reason=state_invalid" in bad.headers["location"]


def test_the_related_video_link_is_never_verified_by_an_upload(world, google):
    connect(google)
    p = approved_short(world)
    run(world, google, p)
    links = world.registry.sync_routing(pub(world, "youtube", "short", "C1"))
    assert links and all(link.status != "clickability_verified" and link.verified_at is None for link in links)
    related = next(link for link in links if link.method == "related_video")
    assert related.status == "manual_action_required"
    with pytest.raises(Exception, match="no URL|created|note"):
        world.registry.update_routing(related.id, status="clickability_verified")


def test_monitoring_shows_a_running_and_an_interrupted_upload_once_each(world, google):
    planned(world)
    p = pub(world, "youtube", "short", "C1")
    row = YouTubeUploadSession(publication_id=p.id, status="uploading", total_bytes=100, bytes_sent=50)
    world.db.add(row)
    world.db.commit()
    running = [i for i in publishing_issues(world.db, world.case.id) if "Uploading privately" in i.title]
    assert len(running) == 1 and running[0].severity == "info" and "50%" in running[0].detail
    row.status, row.error, row.required_action = "interrupted", "The connection failed.", "retry"
    world.db.commit()
    issues = [i for i in publishing_issues(world.db, world.case.id) if "YouTube upload needs you" in i.title]
    assert len(issues) == 1 and issues[0].severity == "problem" and "retry" in issues[0].detail
    assert not [i for i in publishing_issues(world.db, world.case.id) if "Uploading privately" in i.title]


def test_an_unfinished_processing_check_later_completes_the_publication(world, google):
    connect(google)
    p = approved_short(world)
    google.cfg.upload.processing_max_wait_seconds = 0.001
    google.cfg.upload.processing_poll_seconds = 0.01
    google.fake.processing = [{"processingStatus": "processing", "processingProgress": {"partsTotal": 4, "partsProcessed": 1}}]
    session = run(world, google, p)
    assert session.status == "processing" and session.processing_percent == 25 and pub(world, "youtube", "short", "C1").status == "processing"
    google.fake.processing = [{"processingStatus": "succeeded"}]
    done = asyncio.run(uploader(world, google).check_processing(p.id))
    assert done.status == "processed" and pub(world, "youtube", "short", "C1").status == "ready"


def test_a_video_that_youtube_reports_as_public_is_flagged_and_stops_the_follow_up(world, google):
    connect(google)
    p = approved_short(world)
    google.fake.privacy = "public"
    session = run(world, google, p)
    assert events(world, "youtube_privacy_unexpected") and session.status != "processed" and "public" in (session.error or "")


def test_session_expired_is_a_distinct_error():
    assert issubclass(SessionExpired, YouTubeError) and SessionExpired().reason == "session_expired"


# --- 7. several channels (one Google OAuth client, one set of credentials per language) --------------------------------------------------------------

EN_CHANNEL = "UC" + "e" * 22


@pytest.fixture
def channels(world, google):
    """The channel registry over the fake Google, with the English channel registered; the English account row is reset afterwards."""
    registry = YouTubeChannels(world.db, google.cfg, http=google.fake.client)
    registry.register("en", EN_CHANNEL)
    yield registry
    world.db.rollback()
    world.db.query(PublicationAccount).filter(PublicationAccount.platform == "youtube", PublicationAccount.language == "en").delete()
    world.db.query(PublicationAccount).filter(PublicationAccount.platform == "youtube", PublicationAccount.language == "fa").update({"secret_ref": "", "status": "connected"})
    world.db.commit()


def authorize(registry: YouTubeChannels, google, language: str, channel: str) -> None:
    google.fake.channel = channel
    oauth = registry.oauth(language)
    state = parse_qs(urlparse(oauth.authorization_url("/cases/15?tab=Publishing")).query)["state"][0]
    asyncio.run(oauth.complete("auth-code", state))
    registry.sync(language)


def test_each_channel_keeps_its_own_credentials_and_connecting_one_never_touches_the_other(world, google, channels):
    authorize(channels, google, "fa", CHANNEL)
    persian = channels.oauth("fa").tokens
    before = persian.path.read_bytes()
    authorize(channels, google, "en", EN_CHANNEL)
    english = channels.oauth("en").tokens
    assert persian.path != english.path and english.path.name == "token_en.json" and oct(english.path.stat().st_mode & 0o777) == "0o600"
    assert persian.path.read_bytes() == before                       # the Persian token file is byte for byte what it was
    assert persian.load().channel_id == CHANNEL and english.load().channel_id == EN_CHANNEL
    google.fake.channel = EN_CHANNEL
    asyncio.run(channels.disconnect("en"))
    assert not english.exists() and persian.path.read_bytes() == before and channels.status("fa")["connected"] and not channels.status("en")["connected"]


def test_the_account_rows_mirror_the_real_connections(world, google, channels):
    rows = lambda language: world.db.query(PublicationAccount).filter_by(platform="youtube", language=language).one()  # noqa: E731
    assert rows("en").status == "not_connected" and rows("en").external_id == EN_CHANNEL
    authorize(channels, google, "en", EN_CHANNEL)
    assert rows("en").status == "connected" and rows("en").secret_ref == "youtube/token_en"
    asyncio.run(channels.disconnect("en"))
    assert rows("en").status == "not_connected" and rows("en").secret_ref == ""


def test_the_wrong_channel_is_never_stored_for_a_language(world, google, channels):
    authorize(channels, google, "fa", CHANNEL)
    google.fake.channel = CHANNEL                                    # the owner picks the Persian channel in Google's chooser while connecting English
    oauth = channels.oauth("en")
    state = parse_qs(urlparse(oauth.authorization_url("/")).query)["state"][0]
    with pytest.raises(OAuthError) as caught:
        asyncio.run(oauth.complete("auth-code", state))
    assert caught.value.reason == "channel_mismatch" and not oauth.tokens.exists() and google.fake.revoked == []
    assert channels.status("fa")["connected"] and channels.status("fa")["channel_matches"]


def test_a_language_without_a_registered_channel_cannot_connect_or_upload(world, google, channels):
    world.db.query(PublicationAccount).filter_by(platform="youtube", language="en").update({"external_id": ""})
    world.db.commit()
    assert channels.status("en")["registered"] is False
    with pytest.raises(OAuthError) as caught:
        channels.oauth("en").authorization_url("/")
    assert caught.value.reason == "channel_not_registered"


def test_the_channel_id_is_validated_unique_and_fixed_while_connected(world, google, channels):
    with pytest.raises(ChannelError) as bad:
        channels.register("en", "not-a-channel")
    assert bad.value.reason == "invalid_channel_id"
    with pytest.raises(ChannelError) as clash:
        channels.register("en", CHANNEL)
    assert clash.value.reason == "channel_in_use"
    with pytest.raises(ChannelError) as unknown:
        channels.register("xx", EN_CHANNEL)
    assert unknown.value.reason == "unknown_language"
    authorize(channels, google, "en", EN_CHANNEL)
    with pytest.raises(ChannelError) as busy:
        channels.register("en", "UC" + "f" * 22)
    assert busy.value.reason == "disconnect_first"
    assert channels.register("en", EN_CHANNEL).external_id == EN_CHANNEL


def test_a_token_file_of_another_channel_is_refused_before_any_request(world, google, channels):
    authorize(channels, google, "fa", CHANNEL)
    english = channels.oauth("en").tokens
    english.path.write_bytes(channels.oauth("fa").tokens.path.read_bytes())          # the Persian credentials end up in the English slot
    google.fake.requests.clear()
    with pytest.raises(OAuthError) as caught:
        asyncio.run(channels.oauth("en").access_token())
    assert caught.value.reason == "channel_mismatch" and google.fake.requests == []
    with pytest.raises(OAuthError):
        asyncio.run(channels.oauth("en").test())
    assert google.fake.requests == []


def test_an_attempt_started_for_one_language_cannot_finish_another(world, google, channels):
    google.fake.channel = EN_CHANNEL
    state = parse_qs(urlparse(channels.oauth("en").authorization_url("/")).query)["state"][0]
    with pytest.raises(OAuthError) as caught:
        asyncio.run(channels.oauth("fa").complete("auth-code", state))
    assert caught.value.reason == "state_invalid" and not channels.oauth("fa").tokens.exists()


def test_a_publication_only_ever_goes_to_the_channel_of_its_language(world, google, channels):
    authorize(channels, google, "fa", CHANNEL)
    authorize(channels, google, "en", EN_CHANNEL)
    p = approved_short(world)
    english = YouTubeUploader(world.db, channels.oauth("en"), google.cfg, client=YouTubeClient(channels.oauth("en"), google.cfg, http=google.fake.client, sleep=_nap),
                              registry=world.registry, sleep=_nap)
    with pytest.raises(U.Refused) as caught:
        english.prepare(p.id, confirmation_text(p.id, EN_CHANNEL))
    assert caught.value.reason == "wrong_destination"
    assert YouTubeUploader.for_publication(world.db, p.id).oauth.language == "fa"
    assert confirmation_text(p.id, EN_CHANNEL) != confirmation_text(p.id, CHANNEL)


def test_the_api_lists_every_channel_and_each_one_connects_tests_and_disconnects_alone(world, google, channels, client, monkeypatch):
    use_fake_channels(monkeypatch, google)
    authorize(channels, google, "fa", CHANNEL)
    authorize(channels, google, "en", EN_CHANNEL)
    listing = client.get("/api/youtube-publishing/channels").json()["channels"]
    by_language = {c["language"]: c for c in listing}
    assert list(by_language) == ["fa", "en", "de", "ar", "es", "it", "fr", "pt", "tr"] and by_language["fa"]["name"] == "ردِ خاموش" and by_language["en"]["name"] == "ClueVera"
    assert all(by_language[lang]["connected"] and by_language[lang]["channel_matches"] for lang in ("fa", "en")) and not any(by_language[lang]["registered"] for lang in ("de", "tr"))
    google.fake.channel = EN_CHANNEL
    assert client.post("/api/youtube-publishing/channels/en/test").json()["channel_id"] == EN_CHANNEL
    google.fake.channel = CHANNEL
    assert client.post("/api/youtube-publishing/channels/fa/test").json()["channel_id"] == CHANNEL
    gone = client.post("/api/youtube-publishing/channels/en/disconnect").json()
    assert gone == {"disconnected": True, "language": "en", "revoked": False, "kept_connected": ["fa"]} and google.fake.revoked == []
    after = {c["language"]: c for c in client.get("/api/youtube-publishing/channels").json()["channels"]}
    assert after["fa"]["connected"] and not after["en"]["connected"]
    assert not any(secret in json.dumps(after) for secret in SECRET_TOKENS)
    assert client.post("/api/youtube-publishing/channels/xx/connect", json={}).status_code == 409


def test_the_api_registers_a_channel_and_the_callback_reports_the_language(world, google, channels, client, monkeypatch):
    use_fake_channels(monkeypatch, google)
    world.db.query(PublicationAccount).filter_by(platform="youtube", language="en").update({"external_id": ""})
    world.db.commit()
    assert client.post("/api/youtube-publishing/channels/en/connect", json={}).json()["detail"]["reason"] == "channel_not_registered"
    assert client.post("/api/youtube-publishing/channels/en/register", json={"channel_id": EN_CHANNEL}).json()["registered"] is True
    google.fake.channel = EN_CHANNEL
    state = parse_qs(urlparse(client.post("/api/youtube-publishing/channels/en/connect", json={"return_to": "/cases/15?tab=Publishing"}).json()["authorization_url"]).query)["state"][0]
    done = client.get("/api/youtube-publishing/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert done.headers["location"].endswith("youtube=connected&language=en")
    assert {c["language"]: c for c in client.get("/api/youtube-publishing/channels").json()["channels"]}["en"]["connected"] is True


def test_disconnecting_the_last_channel_revokes_the_grant_but_not_while_another_channel_uses_it(world, google, channels):
    authorize(channels, google, "fa", CHANNEL)
    authorize(channels, google, "en", EN_CHANNEL)
    first = asyncio.run(channels.disconnect("en"))
    assert first["revoked"] is False and google.fake.revoked == [] and channels.status("fa")["connected"]
    last = asyncio.run(channels.disconnect("fa"))
    assert last["revoked"] is True and google.fake.revoked == [SECRET_TOKENS[1]]


def test_the_connection_test_proves_the_refresh_token_and_reports_a_revoked_grant_plainly(world, google, channels):
    authorize(channels, google, "fa", CHANNEL)
    google.fake.requests.clear()
    asyncio.run(channels.oauth("fa").test())
    assert ("POST", "oauth2.googleapis.com/token") in google.fake.requests          # a fresh token every time, not the cached access token
    google.fake.invalid_grant = True
    with pytest.raises(OAuthError) as caught:
        asyncio.run(channels.oauth("fa").test())
    assert caught.value.reason == "reconnect_required" and "connect YouTube again" in str(caught.value)


def test_a_failed_connection_returns_to_the_studio_with_the_language_and_the_reason(world, google, channels, client, monkeypatch):
    use_fake_channels(monkeypatch, google)
    start = lambda: parse_qs(urlparse(client.post("/api/youtube-publishing/channels/en/connect", json={"return_to": "/cases/15?tab=Publishing&language=en"}).json()["authorization_url"]).query)["state"][0]  # noqa: E731
    google.fake.channel = CHANNEL                                                              # the Persian channel was chosen in Google's chooser
    wrong = client.get("/api/youtube-publishing/oauth/callback", params={"code": "c", "state": start()}, follow_redirects=False).headers["location"]
    query = parse_qs(urlparse(wrong).query)
    assert urlparse(wrong).path == "/cases/15" and query["youtube"] == ["error"] and query["language"] == ["en"] and query["reason"] == ["channel_mismatch"]
    assert CHANNEL in query["detail"][0] and EN_CHANNEL in query["detail"][0] and not channels.oauth("en").tokens.exists()
    refused = client.get("/api/youtube-publishing/oauth/callback", params={"error": "access_denied", "state": start()}, follow_redirects=False).headers["location"]
    assert urlparse(refused).path == "/cases/15" and parse_qs(urlparse(refused).query)["reason"] == ["access_denied"] and parse_qs(urlparse(refused).query)["language"] == ["en"]


def test_every_language_of_the_studio_has_its_own_channel_slot_that_stays_closed_until_its_channel_is_registered(world, google, channels):
    from app.youtube_publishing.config import youtube_config as configured

    assert set(configured().channels) == {"fa", "en", "de", "ar", "es", "it", "fr", "pt", "tr"}
    for language in ("de", "ar", "es", "it", "fr", "pt", "tr"):
        status = channels.status(language)
        assert status["registered"] is False and status["connected"] is False
        with pytest.raises(OAuthError) as caught:
            channels.oauth(language).authorization_url("/")
        assert caught.value.reason == "channel_not_registered"
        assert channels.oauth(language).tokens.path.name == f"token_{language}.json"
    for language in ("de", "ar", "es", "it", "fr", "pt", "tr"):
        accounts = world.db.query(PublicationAccount).filter_by(platform="youtube", language=language)
        world.db.query(Publication).filter(Publication.account_ref_id.in_([a.id for a in accounts])).update({"account_ref_id": None}, synchronize_session=False)      # publications that earlier tests left keep no reference
        accounts.delete()
    world.db.commit()


def test_with_default_approvals_a_youtube_upload_needs_no_approval_click_but_still_refuses_a_blocker(world, google, monkeypatch):
    monkeypatch.setattr(world.registry.cfg.approval, "default_approved", True)
    monkeypatch.setattr(world.registry.cfg.approval, "manual_languages", [])
    connect(google)
    planned(world)
    p = pub(world, "youtube", "short", "C1")
    with pytest.raises(Refused) as caught:                                              # no platform text yet
        run(world, google, p)
    assert caught.value.reason == "not_approved" and "metadata" in str(caught.value) and not google.fake.resources
    world.registry.update(p.id, metadata={"title": "A Persian title", "hashtags": [], "keywords": [], "cta": ""})
    assert run(world, google, pub(world, "youtube", "short", "C1")).status == "processed"
    assert {json.loads(e.context_json)["actor"] for e in events(world, "publication_approved")} == {"default"}


def test_the_film_address_goes_to_the_first_rows_of_a_public_shorts_description_on_youtube_and_the_rest_of_the_video_is_kept(world, google):
    connect(google)
    google.cfg.upload.public_after_processing = True
    p = approved_short(world)
    run(world, google, p)
    film = pub(world, "youtube", "film", f"V{world.film.id}")
    film.remote_url = "https://www.youtube.com/watch?v=FILM123"
    world.db.commit()
    done = asyncio.run(uploader(world, google).add_film_addresses(world.case.id, "fa"))
    assert done == [{"publication_id": p.id, "updated": True, "video_id": "VID123abc"}]
    sent = google.fake.snippet_updates[-1]["snippet"]
    assert sent["description"].startswith("https://www.youtube.com/watch?v=FILM123\n\n") and sent["title"] == "T" and sent["tags"] == ["a"] and sent["categoryId"] == google.fake.category      # nothing else was lost
    assert pub(world, "youtube", "short", "C1").caption.startswith("https://www.youtube.com/watch?v=FILM123\n\n")
    assert asyncio.run(uploader(world, google).add_film_addresses(world.case.id, "fa")) == [] and len(google.fake.snippet_updates) == 1       # nothing is sent twice


def test_discovering_a_channel_reports_which_channel_the_account_has_and_stores_nothing(google):
    url = google.oauth.authorization_url("/cases/15?tab=Publishing", discover=True)
    state = parse_qs(urlparse(url).query)["state"][0]
    outcome = asyncio.run(google.oauth.complete("auth-code", state))
    assert outcome["discovered"]["id"] == CHANNEL and not google.tokens.exists()


def test_discovery_does_not_open_the_door_to_connecting_a_channel_that_is_not_the_registered_one(google):
    google.fake.channel = "UCsomeoneElse"
    state = parse_qs(urlparse(google.oauth.authorization_url()).query)["state"][0]
    with pytest.raises(OAuthError, match="not the fa channel"):
        asyncio.run(google.oauth.complete("auth-code", state))
    assert not google.tokens.exists()


def test_a_channel_that_does_not_exist_yet_cannot_be_connected(db_session):
    from app.youtube_publishing.channels import ChannelError, YouTubeChannels

    channels = YouTubeChannels(db_session)
    with pytest.raises(ChannelError) as caught:
        channels.require_existing("pt")
    assert caught.value.reason == "awaiting_channel" and channels.status("pt")["awaiting"] == "channel_creation"
    channels.require_existing("de")
