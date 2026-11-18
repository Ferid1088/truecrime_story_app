"""Upload one approved publication to the Farsi YouTube channel, PRIVATE, through the publications registry. Uploading a private video is not publishing it: nothing here makes a
video public or unlisted, and no code path in this phase can (the client refuses any privacyStatus but "private").

Before the first byte (and again before a resumed upload) the uploader checks, in this order: live calls are enabled; the Emergency Stop is clear; the publication is a youtube
publication of the configured language; the owner typed the confirmation for THIS publication and channel; the publication has a current approval and still meets every
requirement (the registry revalidates it and hashes the video file afresh); the same file is not already uploaded; the authorized channel is the configured channel. The state
of the upload lives in `youtube_upload_sessions` (one active session per publication), every step is a PolicyEvent."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Publication, YouTubeUploadSession
from app.documentary import storage
from app.publishing.intelligence import validate as V
from app.publishing.registry import PublicationRegistry, PublishingRefused, ReadinessChanged
from app.utils import utc_now
from app.youtube_publishing.channels import ChannelError, YouTubeChannels
from app.youtube_publishing.client import SessionExpired, YouTubeClient, YouTubeError
from app.youtube_publishing.config import YouTubeConfig, youtube_config
from app.youtube_publishing.oauth import OAuthError, YouTubeOAuth

ACTIVE = ("created", "uploading", "interrupted", "uploaded", "processing")
_RUNNING: set[int] = set()          # publications with an upload running in this process
_ACTION = {"reconnect_required": "reconnect", "not_configured": "set_up_google", "token_rejected": "reconnect", "channel_mismatch": "reconnect", "scopes_missing": "reconnect",
           "quota": "wait_for_quota", "forbidden": "check_permissions", "session_expired": "restart_upload", "unavailable": "retry"}


class Refused(RuntimeError):
    """A safety control said no. `reason` is a short code the interface shows."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def confirmation_text(publication_id: int, channel_id: str) -> str:
    return f"UPLOAD PRIVATE {publication_id} {channel_id}"


@dataclass(frozen=True)
class Target:
    publication: Publication
    path: Path
    resource: dict
    metadata_hash: str


def compose_description(pub: Publication) -> str:
    """The description as it is sent: the approved caption with its hashtags, and for a film the chapters (when the caption does not carry them already)."""
    stored = json.loads(pub.metadata_json or "{}")
    text = V.composed_text(pub.caption, [t for t in stored.get("hashtags") or []])
    chapters = [c for c in stored.get("chapters") or [] if c.get("time") and c.get("title")]
    if pub.kind == "film" and chapters and not all(c["time"] in text for c in chapters[:2]):
        text += "\n\n" + "\n".join(f"{c['time']} {c['title']}" for c in chapters)
    return text


