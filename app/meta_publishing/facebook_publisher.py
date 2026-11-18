"""Upload one approved publication to the Farsi Facebook Page, through the publications registry: a Reel (a Short) or a long film. Nothing here publishes by itself.

  DRY_RUN      no request at all: what each step would send, and what blocks it
  UPLOAD_ONLY  upload the file and wait until Meta has processed it; nothing is created on the Page (a Reel's session stays unfinished at Meta, a film is only a file handle)
  SAVE_DRAFT   upload (or continue) and finish as a DRAFT. What a draft is on the Page, and whether it can be published later through the API, is not documented by Meta: the
               first controlled test shows it, and a draft is never published by this code
  PUBLISH      upload (or continue) and finish as PUBLIC, in one go: the owner's click, or the automatic publisher at the planned time. It needs the separate switch
               `facebook.allow_public_publish` (off until the owner says go) and no typed text; never from a draft

Before the first byte (and again before a resumed upload and before a finish) the publisher checks, in this order: live calls are on; the Emergency Stop is clear; the publication is a
facebook publication of the configured language; for the two test modes the owner typed the confirmation for THIS mode, publication and Page; the publication has a current approval (given by default when it has no blocker) and still meets every
requirement (the registry revalidates it and hashes the video file afresh); the same file is not already on the Page; the token, the Page and its Instagram link are the configured ones.
The state of the upload lives in `facebook_upload_sessions` (one active session per publication, one session per file and Page), and every step is a PolicyEvent."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import FacebookUploadSession, Publication
from app.documentary import storage
from app.meta_publishing.config import FacebookSettings, InstagramSettings, meta_config
from app.meta_publishing.connection import ConnectError
from app.meta_publishing.facebook_client import FacebookClient
from app.meta_publishing.facebook_connection import FacebookConnection
from app.meta_publishing.graph import MetaError, PublishOutcomeUnknown
from app.meta_publishing.probe import ProbeError, run_ffprobe
from app.meta_publishing.token import TokenStore
from app.publishing.hashing import fresh_file_sha256
from app.publishing.registry import PublicationRegistry, PublishingRefused, ReadinessChanged
from app.utils import utc_now
from app.youtube_publishing.uploader import compose_description

ACTIVE = ("created", "uploading", "interrupted", "processing", "ready")
LOCKING = (*ACTIVE, "drafted", "published", "uncertain")
_RUNNING: set[int] = set()          # publications with an upload running in this process
_ACTION = {"token_expired": "replace_token", "token_invalid": "replace_token", "permission_missing": "check_permissions", "rate_limited": "wait_and_retry",
           "upload_failed": "retry", "processing_failed": "restart_upload", "timeout": "retry", "network": "retry", "server": "retry"}


class Mode(StrEnum):
    DRY_RUN = "DRY_RUN"
    UPLOAD_ONLY = "UPLOAD_ONLY"
    SAVE_DRAFT = "SAVE_DRAFT"
    PUBLISH = "PUBLISH"


_WORD = {Mode.UPLOAD_ONLY: "UPLOAD", Mode.SAVE_DRAFT: "DRAFT", Mode.PUBLISH: "PUBLISH"}


class Refused(RuntimeError):
    """A safety control said no. `reason` is a short code the interface shows."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class Stopped(RuntimeError):
    """The Emergency Stop was pressed during an upload: the session is interrupted, resumable."""


def text_hash(title: str, description: str) -> str:
    """The text a Facebook video was finished with, as one hash: a later difference means the text was corrected after it."""
    return hashlib.sha256(json.dumps([title, description], ensure_ascii=False).encode()).hexdigest()


def confirmation_text(mode: Mode, publication_id: int, page_id: str) -> str:
    return f"{_WORD[mode]} FACEBOOK {publication_id} {page_id}"


@dataclass(frozen=True)
class Target:
    publication: Publication
    path: Path
    title: str
    description: str
    metadata_hash: str


@dataclass
class Step:
    name: str
    status: str            # ok | failed | skipped | planned
    detail: str = ""


