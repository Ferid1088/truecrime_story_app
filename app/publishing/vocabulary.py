"""The one vocabulary of the publishing layer: a destination is a platform and a kind of content. The older modules each name the same destinations differently
(`youtube_short`, `instagram_reel`, `tiktok_video`, `youtube_long`, `tiktok`, `youtube`...); `Destination.from_legacy` reads those names (config/publishing.json:
vocabulary.legacy_names) so that nothing has to be renamed before the registry replaces them."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Platform(StrEnum):
    YOUTUBE = "youtube"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


class Kind(StrEnum):
    FILM = "film"
    SHORT = "short"


class PublishingMode(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class AutomaticRequirement(StrEnum):
    """Everything that must hold, at the moment of publishing, for an item to publish without the owner's click."""

    GLOBAL_SWITCH_ON = "global_switch_on"
    PLATFORM_LANGUAGE_SWITCH_ON = "platform_language_switch_on"
    DESTINATION_AUTO_ELIGIBLE = "destination_auto_eligible"
    DESTINATION_AUTOMATION_ALLOWED = "destination_automation_allowed"
    CONTENT_APPROVED = "content_approved"
    PUBLICATION_APPROVED_AND_FRESH = "publication_approved_and_fresh"
    INSIDE_PUBLICATION_WINDOW = "inside_publication_window"
    EMERGENCY_STOP_CLEAR = "emergency_stop_clear"


class ApprovalKind(StrEnum):
    CONTENT = "content"
    PUBLICATION = "publication"


class ApprovalInput(StrEnum):
    """The parts of a publication whose change voids its approval."""

    VIDEO = "video"
    COVER = "cover"
    CAPTION = "caption"
    LINKS = "links"
    SETTINGS = "settings"
    DESCRIPTION_LINKS = "description_links"


class Evidence(StrEnum):
    CONFIRMED = "confirmed"        # read in the platform's own documentation for this project
    UNVERIFIED = "unverified"      # not read there, or only inferred


class CapabilityKey(StrEnum):
    NATIVE_SCHEDULING = "native_scheduling"
    AI_DISCLOSURE_FIELD = "ai_disclosure_field"
    PUBLIC_POSTING = "public_posting"
    PERSON_REQUIRED_PER_POST = "person_required_per_post"
    POST_PUBLICATION_METADATA_UPDATE = "post_publication_metadata_update"


class Restriction(StrEnum):
    PRIVATE_UNTIL_AUDIT = "private_until_audit"
    PERSON_REQUIRED_PER_POST = "person_required_per_post"
    NO_CLICKABLE_CAPTION_LINKS = "no_clickable_caption_links"
    NO_API_RELATED_VIDEO = "no_api_related_video"
    LONG_VIDEO_LIMITS_UNVERIFIED = "long_video_limits_unverified"
    PAGE_PUBLISHING_AUTHORIZATION_MAY_BLOCK = "page_publishing_authorization_may_block"


class RoutingMethod(StrEnum):
    RELATED_VIDEO = "related_video"
    LINK_POST = "link_post"
    DESCRIPTION_LINK = "description_link"
    PROFILE_LINK = "profile_link"
    PLAIN_TEXT_CTA = "plain_text_cta"
    COMMENT_TO_DM = "comment_to_dm"


class RoutingStatus(StrEnum):
    """A stored URL is not a working link: the statuses keep what was recorded apart from what was created and what a person confirmed."""

    URL_RECORDED = "url_recorded"
    LINK_CREATED = "link_created"
    CLICKABILITY_VERIFIED = "clickability_verified"
    MANUAL_ACTION_REQUIRED = "manual_action_required"
    UNSUPPORTED = "unsupported"


class QueueKey(StrEnum):
    SCHEDULED_AT = "scheduled_at"
    QUEUE_POSITION = "queue_position"


class Severity(StrEnum):
    """The severities of the Monitoring page (app/documentary/monitoring.py: Issue.severity)."""

    PROBLEM = "problem"
    ATTENTION = "attention"
    INFO = "info"


class EventCategory(StrEnum):
    PUBLICATION_SCHEDULED = "publication_scheduled"
    PUBLICATION_STARTED = "publication_started"
    PUBLICATION_PUBLISHED = "publication_published"
    PUBLICATION_FAILED = "publication_failed"
    PUBLICATION_UNCERTAIN = "publication_uncertain"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_VOIDED = "approval_voided"
    WINDOW_MISSED = "window_missed"
    AUTOMATIC_BLOCKED = "automatic_blocked"
    EMERGENCY_STOP_BLOCKED = "emergency_stop_blocked"
    ACCOUNT_NEEDS_REAUTH = "account_needs_reauth"
    ROUTING_ACTION_REQUIRED = "routing_action_required"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    PLAN_GENERATION_RUNNING = "plan_generation_running"
    PLAN_GENERATION_FAILED = "plan_generation_failed"
    YOUTUBE_UPLOAD_RUNNING = "youtube_upload_running"
    YOUTUBE_UPLOAD_NEEDS_ACTION = "youtube_upload_needs_action"
    INSTAGRAM_CONNECTION_PROBLEM = "instagram_connection_problem"
    INSTAGRAM_CONNECTION_EXPIRING = "instagram_connection_expiring"


@dataclass(frozen=True)
class Destination:
    platform: Platform
    kind: Kind

    @classmethod
    def parse(cls, platform: str, kind: str) -> Destination:
        try:
            return cls(Platform(platform), Kind(kind))
        except ValueError as exc:
            raise ValueError(f"unknown destination {platform!r}/{kind!r}") from exc

    @classmethod
    def from_legacy(cls, name: str) -> Destination:
        """The destination an older module's platform name stands for."""
        from app.publishing.config import publishing_config

        entry = publishing_config().vocabulary.legacy_names.get(name)
        if entry is None:
            raise ValueError(f"unknown legacy platform name {name!r}")
        return cls(entry.platform, entry.kind)

    def __str__(self) -> str:
        return f"{self.platform.value}/{self.kind.value}"
