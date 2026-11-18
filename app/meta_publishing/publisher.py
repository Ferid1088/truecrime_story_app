"""The Instagram Reels publisher for the Farsi account: one clip, three explicit modes, nothing automatic.

  DRY_RUN      no external request: the clip is validated and the requests that would be made are listed (the default)
  UPLOAD_ONLY  verify the account, make the Reel container, upload the file, wait for Meta to process it; never calls media_publish
  PUBLISH      calls media_publish for a container that UPLOAD_ONLY prepared, only with the owner's confirmation text for this clip

Hard blocks: any language but the configured one, any Instagram account but the configured one (the remote id and username must match before a container is made),
a clip whose file fails the Instagram preflight, a file that is already published (or whose publish outcome is unknown) on the account."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import Publication
from app.meta_publishing.config import InstagramSettings, meta_config
from app.meta_publishing.copies import prepared_copy_of
from app.meta_publishing.graph import MetaError, MetaGraphClient, PublishOutcomeUnknown
from app.meta_publishing.preflight import ClipReport, ReelsPreflight
from app.meta_publishing.token import TokenError, TokenStore
from app.meta_publishing.validator import Verdict
from app.publishing.hashing import fresh_file_sha256
from app.publishing.registry import PublicationRegistry, PublishingRefused, ReadinessChanged
from app.shortform.delivery import delivery_of
from app.youtube_publishing.uploader import compose_description

PLATFORM = "instagram"       # the destination instagram/short; the preflight keys of config/meta_publishing.json keep the older name
LEGACY_PLATFORM = "instagram_reel"
_DONE = {"FINISHED", "PUBLISHED"}
_FAILED = {"ERROR", "EXPIRED"}


class Mode(StrEnum):
    DRY_RUN = "DRY_RUN"
    UPLOAD_ONLY = "UPLOAD_ONLY"
    PUBLISH = "PUBLISH"


class Refused(RuntimeError):
    """A safety control said no. `reason` is a short code the interface shows."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Target:
    clip_key: str
    language: str
    case_id: int
    path: Path
    caption: str
    manual_steps: list[dict]
    preflight: ClipReport


@dataclass
class Step:
    name: str
    status: str            # ok | failed | skipped | planned
    detail: str = ""


@dataclass
class Report:
    mode: Mode
    clip_key: str
    language: str
    account: dict
    steps: list[Step] = field(default_factory=list)
    operation: dict | None = None
    manual_steps: list[dict] = field(default_factory=list)


def confirmation_text(language: str, clip_key: str, account_id: str) -> str:
    return f"PUBLISH {language} {clip_key} {account_id}"


def operation_dict(op: Publication) -> dict:
    return {"id": op.id, "case_id": op.case_id, "language": op.language, "clip_key": op.asset_key, "status": op.status, "container_id": op.container_id, "media_id": op.remote_id,
            "permalink": op.remote_url, "error": op.error, "content_hash": op.content_hash[:12], "updated_at": op.updated_at.isoformat() if op.updated_at else None}


