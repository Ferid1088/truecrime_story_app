"""The publication registry: one `Publication` per asset (a film or a Short) and destination (platform and language), with its plan, its two approvals, its routing links and the
audit trail. Nothing here publishes: live operations (the Instagram module today) work on these rows and are gated by their approvals.

Approval is in two kinds. The *content* approval binds the video file, the cover and the caption. The *publication* approval binds those and the links and settings (visibility,
schedule); it is valid for `approval.max_age_days`. A change to anything approved voids the approval (status `voided`); an old approval becomes `expired`. A film's URL and
publication state are written only here (`record_film_published`), which mirrors them to the `videos` row."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import (
    CoverProposal,
    PolicyEvent,
    Publication,
    PublicationAccount,
    PublishingPlanRun,
    RoutingLink,
    ShortClip,
    Video,
)
from app.documentary import storage
from app.meta_publishing.config import meta_config
from app.publishing.config import PublishingConfig, publishing_config
from app.publishing.hashing import cached_file_sha256, composite_hash, fresh_file_sha256
from app.publishing.vocabulary import CapabilityKey, Destination, Kind, Platform, RoutingStatus
from app.utils import utc_now

PLAN_STATUSES = ("draft", "content_approved", "publication_approved", "scheduled", "voided", "expired")
LIVE_STATUSES = ("uploading", "processing", "ready", "publishing")
FINAL_STATUSES = ("published", "failed", "publish_uncertain")
EDITABLE = (*PLAN_STATUSES, "failed")
CONTENT_APPROVABLE = ("draft", "content_approved", "voided", "expired", "failed")
PUBLICATION_APPROVABLE = ("content_approved", "voided", "expired", "publication_approved")
AI_WRITABLE = ("draft", "voided", "expired", "failed")
MANUAL_LINK_STATUSES = (RoutingStatus.URL_RECORDED.value, RoutingStatus.LINK_CREATED.value, RoutingStatus.CLICKABILITY_VERIFIED.value,
                        RoutingStatus.MANUAL_ACTION_REQUIRED.value, RoutingStatus.UNSUPPORTED.value)


class PublishingRefused(ValueError):
    """A rule of the publishing layer said no (the API answers 409 with this message and, when there are several, each reason on its own)."""

    def __init__(self, message: str, reasons: list[str] | None = None):
        super().__init__(message)
        self.reasons = list(reasons or [])


class ReadinessChanged(PublishingRefused):
    """An approved publication no longer meets the requirements. The registry has already recorded why (and voided the approval) when this is raised."""


@dataclass(frozen=True)
class Asset:
    """What can be published for a language: the file, where it comes from and what the cover is."""

    kind: Kind
    key: str                       # "V<video id>" or the clip key
    label: str
    path: Path | None
    video: Video | None
    clip: ShortClip | None
    cover_key: str                 # "long" or the clip key


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat() if moment else None


def _aware(moment: datetime | None) -> datetime | None:
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=UTC)


class PublicationRegistry:
    def __init__(self, db: Session, cfg: PublishingConfig | None = None, *, now=utc_now):
        self.db, self.cfg, self.now = db, cfg or publishing_config(), now

    # -- events --------------------------------------------------------------------------------
    def event(self, pub: Publication, decision: str, reason: str = "", actor: str = "system", **context) -> None:
        self.db.add(PolicyEvent(case_id=pub.case_id, language=pub.language, policy="publication", decision=decision, reason=reason or None,
                                context_json=json.dumps({"publication_id": pub.id, "destination": f"{pub.platform}/{pub.kind}", "asset": pub.asset_key,
                                                         "actor": actor, **context}, sort_keys=True, default=str)))

    # -- accounts ------------------------------------------------------------------------------
    def ensure_accounts(self, language: str) -> dict[str, PublicationAccount]:
        """One account row per platform for the language, filled from the configuration that already holds the ids (read, never changed). Idempotent."""
        from app.meta_publishing.config import meta_config
        from app.shortform.release_plan import load_config as social_config

        known: dict[str, tuple[str, str, str]] = {}
        youtube = ((social_config().get("accounts") or {}).get("youtube_short") or {}).get(language) or {}
        if youtube.get("account_id"):
            known["youtube"] = (youtube["account_id"], youtube.get("channel_url") or "", "connected")
        ig = meta_config().instagram
        if language == ig.language:
            known["instagram"] = (ig.account.ig_account_id, f"@{ig.account.username}", "not_connected")      # known, not yet verified: the connection check decides
        out = {}
        for platform in Platform:
            row = self.db.query(PublicationAccount).filter_by(platform=platform.value, language=language).first()
            if row is None:
                external, name, status = known.get(platform.value, ("", "", "not_connected"))
                row = PublicationAccount(platform=platform.value, language=language, external_id=external, display_name=name, status=status)
                self.db.add(row)
            out[platform.value] = row
        self.db.commit()
        return out

    # -- assets --------------------------------------------------------------------------------
    def assets(self, case_id: int, language: str) -> list[Asset]:
        """The newest non-archived film of the language and its valid ShortClips (newest version of each clip), straight from the database."""
        from app.shortform import clips as C

        out: list[Asset] = []
        video = (self.db.query(Video).filter(Video.case_id == case_id, Video.language == language, Video.state != "archived").order_by(Video.id.desc()).first())
        if video is not None:
            out.append(Asset(Kind.FILM, f"V{video.id}", video.youtube_title or video.title or f"Film {video.id}", storage.resolve(video.file_path), video, None, "long"))
        for row in C.clips_of(self.db, case_id, language):
            media = (json.loads(row.media_json or "{}").get("video") or {}).get("path")
            out.append(Asset(Kind.SHORT, row.clip_key, f"Short {row.clip_key}", storage.resolve(media) if media else None, None, row, row.clip_key))
        return out

    def _cover(self, case_id: int, language: str, cover_key: str) -> tuple[str | None, str | None, str, str | None]:
        """(cover reference, cover hash, state, problem) of the newest cover proposal of a key. Reference and hash exist only for an approved cover whose final file is a readable
        image of the right shape; `problem` says what is wrong with a cover that is not usable."""
        from app.covers import proposals as P
        from app.covers import service as CS

        proposal: CoverProposal | None = P.current(self.db, case_id, language).get(cover_key)
        if proposal is None:
            return None, None, "none", "The cover is not approved."
        if proposal.status != "approved":
            return None, None, proposal.status, "The cover is not approved."
        final = CS.covers_dir(case_id, language) / ("long.jpg" if cover_key == "long" else f"short_{cover_key}.jpg")
        if not final.is_file():
            return None, None, "approved", "The cover is not approved."          # an approval whose file is gone is no approval
        problem = self._cover_file_problem(final, cover_key)
        if problem:
            return None, None, "invalid", problem
        return str(proposal.id), cached_file_sha256(self.db, final), "approved", None

    @staticmethod
    def _cover_file_problem(final: Path, cover_key: str) -> str | None:
        from PIL import Image, UnidentifiedImageError

        from app.core.ai_config import ai_config

        try:
            with Image.open(final) as image:
                width, height = image.size
        except (OSError, UnidentifiedImageError):
            return "The approved cover file is not a readable image."
        shape = ai_config.covers_settings().formats["long" if cover_key == "long" else "short"]
        if abs(width / height - shape.width / shape.height) > 0.02 * shape.width / shape.height:
            return f"The approved cover has the wrong shape ({'16:9' if cover_key == 'long' else '9:16'} is needed)."
        return None

    # -- plan ----------------------------------------------------------------------------------
    def _destinations(self, kind: Kind) -> list[Destination]:
        return [d.destination for d in self.cfg.destinations if d.kind == kind]

    def _rotation(self) -> dict[tuple[int, str], int]:
        """(short number, destination) -> template day, from the rotation template in social_distribution.json (read only)."""
        from app.shortform.release_plan import load_config as social_config

        out: dict[tuple[int, str], int] = {}
        for slot in social_config().get("schedule_template") or []:
            for legacy in slot.get("platforms", []):
                dest = Destination.from_legacy(legacy)
                out.setdefault((int(slot["short"]), str(dest)), int(slot["day"]))
        return out

    def default_start(self, language: str) -> date:
        zone = ZoneInfo(self.cfg.languages[language].timezone or "UTC")
        return self.now().astimezone(zone).date() + timedelta(days=self.cfg.plan.start_offset_days)

    def _set_schedule(self, pub: Publication, language: str, start: date, number: int | None, rotation: dict[tuple[int, str], int]) -> None:
        if pub.kind == Kind.FILM.value:
            day = 0
        else:
            day = rotation.get((number or 0, f"{pub.platform}/{pub.kind}"))
        if day is None:
            pub.scheduled_at = pub.window_ends_at = None
            return
        pub.scheduled_at = self.cfg.slot_utc(language, start + timedelta(days=day))
        pub.window_ends_at = pub.scheduled_at + timedelta(minutes=self.cfg.automatic_publishing.missed_window_minutes)

    def plan(self, case_id: int, language: str, *, start_date: date | None = None, reset_schedule: bool = False) -> list[Publication]:
        """Create the missing publications of the language (every asset on every destination that supports its kind) and refresh the existing ones. A plan can only be made for a
        language that has a publishing schedule. Publications of an older render that were never published are voided."""
        if not self.cfg.language_enabled(language):
            raise PublishingRefused(f"Language '{language}' has no publishing schedule (config/publishing.json): its publishing is not active.")
        accounts = self.ensure_accounts(language)
        assets = self.assets(case_id, language)
        start = start_date or self.default_start(language)
        rotation = self._rotation()
        clip_numbers = {a.key: i for i, a in enumerate((x for x in assets if x.kind is Kind.SHORT), start=1)}
        current = {(a.kind.value, a.key) for a in assets}
        for old in self.db.query(Publication).filter(Publication.case_id == case_id, Publication.language == language).all():
            if (old.kind, old.asset_key) not in current and old.status in PLAN_STATUSES and old.status != "voided":
                old.status, old.error = "voided", "Superseded by a newer render."
                self.event(old, "voided", "superseded by a newer render")
        made: list[Publication] = []
        for asset in assets:
            for dest in self._destinations(asset.kind):
                pub = (self.db.query(Publication).filter_by(case_id=case_id, language=language, platform=dest.platform.value, kind=dest.kind.value, asset_key=asset.key).first())
                fresh = pub is None
                if fresh:
                    account = accounts[dest.platform.value]
                    pub = Publication(case_id=case_id, language=language, platform=dest.platform.value, kind=dest.kind.value, asset_key=asset.key,
                                      video_id=asset.video.id if asset.video else None, short_clip_id=asset.clip.id if asset.clip else None,
                                      account_ref_id=account.id, account_id=account.external_id, visibility=self.cfg.plan.default_visibility, status="draft",
                                      caption=self._default_caption(asset, dest, language, case_id))
                    self.db.add(pub)
                    self.db.flush()
                if (fresh or reset_schedule) and pub.status in EDITABLE:
                    self._set_schedule(pub, language, start, clip_numbers.get(asset.key), rotation)
                if pub.status in PLAN_STATUSES or fresh:
                    path = self._path_for(asset, dest.platform)
                    pub.video_path = storage.rel(path) if path else ""
                self.refresh(pub, asset=asset)
                made.append(pub)
        self.db.commit()
        for pub in made:
            self.sync_routing(pub)
        self.db.commit()
        return made

    @staticmethod
    def _path_for(asset: Asset, platform: str) -> Path | None:
        """The file a destination gets: Instagram and Facebook get a Short's prepared Reel copy when one exists (config/meta_publishing.json), every other destination the original."""
        if asset.kind is Kind.SHORT and platform in ("instagram", "facebook"):
            from app.meta_publishing.copies import prepared_copy_of

            return prepared_copy_of(asset.path) or asset.path
        return asset.path

    def _default_caption(self, asset: Asset, dest: Destination, language: str, case_id: int) -> str:
        if asset.kind is Kind.FILM:
            return asset.label
        from app.shortform.publish_manifest import build_publish_manifest

        legacy = next(name for name, entry in self.cfg.vocabulary.legacy_names.items() if entry.platform == dest.platform and entry.kind is Kind.SHORT and "_" in name)
        return build_publish_manifest(legacy, film_id=case_id, short_id=asset.key, language=language, uses_generated_music=True).caption

    # -- hashes, readiness and approval state ----------------------------------------------------
    def _links_input(self, pub: Publication) -> list[list]:
        """The links that belong to the approval: method, target and URL of every link that is not a post-publication update."""
        later = {(r.source, r.target, r.method.value) for r in self.cfg.routing.rules if r.after_publication}
        source = f"{pub.platform}/{pub.kind}"
        rows = self.db.query(RoutingLink).filter_by(publication_id=pub.id).order_by(RoutingLink.id).all()
        return [[r.target_destination, r.method, r.url or ""] for r in rows if (source, r.target_destination, r.method) not in later]

    def refresh(self, pub: Publication, *, asset: Asset | None = None) -> Publication:
        """Recompute the hashes of a publication and apply what they say: a changed approved part voids the approval, an old publication approval expires."""
        if pub.status in (*LIVE_STATUSES, *FINAL_STATUSES) and pub.status != "failed":
            return pub
        path = storage.resolve(pub.video_path) if pub.video_path else None
        if path is not None and path.is_file():
            pub.content_hash = cached_file_sha256(self.db, path)
        cover_key = "long" if pub.kind == Kind.FILM.value else pub.asset_key
        cover_ref, cover_hash, _, _ = self._cover(pub.case_id, pub.language, cover_key)
        pub.cover_ref, pub.cover_hash = cover_ref, cover_hash
        metadata = json.dumps(json.loads(pub.metadata_json or "{}"), sort_keys=True, ensure_ascii=False)
        content = composite_hash({"video": pub.content_hash, "cover": pub.cover_hash, "caption": pub.caption, "metadata": metadata})
        settings = {"visibility": pub.visibility, "scheduled_at": _iso(pub.scheduled_at), "destination": f"{pub.platform}/{pub.kind}"}
        links = self._links_input(pub) if pub.id else []
        full = composite_hash({"content": content, "links": links, "settings": settings})
        previous = json.loads(pub.hash_inputs_json or "{}")
        pub.hash_inputs_json = json.dumps({"content": content, "links": links, "settings": settings, "readiness": previous.get("readiness", {})}, sort_keys=True)
        pub.publication_hash = full
        self._apply_approval_state(pub, content, full)
        return pub

    def _apply_approval_state(self, pub: Publication, content: str, full: str) -> None:
        if pub.status not in ("content_approved", "publication_approved", "scheduled"):
            return
        if pub.content_approved_hash != content:
            pub.status, pub.content_approved_hash, pub.content_approved_at = "voided", None, None
            pub.publication_approved_hash = pub.publication_approved_at = None
            self.event(pub, "approval_voided", "the video, cover or caption changed")
            return
        if pub.status in ("publication_approved", "scheduled"):
            if pub.publication_approved_hash != full:
                pub.status, pub.publication_approved_hash, pub.publication_approved_at = "voided", None, None
                self.event(pub, "approval_voided", "the links or settings changed")
            elif self.approval_expired(pub):
                pub.status, pub.publication_approved_hash, pub.publication_approved_at = "expired", None, None
                self.event(pub, "approval_expired", f"older than {self.cfg.approval.max_age_days} days")

    def approval_expired(self, pub: Publication) -> bool:
        at = _aware(pub.publication_approved_at)
        return at is not None and self.now() - at > timedelta(days=self.cfg.approval.max_age_days)

    def approval_current(self, pub: Publication) -> bool:
        """The publication approval exists, matches the current hash and is not older than the configured age (whatever the status: a live operation uses this)."""
        return bool(pub.publication_approved_hash and pub.publication_approved_hash == pub.publication_hash and not self.approval_expired(pub))

    def approval_valid(self, pub: Publication) -> bool:
        return pub.status in ("publication_approved", "scheduled") and self.approval_current(pub)

    def blockers(self, pub: Publication) -> list[str]:
        """Why this publication cannot be approved (an empty list: it can). These are facts of the asset, not of the owner's wishes."""
        out: list[str] = []
        path = storage.resolve(pub.video_path) if pub.video_path else None
        if path is None or not path.is_file():
            return ["The video file is missing."]
        if pub.kind == Kind.FILM.value:
            video = self.db.get(Video, pub.video_id) if pub.video_id else None
            if video is not None:
                from app.lifecycle import videos as V

                out += V.publish_blockers(self.db, video)
        elif pub.platform in ("instagram", "facebook"):
            out += self._preflight(pub, path)
        out += self._metadata_blockers(pub)
        if self.cfg.readiness.require_approved_cover:
            *_, problem = self._cover(pub.case_id, pub.language, "long" if pub.kind == Kind.FILM.value else pub.asset_key)
            if problem:
                out.append(problem)
        return out

    def _metadata_blockers(self, pub: Publication) -> list[str]:
        """The platform's own limits and formats on the metadata that is there (what is missing is the plan approval's business, not an item's)."""
        from app.publishing.intelligence import validate as V

        stored = json.loads(pub.metadata_json or "{}")
        if not stored:
            return []
        limit = self.cfg.intelligence.metadata_limits[f"{pub.platform}/{pub.kind}"]
        item, _ = V.tidy({**stored, "text": pub.caption})
        return [f"Metadata: {f['message']}." for f in V.metadata_findings(item, pub.kind, limit) if f["severity"] == "error" and f["type"] not in ("no_title", "empty_text")]

    def _preflight(self, pub: Publication, path: Path) -> list[str]:
        """The Meta media preflight of a Short for Instagram or Facebook; the result is kept with the publication, per file hash, so it runs once per file."""
        data = json.loads(pub.hash_inputs_json or "{}")
        kept = data.get("readiness", {}).get("preflight")
        if not kept or kept.get("for") != pub.content_hash:
            from app.meta_publishing.preflight import ReelsPreflight

            report = ReelsPreflight().check_file(pub.asset_key, path)
            platform = f"{pub.platform}_reel"
            failing = [c.name for c in report.platforms[platform].checks if c.verdict.value == "FAIL"] if platform in report.platforms else []
            kept = {"for": pub.content_hash, "verdict": report.verdict(platform).value, "failing": failing, "error": report.error}
            data.setdefault("readiness", {})["preflight"] = kept
            pub.hash_inputs_json = json.dumps(data, sort_keys=True)
            self.db.commit()
        if kept.get("error"):
            return [f"The video could not be checked: {kept['error']}"]
        if kept["verdict"] == "FAIL":
            return [f"Fails the {pub.platform.title()} media check: {', '.join(kept['failing'])}."]
        return []

    def warnings(self, pub: Publication) -> list[str]:
        out = []
        account = self.db.get(PublicationAccount, pub.account_ref_id) if pub.account_ref_id else None
        if account is None or account.status != "connected":
            out.append("The account is not connected.")
        if not pub.cover_ref:
            out.append("No approved cover.")
        destination = self.cfg.destination(pub.platform, pub.kind)
        if destination is not None and not destination.automation_allowed:
            out.append("Automatic publishing is not allowed for this destination.")
        return out

    # -- editing and approval --------------------------------------------------------------------
    def get(self, publication_id: int) -> Publication:
        pub = self.db.get(Publication, publication_id)
        if pub is None:
            raise PublishingRefused("Publication not found.")
        return pub

    def update(self, publication_id: int, *, caption: str | None = None, scheduled_local: str | None = None, clear_schedule: bool = False,
               visibility: str | None = None, metadata: dict | None = None, actor: str = "owner") -> Publication:
        """Edit the caption, the schedule (a local time in the language's zone) or the visibility. Any of these changes what an approval was bound to, so it voids it."""
        pub = self.get(publication_id)
        if pub.status not in EDITABLE:
            raise PublishingRefused(f"A publication that is {pub.status} cannot be edited.")
        changed = []
        if caption is not None and caption != pub.caption:
            if not caption.strip():
                raise PublishingRefused("The caption cannot be empty.")
            pub.caption = caption
            if pub.metadata_source == "ai":
                pub.metadata_source = "owner"                         # a text you changed is yours: a later AI run offers its version, it does not replace yours
            changed.append("caption")
        if metadata is not None:
            changed += self._set_metadata(pub, metadata, "owner")
        if visibility is not None and visibility != pub.visibility:
            if visibility not in ("private", "unlisted", "public"):
                raise PublishingRefused("Visibility is private, unlisted or public.")
            pub.visibility = visibility
            changed.append("visibility")
        if clear_schedule:
            pub.scheduled_at = pub.window_ends_at = None
            changed.append("schedule")
        elif scheduled_local is not None:
            zone = ZoneInfo(self.cfg.languages[pub.language].timezone or "UTC") if self.cfg.language_enabled(pub.language) else ZoneInfo("UTC")
            try:
                local = datetime.fromisoformat(scheduled_local).replace(tzinfo=zone)
            except ValueError as exc:
                raise PublishingRefused("The time is not a valid local date and time.") from exc
            pub.scheduled_at = local.astimezone(UTC)
            pub.window_ends_at = pub.scheduled_at + timedelta(minutes=self.cfg.automatic_publishing.missed_window_minutes)
            changed.append("schedule")
        if changed:
            self.event(pub, "edited", ", ".join(changed), actor)
        self.refresh(pub)
        self.db.commit()
        return pub

    @staticmethod
    def _set_metadata(pub: Publication, metadata: dict, source: str) -> list[str]:
        """Store the destination's metadata (title, hashtags, keywords, chapters, call to action, routing note) in its tidy form; returns ["metadata"] when it changed. Whatever the
        owner sets makes the metadata the owner's: a later AI run does not replace it."""
        from app.publishing.intelligence.validate import tidy

        item, _ = tidy({**json.loads(pub.metadata_json or "{}"), **metadata})
        stored = {k: item[k] for k in ("title", "hashtags", "keywords", "chapters", "cta", "routing_note", "facts_used")}
        if stored == json.loads(pub.metadata_json or "{}") and pub.metadata_source == source:
            return []
        pub.metadata_json, pub.metadata_source = json.dumps(stored, ensure_ascii=False, sort_keys=True), source
        return ["metadata"]

    def approve_content(self, publication_id: int, actor: str = "owner") -> Publication:
        pub = self.refresh(self.get(publication_id))
        self.db.commit()
        if pub.status not in CONTENT_APPROVABLE:
            raise PublishingRefused(f"The content of a publication that is {pub.status} cannot be approved.")
        blockers = self.blockers(pub)
        if blockers:
            raise PublishingRefused("Not ready: " + "; ".join(blockers), blockers)
        pub.content_approved_hash = json.loads(pub.hash_inputs_json)["content"]
        pub.content_approved_at = self.now()
        pub.publication_approved_hash = pub.publication_approved_at = None
        pub.status, pub.error = "content_approved", None
        self.event(pub, "content_approved", actor=actor, hash=pub.content_approved_hash)
        self.db.commit()
        return pub

    def approve_publication(self, publication_id: int, actor: str = "owner") -> Publication:
        pub = self.refresh(self.get(publication_id))
        self.db.commit()
        if pub.status not in (*PUBLICATION_APPROVABLE, "draft"):
            raise PublishingRefused(f"A publication that is {pub.status} cannot be approved for publishing.")
        content = json.loads(pub.hash_inputs_json)["content"]
        if pub.content_approved_hash != content:
            raise PublishingRefused("Approve the content first: it is not approved, or it changed since.")
        blockers = self.blockers(pub)
        if blockers:
            raise PublishingRefused("Not ready: " + "; ".join(blockers), blockers)
        pub.publication_approved_hash, pub.publication_approved_at = pub.publication_hash, self.now()
        pub.status = "publication_approved"
        self.event(pub, "publication_approved", actor=actor, hash=pub.publication_hash)
        self.db.commit()
        return pub

    def revoke(self, publication_id: int, actor: str = "owner") -> Publication:
        pub = self.get(publication_id)
        if pub.status not in ("content_approved", "publication_approved", "scheduled"):
            raise PublishingRefused(f"A publication that is {pub.status} has no approval to withdraw.")
        pub.status = "draft"
        pub.content_approved_hash = pub.content_approved_at = pub.publication_approved_hash = pub.publication_approved_at = None
        self.event(pub, "approval_revoked", actor=actor)
        self.refresh(pub)
        self.db.commit()
        return pub

    def schedule_blockers(self, pub: Publication) -> list[str]:
        out = []
        if pub.scheduled_at is None:
            out.append("No publication time is set.")
        if self.cfg.scheduling.film_before_shorts and pub.kind == Kind.SHORT.value:
            for link in self.db.query(RoutingLink).filter_by(publication_id=pub.id).all():
                film = self.film_publication(pub, link.target_destination)
                if film is None or film.status != "published" or not film.remote_url:
                    out.append(f"The {link.target_destination} film is not published yet (the film comes before its Shorts).")
        return list(dict.fromkeys(out))

    def assert_ready(self, pub: Publication, stage: str, actor: str = "system") -> None:
        """Check, now, that an approved publication still meets every requirement; an earlier approval is never trusted on its own. `stage` is `schedule`, `upload` or `publish`.
        The approval must be current (hashes and age); the film's publish rules, the cover and the platform's media rules must hold; before an upload or a publish the video file
        is hashed afresh. When something no longer holds the reason is recorded (an audit event, the publication's error) and persisted at once, the approval is voided, and
        `ReadinessChanged` is raised. A lapsed or voided approval is persisted the same way."""
        self.refresh(pub)
        self.db.commit()                                                     # an expiry or a void found by refresh is saved now, whatever happens next
        reasons: list[str] = []
        if not self.approval_current(pub):
            reasons.append("There is no current approval: it is missing, expired, or something approved has changed.")
            raise ReadinessChanged(" ".join(reasons), reasons)
        if stage in ("upload", "publish"):
            path = storage.resolve(pub.video_path) if pub.video_path else None
            if path is not None and path.is_file() and fresh_file_sha256(path) != pub.content_hash:
                reasons.append("The video file differs from the one that was approved.")
        reasons += self.blockers(pub)
        if not reasons:
            return
        pub.status, pub.publication_approved_hash, pub.publication_approved_at = "voided", None, None
        pub.error = "; ".join(reasons)
        self.event(pub, "readiness_blocked", "; ".join(reasons), actor, stage=stage)
        self.db.commit()
        raise ReadinessChanged("No longer ready: " + "; ".join(reasons), reasons)

    def schedule(self, publication_id: int, actor: str = "owner") -> Publication:
        pub = self.get(publication_id)
        if pub.status not in ("publication_approved", "scheduled") and not self.approval_current(pub):
            self.refresh(pub)
            self.db.commit()
            raise PublishingRefused("Only a publication with a current, unexpired approval can be scheduled.")
        self.assert_ready(pub, "schedule", actor)
        if pub.status != "publication_approved":
            raise PublishingRefused("Only a publication with a current, unexpired approval can be scheduled.")
        blockers = self.schedule_blockers(pub)
        if blockers:
            raise PublishingRefused("Cannot be scheduled: " + " ".join(blockers), blockers)
        pub.status = "scheduled"
        self.event(pub, "scheduled", actor=actor, scheduled_at=_iso(pub.scheduled_at))
        self.db.commit()
        return pub

    # -- routing -------------------------------------------------------------------------------
    def film_publication(self, pub: Publication, destination: str) -> Publication | None:
        platform, kind = destination.split("/")
        return (self.db.query(Publication).filter(Publication.case_id == pub.case_id, Publication.language == pub.language, Publication.platform == platform,
                                                  Publication.kind == kind, Publication.status != "voided").order_by(Publication.id.desc()).first())

    def sync_routing(self, pub: Publication) -> list[RoutingLink]:
        """Make sure each routing rule of the publication's destination has a link row, and bring its URL and status in line with what is known of the target film. A recorded URL that
        changes sends a link that was created or verified back to `url_recorded`: the earlier confirmation was about another URL."""
        source = f"{pub.platform}/{pub.kind}"
        links = []
        for rule in self.cfg.routing_rules(source):
            link = self.db.query(RoutingLink).filter_by(publication_id=pub.id, target_destination=rule.target, method=rule.method.value).first()
            film = self.film_publication(pub, rule.target)
            url = film.remote_url if film is not None and film.status == "published" else None
            if link is None:
                link = RoutingLink(publication_id=pub.id, target_destination=rule.target, method=rule.method.value, url=url,
                                   status=RoutingStatus.URL_RECORDED.value if url else RoutingStatus.MANUAL_ACTION_REQUIRED.value)
                self.db.add(link)
            elif (link.url or None) != url:
                link.url = url
                if link.status != RoutingStatus.UNSUPPORTED.value:
                    link.status = RoutingStatus.URL_RECORDED.value if url else RoutingStatus.MANUAL_ACTION_REQUIRED.value
                    link.verified_by = link.verified_at = None
            links.append(link)
        self.db.flush()
        return links

    def update_routing(self, link_id: int, *, status: str, evidence_note: str | None = None, actor: str = "owner") -> RoutingLink:
        link = self.db.get(RoutingLink, link_id)
        if link is None:
            raise PublishingRefused("Routing link not found.")
        if status not in MANUAL_LINK_STATUSES:
            raise PublishingRefused("Unknown routing status.")
        if status in (RoutingStatus.LINK_CREATED.value, RoutingStatus.CLICKABILITY_VERIFIED.value, RoutingStatus.URL_RECORDED.value) and not link.url:
            raise PublishingRefused("There is no URL to record: the target film is not published yet.")
        if status == RoutingStatus.CLICKABILITY_VERIFIED.value:
            if link.status != RoutingStatus.LINK_CREATED.value:
                raise PublishingRefused("Create the link first: only a created link can be verified.")
            if not (evidence_note or link.evidence_note or "").strip():
                raise PublishingRefused("Verifying that a link is clickable needs a note on how it was checked.")
        if evidence_note is not None:
            link.evidence_note = evidence_note
        link.status = status
        link.verified_by, link.verified_at = (actor, self.now()) if status == RoutingStatus.CLICKABILITY_VERIFIED.value else (None, None)
        pub = self.db.get(Publication, link.publication_id)
        self.event(pub, "routing_updated", status, actor, method=link.method, target=link.target_destination)
        self.refresh(pub)
        self.db.commit()
        return link

    # -- the AI plan: applying it, reordering, one-click approval ------------------------------------------------------------------
    def apply_ai(self, pub: Publication, item: dict | None, *, scheduled_at, sequence: int | None) -> list[str]:
        """Write one destination's AI metadata, order and slot into its publication. Returns what was NOT written and why: an approved or scheduled publication is never changed behind
        its approval, and metadata you wrote is kept (the AI's version stays in the saved run, one click away)."""
        kept: list[str] = []
        if pub.status not in AI_WRITABLE:
            return [f"{pub.status.replace('_', ' ')}: nothing changed"]
        changed = False
        if item is not None:
            if pub.metadata_source == "owner":
                kept.append("metadata edited by you: kept")
            else:
                pub.caption = item["text"]
                self._set_metadata(pub, item, "ai")
                changed = True
        if scheduled_at is not None:
            pub.scheduled_at = scheduled_at
            pub.window_ends_at = scheduled_at + timedelta(minutes=self.cfg.automatic_publishing.missed_window_minutes)
            changed = True
        pub.sequence = sequence
        if changed:
            self.event(pub, "ai_plan_applied", actor="ai", sequence=sequence)
        self.refresh(pub)
        return kept

    def use_suggestion(self, publication_id: int, actor: str = "owner") -> Publication:
        """Replace the stored metadata with the newest AI version (your decision, for one destination)."""
        pub = self.get(publication_id)
        if pub.status not in EDITABLE:
            raise PublishingRefused(f"A publication that is {pub.status} cannot be edited.")
        found = self.suggestions(pub.case_id, pub.language).get(self.suggestion_key(pub))
        if not found:
            raise PublishingRefused("There is no AI suggestion for this destination.")
        pub.caption = found["text"]
        self._set_metadata(pub, found, "ai")
        pub.metadata_source = "ai"
        self.event(pub, "ai_suggestion_used", actor=actor)
        self.refresh(pub)
        self.db.commit()
        return pub

    def reorder(self, case_id: int, language: str, platform: str, order: list[str], actor: str = "owner") -> list[Publication]:
        """Put the Shorts of one platform in this order. The calendar's slots stay where they are and move with the order: the first slot goes to the first Short, and so on. An edit of
        the time of an approved Short still voids that approval, as any change of its settings does."""
        pubs = {p.asset_key: p for p in self.db.query(Publication).filter_by(case_id=case_id, language=language, platform=platform, kind="short").all()}
        if not pubs or sorted(order) != sorted(pubs):
            raise PublishingRefused("The order must list every Short of the platform exactly once.")
        locked = [p.asset_key for p in pubs.values() if p.status not in EDITABLE]
        if locked:
            raise PublishingRefused(f"Cannot reorder while {', '.join(locked)} {'is' if len(locked) == 1 else 'are'} being published or published.")
        slots = sorted(p.scheduled_at for p in pubs.values() if p.scheduled_at is not None)
        for index, key in enumerate(order):
            pub = pubs[key]
            pub.sequence = index + 1
            if len(slots) == len(pubs):
                pub.scheduled_at = slots[index]
                pub.window_ends_at = slots[index] + timedelta(minutes=self.cfg.automatic_publishing.missed_window_minutes)
            self.event(pub, "reordered", actor=actor, sequence=index + 1)
        for pub in pubs.values():
            self.refresh(pub)
        self.db.commit()
        return [pubs[k] for k in order]

    def _plan_publications(self, case_id: int, language: str) -> list[Publication]:
        rank = {p: i for i, p in enumerate(Platform)}
        pubs = self.db.query(Publication).filter_by(case_id=case_id, language=language).all()
        return sorted((p for p in pubs if p.status != "voided" or p.error != "Superseded by a newer render."),
                      key=lambda p: (rank[Platform(p.platform)], p.kind != Kind.FILM.value, p.sequence or 99, p.asset_key))

    def _why_not_approved(self, pub: Publication) -> list[str]:
        """Why the one-click approval leaves this publication out (an empty list: it is approved). The same blockers as an individual approval, plus what a whole plan needs."""
        if pub.status in (*LIVE_STATUSES, "published", "publish_uncertain"):
            return [f"It is {pub.status.replace('_', ' ')}."]
        if pub.status == "failed":
            return ["It failed earlier: review it and approve it on its own."]
        reasons = list(self.blockers(pub))
        if pub.metadata_source == "default":
            reasons.append("There is no platform metadata yet: generate the publishing plan or write it.")
        limit = self.cfg.intelligence.metadata_limits[f"{pub.platform}/{pub.kind}"]
        if limit.title_required and not json.loads(pub.metadata_json or "{}").get("title") and pub.metadata_source != "default":
            reasons.append("A title is needed.")
        if pub.scheduled_at is None:
            reasons.append("No publication time is set.")
        return reasons

    def _stopped(self) -> bool:
        from app.services.case_control import global_stop_active

        return global_stop_active(self.db)

    def overview(self, case_id: int) -> list[dict]:
        """One compact row per publication of the case, every language: the Studio's lamps (published, waiting for its time, failed, uncertain, not scheduled). Nothing is changed."""
        rows = self.db.query(Publication).filter_by(case_id=case_id).order_by(Publication.language, Publication.platform, Publication.sequence, Publication.asset_key).all()
        return [{"id": p.id, "language": p.language, "platform": p.platform, "kind": p.kind, "asset_key": p.asset_key, "status": p.status, "state": self.lamp_state(p),
                 "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None, "error": p.error}
                for p in rows if p.status != "voided" or p.error != "Superseded by a newer render."]

    @staticmethod
    def lamp_state(pub: Publication) -> str:
        if pub.status == "published":
            return "published"
        if pub.status == "failed":
            return "failed"
        if pub.status == "publish_uncertain":
            return "uncertain"
        if pub.status in ("voided", "expired") or pub.scheduled_at is None:
            return "unscheduled"
        return "waiting"

    def plan_row(self, pub: Publication) -> dict:
        zone = ZoneInfo(self.cfg.languages[pub.language].timezone or "UTC") if self.cfg.language_enabled(pub.language) else ZoneInfo("UTC")
        return {"id": pub.id, "platform": pub.platform, "kind": pub.kind, "asset_key": pub.asset_key, "status": pub.status, "reasons": [], "notes": [],
                "scheduled_local": pub.scheduled_at.astimezone(zone).strftime("%Y-%m-%d %H:%M") if pub.scheduled_at else None}

    def plan_approval(self, case_id: int, language: str) -> dict:
        """What the one-click approval would do, publication by publication: approve, leave out (with the reasons), or already approved. Nothing is changed."""
        for pub in self.db.query(Publication).filter_by(case_id=case_id, language=language).all():
            self.refresh(pub)
        self.db.commit()
        approve, excluded, already = [], [], []
        for pub in self._plan_publications(case_id, language):
            row = self.plan_row(pub)
            if self.approval_valid(pub):
                already.append(row)
                continue
            reasons = self._why_not_approved(pub)
            (excluded if reasons else approve).append({**row, "reasons": reasons})
        for row in approve:
            if self.cfg.capability(row["platform"], row["kind"], CapabilityKey.PERSON_REQUIRED_PER_POST).value:
                row["notes"] = ["Approved here; the platform requires a person to post it."]
        return {"stopped": self._stopped(), "approve": approve, "excluded": excluded, "already_approved": already}

    def approve_plan(self, case_id: int, language: str, actor: str = "owner") -> dict:
        """Approve every publication the preview lists as approvable: content, then publication, each on its own and each recorded. Everything else stays as it is, with its reasons.
        Refused while the Emergency Stop is active. Nothing is scheduled or published."""
        if self._stopped():
            raise PublishingRefused("The Emergency Stop is active: nothing is approved until it is released.")
        preview = self.plan_approval(case_id, language)
        approved, excluded = [], list(preview["excluded"])
        for row in preview["approve"]:
            try:
                self.approve_content(row["id"], actor)
                self.approve_publication(row["id"], actor)
                approved.append(row)
            except PublishingRefused as exc:
                excluded.append({**row, "reasons": exc.reasons or [str(exc)]})
        first = self.db.query(Publication).filter_by(case_id=case_id, language=language).first()
        if first is not None:
            self.event(first, "plan_approved", f"{len(approved)} approved, {len(excluded)} left out", actor, approved=[r["id"] for r in approved], excluded=[r["id"] for r in excluded])
            self.db.commit()
        return {"approved": approved, "excluded": excluded, "already_approved": preview["already_approved"]}

    # -- the film's URL and state: the one writer -------------------------------------------------
    def record_film_published(self, video: Video, *, url: str | None, published_at: datetime | None, episode_number: int | None) -> Video:
        """Mark a film published: the YouTube publication row takes the URL and the time, then the `videos` row mirrors them (its own publish rules apply there). The routing
        links of the Shorts that point at this film pick the URL up."""
        from app.lifecycle import videos as V

        pub = (self.db.query(Publication).filter_by(case_id=video.case_id, language=video.language, platform="youtube", kind="film", asset_key=f"V{video.id}").first())
        if pub is None:
            account = self.ensure_accounts(video.language)["youtube"]
            pub = Publication(case_id=video.case_id, language=video.language, platform="youtube", kind="film", asset_key=f"V{video.id}", video_id=video.id, account_ref_id=account.id,
                              account_id=account.external_id, visibility="public", status="draft", caption=video.youtube_title or video.title or "",
                              video_path=video.file_path or "")
            self.db.add(pub)
            self.db.flush()
        was_published = pub.status == "published"
        path = storage.resolve(pub.video_path) if pub.video_path else None
        if not was_published:
            pub.content_hash = cached_file_sha256(self.db, path) if path is not None and path.is_file() else f"legacy-video-{video.id}"
        pub.status = "published"
        pub.remote_url = url or pub.remote_url
        pub.published_at = published_at or pub.published_at or self.now()
        pub.error = None
        self.event(pub, "film_published" if not was_published else "film_link_updated", actor="owner", url=pub.remote_url)
        try:
            self.db.flush()
        except Exception as exc:  # the same file already published on this account
            self.db.rollback()
            raise PublishingRefused("This exact file is already published on this account.") from exc
        result = V.publish(self.db, video, published_at=pub.published_at, youtube_url=pub.remote_url, episode_number=episode_number)
        for other in self.db.query(Publication).filter(Publication.case_id == video.case_id, Publication.language == video.language, Publication.kind == "short").all():
            self.sync_routing(other)
            self.refresh(other)
        self.db.commit()
        return result

    # -- the read model --------------------------------------------------------------------------
    def latest_run(self, case_id: int, language: str) -> PublishingPlanRun | None:
        """The newest finished AI publishing plan of the case and language."""
        return (self.db.query(PublishingPlanRun).filter_by(case_id=case_id, language=language, status="done").order_by(PublishingPlanRun.id.desc()).first())

    def suggestions(self, case_id: int, language: str) -> dict[str, dict]:
        """The metadata the newest AI plan wrote, by "<platform>/<kind>/<asset>" (the film's asset is "film")."""
        run = self.latest_run(case_id, language)
        return {k: v["item"] for k, v in json.loads(run.result_json or "{}").get("metadata", {}).items()} if run is not None else {}

    @staticmethod
    def suggestion_key(pub: Publication) -> str:
        return f"{pub.platform}/{pub.kind}/{'film' if pub.kind == Kind.FILM.value else pub.asset_key}"

    def view(self, pub: Publication, suggestions: dict[str, dict] | None = None) -> dict:
        links = self.db.query(RoutingLink).filter_by(publication_id=pub.id).order_by(RoutingLink.id).all()
        rules = {(r.target, r.method.value): r for r in self.cfg.routing_rules(f"{pub.platform}/{pub.kind}")}
        zone = ZoneInfo(self.cfg.languages[pub.language].timezone or "UTC") if self.cfg.language_enabled(pub.language) else ZoneInfo("UTC")
        local = pub.scheduled_at.astimezone(zone).strftime("%Y-%m-%dT%H:%M") if pub.scheduled_at else None
        blockers = self.blockers(pub) if pub.status in EDITABLE else []
        schedule_blockers = self.schedule_blockers(pub) if pub.status == "publication_approved" else []
        content_ok = bool(pub.content_approved_hash and pub.content_approved_hash == json.loads(pub.hash_inputs_json or "{}").get("content"))
        # what the buttons may do: decided here, with the rules the actions themselves apply
        eligibility = {"edit": pub.status in EDITABLE,
                       "approve_content": pub.status in CONTENT_APPROVABLE and not blockers,
                       "approve_publication": content_ok and pub.status in PUBLICATION_APPROVABLE and not blockers,
                       "schedule": self.approval_valid(pub) and pub.status == "publication_approved" and not blockers and not schedule_blockers,
                       "withdraw": pub.status in ("content_approved", "publication_approved", "scheduled")}
        return {"id": pub.id, "platform": pub.platform, "kind": pub.kind, "asset_key": pub.asset_key, "status": pub.status, "visibility": pub.visibility, "mode": pub.mode,
                "caption": pub.caption, "scheduled_at": _iso(pub.scheduled_at), "scheduled_local": local, "window_ends_at": _iso(pub.window_ends_at),
                "content_approved_at": _iso(pub.content_approved_at), "publication_approved_at": _iso(pub.publication_approved_at),
                "approval_valid": self.approval_valid(pub),
                "content_approved": content_ok, "eligibility": eligibility,
                "approval_expires_at": _iso(_aware(pub.publication_approved_at) + timedelta(days=self.cfg.approval.max_age_days)) if pub.publication_approved_at else None,
                "blockers": blockers, "warnings": self.warnings(pub), "schedule_blockers": schedule_blockers,
                "metadata": {**{"title": "", "hashtags": [], "keywords": [], "chapters": [], "cta": "", "routing_note": "", "facts_used": []}, **json.loads(pub.metadata_json or "{}")},
                "metadata_source": pub.metadata_source, "sequence": pub.sequence, "limit": self._limit_view(pub), "metadata_findings": self._metadata_findings(pub),
                "suggestion": self._suggestion_view(pub, suggestions),
                "file": pub.video_path, "prepared_copy": f"/{meta_config().instagram.prepared_copy_dir}/" in pub.video_path, "remote_url": pub.remote_url, "remote_id": pub.remote_id, "error": self._own_error(pub, blockers), "content_hash": pub.content_hash[:12], "publication_hash": (pub.publication_hash or "")[:12],
                "cover": {"ref": pub.cover_ref, "approved": bool(pub.cover_ref)},
                "links": [{"id": link.id, "target": link.target_destination, "method": link.method, "status": link.status, "url": link.url, "evidence_note": link.evidence_note,
                           "verified_at": _iso(link.verified_at), "automatable": rules[(link.target_destination, link.method)].automatable if (link.target_destination, link.method) in rules else False,
                           "note": rules[(link.target_destination, link.method)].note if (link.target_destination, link.method) in rules else ""} for link in links]}

    @staticmethod
    def _own_error(pub: Publication, blockers: list[str]) -> str | None:
        """The error to show: not when it only repeats the blocking reasons that are listed anyway."""
        if pub.error and blockers and all(part in blockers for part in pub.error.split("; ")):
            return None
        return pub.error

    @staticmethod
    def _preview_url(case_id: int, language: str, asset: Asset) -> str | None:
        """Where the Studio plays the asset (the app's own file routes; the video loads only when played)."""
        if asset.path is None or not asset.path.is_file():
            return None
        if asset.kind is Kind.SHORT:
            return f"/api/cases/{case_id}/short-clips/{language}/{asset.key}/video"
        return f"/api/documentary/production/{asset.video.production_script_id}/video.mp4" if asset.video is not None and asset.video.production_script_id else None

    def _cover_url(self, case_id: int, language: str, cover_key: str) -> str | None:
        """The cover's picture: the approved file, else the preview of the newest proposal, else nothing."""
        from app.covers import proposals as P
        from app.covers.api import final_path, preview_path

        proposal = P.current(self.db, case_id, language).get(cover_key)
        if proposal is None:
            return None
        if proposal.status == "approved" and final_path(case_id, language, cover_key).is_file():
            return f"/api/cases/{case_id}/covers/{language}/{cover_key}/file?row={proposal.id}"
        if preview_path(case_id, language, proposal).is_file():
            return f"/api/cases/{case_id}/covers/{language}/{cover_key}/preview?row={proposal.id}"
        return None

    def _limit_view(self, pub: Publication) -> dict:
        return self.cfg.intelligence.metadata_limits[f"{pub.platform}/{pub.kind}"].model_dump(mode="json")

    def _metadata_findings(self, pub: Publication) -> list[dict]:
        """The limits and formats of the platform applied to what is stored (shown beside the fields, with counts)."""
        from app.publishing.intelligence import validate as V

        item, _ = V.tidy({**json.loads(pub.metadata_json or "{}"), "text": pub.caption})
        limit = self.cfg.intelligence.metadata_limits[f"{pub.platform}/{pub.kind}"]
        return [f for f in V.metadata_findings(item, pub.kind, limit) if f["type"] != "no_title" or pub.metadata_source != "default"]

    def _suggestion_view(self, pub: Publication, suggestions: dict[str, dict] | None) -> dict | None:
        """The newest AI metadata for this destination when it differs from what is stored (so that an edit of yours is never lost silently, and a suggestion is never hidden)."""
        found = (suggestions or {}).get(self.suggestion_key(pub))
        if not found:
            return None
        stored = {"title": "", "hashtags": [], "keywords": [], "chapters": [], "cta": "", "routing_note": "", "facts_used": [], **json.loads(pub.metadata_json or "{}")}
        same = pub.caption == found["text"] and all(stored.get(k) == found.get(k) for k in ("title", "hashtags", "keywords", "chapters", "cta"))
        return None if same else found

    def snapshot(self, case_id: int, language: str) -> dict:
        """The Publishing Studio of one case and language: the film and the ShortClips from the database, their publications and the plan's settings."""
        scheduled = self.cfg.language_enabled(language)
        accounts = self.ensure_accounts(language) if scheduled else {}
        assets = self.assets(case_id, language)
        pubs = (self.db.query(Publication).filter(Publication.case_id == case_id, Publication.language == language).order_by(Publication.id).all())
        for pub in pubs:
            self.refresh(pub)
        self.db.commit()
        by_asset: dict[tuple[str, str], list[Publication]] = {}
        for pub in pubs:
            by_asset.setdefault((pub.kind, pub.asset_key), []).append(pub)
        out_assets = []
        suggestions = self.suggestions(case_id, language)
        for asset in assets:
            cover_ref, _, cover_state, _ = self._cover(case_id, language, asset.cover_key)
            size = asset.path.stat().st_size if asset.path is not None and asset.path.is_file() else None
            out_assets.append({"kind": asset.kind.value, "key": asset.key, "label": asset.label, "file": asset.path.name if asset.path else None, "bytes": size,
                               "preview_url": self._preview_url(case_id, language, asset), "cover_url": self._cover_url(case_id, language, asset.cover_key),
                               "video_id": asset.video.id if asset.video else None, "state": asset.video.state if asset.video else asset.clip.status if asset.clip else None,
                               "cover_state": cover_state, "cover_ref": cover_ref,
                               "publications": [self.view(p, suggestions) for p in by_asset.get((asset.kind.value, asset.key), [])]})
        lang = self.cfg.languages.get(language)
        return {"case_id": case_id, "language": language, "scheduled": scheduled,
                "schedule": {"timezone": lang.timezone, "publish_time_local": lang.publish_time_local} if lang and lang.enabled else None,
                "approval_max_age_days": self.cfg.approval.max_age_days, "missed_window_minutes": self.cfg.automatic_publishing.missed_window_minutes,
                "default_start": self.default_start(language).isoformat() if scheduled else None,
                "accounts": [{"platform": a.platform, "external_id": a.external_id, "display_name": a.display_name, "status": a.status, "auto_eligible": a.auto_eligible}
                             for a in accounts.values()],
                "assets": out_assets, "planned": bool(pubs)}