class FacebookPublisher:
    def __init__(self, db: Session, settings: InstagramSettings | None = None, facebook: FacebookSettings | None = None, *, client: FacebookClient | None = None,
                 registry: PublicationRegistry | None = None, tokens: TokenStore | None = None, transport=None, sleep=None):
        import asyncio

        config = meta_config()
        self.db, self.s, self.f = db, settings or config.instagram, facebook or config.facebook
        self.tokens = tokens or TokenStore(self.s)
        self.sleep = sleep or asyncio.sleep
        self.client = client or FacebookClient(self.s, self.f, self.tokens, transport=transport, sleep=self.sleep)
        self.registry = registry or PublicationRegistry(db)

    @property
    def page_id(self) -> str:
        return self.f.page.page_id

    # -- the checks that need no call to Meta -------------------------------------------------------
    def prepare(self, publication_id: int, mode: Mode, confirm: str | None) -> Target:
        """Raises Refused for the first control that says no (the registry has recorded and persisted why, when readiness changed)."""
        pub = self.registry.get(publication_id)
        if pub.platform != "facebook" or pub.language != self.f.language:
            raise Refused("wrong_destination", f"Only facebook publications in '{self.f.language}' go to the {self.f.page.brand} Page; this one is {pub.platform}/{pub.language}.")
        if mode is not Mode.DRY_RUN:
            self._allowed(pub, mode, confirm)
        self.registry.refresh(pub)
        self.db.commit()
        session = latest_session(self.db, pub.id)
        if session is not None and session.status == "drafted":
            raise Refused("already_drafted", "This publication is already a draft on the Page. Publish it by hand in Meta Business Suite, or cancel it here after deleting the draft there.")
        if session is not None and session.status in ("published", "uncertain"):
            raise Refused("already_uploaded", f"This publication is already on the Page ({pub.remote_url or pub.remote_id}).")
        if mode is not Mode.DRY_RUN:
            try:
                self.registry.ensure_approved(pub)
            except PublishingRefused as exc:
                raise Refused("not_approved", str(exc)) from exc
        if mode is not Mode.DRY_RUN:
            try:
                self.registry.assert_ready(pub, "publish" if mode is Mode.PUBLISH else "upload", actor="owner")
            except ReadinessChanged as exc:
                raise Refused("readiness_changed", str(exc)) from exc
        path = storage.resolve(pub.video_path) if pub.video_path else None
        if path is None or not path.is_file():
            raise Refused("file_missing", "The video file of this publication is missing.")
        self._no_twin(pub)
        title, description, problems = self._text(pub)
        if problems:
            raise Refused("metadata_invalid", "; ".join(problems))
        if pub.kind == "short":
            self._rate(pub)
        return Target(pub, path, title, description, text_hash(title, description))

    def _allowed(self, pub: Publication, mode: Mode, confirm: str | None) -> None:
        if not self.f.live_enabled:
            raise Refused("live_disabled", "Facebook uploads are off (config/meta_publishing.json: facebook.live_enabled is false).")
        if self.registry._stopped():
            raise Refused("emergency_stop", "The Emergency Stop is active: nothing is uploaded.")
        if mode is Mode.PUBLISH and not self.f.allow_public_publish:
            raise Refused("public_publish_off", "Public publishing is switched off (config/meta_publishing.json: facebook.allow_public_publish is false).")
        if mode is not Mode.PUBLISH and confirm != confirmation_text(mode, pub.id, self.page_id):
            raise Refused("confirmation_missing", "This needs the owner's confirmation text for this mode, publication and Page.")

    def _no_twin(self, pub: Publication) -> None:
        twin = self.db.query(Publication).filter(Publication.platform == "facebook", Publication.content_hash == pub.content_hash, Publication.id != pub.id,
                                                 Publication.remote_id.isnot(None)).first()
        sessions = self.db.query(FacebookUploadSession).filter(FacebookUploadSession.page_id == self.page_id, FacebookUploadSession.content_hash == pub.content_hash,
                                                               FacebookUploadSession.status.in_(LOCKING), FacebookUploadSession.publication_id != pub.id).first()
        if twin is not None or sessions is not None:
            raise Refused("duplicate_file", "This exact file is already on the Page (or being uploaded) through another publication.")

    def _text(self, pub: Publication) -> tuple[str, str, list[str]]:
        stored = json.loads(pub.metadata_json or "{}")
        title, description = (stored.get("title") or "").strip(), compose_description(pub)
        limit = self.registry.cfg.intelligence.metadata_limits[f"facebook/{pub.kind}"]
        problems = []
        if limit.title_required and not title:
            problems.append("The title is empty.")
        if len(title) > limit.title_max:
            problems.append(f"The title has {len(title)} characters (limit {limit.title_max}).")
        if len(description) > limit.text_max:
            problems.append(f"The description has {len(description)} characters (limit {limit.text_max}).")
        return title, description, problems

    def _rate(self, pub: Publication) -> None:
        """Meta allows 30 API-published Reels per 24 hours on a Page: this application counts its own finished Reels and stops before the limit."""
        since = utc_now() - timedelta(hours=24)
        done = self.db.query(FacebookUploadSession).filter(FacebookUploadSession.kind == "short", FacebookUploadSession.status.in_(("drafted", "published", "uncertain")),
                                                           FacebookUploadSession.updated_at >= since).count()
        if done >= self.f.reels.max_posts_per_24h:
            raise Refused("rate_limit", f"{done} Reels were finished in the last 24 hours (Meta allows {self.f.reels.max_posts_per_24h}).")

    def _publication_blockers(self, pub: Publication) -> list[str]:
        if self.registry.approval_current(pub):
            return self.registry.blockers(pub)
        if self.registry.approved_by_default(pub):
            return self.registry._why_not_approved(pub)                      # what stops the default approval: the blockers, no metadata yet, no time
        return ["This publication needs your approval: approve its content and publication in the Publishing Studio.", *self.registry.blockers(pub)]

    def readiness(self, publication_id: int) -> list[str]:
        """Everything that stands between this publication and an upload, as plain sentences (reads only: no call to Meta). An empty list: it can be uploaded now."""
        pub = self.registry.get(publication_id)
        return [*self._publication_blockers(pub), *FacebookConnection(self.s, self.f, tokens=self.tokens).status()["upload_blockers"]]

    def plan(self, publication_id: int) -> list[Step]:
        """DRY_RUN: no request. What each mode would do for this publication, and why it cannot yet when something blocks it."""
        pub = self.registry.get(publication_id)
        steps: list[Step] = []
        try:
            target = self.prepare(publication_id, Mode.DRY_RUN, None)
        except (Refused, PublishingRefused) as exc:
            return [Step("Checks", "failed", str(exc))]
        blockers = self._publication_blockers(pub)
        if blockers:
            return [Step("Checks", "failed", "Something blocks this upload."), *[Step("Blocker", "failed", b) for b in blockers]]
        steps.append(Step("Checks", "ok", f"{target.path.name}, {target.path.stat().st_size} bytes, approved and ready"))
        v, page = self.s.api_version, self.page_id
        if pub.kind == "short":
            steps += [Step("Start", "planned", f"POST {v}/{page}/video_reels upload_phase=start (Page access token)"),
                      Step("Upload", "planned", f"POST <upload address from Meta> offset=0 file_size={target.path.stat().st_size}"),
                      Step("Processing", "planned", "GET <video>?fields=status until video_status is ready"),
                      Step("Finish", "planned", f"POST {v}/{page}/video_reels video_state=DRAFT (SAVE_DRAFT) or PUBLISHED (PUBLISH, off)")]
        else:
            steps += [Step("Limits", "planned", "GET me?fields=video_upload_limits: the file must fit Meta's size and length"),
                      Step("Resumable upload", "planned", f"POST {v}/{self.f.app_id}/uploads, then POST upload:<id> in {self.f.film.chunk_bytes} byte chunks (resumable)"),
                      Step("Create", "planned", f"POST {self.f.video_host}/{v}/{page}/videos published=false unpublished_content_type=DRAFT (SAVE_DRAFT) or published=true (PUBLISH, off)"),
                      Step("Processing", "planned", "GET <video>?fields=status until video_status is ready")]
        return steps

    # -- sessions -----------------------------------------------------------------------------------
    def _save(self, session: FacebookUploadSession, **fields) -> None:
        for key, value in fields.items():
            setattr(session, key, value)
        self.db.commit()

    def _event(self, pub: Publication, decision: str, detail: str = "", **context) -> None:
        self.registry.event(pub, decision, detail, "owner", **context)

    def _session_for(self, target: Target) -> FacebookUploadSession:
        pub = target.publication
        digest = fresh_file_sha256(target.path)
        session = latest_session(self.db, pub.id)
        if session is not None and session.status in ACTIVE:
            if session.content_hash == digest:
                return session
            self._save(session, status="cancelled", error="The file changed after this upload started.", required_action=None)
            self._event(pub, "facebook_upload_cancelled", "the file changed", session_id=session.id)
        session = FacebookUploadSession(publication_id=pub.id, kind=pub.kind, page_id=self.page_id, content_hash=digest, metadata_hash=target.metadata_hash, total_bytes=target.path.stat().st_size)
        self.db.add(session)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise Refused("duplicate_session", "An upload of this publication or of this file is already active or finished.") from exc
        return session

    def _halt(self) -> None:
        if self.registry._stopped():
            raise Stopped("The Emergency Stop was pressed: the upload is paused and can be resumed.")

    # -- the run ------------------------------------------------------------------------------------
    async def run(self, publication_id: int, mode: Mode, confirm: str | None) -> FacebookUploadSession | None:
        """Upload (or resume, or finish) one publication. Returns the session; an interruption leaves it resumable. Never publishes unless mode is PUBLISH and every switch allows it."""
        if mode is Mode.DRY_RUN:
            raise Refused("dry_run", "A dry run makes no upload: use plan().")
        target = self.prepare(publication_id, mode, confirm)
        pub = target.publication
        if pub.id in _RUNNING:
            raise Refused("already_running", "An upload of this publication is already running.")
        _RUNNING.add(pub.id)
        session = None
        try:
            try:
                connection = FacebookConnection(self.s, self.f, tokens=self.tokens)
                checked = await connection._check(self.client)
            except ConnectError as exc:
                raise Refused(exc.reason, str(exc)) from exc
            if pub.kind == "film":
                self._fits(target, checked["upload_limits"])
            session = self._session_for(target)
            requested = {"requested_public": Mode.PUBLISH, "requested_draft": Mode.SAVE_DRAFT}.get(session.publish_status or "")
            if requested is not None and mode not in (Mode.UPLOAD_ONLY, requested):
                raise Refused("mode_conflict", f"This upload was already finished as {'public' if requested is Mode.PUBLISH else 'a draft'} and is waiting for Meta: it cannot change.")
            if session.status in ("created", "uploading", "interrupted"):
                await (self._reel_upload if pub.kind == "short" else self._film_upload)(session, target)
            if mode is not Mode.UPLOAD_ONLY and session.status in ("ready", "processing"):
                self._halt()
                await self._finish(session, target, public=mode is Mode.PUBLISH)
        except Stopped as exc:
            self._interrupt(session, pub, str(exc), "resume")
        except (MetaError, ConnectError) as exc:
            self._fail(session, pub, exc)
        finally:
            _RUNNING.discard(pub.id)
        return session or latest_session(self.db, publication_id)

    def _fits(self, target: Target, limits: dict) -> None:
        size = target.path.stat().st_size
        try:
            seconds = float(run_ffprobe(["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(target.path)], 60).strip())
        except (ProbeError, ValueError) as exc:
            raise Refused("film_unreadable", f"The film's length could not be read: {exc}") from exc
        if size > limits["size"]:
            raise Refused("film_too_large", f"The film is {size} bytes; Meta's upload limit for this token is {limits['size']} bytes.")
        if seconds > limits["length"]:
            raise Refused("film_too_long", f"The film is {seconds:.0f} seconds; Meta's upload limit for this token is {limits['length']} seconds.")

    # -- Reels --------------------------------------------------------------------------------------
    async def _reel_upload(self, session: FacebookUploadSession, target: Target) -> None:
        pub = target.publication
        resumed = session.upload_ref is not None
        if not resumed:
            video_id, url = await self.client.reel_start()
            self._save(session, remote_video_id=video_id, upload_ref=url, status="uploading", error=None, required_action=None)
            pub.status, pub.error, pub.account_id = "uploading", None, self.page_id
            self._event(pub, "facebook_upload_started", f"{session.total_bytes} bytes (Reel)", session_id=session.id, video_id=video_id)
            self.db.commit()
            offset = 0
        else:
            status = await self.client.video_status(session.remote_video_id)
            phase = status.get("uploading_phase") or {}
            offset = int(phase.get("bytes_transferred") or phase.get("bytes_transfered") or 0)       # Meta's page spells it with one r, its answer with two
            self._save(session, status="uploading", error=None, required_action=None, bytes_sent=offset)
            pub.status = "uploading"
            self._event(pub, "facebook_upload_resumed", f"from byte {offset}", session_id=session.id)
            self.db.commit()
        self._halt()
        if offset < session.total_bytes:
            await self.client.reel_upload(session.upload_ref, target.path, offset)
        self._save(session, bytes_sent=session.total_bytes, status="processing")
        pub.status = "processing"
        self.db.commit()
        await self._follow(session, pub)

    async def _follow(self, session: FacebookUploadSession, pub: Publication) -> bool:
        """Ask Meta about the video until it is done. Before the finish step a Reel is done when its file is complete at Meta (`upload_complete`: Meta starts processing only at the finish step,
        as the first real upload showed); after it, and for a created film video, when `ready`. True when done; False when Meta is still working after the configured wait (the session stays
        `processing`)."""
        wait = self.f.reels if session.kind == "short" else self.f.film
        finishing = session.publish_status in ("requested_draft", "requested_public")
        done = ("ready",) if finishing else ("upload_complete", "ready")
        waited = 0.0
        while True:
            state = await self.client.video_status(session.remote_video_id)
            video = state.get("video_status")
            self._save(session, video_status=video)
            if video in done:
                if not finishing:
                    self._ready(session, pub)
                return True
            if video in ("error", "expired", "upload_failed"):
                detail = (state.get("processing_phase") or {}).get("error") or ((state.get("uploading_phase") or {}).get("errors")) or video
                raise MetaError("processing_failed", f"Meta did not accept the video: {detail}")
            if waited >= wait.poll_max_s:
                self._save(session, error="Meta is still processing this video; check again later.")
                return False
            self._halt()
            await self.sleep(wait.poll_interval_s)
            waited += wait.poll_interval_s

    def _ready(self, session: FacebookUploadSession, pub: Publication) -> None:
        self._save(session, status="ready", error=None, required_action=None)
        pub.status, pub.error = "ready", None
        self._event(pub, "facebook_upload_ready", session.remote_video_id or "file handle", session_id=session.id)
        self.db.commit()

    async def _finish(self, session: FacebookUploadSession, target: Target, *, public: bool) -> None:
        pub = target.publication
        if session.kind == "short":
            if session.status == "processing" and not await self._follow(session, pub):
                return
            self._halt()
            try:
                self.registry.assert_ready(pub, "publish" if public else "upload", actor="owner")        # the last look before the finish
            except ReadinessChanged as exc:
                raise Refused("readiness_changed", str(exc)) from exc
            self._save(session, publish_status="requested_public" if public else "requested_draft", metadata_hash=target.metadata_hash)
            if public:
                pub.status = "publishing"
                self.db.commit()
            try:
                await self.client.reel_finish(session.remote_video_id, state="PUBLISHED" if public else "DRAFT", description=target.description, title=target.title)
            except PublishOutcomeUnknown as exc:
                self._uncertain(session, pub, exc)
                return
            except MetaError:
                if public:
                    pub.status = "ready"
                    self.db.commit()
                raise
            self._save(session, status="processing", error=None)
            if await self._follow(session, pub):
                await self._finished(session, pub, public)
        else:
            if session.remote_video_id is None:
                try:
                    self.registry.assert_ready(pub, "publish" if public else "upload", actor="owner")
                except ReadinessChanged as exc:
                    raise Refused("readiness_changed", str(exc)) from exc
                self._save(session, publish_status="requested_public" if public else "requested_draft", metadata_hash=target.metadata_hash)
                if public:
                    pub.status = "publishing"
                    self.db.commit()
                try:
                    video_id = await self.client.film_create(session.file_handle, title=target.title, description=target.description, draft=not public, public=public)
                except PublishOutcomeUnknown as exc:
                    self._uncertain(session, pub, exc)
                    return
                except MetaError:
                    if public:
                        pub.status = "ready"
                        self.db.commit()
                    raise
                self._save(session, remote_video_id=video_id, status="processing")
                pub.remote_id = video_id
                self.db.commit()
            if await self._follow(session, pub):
                await self._finished(session, pub, session.publish_status == "requested_public")

    async def _finished(self, session: FacebookUploadSession, pub: Publication, public: bool) -> None:
        pub.remote_id, pub.error = session.remote_video_id, None
        if public:
            self._save(session, status="published", publish_status="published", error=None)
            pub.status, pub.published_at = "published", utc_now()
            try:
                pub.remote_url = await self.client.permalink(session.remote_video_id)
            except MetaError:
                pub.remote_url = None
            self._event(pub, "facebook_published", session.remote_video_id, session_id=session.id)
        else:
            self._save(session, status="drafted", publish_status="draft", error=None, required_action="publish_in_business_suite")
            pub.status = "ready"
            self._event(pub, "facebook_draft_saved", session.remote_video_id, session_id=session.id)
        self.db.commit()

    # -- film upload --------------------------------------------------------------------------------
    async def _film_upload(self, session: FacebookUploadSession, target: Target) -> None:
        pub = target.publication
        resumed = session.upload_ref is not None
        if not resumed:
            session_id = await self.client.film_session(target.path.name, session.total_bytes)
            self._save(session, upload_ref=session_id, status="uploading", error=None, required_action=None)
            pub.status, pub.error, pub.account_id = "uploading", None, self.page_id
            self._event(pub, "facebook_upload_started", f"{session.total_bytes} bytes (film)", session_id=session.id)
            self.db.commit()
            offset = 0
        else:
            offset = await self.client.film_offset(session.upload_ref)
            self._save(session, status="uploading", error=None, required_action=None, bytes_sent=offset)
            pub.status = "uploading"
            self._event(pub, "facebook_upload_resumed", f"from byte {offset}", session_id=session.id)
            self.db.commit()
        handle = None
        chunk = self.f.film.chunk_bytes
        with target.path.open("rb") as stream:
            while offset < session.total_bytes:
                self._halt()
                stream.seek(offset)
                data = stream.read(chunk)
                handle = await self.client.film_chunk(session.upload_ref, offset, data) or handle
                offset += len(data)
                self._save(session, bytes_sent=offset)
        if handle is None:
            handle = session.file_handle
        if handle is None:
            raise MetaError("upload_failed", "Meta did not give a file handle for the uploaded film.")
        self._save(session, file_handle=handle, status="ready", bytes_sent=session.total_bytes, error=None, required_action=None)
        pub.status = "ready"
        self._event(pub, "facebook_upload_ready", "file handle", session_id=session.id)
        self.db.commit()

    # -- checking, cancelling, failures, restart ------------------------------------------------------
    async def check_processing(self, publication_id: int) -> FacebookUploadSession:
        """Ask Meta again about a video that was still processing (a read-only call); finishes the session when Meta is done."""
        session = latest_session(self.db, publication_id)
        if session is None or session.status != "processing" or not session.remote_video_id:
            raise Refused("nothing_to_check", "This publication has no upload waiting for Meta.")
        pub = self.registry.get(publication_id)
        try:
            if await self._follow(session, pub) and session.publish_status in ("requested_draft", "requested_public"):
                await self._finished(session, pub, session.publish_status == "requested_public")
        except MetaError as exc:
            self._fail(session, pub, exc)
        return session

    async def set_cover(self, publication_id: int, variant_id: int) -> dict:
        """Sets one of the Short's cover pictures (the reel shape) as the cover of the video already uploaded for this publication. Refused without an upload, without the permissions the cover call
        needs, or for a picture that is not a usable reel cover. The result is read back from Meta: which thumbnail it now prefers. A cover posted to Meta cannot be changed through the API."""
        from app.db.models import CoverVariant
        from app.documentary import storage

        session = latest_session(self.db, publication_id)
        if session is None or not session.remote_video_id:
            raise Refused("no_upload", "This publication has no video on Facebook yet: upload it first.")
        pub = self.registry.get(publication_id)
        variant = self.db.get(CoverVariant, variant_id)
        if variant is None or variant.case_id != pub.case_id or variant.language != pub.language:
            raise Refused("unknown_cover", "That cover does not belong to this publication.")
        if variant.shape != "reel" or variant.status not in ("proposed", "approved"):
            raise Refused("not_a_reel_cover", "Only a current 9:16 reel cover can be the cover of a Reel.")
        if json.loads(variant.problems_json or "[]"):
            raise Refused("cover_has_problems", "That cover has problems the review reported; make a new version first.")
        picture = storage.resolve(variant.file_path)
        if not picture.is_file():
            raise Refused("cover_file_missing", "The cover picture file is missing.")
        granted = await self.client.granted_permissions()
        missing = [p for p in self.f.cover.required_permissions if p not in granted]
        if missing:
            raise Refused("cover_permission_missing", f"The token does not hold: {', '.join(missing)}. Generate the system-user token again with these permissions (Meta Business settings), then try again.")
        try:
            await self.client.set_thumbnail(session.remote_video_id, picture)
            rows = await self.client.thumbnails(session.remote_video_id)
        except MetaError as exc:
            raise Refused("cover_not_set", self.tokens.redact(str(exc))) from exc
        self._event(pub, "facebook_cover_set", f"reel cover v{variant.version}", session_id=session.id, video_id=session.remote_video_id, cover_variant_id=variant.id, cover_sha256=variant.file_hash)
        self.db.commit()
        return {"video_id": session.remote_video_id, "variant_id": variant.id, "thumbnails": rows, "preferred_id": next((t["id"] for t in rows if t["is_preferred"]), None)}

    def cancel(self, publication_id: int) -> FacebookUploadSession:
        """Forget an active or drafted upload here. Nothing is deleted on Facebook: a video that exists there must be deleted by hand (the session says so)."""
        session = latest_session(self.db, publication_id)
        if session is None or session.status not in (*ACTIVE, "drafted"):
            raise Refused("nothing_to_cancel", "This publication has no active or drafted upload.")
        pub = self.registry.get(publication_id)
        left_behind = session.remote_video_id is not None
        self._save(session, status="cancelled", required_action="delete_on_facebook" if left_behind else None,
                   error="Cancelled here. Delete the video in Meta Business Suite if you do not want it." if left_behind else None)
        pub.status, pub.remote_id, pub.error = "publication_approved" if self.registry.approval_current(pub) else "draft", None, None
        self._event(pub, "facebook_upload_cancelled", "by the owner", session_id=session.id, video_id=session.remote_video_id)
        self.db.commit()
        return session

    def _uncertain(self, session: FacebookUploadSession, pub: Publication, exc: MetaError) -> None:
        message = self.tokens.redact(str(exc))
        self._save(session, status="uncertain", error=message, required_action="check_facebook")
        pub.status, pub.error = "publish_uncertain", message
        self._event(pub, "facebook_finish_unknown", message, session_id=session.id)
        self.db.commit()

    def _interrupt(self, session: FacebookUploadSession | None, pub: Publication, message: str, action: str) -> None:
        if session is not None:
            self._save(session, status="interrupted" if session.status in ("created", "uploading") else session.status, error=message, required_action=action)
        pub.error = message
        self._event(pub, "facebook_upload_interrupted", message, session_id=session.id if session else None)
        self.db.commit()

    def _fail(self, session: FacebookUploadSession | None, pub: Publication, exc: Exception) -> None:
        kind = getattr(exc, "kind", None) or getattr(exc, "reason", "retry")
        message = self.tokens.redact(str(exc))
        resumable = session is not None and session.upload_ref is not None and session.status in ("uploading", "interrupted") and kind in ("timeout", "network", "server", "rate_limited", "token_expired", "token_invalid", "upload_failed")
        if session is not None:
            self._save(session, status="interrupted" if resumable else ("processing" if session.status == "processing" and kind != "processing_failed" else "failed"), error=message,
                       required_action=_ACTION.get(kind, "retry"))
        if session is None or session.status == "failed":
            pub.status = "failed"
        pub.error = message
        self._event(pub, "facebook_upload_interrupted" if resumable else "facebook_upload_failed", message, error_kind=kind, session_id=session.id if session else None)
        self.db.commit()


def latest_session(db: Session, publication_id: int) -> FacebookUploadSession | None:
    return db.query(FacebookUploadSession).filter_by(publication_id=publication_id).order_by(FacebookUploadSession.id.desc()).first()


def sweep_interrupted(db: Session) -> int:
    """At startup: an upload that was running when the process stopped is interrupted (resumable from the owner's click); nothing is sent."""
    rows = db.query(FacebookUploadSession).filter(FacebookUploadSession.status == "uploading").all()
    for row in rows:
        row.status, row.error, row.required_action, row.updated_at = "interrupted", "The application stopped during the upload; resume it.", "resume", utc_now()
        pub = db.get(Publication, row.publication_id)
        if pub is not None:
            PublicationRegistry(db).event(pub, "facebook_upload_interrupted", "the application stopped during the upload", "system", session_id=row.id)
    db.commit()
    return len(rows)