class InstagramReelsPublisher:
    def __init__(self, settings: InstagramSettings | None = None, *, client: MetaGraphClient | None = None, preflight: ReelsPreflight | None = None,
                 tokens: TokenStore | None = None, sleep=None):
        import asyncio

        self.s = settings or meta_config().instagram
        self.tokens = tokens or TokenStore(self.s)
        self.sleep = sleep or asyncio.sleep
        self.client = client or MetaGraphClient(self.s, self.tokens, sleep=self.sleep)
        self.preflight = preflight or ReelsPreflight()

    @property
    def account(self) -> dict:
        return {"ig_account_id": self.s.account.ig_account_id, "username": self.s.account.username, "brand": self.s.account.brand}

    def resolve(self, db: Session, language: str, clip_key: str) -> Target:
        """The clip, its file (the delivery copy, else a prepared copy beside the original, when one exists), its caption and its preflight. Refuses any other language."""
        from app.documentary import storage
        from app.shortform import clips as C
        from app.shortform.publish_manifest import build_publish_manifest

        if language != self.s.language:
            raise Refused("language_blocked", f"Only the '{self.s.language}' account is connected; '{language}' is blocked.")
        case_id = meta_config().preflight.case_id
        row = next((r for r in C.clips_of(db, case_id, language) if r.clip_key == clip_key), None)
        video = (json.loads(row.media_json or "{}").get("video") or {}).get("path") if row else None
        original = storage.resolve(video) if video else None
        if row is None or original is None:
            raise Refused("clip_not_found", f"Case {case_id} has no finished {language} clip {clip_key}.")
        path = delivery_of(original) or prepared_copy_of(original) or original
        manifest = build_publish_manifest(LEGACY_PLATFORM, film_id=case_id, short_id=clip_key, language=language, uses_generated_music=True)
        pub = db.query(Publication).filter_by(case_id=case_id, language=language, platform=PLATFORM, kind="short", asset_key=clip_key).first()
        caption = compose_description(pub) if pub is not None and pub.caption.strip() else manifest.caption       # the approved text of the publication, with its hashtags
        return Target(clip_key, language, case_id, path, caption, manifest.manual_steps, self.preflight.check_file(clip_key, path))

    def _validation_step(self, target: Target) -> Step:
        report = target.preflight
        if report.error:
            return Step("Video validation", "failed", report.error)
        verdict = report.verdict(LEGACY_PLATFORM)
        detail = f"Instagram preflight: {verdict.value} ({target.path.name})"
        failed = [c.name for c in report.platforms[LEGACY_PLATFORM].by(Verdict.FAIL)]
        return Step("Video validation", "failed" if verdict is Verdict.FAIL else "ok", detail + (f"; failing: {', '.join(failed)}" if failed else ""))

    def dry_run(self, target: Target) -> Report:
        """No external request: what would be sent where."""
        report = Report(Mode.DRY_RUN, target.clip_key, target.language, self.account, manual_steps=target.manual_steps)
        report.steps.append(self._validation_step(target))
        v, ig = self.s.api_version, self.s.account.ig_account_id
        planned = [("Account check", f"GET {v}/{ig}?fields=id,username (must equal {self.s.account.username})"),
                   ("Permission check", f"GET {v}/{ig}/content_publishing_limit"),
                   ("Create container", f"POST {v}/{ig}/media  media_type=REELS upload_type=resumable caption=({len(target.caption)} characters)"),
                   ("Upload", f"POST {self.s.upload_host}/ig-api-upload/{v}/<container>  file_size={target.path.stat().st_size}"),
                   ("Container processing", "GET <container>?fields=status_code until FINISHED"),
                   ("Publish", f"POST {v}/{ig}/media_publish  (only in PUBLISH mode, with the owner's confirmation)")]
        report.steps += [Step(name, "planned", detail) for name, detail in planned]
        return report

    def guard_live(self, target: Target, mode: Mode, confirm: str | None) -> None:
        if not self.s.live_enabled:
            raise Refused("live_disabled", "Live calls are off (config/meta_publishing.json: instagram.live_enabled is false).")
        try:
            self.tokens.get()
        except TokenError as exc:
            raise Refused("token_missing", str(exc)) from exc
        if target.preflight.error or target.preflight.verdict(LEGACY_PLATFORM) is Verdict.FAIL:
            raise Refused("video_invalid", self._validation_step(target).detail)
        if mode is Mode.PUBLISH and confirm != confirmation_text(target.language, target.clip_key, self.s.account.ig_account_id):
            raise Refused("confirmation_missing", "PUBLISH needs the owner's confirmation text for this clip and account.")

    async def authorize(self) -> list[Step]:
        """Verify the configured account and that the token may publish to it. Raises MetaError / Refused; never exposes the token."""
        info = await self.client.account()
        if str(info.get("id")) != self.s.account.ig_account_id or info.get("username") != self.s.account.username:
            raise Refused("account_mismatch", "The token reaches a different Instagram account than the configured one; nothing was sent.")
        missing = [p for p in self.s.required_permissions if p not in await self.client.granted_permissions()]
        if missing:
            raise Refused("permission_missing", f"Meta documents these permissions for publishing and the token does not hold them: {', '.join(missing)}. Nothing was sent.")
        limit = await self.client.publishing_limit()
        usage = ((limit.get("data") or [{}])[0]).get("quota_usage") if isinstance(limit.get("data"), list) else None
        return [Step("Account", "ok", f"{info.get('username')} ({info.get('id')}) matches the configuration"),
                Step("Publishing permission", "ok", f"content_publishing_limit answered; quota used: {usage if usage is not None else 'n/a'}")]

    def _ops(self, db: Session, content_hash: str, *statuses: str) -> list[Publication]:
        return (db.query(Publication).filter(Publication.platform == PLATFORM, Publication.account_id == self.s.account.ig_account_id,
                                                      Publication.content_hash == content_hash, Publication.status.in_(statuses))
                .order_by(Publication.id.desc()).all())

    @staticmethod
    def _save(db: Session, op: Publication, status: str | None = None, error: str | None = None) -> None:
        if status:
            op.status = status
        op.error = error
        db.commit()

    @staticmethod
    def _readiness(registry: PublicationRegistry, op: Publication, stage: str) -> None:
        """The requirements are checked again now (cover, film rules, the platform's media rules, the file's hash): an earlier approval is not enough. The registry records why."""
        try:
            registry.assert_ready(op, stage, actor="owner")
        except ReadinessChanged as exc:
            raise Refused("readiness_changed", str(exc)) from exc

    async def _poll(self, db: Session, op: Publication) -> Step:
        waited = 0.0
        while True:
            status = await self.client.container_status(op.container_id)
            if status in _DONE:
                self._save(db, op, "ready" if status == "FINISHED" else "published")
                return Step("Container processing", "ok", status)
            if status in _FAILED:
                self._save(db, op, "failed", f"Meta reported the container as {status}")
                return Step("Container processing", "failed", status)
            if waited >= self.s.poll_max_s:
                self._save(db, op, "processing")
                return Step("Container processing", "failed", f"still {status} after {self.s.poll_max_s:g} s; run UPLOAD_ONLY again to keep waiting")
            await self.sleep(self.s.poll_interval_s)
            waited += self.s.poll_interval_s

    async def run(self, db: Session, *, language: str, clip_key: str, mode: Mode, confirm: str | None = None) -> Report:
        target = self.resolve(db, language, clip_key)
        if mode is Mode.DRY_RUN:
            return self.dry_run(target)
        self.guard_live(target, mode, confirm)
        content_hash = fresh_file_sha256(target.path)
        report = Report(mode, clip_key, language, self.account, manual_steps=target.manual_steps)
        report.steps.append(self._validation_step(target))
        if self._ops(db, content_hash, "published", "publish_uncertain", "publishing"):
            raise Refused("duplicate_publish", "This exact file is already published, being published, or its publish outcome is unknown on this account. Check Instagram first.")
        registry = PublicationRegistry(db)
        op = db.query(Publication).filter_by(case_id=target.case_id, language=language, platform=PLATFORM, kind="short", asset_key=clip_key).first()
        if op is None:
            raise Refused("not_planned", "This clip has no publication yet: make the publishing plan for this case and language in the Publishing Studio first.")
        if waiting := registry.film_blockers(op):
            raise Refused("film_first", " ".join(waiting))                    # the approval is left as it is: only the order is wrong
        registry.refresh(op)
        db.commit()                                         # an expiry or a void the refresh found is saved before anything else happens
        try:
            if op.status not in ("uploading", "processing", "ready", "publishing", "published"):
                registry.ensure_approved(op)                # approved by default when nothing blocks it (approval.default_approved)
            elif not registry.approval_current(op):
                raise PublishingRefused("This publication has no current approval.")
        except PublishingRefused as exc:
            raise Refused("not_approved", str(exc)) from exc
        self._readiness(registry, op, "publish" if mode is Mode.PUBLISH else "upload")
        if mode is Mode.PUBLISH and not (op.status == "ready" and op.container_id):
            raise Refused("not_prepared", "No prepared Reel for this file: run UPLOAD_ONLY and wait until it is ready.")
        if op.status == "failed":
            op.container_id, op.error = None, None
        op.account_id, op.attempt_count = self.s.account.ig_account_id, (op.attempt_count or 0) + (1 if op.container_id is None else 0)
        db.commit()
        try:
            report.steps += await self.authorize()
            if mode is Mode.UPLOAD_ONLY:
                await self._upload_only(db, op, target, report)
            else:
                await self._publish(db, op, report)
        except (MetaError, Refused) as exc:
            if isinstance(exc, Refused) and exc.reason == "readiness_changed":
                report.steps.append(Step("Stopped", "failed", self.tokens.redact(str(exc))))      # the registry has recorded the reason and voided the approval
            else:
                keep = op.status if op.status in {"ready", "processing"} and mode is Mode.PUBLISH else "failed"
                self._save(db, op, keep, self.tokens.redact(str(exc)))
                report.steps.append(Step("Stopped", "failed", self.tokens.redact(str(exc))))
        report.operation = operation_dict(op)
        return report

    async def _upload_only(self, db: Session, op: Publication, target: Target, report: Report) -> None:
        if op.container_id is None:
            op.container_id = await self.client.create_container(target.caption)
            self._save(db, op, "uploading")
            report.steps.append(Step("Container", "ok", op.container_id))
            await self.client.upload(op.container_id, target.path)
            self._save(db, op, "processing")
            report.steps.append(Step("Upload", "ok", f"{target.path.stat().st_size} bytes"))
        else:
            report.steps.append(Step("Container", "ok", f"{op.container_id} (reused, no second upload)"))
        report.steps.append(await self._poll(db, op))
        report.steps.append(Step("Publish", "skipped", "UPLOAD_ONLY never calls media_publish"))

    async def _publish(self, db: Session, op: Publication, report: Report) -> None:
        if op.status != "ready":
            raise Refused("not_ready", f"The prepared Reel is '{op.status}', not ready.")
        status = await self.client.container_status(op.container_id)
        if status != "FINISHED":
            raise Refused("not_ready", f"Meta reports the container as {status}; only FINISHED can be published.")
        self._readiness(PublicationRegistry(db), op, "publish")        # the last look before the one irreversible call
        self._save(db, op, "publishing")
        try:
            op.remote_id = await self.client.publish(op.container_id)
        except PublishOutcomeUnknown as exc:
            self._save(db, op, "publish_uncertain", self.tokens.redact(str(exc)))
            report.steps.append(Step("Publish", "failed", "No answer from Meta: the Reel may be public. Check Instagram; this file is blocked from a second publish."))
            return
        except MetaError:
            self._save(db, op, "ready")
            raise
        self._save(db, op, "published")
        report.steps.append(Step("Publish", "ok", f"media id {op.remote_id}"))
        try:
            op.remote_url = (await self.client.media(op.remote_id)).get("permalink")
            db.commit()
            report.steps.append(Step("Permalink", "ok" if op.remote_url else "failed", op.remote_url or "Meta returned no permalink"))
        except MetaError as exc:
            report.steps.append(Step("Permalink", "failed", self.tokens.redact(str(exc))))