def tag_length(tags: list[str]) -> int:
    """YouTube counts the commas between tags and two quote marks around a tag that contains a space."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


class YouTubeUploader:
    def __init__(self, db: Session, oauth: YouTubeOAuth, cfg: YouTubeConfig | None = None, *, client: YouTubeClient | None = None, registry: PublicationRegistry | None = None,
                 sleep=None):
        """One uploader per channel: it takes publications of `oauth`'s language only, and sends them with that channel's credentials only."""
        import asyncio

        self.db, self.cfg, self.oauth = db, cfg or youtube_config(), oauth
        self.sleep = sleep or asyncio.sleep
        self.client = client or YouTubeClient(oauth, self.cfg, sleep=self.sleep)
        self.registry = registry or PublicationRegistry(db)

    @classmethod
    def for_publication(cls, db: Session, publication_id: int) -> YouTubeUploader:
        """The uploader of the channel a publication belongs to (its language decides: never a choice of the caller)."""
        pub = db.get(Publication, publication_id)
        if pub is None or pub.platform != "youtube":
            raise Refused("wrong_destination", "This is not a YouTube publication.")
        try:
            return cls(db, YouTubeChannels(db).oauth(pub.language))
        except ChannelError as exc:
            raise Refused(exc.reason, str(exc)) from exc

    # -- the checks ----------------------------------------------------------------------------------
    def prepare(self, publication_id: int, confirm: str | None, *, need_confirmation: bool = True) -> Target:
        """Every check that needs no call to Google. Raises Refused (and the registry has recorded and persisted why, when readiness changed)."""
        pub = self.registry.get(publication_id)
        if not self.cfg.live_enabled:
            raise Refused("live_disabled", "Live YouTube calls are off (config/youtube_publishing.json: live_enabled is false).")
        if self.registry._stopped():
            raise Refused("emergency_stop", "The Emergency Stop is active: nothing is uploaded.")
        if pub.platform != "youtube" or pub.language != self.oauth.language:
            raise Refused("wrong_destination", f"Only youtube publications in '{self.oauth.language}' go to the {self.oauth.language} channel; this one is {pub.platform}/{pub.language}.")
        try:
            channel_id = self.oauth.expected_channel()
        except OAuthError as exc:
            raise Refused(exc.reason, str(exc)) from exc
        if need_confirmation and confirm != confirmation_text(pub.id, channel_id):
            raise Refused("confirmation_missing", "The upload needs the owner's confirmation text for this publication and channel.")
        self.registry.refresh(pub)
        self.db.commit()
        if pub.status not in ("uploading", "processing", "ready", "published"):
            try:
                self.registry.ensure_approved(pub)                 # approved by default when nothing blocks it (approval.default_approved); a blocker is refused with its reason
            except PublishingRefused as exc:
                raise Refused("not_approved", str(exc)) from exc
        if pub.status not in ("publication_approved", "scheduled", "uploading", "processing") or (pub.status in ("publication_approved", "scheduled") and not self.registry.approval_current(pub)):
            if pub.status in ("ready", "published"):
                raise Refused("already_uploaded", f"This publication is already on YouTube ({pub.remote_url or pub.remote_id}).")
            raise Refused("not_approved", "This publication has no current approval: approve its content and publication first (an approval ends when anything approved changes).")
        if pub.status in ("publication_approved", "scheduled"):
            try:
                self.registry.assert_ready(pub, "upload", actor="owner")
            except ReadinessChanged as exc:
                raise Refused("readiness_changed", str(exc)) from exc
        path = storage.resolve(pub.video_path) if pub.video_path else None
        if path is None or not path.is_file():
            raise Refused("file_missing", "The video file of this publication is missing.")
        if pub.status in ("uploading", "processing"):
            self._same_file_as_approved(pub, path)
        twin = self.db.query(Publication).filter(Publication.platform == "youtube", Publication.content_hash == pub.content_hash, Publication.id != pub.id,
                                                 Publication.remote_id.isnot(None)).first()
        if twin is not None:
            raise Refused("duplicate_file", f"This exact file is already on YouTube through another publication ({twin.remote_url or twin.remote_id}).")
        resource, problems = self._resource(pub)
        if problems:
            raise Refused("metadata_invalid", "; ".join(problems))
        return Target(pub, path, resource, hashlib.sha256(json.dumps(resource, sort_keys=True, ensure_ascii=False).encode()).hexdigest())

    def _same_file_as_approved(self, pub: Publication, path: Path) -> None:
        """A started upload goes on only while the file is still the approved one: the file is hashed again, and a different file cancels the session and voids the approval."""
        from app.publishing.hashing import fresh_file_sha256

        if fresh_file_sha256(path) == pub.content_hash:
            return
        session = self.session_of(pub.id)
        if session is not None and session.status in ACTIVE:
            self._save(session, status="cancelled", error="The video file differs from the one that was approved.", required_action=None)
        pub.status, pub.publication_approved_hash, pub.publication_approved_at, pub.error = "voided", None, None, "The video file differs from the one that was approved."
        self._event(pub, "readiness_blocked", pub.error, stage="upload")
        self.db.commit()
        raise Refused("readiness_changed", pub.error)

    def _resource(self, pub: Publication) -> tuple[dict, list[str]]:
        stored = json.loads(pub.metadata_json or "{}")
        title, description, tags = (stored.get("title") or "").strip(), compose_description(pub), [str(k) for k in stored.get("keywords") or []]
        problems = []
        if not title:
            problems.append("The title is empty.")
        if len(title) > 100:
            problems.append(f"The title has {len(title)} characters (limit 100).")
        if len(description.encode("utf-8")) > 5000:
            problems.append(f"The description has {len(description.encode('utf-8'))} bytes (limit 5000 bytes).")
        if tag_length(tags) > 500:
            problems.append(f"The tags use {tag_length(tags)} of 500 characters.")
        if any(c in f"{title}{description}" for c in "<>"):
            problems.append("The title or description contains < or >, which YouTube refuses.")
        up = self.cfg.upload
        return (YouTubeClient.video_resource(title=title, description=description, tags=tags, category_id=up.category_id, language=self.oauth.language, privacy=up.initial_privacy,
                                             made_for_kids=up.self_declared_made_for_kids, synthetic=up.contains_synthetic_media), problems)

    # -- the sessions --------------------------------------------------------------------------------
    def session_of(self, publication_id: int) -> YouTubeUploadSession | None:
        return latest_session(self.db, publication_id)

    def _save(self, session: YouTubeUploadSession, **fields) -> None:
        for key, value in fields.items():
            setattr(session, key, value)
        self.db.commit()

    def _event(self, pub: Publication, decision: str, detail: str = "", **context) -> None:
        self.registry.event(pub, decision, detail, "owner", **context)

    async def run(self, publication_id: int, confirm: str | None) -> YouTubeUploadSession:
        """Upload (or resume) one publication privately and follow it until YouTube has processed it. Returns the session; an interruption leaves it resumable."""
        target = self.prepare(publication_id, confirm)
        pub = target.publication
        if pub.id in _RUNNING:
            raise Refused("already_running", "An upload of this publication is already running.")
        _RUNNING.add(pub.id)
        session = None
        try:
            try:
                await self.oauth.test()
            except OAuthError as exc:
                raise Refused(exc.reason, str(exc)) from exc
            session = self._session_for(target)
            await self._transfer(session, target)
            await self._follow(session, pub)
        except (YouTubeError, OAuthError) as exc:
            self._fail(session, pub, exc)
        finally:
            _RUNNING.discard(pub.id)
        return session or self.session_of(publication_id)

    def _session_for(self, target: Target) -> YouTubeUploadSession:
        pub = target.publication
        from app.publishing.hashing import fresh_file_sha256

        digest = fresh_file_sha256(target.path)
        session = self.session_of(pub.id)
        if session is not None and session.status in ACTIVE:
            if session.content_hash != digest or session.metadata_hash != target.metadata_hash:
                self._save(session, status="cancelled", error="The file or the metadata changed after this upload started.")
                self._event(pub, "youtube_upload_cancelled", "the file or the metadata changed", session_id=session.id)
                session = None
            else:
                return session
        elif session is not None and session.status in ("processed",):
            raise Refused("already_uploaded", "This publication was already uploaded and processed.")
        session = YouTubeUploadSession(publication_id=pub.id, channel_id=self.oauth.expected_channel(), content_hash=digest, metadata_hash=target.metadata_hash,
                                       privacy=self.cfg.upload.initial_privacy, total_bytes=target.path.stat().st_size)
        self.db.add(session)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise Refused("duplicate_session", "An upload of this publication is already active.") from exc
        return session

    async def _transfer(self, session: YouTubeUploadSession, target: Target) -> None:
        pub = target.publication
        if session.status in ("uploaded", "processing"):
            return
        resumed = session.session_uri is not None
        try:
            if session.session_uri is None:
                self._save(session, session_uri=await self.client.start_session(target.resource, session.total_bytes), status="uploading", error=None, required_action=None)
                pub.status, pub.error, pub.account_id = "uploading", None, self.oauth.expected_channel()
                self._event(pub, "youtube_upload_started", f"{session.total_bytes} bytes, privacy {session.privacy}", session_id=session.id)
                self.db.commit()
            else:
                start, finished = await self.client.offset(session.session_uri, session.total_bytes)
                self._save(session, status="uploading", error=None, required_action=None, bytes_sent=start)
                pub.status = "uploading"
                self._event(pub, "youtube_upload_resumed", f"from byte {start}", session_id=session.id)
                self.db.commit()
                if finished is not None:
                    self._uploaded(session, pub, finished)
                    return
            video = await self.client.upload(session.session_uri, target.path, session.total_bytes, session.bytes_sent, lambda sent: self._save(session, bytes_sent=sent))
        except SessionExpired:
            self._save(session, status="expired", session_uri=None, error="The upload session expired.", required_action="restart_upload")
            self._event(pub, "youtube_upload_failed", "the upload session expired", session_id=session.id, resumed=resumed)
            raise
        self._uploaded(session, pub, video)

    def _uploaded(self, session: YouTubeUploadSession, pub: Publication, video: dict) -> None:
        video_id = str(video["id"])
        status = video.get("status") or {}
        if status.get("privacyStatus") not in (None, "private"):
            self._event(pub, "youtube_privacy_unexpected", f"YouTube reports {status.get('privacyStatus')}", session_id=session.id, video_id=video_id)
        self._save(session, status="uploaded", bytes_sent=session.total_bytes, remote_video_id=video_id, upload_status=status.get("uploadStatus"), error=None)
        pub.remote_id, pub.remote_url, pub.status, pub.error = video_id, self.watch_url(pub, video_id), "processing", None
        self._event(pub, "youtube_uploaded_private", video_id, session_id=session.id, video_id=video_id)
        self.db.commit()
        if pub.kind == "film":
            self.registry.link_shorts_to_film(pub)

    def watch_url(self, pub: Publication, video_id: str) -> str:
        return f"https://www.youtube.com/{'shorts/' if pub.kind == 'short' else 'watch?v='}{video_id}"

    # -- processing ----------------------------------------------------------------------------------
    async def _follow(self, session: YouTubeUploadSession, pub: Publication) -> None:
        waited = 0.0
        while True:
            state = await self.client.video(session.remote_video_id)
            if (state.get("status") or {}).get("privacyStatus") not in (None, "private"):
                self._event(pub, "youtube_privacy_unexpected", f"YouTube reports {state['status']['privacyStatus']}", session_id=session.id, video_id=session.remote_video_id)
                raise YouTubeError("privacy_refused", f"The video is {state['status']['privacyStatus']} on YouTube although it was uploaded private: check it in YouTube Studio.")
            details, status = state.get("processingDetails") or {}, state.get("status") or {}
            progress = details.get("processingProgress") or {}
            percent = round(100 * int(progress.get("partsProcessed", 0)) / int(progress["partsTotal"])) if progress.get("partsTotal") else None
            self._save(session, status="processing", processing_status=details.get("processingStatus"), upload_status=status.get("uploadStatus"), processing_percent=percent)
            processing, upload = details.get("processingStatus"), status.get("uploadStatus")
            if processing == "succeeded" or upload == "processed":
                break
            if processing in ("failed", "terminated") or upload in ("failed", "rejected", "deleted"):
                reason = details.get("processingFailureReason") or status.get("failureReason") or status.get("rejectionReason") or processing or upload
                raise YouTubeError("google_error", f"YouTube did not accept the video: {reason}.")
            if waited >= self.cfg.upload.processing_max_wait_seconds:
                self._save(session, error="YouTube is still processing this video; check again later.")
                return
            await self.sleep(self.cfg.upload.processing_poll_seconds)
            waited += self.cfg.upload.processing_poll_seconds
        await self._extras(session, pub)
        self._save(session, status="processed", processing_percent=100, error=None, required_action=None)
        pub.status, pub.error = "ready", None
        self._event(pub, "youtube_processing_done", session.remote_video_id, session_id=session.id)
        self.db.commit()
        if self.cfg.upload.public_after_processing:
            await self.make_public(session, pub)

    async def make_public(self, session: YouTubeUploadSession, pub: Publication) -> None:
        """Make an uploaded and processed video public (config upload.public_after_processing). Never while the Emergency Stop is active; a failure leaves the video private and says so."""
        if self.registry._stopped():
            self._event(pub, "youtube_public_skipped", "the Emergency Stop is active", session_id=session.id)
            self.db.commit()
            return
        up = self.cfg.upload
        try:
            await self.client.set_privacy(session.remote_video_id, "public", made_for_kids=up.self_declared_made_for_kids, synthetic=up.contains_synthetic_media)
        except (YouTubeError, OAuthError) as exc:
            self._save(session, error=f"The video is processed but could not be made public: {self.oauth.tokens.redact(str(exc))[:200]}", required_action="make_public")
            return
        self._became_public(session, pub, "public", "youtube_made_public")

    def _became_public(self, session: YouTubeUploadSession, pub: Publication, privacy: str, decision: str) -> None:
        pub.visibility, pub.status, pub.published_at = privacy, "published", pub.published_at or utc_now()
        self._save(session, privacy=privacy, error=None, required_action=None)
        self._event(pub, decision, session.remote_video_id, session_id=session.id)
        self.db.commit()
        if pub.kind == "film" and pub.video_id:
            self._record_film(pub)

    async def sync_visibility(self, publication_id: int) -> str:
        """Read the visibility of the uploaded video from YouTube (a video can be made public or unlisted in Studio) and bring this application's record in line. Returns what YouTube says."""
        session, pub = self.session_of(publication_id), self.registry.get(publication_id)
        if session is None or not session.remote_video_id or session.status not in ("uploaded", "processing", "processed"):
            raise Refused("no_video", "This publication has no video on YouTube yet.")
        try:
            privacy = (await self.client.video(session.remote_video_id))["status"]["privacyStatus"]
        except (YouTubeError, OAuthError) as exc:
            raise Refused("sync_failed", self.oauth.tokens.redact(str(exc))) from exc
        if privacy in ("public", "unlisted") and pub.status != "published":
            self._became_public(session, pub, privacy, "youtube_found_public")
        return privacy

    def _record_film(self, pub: Publication) -> None:
        """Mirror the public film in its `videos` row (its own publish rules apply there). A refusal there never undoes what is public: it is recorded for the owner."""
        from app.db.models import Video
        from app.publishing.registry import PublishingRefused

        try:
            self.registry.record_film_published(self.db.get(Video, pub.video_id), url=pub.remote_url, published_at=pub.published_at, episode_number=None)
        except (PublishingRefused, ValueError) as exc:
            self.db.rollback()
            self._event(self.registry.get(pub.id), "film_record_refused", str(exc)[:300])
            self.db.commit()

    async def check_processing(self, publication_id: int) -> YouTubeUploadSession:
        """Ask YouTube again about an uploaded video that was still processing (a read-only call); finishes the publication when YouTube is done."""
        session = self.session_of(publication_id)
        if session is None or session.status not in ("uploaded", "processing") or not session.remote_video_id:
            raise Refused("nothing_to_check", "This publication has no upload waiting for YouTube.")
        pub = self.registry.get(publication_id)
        try:
            await self._follow(session, pub)
        except (YouTubeError, OAuthError) as exc:
            self._fail(session, pub, exc)
        return session

    async def set_category(self, publication_id: int) -> bool:
        """Give the uploaded video the configured category (config upload.category_id). True when it was changed, False when it had that category already; the rest of the video is untouched."""
        session = self.session_of(publication_id)
        if session is None or not session.remote_video_id or session.status not in ("uploaded", "processing", "processed"):
            raise Refused("no_video", "This publication has no video on YouTube yet.")
        pub = self.registry.get(publication_id)
        try:
            video = await self.client.video(session.remote_video_id)
            if video["snippet"].get("categoryId") == self.cfg.upload.category_id:
                return False
            await self.client.set_category(video, self.cfg.upload.category_id)
        except (YouTubeError, OAuthError) as exc:
            raise Refused("category_failed", self.oauth.tokens.redact(str(exc))) from exc
        self._event(pub, "youtube_category_set", self.cfg.upload.category_id, session_id=session.id)
        self.db.commit()
        return True

    async def replace_thumbnail(self, publication_id: int) -> YouTubeUploadSession:
        """Put the publication's current approved cover on the video that is already on YouTube (thumbnails.set; the video itself is not touched). The cover is taken from the registry again, so a cover
        approved after the upload is the one that goes up."""
        session, pub = self.session_of(publication_id), self.registry.get(publication_id)
        if session is None or not session.remote_video_id or session.status not in ("uploaded", "processing", "processed"):
            raise Refused("no_video", "This publication has no video on YouTube yet.")
        pub.cover_ref, pub.cover_hash, _, _ = self.registry._cover(pub.case_id, pub.language, "long" if pub.kind == "film" else pub.asset_key)
        self.db.commit()
        cover = self._cover_file(pub)
        if cover is None:
            raise Refused("no_approved_cover", "This publication has no approved cover to put on the video.")
        try:
            await self.client.set_thumbnail(session.remote_video_id, cover)
        except (YouTubeError, OAuthError) as exc:
            self._save(session, thumbnail_status=f"failed: {self.oauth.tokens.redact(str(exc))[:120]}")
            raise Refused("thumbnail_failed", self.oauth.tokens.redact(str(exc))) from exc
        self._save(session, thumbnail_status="done")
        self._event(pub, "youtube_thumbnail_replaced", cover.name, session_id=session.id)
        self.db.commit()
        return session

    async def _extras(self, session: YouTubeUploadSession, pub: Publication) -> None:
        """The thumbnail (the approved cover) and the subtitles that exist. A failure here never undoes the upload: it is shown for the owner to act on."""
        cover = self._cover_file(pub)
        if session.thumbnail_status != "done":
            if cover is None:
                self._save(session, thumbnail_status="no approved cover")
            else:
                try:
                    await self.client.set_thumbnail(session.remote_video_id, cover)
                    self._save(session, thumbnail_status="done")
                except YouTubeError as exc:
                    self._save(session, thumbnail_status=f"failed: {self.oauth.tokens.redact(str(exc))[:120]}")
        srt = self._subtitle_file(pub)
        if session.captions_status != "done":
            if srt is None:
                self._save(session, captions_status="no subtitle file")
            else:
                try:
                    await self.client.insert_caption(session.remote_video_id, self.oauth.language, self.cfg.channels[self.oauth.language].label, srt)
                    self._save(session, captions_status="done")
                except YouTubeError as exc:
                    self._save(session, captions_status=f"failed: {self.oauth.tokens.redact(str(exc))[:120]}")

    def _cover_file(self, pub: Publication) -> Path | None:
        from app.covers import service as CS

        if not pub.cover_ref:
            return None
        shape, _, ident = pub.cover_ref.partition(":")
        if shape in ("film", "reel"):                                  # a cover made in the new style and approved (CoverVariant)
            from app.db.models import CoverVariant

            made = self.db.get(CoverVariant, int(ident))
            picture = storage.resolve(made.file_path) if made is not None and made.status == "approved" else None
            return picture if picture is not None and picture.is_file() else None
        final = CS.covers_dir(pub.case_id, pub.language) / ("long.jpg" if pub.kind == "film" else f"short_{pub.asset_key}.jpg")
        return final if final.is_file() else None

    def _subtitle_file(self, pub: Publication) -> Path | None:
        if pub.kind != "film" or pub.language not in self.cfg.upload.caption_languages or not pub.video_path:
            return None
        srt = storage.resolve(pub.video_path).with_suffix(".srt")
        return srt if srt.is_file() else None

    # -- failures and restart ------------------------------------------------------------------------
    def _fail(self, session: YouTubeUploadSession | None, pub: Publication, exc: Exception) -> None:
        reason = getattr(exc, "reason", "google_error")
        message = self.oauth.tokens.redact(str(exc))
        resumable = session is not None and session.session_uri is not None and reason in ("unavailable", "quota", "reconnect_required", "token_rejected", "forbidden") and not session.remote_video_id
        if session is not None:
            self._save(session, status=("interrupted" if resumable else ("processing" if session.remote_video_id else "failed")) if session.status != "expired" else "expired",
                       error=message, required_action=_ACTION.get(reason))
        if session is None or session.status == "failed":
            pub.status = "failed"
        pub.error = message
        self._event(pub, "youtube_upload_interrupted" if resumable else "youtube_upload_failed", message, error_kind=reason, session_id=session.id if session else None)
        self.db.commit()


def latest_session(db: Session, publication_id: int) -> YouTubeUploadSession | None:
    return db.query(YouTubeUploadSession).filter_by(publication_id=publication_id).order_by(YouTubeUploadSession.id.desc()).first()


def sweep_interrupted(db: Session) -> int:
    """At startup: an upload that was running when the process stopped is interrupted (resumable from the owner's click); nothing is sent."""
    rows = db.query(YouTubeUploadSession).filter(YouTubeUploadSession.status == "uploading").all()
    for row in rows:
        row.status, row.error, row.required_action, row.updated_at = "interrupted", "The application stopped during the upload; resume it.", "resume", utc_now()
        pub = db.get(Publication, row.publication_id)
        if pub is not None:
            PublicationRegistry(db).event(pub, "youtube_upload_interrupted", "the application stopped during the upload", "system", session_id=row.id)
    db.commit()
    return len(rows)
