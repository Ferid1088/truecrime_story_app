"""The publishing configuration (config/publishing.json): the vocabulary, which destinations exist and may be automated, the languages with their publishing time and
zone, and the rules of automatic publishing. A language is either disabled (no schedule at all) or has a zone and a local time; the scheduler turns that into UTC."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.publishing.vocabulary import (
    ApprovalInput,
    ApprovalKind,
    AutomaticRequirement,
    CapabilityKey,
    Destination,
    EventCategory,
    Evidence,
    Kind,
    Platform,
    PublishingMode,
    QueueKey,
    Restriction,
    RoutingMethod,
    RoutingStatus,
    Severity,
)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "publishing.json"
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class LegacyName(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Platform
    kind: Kind


class VocabularyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platforms: list[Platform]
    kinds: list[Kind]
    legacy_names: dict[str, LegacyName]

    @model_validator(mode="after")
    def _complete(self):
        if set(self.platforms) != set(Platform) or len(self.platforms) != len(set(self.platforms)):
            raise ValueError("vocabulary.platforms must list every platform exactly once")
        if set(self.kinds) != set(Kind) or len(self.kinds) != len(set(self.kinds)):
            raise ValueError("vocabulary.kinds must list every kind exactly once")
        return self


class Capability(BaseModel):
    """One fact about a destination and how well it is known. `value` None means unknown, which is only allowed as `unverified`."""

    model_config = ConfigDict(extra="forbid")

    value: bool | str | None
    evidence: Evidence
    note: str = ""

    @model_validator(mode="after")
    def _unknown_is_unverified(self):
        if self.value is None and self.evidence is Evidence.CONFIRMED:
            raise ValueError("an unknown capability cannot be confirmed")
        return self


class DestinationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Platform
    kind: Kind
    automation_allowed: bool
    reason: str = Field(min_length=1)
    capabilities: dict[CapabilityKey, Capability]
    restrictions: list[Restriction]

    @model_validator(mode="after")
    def _complete(self):
        if set(self.capabilities) != set(CapabilityKey):
            raise ValueError(f"{self.platform.value}/{self.kind.value}: every capability must be stated")
        if len(self.restrictions) != len(set(self.restrictions)):
            raise ValueError(f"{self.platform.value}/{self.kind.value}: a restriction is listed twice")
        return self

    @property
    def destination(self) -> Destination:
        return Destination(self.platform, self.kind)


class LanguageConfig(BaseModel):
    """`enabled` false: no schedule of any kind. `enabled` true: a zone and a local time (HH:MM) are required."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    timezone: str | None = None
    publish_time_local: str | None = None

    @model_validator(mode="after")
    def _schedule(self):
        if not self.enabled:
            if self.timezone is not None or self.publish_time_local is not None:
                raise ValueError("a disabled language has no schedule")
            return self
        if not self.timezone or not self.publish_time_local:
            raise ValueError("an enabled language needs timezone and publish_time_local")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {self.timezone!r}") from exc
        if not _CLOCK.match(self.publish_time_local):
            raise ValueError("publish_time_local is HH:MM (24 hours)")
        return self


class AutomaticPublishingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_enabled: bool
    enabled: bool                 # the master switch of the automatic publisher: publications whose time has come are published by the application (off until the owner says go)
    poll_seconds: int = Field(ge=10)
    missed_window_minutes: int = Field(ge=1)
    automatic_retries: int = Field(ge=0)


class ApprovalConfig(BaseModel):
    """Approval is per destination, in two kinds, and is bound to a hash of the listed parts. A change to a part of `unaffected_after_publication` does not void a published item."""

    model_config = ConfigDict(extra="forbid")

    max_age_days: int = Field(ge=1)
    default_approved: bool        # a publication with no blocker is approved by default (both kinds, recorded as the default): the owner publishes by the plan or with one click
    manual_languages: list[str]   # the exception: a publication in one of these languages (the owner reads Persian) waits for the owner's own approval before it is uploaded or published
    scope: Literal["destination"]
    required: list[ApprovalKind]
    invalidated_by: list[ApprovalInput]
    unaffected_after_publication: list[ApprovalInput]

    @model_validator(mode="after")
    def _consistent(self):
        if set(self.required) != set(ApprovalKind):
            raise ValueError("approval.required must list content and publication")
        if set(self.unaffected_after_publication) & set(self.invalidated_by):
            raise ValueError("a part cannot both void an approval and leave it unaffected")
        return self


class ModesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: PublishingMode
    automatic_requires: list[AutomaticRequirement]

    @model_validator(mode="after")
    def _safe(self):
        if self.default is not PublishingMode.MANUAL:
            raise ValueError("the default publishing mode is manual")
        if set(self.automatic_requires) != set(AutomaticRequirement) or len(self.automatic_requires) != len(set(self.automatic_requires)):
            raise ValueError("modes.automatic_requires must list every requirement exactly once")
        return self


class SchedulingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_as: Literal["utc"]
    film_before_shorts: bool
    queue_order: list[QueueKey]

    @model_validator(mode="after")
    def _order(self):
        if set(self.queue_order) != set(QueueKey) or len(self.queue_order) != len(set(self.queue_order)):
            raise ValueError("scheduling.queue_order must list every key exactly once")
        return self


class RoutingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    method: RoutingMethod
    automatable: bool
    evidence: Evidence
    after_publication: bool = False
    note: str = ""


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    methods: list[RoutingMethod]
    statuses: list[RoutingStatus]
    rules: list[RoutingRule]
    bio_link_note: dict[str, str]                    # language -> the line that points to the link in the profile bio (Instagram's tappable link)
    bio_link_platforms: list[str]                    # the platforms whose Shorts carry that line
    film_address_in_text: dict[str, list[str]]       # a film's address goes into the text of the Shorts of these platforms ('youtube/film' -> ['youtube', 'facebook', 'instagram'])

    @model_validator(mode="after")
    def _complete(self):
        if set(self.methods) != set(RoutingMethod) or len(self.methods) != len(set(self.methods)):
            raise ValueError("routing.methods must list every method exactly once")
        if set(self.statuses) != set(RoutingStatus) or len(self.statuses) != len(set(self.statuses)):
            raise ValueError("routing.statuses must list every status exactly once")
        keys = [(r.source, r.target, r.method) for r in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("a routing rule is listed twice")
        return self


class MonitoringEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Severity
    alert: bool


class MonitoringConfig(BaseModel):
    """The events the Monitoring page can show for publishing, with the page's own severities."""

    model_config = ConfigDict(extra="forbid")

    area: str = Field(min_length=1)
    events: dict[EventCategory, MonitoringEvent]

    @model_validator(mode="after")
    def _complete(self):
        if set(self.events) != set(EventCategory):
            raise ValueError("monitoring.events must define every event category")
        return self


class PlanConfig(BaseModel):
    """Defaults of a new publishing plan: every publication starts private, and the plan starts `start_offset_days` local days after today."""

    model_config = ConfigDict(extra="forbid")

    default_visibility: Literal["private", "unlisted", "public"]
    start_offset_days: int = Field(ge=0)
    make_delivery_copies: bool        # planning makes each Short's delivery copy, opening with its approved reel cover (shortform.delivery.prepare_shorts), so the files exist before anything is approved


class ReadinessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_approved_cover: bool


class GenerationSettings(BaseModel):
    """The AI publishing plan: how many repair rounds a checked answer gets, how much of the film's story the model reads, how many platforms are written at once, and when a run that has
    stopped reporting counts as interrupted."""

    model_config = ConfigDict(extra="forbid")

    repairs: int = Field(ge=0, le=3)
    film_excerpt_chars: int = Field(ge=500)
    concurrency: int = Field(ge=1, le=4)
    running_timeout_seconds: int = Field(ge=60)
    examples_dir: str = Field(min_length=1)          # the owner's collection of top-viewed Shorts per language (few-shot examples for the writer); not committed
    examples_per_call: int = Field(ge=0, le=60)
    example_caption_chars: int = Field(ge=0, le=1000)
    proofread_languages: list[str]                   # languages whose texts go through the proofreader (Sonnet) before the owner sees them: the ones the owner reads
    proofread_min_similarity: float = Field(gt=0, le=1)    # a corrected text that differs more than this from the original is a rewrite, not a correction, and is refused


class SchedulingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    earliest_local_time: str
    latest_local_time: str
    max_shorts_per_platform_per_day: int = Field(ge=1)
    min_days_between_films: int = Field(ge=0)
    horizon_days: int = Field(ge=1)
    data_notice: str = Field(min_length=1)

    @model_validator(mode="after")
    def _times(self):
        if not _CLOCK.match(self.earliest_local_time) or not _CLOCK.match(self.latest_local_time) or self.earliest_local_time >= self.latest_local_time:
            raise ValueError("scheduling: earliest_local_time < latest_local_time, both HH:MM")
        return self


class MetadataLimit(BaseModel):
    """What a destination's metadata may hold. `title_max` 0: the destination has no title; `keywords_max_chars` 0: no keywords field. `evidence` says whether the limits come from the
    platform's own documentation read for this project."""

    model_config = ConfigDict(extra="forbid")

    title_max: int = Field(ge=0)
    title_required: bool
    text_max: int = Field(ge=1)
    hashtags_max: int = Field(ge=0)
    hashtags_recommended: int = Field(ge=0)
    keywords_max_chars: int = Field(ge=0)
    chapters: bool
    evidence: Evidence
    note: str = ""
    text_max_bytes: int = Field(default=0, ge=0)        # 0: no limit in bytes (YouTube's description limit is in bytes, not characters)
    forbidden_chars: str = ""                            # characters the destination refuses in the title and the text
    web_address_allowed: bool = False                    # a web address in the text (the link from a Short to its film); everywhere else a link in a caption is no route and is refused


class IntelligenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: GenerationSettings
    scheduling: SchedulingSettings
    metadata_limits: dict[str, MetadataLimit]


class FileNameConfig(BaseModel):
    """The names of the files that are published (config/publishing.json: file_names): the full film's title words first (a Short's name always shows which film it belongs to), then the Short's own
    title words, the kind and number, the channel and the language; words joined by `separator`, the whole name at most `max_bytes` bytes of UTF-8 (the Short's words are cut first)."""

    model_config = ConfigDict(extra="forbid")

    folder: str                                    # beside the clips: where the named copies are kept
    separator: str = Field(min_length=1, max_length=1)
    max_bytes: int = Field(ge=60, le=250)          # file systems take 255 bytes of UTF-8 per name: Persian letters are two bytes each
    film_title_words: int = Field(ge=1)
    short_title_words: int = Field(ge=1)
    min_film_words: int = Field(ge=1)              # a long name is cut down to these many words of the film's title (the Short's words are cut first, down to `min_short_words`)
    min_short_words: int = Field(ge=1)
    include_channel: bool
    ascii_fold_languages: list[str]                # languages written in the Latin alphabet: accents are folded (é becomes e); Persian and Arabic stay in their own letters
    kind_words: dict[str, dict[str, str]]          # {"film" | "short": {language: the words that say what the file is}}


class PublishingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    vocabulary: VocabularyConfig
    destinations: list[DestinationConfig]
    languages: dict[str, LanguageConfig]
    automatic_publishing: AutomaticPublishingConfig
    approval: ApprovalConfig
    modes: ModesConfig
    scheduling: SchedulingConfig
    routing: RoutingConfig
    monitoring: MonitoringConfig
    plan: PlanConfig
    readiness: ReadinessConfig
    intelligence: IntelligenceConfig
    file_names: FileNameConfig

    @model_validator(mode="after")
    def _consistent(self):
        seen = [d.destination for d in self.destinations]
        if len(seen) != len(set(seen)):
            raise ValueError("a destination is listed twice")
        known = {str(d) for d in seen}
        for rule in self.routing.rules:
            if rule.source not in known or rule.target not in known:
                raise ValueError(f"routing rule {rule.source} -> {rule.target} uses a destination that does not exist")
            if not rule.target.endswith("/film"):
                raise ValueError("a routing rule leads to a film")
        if set(self.intelligence.metadata_limits) != known:
            raise ValueError("intelligence.metadata_limits must have exactly one entry per destination")
        for code in self.languages:
            if not re.fullmatch(r"[a-z]{2}", code):
                raise ValueError(f"language code {code!r} is not a two-letter lower-case code")
        for name, entry in self.vocabulary.legacy_names.items():
            if Destination(entry.platform, entry.kind) not in seen:
                raise ValueError(f"legacy name {name!r} points at a destination that does not exist")
        return self

    def destination(self, platform: Platform | str, kind: Kind | str) -> DestinationConfig | None:
        wanted = Destination.parse(str(platform), str(kind))
        return next((d for d in self.destinations if d.destination == wanted), None)

    def automation_allowed(self, platform: Platform | str, kind: Kind | str) -> bool:
        """Whether this destination may ever be published automatically (the owner's verified live test is a separate, further condition)."""
        found = self.destination(platform, kind)
        return found is not None and found.automation_allowed

    @property
    def supported_languages(self) -> list[str]:
        """Every language the architecture supports (the application's nine), whether or not it has a schedule."""
        return list(self.languages)

    def capability(self, platform: Platform | str, kind: Kind | str, key: CapabilityKey) -> Capability:
        found = self.destination(platform, kind)
        if found is None:
            raise ValueError(f"{platform}/{kind} is not a destination")
        return found.capabilities[key]

    def routing_rules(self, source: Destination | str) -> list[RoutingRule]:
        return [r for r in self.routing.rules if r.source == str(source)]

    def language_enabled(self, language: str) -> bool:
        found = self.languages.get(language)
        return found is not None and found.enabled

    def slot_utc(self, language: str, on: date) -> datetime:
        """The language's publishing moment on a local calendar day, in UTC (the zone's own rules, daylight saving included)."""
        found = self.languages.get(language)
        if found is None or not found.enabled:
            raise ValueError(f"language {language!r} has no publishing schedule")
        hour, minute = (int(part) for part in (found.publish_time_local or "").split(":"))
        return datetime.combine(on, time(hour, minute), tzinfo=ZoneInfo(found.timezone or "UTC")).astimezone(UTC)


@lru_cache(maxsize=1)
def publishing_config() -> PublishingConfig:
    return PublishingConfig.model_validate(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
