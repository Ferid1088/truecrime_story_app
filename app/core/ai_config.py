"""Central, typed loader for the layered configuration in config/.

Every non-secret AI parameter lives in that one file — agents request
models and generation settings by logical role and never hardcode
model IDs, temperatures, token limits or thresholds. The config is
validated at import time so a malformed file fails fast on startup
instead of degrading silently mid-pipeline.
"""

import json
import logging
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

# Provider secrets are referenced by env-var NAME in the config below
# (secret_env). Loading .env here — once, alongside the module that
# defines those names — guarantees os.environ lookups resolve in any
# process that imports this module, whichever import order runs first.
load_dotenv()

# Layers (each file holds whole top-level sections; a section may be split
# over layers, e.g. generation_providers = connection in connections.json +
# models in models.json):
#   connections.json   where the services are: endpoints, secret env names, search, avatar
#   models.json        which model does which role: aliases, routing, per-role settings
#   agents.json        the agents: name -> model role, prompt file, output kind
#   parameters/*.json  how each area behaves: story, research, audio, documentary,
#                      visuals, lifecycle, identity
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
CONFIG_FILES = ("connections.json", "models.json", "agents.json")
PARAMETER_DIR = "parameters"

# Every role the pipeline uses must have a routed model alias AND
# per-role generation settings in the config file. Research-side roles
# (search/planning/verification/normalization) route through the same
# table — agents never name a model ID.
REQUIRED_ROLES = {
    # Research intelligence roles — the ENGINE searches/fetches; these
    # LLM roles only plan and analyze retrieved content (search-engine
    # architecture: search is not an LLM task).
    "case_discovery_agent",
    "research_query_planner",
    "result_reranker",
    "fact_extractor",
    "timeline_builder",
    "contradiction_analyzer",
    "story_director",
    "grounding_validator",
    "consistency_checker",
    "evidence_normalizer",
    "semantic_consistency_checker",
    "transcript_normalizer",
    "transcript_intelligence_extractor",
    "claim_clusterer",
    "evidence_verifier",
    "research_evaluator",
    "research_assembler",
    # Documentary engine: editorial blueprint (beats, reveals, intents).
    "narrative_director",
    # Language pipeline: the shared English draft, then per language the native
    # rewrite, the independent inspection and the final text.
    "language_draft_writer",
    "language_rewrite_writer",
    "language_inspector",
    "language_safety_inspector",
    "language_final_writer",
    # The Persian and Arabic writers have their own roles, so that their model is chosen apart from
    # the English and German writers (owner decision, 2026-10-18).
    "language_rewrite_writer_fa_ar",
    "language_final_writer_fa_ar",
    "english_evidence_auditor",
    # Spoken storytelling adaptation (author) + its independent checks.
    "spoken_writer",
    "spoken_meaning_checker",
    "spoken_style_critic",
    # Pronunciation key: for words a voice can misread (Persian homographs
    # like ملک = melk / molk / malek / malak) the reading the MEANING needs
    # and the harakat that force it — used by the listening check.
    "pronunciation_editor",
    # Voice performance: ElevenLabs v4 audio tags, emphasis and the
    # narrator's tension arc (neutral -> dark -> slow and plain climaxes, never whispered).
    "voice_performance_director",
    # Professional audio direction: breaths, music moments, silences.
    "audio_director",
    # Solved / unsolved: judges search evidence for discovery suggestions
    # and for the unsolved-case monitor's deep verification.
    "case_status_verifier",
    # The recurring on-screen host: when to appear and why (director),
    # the words natively per language (writer), and an independent check
    # of facts, memories, repetition and the persona (critic).
    "host_director",
    "host_writer",
    "host_writer_fa_ar",
    "host_critic",
    "host_meaning_checker",
    # Camera, outfit, posture, gestures and lighting of one host appearance,
    # decided from its real audio (Part 7c).
    "host_stage_director",
    # Reads the title, channel and description of a borderline YouTube candidate
    # and decides whether it is about the case.
    "video_relevance_checker",
    # Visual intelligence: needs per beat, image verification (vision),
    # shot direction, on-screen text per language.
    # The Main Visual Planner's own agents (config/parameters/main_visual_planner.json):
    # needs per beat from the reveal graph, search queries per unmet requirement, candidate
    # verification (vision) with an independent second verifier, video segment scouting
    # (vision) and shot direction. Rights and coverage are deterministic.
    "mvp_needs_planner",
    "mvp_query_planner",
    "mvp_candidate_verifier",
    "mvp_second_verifier",
    "mvp_segment_scout",
    # Moment cutting: picks the footage that may show what is said at a bare moment, finds the exact
    # window in it (vision), and checks the finished cut independently (vision).
    "mvp_moment_picker",
    "mvp_moment_cutter",
    "mvp_moment_checker",
    # Topical fill: which approved pictures and videos are related to what is said at a bare moment.
    "mvp_topic_matcher",
    # Cinematic engines: whether a beat is clearer with one of the eight engines, and its structured data.
    "mvp_engine_planner",
    # Other people's logos, watermarks and caption strips in a picture or footage: where they are (vision).
    "mvp_overlay_scanner",
    # When in the story a picture or clip may first be shown: the earliest beat that has told what it shows.
    "mvp_timing_classifier",
    "mvp_scene_director",
    # The alternative graphics: a disputed point structured into two sourced sides, stored facts
    # shortened faithfully, and the earliest beat a real newspaper headline may be shown.
    "mvp_conflict_structurer",
    "mvp_copywriter",
    "mvp_headline_placer",
    # A second, independent model looks at the same picture and the same inputs when the first
    # left a place, object, document or map in needs_review.
    # Independent of the planner: may this editorial-review picture be used at all (right
    # person, place and period; identity supported; caption matches the picture)?
    # Strict gate before render: is THIS picture/clip right for THESE
    # words (exact kind, tone, honesty)? Independent of the director.
    # Video pieces: the segmenter watches a whole video and cuts it by
    # MEANING (complete actions/moments, named and described); the video
    # auditor checks each piece frame by frame — the cut, the description,
    # the content — and judges it against the words before render.
    # Chapters and the running case timeline: the writer gives every
    # chapter a title and every dated event the story tells a short label
    # (all languages at once); an independent auditor checks each text —
    # faithful, no spoiler before its reveal, sober, a true translation.
    "chapter_writer",
    "chapter_auditor",
    "overlay_localizer",
    # Documentary critics (independent of the visual director).
    "automation_feel_critic",
    "attention_critic",
    "visual_accuracy_critic",
    "production_critic",
    # Case naming: native candidate titles per language, an independent
    # critic for quality/spoiler/epistemic risk, a native-language check.
    "case_naming_agent",
    "case_title_critic",
    "native_title_critic",
    # Thumbnails: optional 0-4 word text, and the vision critic.
    "thumbnail_critic",
}

# Roles that send images and need a vision-capable model.
VISION_ROLES = {"mvp_candidate_verifier", "mvp_second_verifier", "mvp_segment_scout", "mvp_moment_cutter",
                "mvp_moment_checker", "mvp_overlay_scanner", "thumbnail_critic"}

# Strict provider split: the research PROVIDER is now infrastructure
# (the TrueCrime Search Engine — Brave Search + fetcher + index), not an LLM
# vendor, so no LLM role routes to it. Every intelligence role resolves
# through the GENERATION provider (APIMaster). The set is kept for the
# symmetric section lookup — an LLM-backed research provider would add
# roles here and re-enable the capability validation below.
RESEARCH_ROLES: set[str] = set()
GENERATION_ROLES = REQUIRED_ROLES - RESEARCH_ROLES

# Roles that cannot function without live web retrieval — validated at
# startup when the research provider is LLM-backed (type == "llm").
_WEB_SEARCH_ROLES: set[str] = set()

# Research-side roles that fetch URLs rather than search.
_WEB_FETCH_ROLES: set[str] = set()


class ProviderSelection(BaseModel):
    research: str
    generation: str


class UserMediaPolicies(BaseModel):
    require_rights_confirmation: bool
    preserve_original_audio: bool
    default_auto_audio_use: bool


class MediaReusePolicies(BaseModel):
    default_policy: Literal["no_repeat"]
    anchor_reuse_enabled: bool
    min_anchor_reuse_minutes: float = Field(ge=0.0)


class AudioRecoveryPolicies(BaseModel):
    max_repair_attempts: int = Field(ge=0, le=10)
    fallback_order: list[Literal[
        "room_tone", "verified_ambience", "valid_music", "intentional_silence"
    ]] = Field(min_length=1)

    @field_validator("fallback_order")
    @classmethod
    def _unique_fallbacks(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("audio_recovery.fallback_order must not contain duplicates")
        return value


class ShortFormPolicies(BaseModel):
    status: Literal["prototype"]
    blocks_long_form_release: bool


class TimelinePolicies(BaseModel):
    multi_year_threshold_years: int = Field(ge=1)
    hide_labels: bool
    empty_calendar_markers: bool


class PublishingPolicies(BaseModel):
    default_visibility: Literal["private", "draft"]
    live_mode_default: bool


class RetryPolicyConfig(BaseModel):
    chapter_texts: Literal["STOP", "OMIT", "DEGRADE"]
    spoken_versions: Literal["STOP", "OMIT", "DEGRADE"]
    host_segments: Literal["STOP", "OMIT", "DEGRADE"]
    video_pieces: Literal["STOP", "OMIT", "DEGRADE"]
    audio_plan: Literal["STOP", "OMIT", "DEGRADE"]


class IncrementalPolicyConfig(BaseModel):
    mode: Literal["off", "verify", "enforce"] = "off"
    enforced_types: list[str] = Field(default_factory=list)


class ProductionPolicies(BaseModel):
    user_media: UserMediaPolicies
    media_reuse: MediaReusePolicies
    audio_recovery: AudioRecoveryPolicies
    short_form: ShortFormPolicies
    timeline: TimelinePolicies
    publishing: PublishingPolicies
    retry_policy: RetryPolicyConfig
    incremental: IncrementalPolicyConfig


class ModelCapabilities(BaseModel):
    """What a model can actually do — never send unsupported parameters.

    web_search / web_fetch: "native" (model retrieves on its own, e.g.
    Sonar), "plugin" (needs the provider's web plugin), or "none"."""

    web_search: str = "none"  # native | plugin | none
    web_fetch: str = "none"  # native | plugin | none
    structured_output: bool = True
    tool_calling: bool = False
    vision: bool = False
    # How the gateway lets a request reuse a repeated prompt prefix:
    # "cache_control" (an ephemeral marker on the prefix block), "prompt_cache_key"
    # (a stable key on the request) or "none". Declared from live probes only.
    prompt_caching: Literal["none", "cache_control", "prompt_cache_key"] = "none"
    # Whether the model takes `reasoning_effort` (the gateway validates the value).
    reasoning_effort: bool = False
    # The gateway streams this model's answer (with usage in the last chunk), so the
    # read timeout is a real inactivity timeout rather than a ceiling on the whole call.
    streaming: bool = False
    # The gateway sometimes drops the system message of a request with images (the model then
    # answers "there was no instruction"): the instructions are repeated in the user message.
    instructions_in_user_with_images: bool = False

    @field_validator("web_search", "web_fetch")
    @classmethod
    def _cap_value(cls, v: str) -> str:
        if v not in ("native", "plugin", "none"):
            raise ValueError(
                f"capability must be native|plugin|none, got {v!r}"
            )
        return v


class ResearchProviderSection(BaseModel):
    """The web-research provider.

    type="search_engine"  — first-party infrastructure (TrueCrime
        Search Engine: Brave Search + fetcher + index). No LLM credentials,
        no model routing; intelligence roles live in the generation
        provider instead.
    type="llm"            — an LLM-vendor research provider; requires
        model aliases, routing and declared capabilities.
    """
    enabled: bool
    role: str = "internet_research"
    type: str = "llm"
    secret_env: str | None = None
    base_url: str | None = None
    models: dict[str, str] = {}
    capabilities: dict[str, ModelCapabilities] = {}
    routing: dict[str, str] = {}
    fallbacks: dict[str, list[str]] = {}

    @model_validator(mode="before")
    @classmethod
    def _fold_model_specs(cls, v):
        if not isinstance(v, dict):
            return v
        caps = dict(v.get("capabilities") or {})
        models = {}
        for alias, spec in (v.get("models") or {}).items():
            if isinstance(spec, dict):
                models[alias] = spec.get("model")
                inline = spec.get("capabilities")
                if inline:
                    caps.setdefault(spec["model"], inline)
            else:
                models[alias] = spec
        return {**v, "models": models, "capabilities": caps}

    @model_validator(mode="after")
    def _validate(self):
        if self.type == "search_engine":
            # Infrastructure provider — no models or routing to check.
            return self
        if self.enabled and not self.models:
            raise ValueError("research provider must define model aliases")
        missing = RESEARCH_ROLES - set(self.routing)
        if missing:
            raise ValueError(
                f"research routing missing roles: {sorted(missing)}"
            )
        unknown = set(self.routing) - RESEARCH_ROLES
        if unknown:
            raise ValueError(
                f"research routing defines non-research roles: "
                f"{sorted(unknown)}"
            )
        bad = {r: a for r, a in self.routing.items() if a not in self.models}
        if bad:
            raise ValueError(
                f"research routing uses unknown aliases: {bad}. "
                f"Known: {sorted(self.models)}"
            )
        for alias, chain in self.fallbacks.items():
            if alias not in self.models:
                raise ValueError(
                    f"research fallback defined for unknown alias: {alias!r}"
                )
            for target in chain:
                if target not in self.models:
                    raise ValueError(
                        f"research fallback for {alias!r} targets unknown "
                        f"alias: {target!r}"
                    )
        unknown_caps = set(self.capabilities) - set(self.models.values())
        if unknown_caps:
            raise ValueError(
                f"research capabilities declared for unknown models: "
                f"{sorted(unknown_caps)}"
            )
        return self


class GenerationProviderSection(BaseModel):
    enabled: bool
    base_url: str
    secret_env: str
    # HTTP read/inactivity timeout: a provider call may run longer than this
    # overall, but must emit another response chunk within this interval.
    inactivity_timeout_s: float = Field(default=90.0, gt=0.0, le=900.0)
    models: dict[str, str]
    # Declared capabilities keyed by concrete model ID — never send a
    # server tool or parameter the model cannot use (Part 8).
    capabilities: dict[str, ModelCapabilities] = {}
    routing: dict[str, str]
    fallbacks: dict[str, list[str]] = {}

    @model_validator(mode="before")
    @classmethod
    def _fold_model_specs(cls, v):
        # Accepts either shape: "alias": "model/id" shorthand or
        # "alias": {"model": "...", "capabilities": {...}}; inline
        # capabilities fold into the model-ID-keyed capabilities map.
        if not isinstance(v, dict):
            return v
        caps = dict(v.get("capabilities") or {})
        models = {}
        for alias, spec in (v.get("models") or {}).items():
            if isinstance(spec, dict):
                models[alias] = spec.get("model")
                inline = spec.get("capabilities")
                if inline:
                    caps.setdefault(spec["model"], inline)
            else:
                models[alias] = spec
        return {**v, "models": models, "capabilities": caps}

    @model_validator(mode="after")
    def _validate(self):
        if not self.models:
            raise ValueError("generation provider must define at least one model alias")
        missing_roles = GENERATION_ROLES - set(self.routing)
        if missing_roles:
            raise ValueError(f"routing missing roles: {sorted(missing_roles)}")
        bad_routes = {r: a for r, a in self.routing.items() if a not in self.models}
        if bad_routes:
            raise ValueError(
                f"routing uses unknown model aliases: {bad_routes}. "
                f"Known aliases: {sorted(self.models)}"
            )
        unknown_roles = set(self.routing) - GENERATION_ROLES
        if unknown_roles:
            raise ValueError(
                f"routing defines non-generation roles: {sorted(unknown_roles)}"
            )
        for alias, chain in self.fallbacks.items():
            if alias not in self.models:
                raise ValueError(f"fallback defined for unknown alias: {alias!r}")
            for target in chain:
                if target not in self.models:
                    raise ValueError(
                        f"fallback for {alias!r} targets unknown alias: {target!r}"
                    )
                if target == alias:
                    raise ValueError(f"fallback for {alias!r} targets itself")
        known_model_ids = set(self.models.values())
        unknown_caps = set(self.capabilities) - known_model_ids
        if unknown_caps:
            raise ValueError(
                f"capabilities declared for unknown models: "
                f"{sorted(unknown_caps)}. "
                f"Known model IDs: {sorted(known_model_ids)}"
            )
        return self


ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]


class GenerationSettings(BaseModel):
    temperature: float = Field(ge=0.0, le=2.0)
    max_tokens: int = Field(ge=1, le=262144)
    seed: int | None = None
    # Sent as `reasoning_effort` to a model that declares the capability;
    # a role without it asks for the model's default.
    reasoning_effort: ReasoningEffort | None = None


class StoryConfig(BaseModel):
    default_target_minutes: int = Field(ge=1, le=120)
    # Allowed request range for story length. A low minimum enables short
    # pilot segments (e.g. a 3–5 minute documentary test) without code
    # changes; defaults keep the historical 10–90 minute range.
    min_target_minutes: int = Field(default=10, ge=1, le=120)
    max_target_minutes: int = Field(default=90, ge=1, le=240)

    @model_validator(mode="after")
    def _validate(self):
        if not (
            self.min_target_minutes
            <= self.default_target_minutes
            <= self.max_target_minutes
        ):
            raise ValueError(
                "story.default_target_minutes must lie within "
                "[min_target_minutes, max_target_minutes]"
            )
        return self


class StoryQualityConfig(BaseModel):
    """Gates that decide whether a StoryVersion may be marked ready."""

    minimum_grounding_score: float = Field(ge=0.0, le=1.0)
    max_unsupported_claims: int = Field(ge=0)
    max_grounding_repair_iterations: int = Field(ge=0, le=5)
    consistency_max_high_severity: int = Field(ge=0)
    disputed_confidence_threshold: float = Field(ge=0.0, le=1.0)
    strip_markdown_artifacts: bool = True
    section_marker_prefix: str = "[[ACT:"
    # An act's writer, grounding check and repair editor see at most this many
    # evidence items (the strongest); more would overrun the prompt limit.
    max_evidence_per_act: int = Field(ge=10, le=100, default=100)
    # The evidence pack every whole-story step reads holds at most this many
    # facts (the strongest, spread over the fact types): all of a large case's
    # facts overrun the model's prompt limit and are paid for on every call.
    max_evidence_per_story: int = Field(ge=20, le=100, default=100)
    # The long draft reads every promoted fact; set a number to cap it (none = no cap).
    max_evidence_for_long_draft: int | None = Field(default=None, ge=20)


# Brave's web-search `search_lang` enum, read verbatim off a live HTTP 422
# validation error from Brave itself (checked 2026-10-23): any value outside
# this set is rejected by Brave with HTTP 422, not treated as "no results".
VALID_BRAVE_SEARCH_LANG = frozenset({
    "ar", "eu", "bn", "bg", "ca", "zh-hans", "zh-hant", "hr", "cs", "da",
    "nl", "en", "en-gb", "et", "fi", "fr", "gl", "de", "el", "gu", "he",
    "hi", "hu", "is", "it", "ja", "jp", "kn", "ko", "lv", "lt", "ms", "ml",
    "mr", "nb", "pl", "pt-br", "pt-pt", "pa", "ro", "ru", "sr", "sk", "sl",
    "es", "sv", "ta", "te", "th", "tr", "uk", "vi",
})


class LanguageEntry(BaseModel):
    """One language the research can run in: how Brave is asked for it and
    the native query stems used to find videos about a case."""
    name: str
    # The writing direction of the language's script: the page shows its text that way.
    direction: Literal["ltr", "rtl"] = "ltr"
    # Brave's own search_lang code, or None when Brave rejects the parameter
    # (the query text then carries the language).
    brave_search_lang: str | None
    # Three stems (formatted with the case title as {t}); never a literal
    # translation of one English query.
    video_search_stems: list[str] = Field(min_length=1)
    video_query_stem: str

    @field_validator("brave_search_lang")
    @classmethod
    def _brave_search_lang_is_accepted_by_brave(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_BRAVE_SEARCH_LANG:
            raise ValueError(
                f"{v!r} is not a value Brave's search_lang accepts "
                f"(use one of {sorted(VALID_BRAVE_SEARCH_LANG)} or null to omit it)")
        return v


class MultilingualConfig(BaseModel):
    research_languages: list[str] = ["en", "de", "fa", "ar"]
    # Every language research or production can use. A language in here but
    # not in research_languages / production_languages is ready to enable by
    # editing those lists.
    language_catalog: dict[str, LanguageEntry] = {}
    # Language the evidence is normalized into (summary_en, canonical_text_en).
    evidence_language: str = "en"
    # Languages a film is produced in. Each one is finished independently from the
    # shared draft, the shared plan and the shared evidence; none is the reference.
    production_languages: list[str] = ["fa", "en", "de", "ar"]

    @model_validator(mode="after")
    def _validate(self):
        used = {*self.research_languages, self.evidence_language, *self.production_languages}
        missing = sorted(used - set(self.language_catalog))
        if missing:
            raise ValueError(f"languages missing from language_catalog: {missing}")
        if len(set(self.production_languages)) != len(self.production_languages):
            raise ValueError("production_languages must not repeat a language")
        return self

    @property
    def research_only_languages(self) -> list[str]:
        return [lang for lang in self.research_languages if lang not in self.production_languages]

    def video_stems(self, language: str) -> list[str]:
        return (self.language_catalog.get(language) or self.language_catalog["en"]).video_search_stems

    def video_query_stem(self, language: str) -> str:
        return (self.language_catalog.get(language) or self.language_catalog["en"]).video_query_stem

    def brave_search_lang(self, language: str) -> str | None:
        """Brave's search_lang for a language, or None to omit the param.

        A language outside the catalog (e.g. a free-text "other" bucket, or
        any code we have not vetted) never gets its first two letters
        guessed into a Brave code — that produced invalid values Brave
        answers with HTTP 422 (checked live 2026-10-23: "other" -> "ot" ->
        422). Omitting the param lets Brave search without a language
        filter instead of failing the request."""
        entry = self.language_catalog.get(language)
        return entry.brave_search_lang if entry else None


class StoryPlanConfig(BaseModel):
    """Part 5a: the plan a master is written from."""
    # The writer does not run (API) without an approved plan.
    require_approved_plan: bool
    # A one-button documentary run plans and approves by itself, recorded as
    # "automatic", when the owner has not planned first.
    automatic_approval_in_pipeline: bool
    # The beats' target words may differ from the film target by this share
    # before the plan is invalid (half of it is a warning).
    max_total_words_deviation: float = Field(gt=0.0, le=1.0)
    # No act of a plan gets less than this share of the film's words.
    min_act_budget_share: float = Field(gt=0.0, le=0.5)


class WriterMetricsConfig(BaseModel):
    """Deterministic writer metrics (Part 5b2). The stock phrases are the
    genre's own clichés per language, matched case-insensitively."""
    stock_phrases: dict[str, list[str]]
    trigram_min_words: int = Field(ge=3, le=5)
    short_sentence_words: int = Field(ge=1, le=12)


class CalmConfig(BaseModel):
    """How calm the host is: hands rest, a gesture only at real emphasis (the owner finds a busy
    avatar annoying). The code rejects a direction that exceeds it."""

    # Seated postures with the hands at rest; any other needs a continuity_reason.
    seated_postures: list[str]
    # Gestures a direction may use at all (the rest of the vocabulary is too big).
    allowed_gestures: list[str]
    # The gestures that move the hands (counted against the limits below).
    hand_gestures: list[str]
    max_hand_gestures_per_minute: float = Field(gt=0)
    min_gap_seconds: float = Field(ge=0)
    # No gesture within this many seconds of a sentence's start.
    sentence_start_quiet_seconds: float = Field(ge=0)
    max_gesture_seconds: float = Field(gt=0)
    # HeyGen's expressiveness is never higher than this (low | medium | high).
    max_expressiveness: str


class StageDirectionConfig(BaseModel):
    """Deterministic guard-rails of the host's stage direction (Part 7c)."""
    min_shot_seconds: float = Field(gt=0)
    max_shot_seconds: float = Field(gt=0)
    cut_alignment_tolerance_seconds: float = Field(ge=0)
    max_close_shots_per_appearance: int = Field(ge=0)
    close_allowed_emotions: list[str]
    close_allowed_purposes: list[str]
    forbidden_emotions: list[str]
    sensitive_max_gestures: int = Field(ge=0)
    # How many of the newest appearances of the channel the director reads.
    visual_memory_appearances: int = Field(ge=1, le=40)
    # Words that make a segment victim-related (matched case-insensitively in
    # its dialogue and claims): soft lighting, one gesture, a static camera.
    sensitive_terms: dict[str, list[str]]
    # Owner choice: outfits that may appear per language; a language without an
    # entry may use every outfit.
    outfit_allow_list: dict[str, list[str]]
    # How a direction becomes HeyGen's natural-language motion prompt.
    gesture_phrases: dict[str, str]
    posture_phrases: dict[str, str]
    # {hands} in a gesture phrase: how the hands of an action are named.
    hands_phrases: dict[str, str]
    # Gestures a language's culture reads wrongly (pointing at the viewer, a thumbs-up, the OK sign
    # in the Middle East ...): the director may not choose them there.
    forbidden_gestures: dict[str, list[str]]
    # intensity below the first value -> low, below the second -> medium, else high
    expressiveness_thresholds: list[float]
    calm: CalmConfig


class PoseSetEntry(BaseModel):
    """One look of the standard set an outfit is given in one click."""

    label: str
    camera: str
    framing: str
    stance: str
    pose: str

    @model_validator(mode="after")
    def _valid(self):
        if self.stance not in ("SEATED", "STANDING"):
            raise ValueError("a pose set entry sits or stands")
        return self


class LookContinuity(BaseModel):
    """How far two neighbouring scenes of one video may differ in how the host looks: a change of
    stance weighs `stance_weight`, a step between cameras or framings one each."""

    max_change_per_cut: int = Field(ge=0)
    stance_weight: int = Field(ge=0)
    # position of each camera on the line from the left side view (-2) to the right (2)
    camera_position: dict[str, int]
    framing_order: list[str]


class HostLooksConfig(BaseModel):
    """Image generation of the host's looks (Part 7b). The model is one the
    gateway lists with the image-generation endpoint type (checked 2026-10-16);
    the first real generation verifies the edit endpoint's response."""
    image_model: str
    image_timeout_s: float = Field(gt=0)
    # The standard set of looks an outfit is given in one click.
    pose_set: list[PoseSetEntry]
    # The safe zone of the studio picture a stance needs (a seat for SEATED, free floor for
    # STANDING): a look is only requested where the studio has it.
    studio_zone: dict[str, str]
    # The pictures of the outfit (data/outfits/<slug>/<role>.png) a look is generated from, in
    # order of preference, per stance.
    continuity: LookContinuity


class LanguagePipelineGlossaryConfig(BaseModel):
    """The names and places of the evidence and how each language spells them. The owner
    approves the spellings; the facts check requires an approved spelling of every named
    entity of the evidence a text tells."""

    kinds: list[str]
    min_entity_chars: int = Field(ge=1)
    # Lower-case words that may stand inside a proper name ("Bay of Pigs", "van Gogh").
    name_connectors: list[str]
    # Languages whose spellings start as the evidence's own names (Latin script); the others
    # are proposed by the glossary agent. The owner approves both in the UI.
    seed_from_evidence: list[str]
    # Entities proposed to the glossary agent in one call.
    proposal_batch_entities: int = Field(ge=1, le=300)
    # Letters that attach to the front of a name in a script and still leave it spelled right.
    attached_prefixes: dict[str, list[str]]
    attached_suffixes: dict[str, list[str]]


class LanguagePipelineCrossLanguageConfig(BaseModel):
    glossary: LanguagePipelineGlossaryConfig
    # Scripts in which a name may be written with or without a space inside it.
    space_free_names: list[str]


class LanguagePipelineEstimateConfig(BaseModel):
    """Planning figures for the cost estimate printed before a run. They are
    assumptions, never provider prices and never an actual cost."""

    tokens_per_word: dict[str, float]
    chars_per_token: float = Field(gt=0.0)
    plan_tokens: int = Field(ge=0)
    critique_tokens: int = Field(ge=0)
    reasoning_tokens_ratio: dict[str, float]
    # Of the shared draft block, the share every call after the first reads from the cache when the
    # ledger holds no measurement yet. Nothing is assumed then: 0 (no cache) is the conservative figure.
    cache_share_without_data: float = Field(ge=0.0, le=1.0)
    # Planning figures for the checks of a delivered text (no ledger measured them before the first run):
    # what the correction of audit findings reads and the visible answers of the meaning check.
    audit_fix_input_tokens: int = Field(ge=0)
    fidelity_output_tokens: int = Field(ge=0)
    # Measured on a case's ledger, every attempt included (calibration.py measures them again):
    # the share of the range maximum a writer delivers and the draft's calls over its scenes
    # (failed and repeated scenes).
    words_share_of_max: dict[str, float]
    draft_attempt_factor: float = Field(ge=1.0)
    # The evidence audit's visible answer per call (planning assumption, no ledger measured it yet).
    audit_output_tokens_per_call: int = Field(ge=0)
    calibration_source: str
    note: str = ""


class VideoRightsHosts(BaseModel):
    """Hosts a video can be a film candidate from: host -> display name for the credits."""

    open_archives: dict[str, str]
    public_archives: dict[str, str]
    public_broadcasters: dict[str, str]


class VideoRightsYouTube(BaseModel):
    film_candidate_license: str
    # Only these channel kinds (video classification) can be film candidates at all; the
    # videos of other true-crime channels (creator narration, analysis...) never are.
    film_candidate_classifications: list[str]
    reuploader_channel_patterns: list[str]


class VideoSourceClass(BaseModel):
    """What puts a video into one source class: the kind of channel (video classification), words in
    the channel name or the title, or the host of a direct media file. A class needs at least one."""

    classifications: list[str] = []
    channel_markers: list[str] = []
    title_markers: list[str] = []
    hosts: list[str] = []
    # Channel names that can never be this class even when a title marker matches —
    # for witness_family: a commentary outlet titled 'Bodycam' is not a witness's own video.
    blocked_channel_markers: list[str] = []


class YouTubeClipsConfig(BaseModel):
    """The YouTube clip downloader (Settings switch, off by default): only videos of the allowed
    source classes, bounded in size, length of the source, length of a clip and share of a film."""

    default_enabled: bool = False
    max_height: int = Field(default=720, ge=144)
    max_source_seconds: float = Field(default=1800.0, gt=0)
    max_download_mb: float = Field(default=400.0, gt=0)
    # A clip from YouTube plays at most this long, and all of them at most this share of the film.
    max_clip_seconds: float = Field(default=16.0, gt=1)
    max_share: float = Field(default=0.08, ge=0.0, le=1.0)
    credit_suffix: str = "(YouTube)"


class VideoRightsConfig(BaseModel):
    """Which videos may ever go into our film, and why the others are research only.
    Doubt means research only."""

    film_candidate_hosts: VideoRightsHosts
    # The kinds of channel whose videos are other creators' edited films: always research only.
    research_only_classifications: list[str] = []
    # official_body, news_raw (a news agency's or broadcaster's raw source footage), witness_family,
    # creative_commons (a YouTube Creative Commons video of a channel that is not a commentator).
    source_classes: dict[str, VideoSourceClass] = {}
    youtube: VideoRightsYouTube
    open_license_markers: list[str]
    evidence_url_fields: list[str]
    reasons: dict[str, str]


class LanguagePipelineCoverageConfig(BaseModel):
    # A sentence tells an evidence item when their embeddings are at least this
    # similar; an item an act must withhold counts as told when it reaches reveal_similarity.
    min_similarity: float = Field(ge=0.0, le=1.0)
    reveal_similarity: float = Field(ge=0.0, le=1.0)
    max_reveal_violations: int = Field(ge=0)
    min_sentence_words: int = Field(ge=1)


class LanguagePipelineOriginalityConfig(BaseModel):
    """The originality guard: a final text may share facts with a research transcript but not
    its telling."""

    ngram_size: int = Field(ge=3, le=20)
    # Share of the text's n-word sequences that one source also contains.
    max_ngram_share: float = Field(ge=0.0, le=1.0)
    # Longest run of words the text shares with one source.
    max_run_words: int = Field(ge=3)
    # Sentences whose embedding is at least this close to a source sentence are near-verbatim
    # (the embedding is multilingual, so a copy across languages is found too).
    near_verbatim_similarity: float = Field(ge=0.5, le=1.0)
    max_near_verbatim_sentences: int = Field(ge=0)
    min_sentence_words: int = Field(ge=1)
    max_listed_sentences: int = Field(ge=1, le=100)


class LanguagePipelineGatesConfig(BaseModel):
    max_unsupported_figures: int = Field(ge=0)
    max_listed_passages: int = Field(ge=1, le=100)
    coverage: LanguagePipelineCoverageConfig
    originality: LanguagePipelineOriginalityConfig


class LanguagePipelineLedgerConfig(BaseModel):
    """The told_so_far ledger a draft scene call receives (see app/language_pipeline/ledger.py)."""

    # A sentence of the text so far that matches one of these (case-insensitive) already
    # explained an evidentiary limitation.
    limitation_patterns: list[str]
    max_limitation_words: int = Field(ge=5)
    max_limitations: int = Field(ge=1)


class LanguageAgentsConfig(BaseModel):
    """The agents of stages 2-4 for one language, by name (config/agents.json)."""

    rewrite: str
    inspect: str
    final: str
    # The one-time bounded length correction (stage 5); a language without it is a config error.
    length_correction: str
    # The meaning check of a translated language against the approved English text; required for a
    # language whose stage config says check "fidelity".
    fidelity: str | None = None


class LanguageStageConfig(BaseModel):
    """What one language's stages 2-4 are held to. Gates not listed here do not block."""

    blocking_gates: list[str]
    # A final text whose blocking gates pass is approved by the system itself (approved_by
    # "automatic" with the time, the stage versions and the cost); the owner approves nothing.
    automatic_approval: bool = False
    # What the post-master report compares besides numbers: the names both ways ("added_and_missing"),
    # only the master's names the text lacks ("missing"; a language that capitalises all nouns cannot
    # tell added names from other words), or none (another script cannot be matched).
    report_names: Literal["added_and_missing", "missing", "none"] = "none"
    # Also count what a right-to-left display or a voice can trip over (Persian-form letters,
    # mixed digit styles, bidirectional control characters, tatweel, Latin letters, text beside a
    # marker). Information only.
    report_script_hygiene: bool = False
    # No act's word target exceeds this many times the average act target (the targets add up to
    # the middle of the language's allowed word range; see lengths.act_word_targets).
    act_target_cap_ratio: float = Field(ge=1.0, le=5.0)
    # What the language is re-told from: the approved English draft (English itself) or the approved
    # English final text (every other language), and what its delivered text is checked for after the
    # inspection: the evidence ("evidence_audit", English) or the meaning of the English text ("fidelity").
    source: Literal["draft", "english_final"]
    check: Literal["evidence_audit", "fidelity"]


class ReviewConfig(BaseModel):
    """What the review of a delivered text (inspector, fidelity check, evidence audit) lets through."""

    # An unresolved item of one of these severities keeps the text open (no approval).
    blocking_severities: list[Literal["high", "medium", "low"]]
    # The items of these severities go into the one revision round.
    revision_severities: list[Literal["high", "medium", "low"]]


class AuditFixConfig(BaseModel):
    """The correction of confirmed factual findings in the English text: the findings are checked
    against the evidence first, and only a confirmed one is edited."""

    severities: list[Literal["high", "medium", "low"]]
    max_findings: int = Field(ge=1, le=100)
    # Facts nearest to a finding's quote (by embedding), besides the facts the finding cites.
    evidence_per_finding: int = Field(ge=0, le=30)
    # A paragraph repeats a finding's claim when this share of the quote's word trigrams stands in it.
    repeat_overlap: float = Field(gt=0.0, le=1.0)
    max_paragraphs_per_finding: int = Field(ge=1, le=20)
    # English's own fact audit/fix rounds across the whole case (every audit_fix or deterministic_fix
    # version already produced for this language, not per StoryVersion): once this many LLM rounds have
    # run without a clean result, the next round is the deterministic fallback — never a 4th model
    # attempt. Not a per-language rule: de/fa/ar have no fact audit at all (they run a meaning check
    # against the approved English text instead), so this cap never applies to them.
    max_fix_rounds: int = Field(ge=1, le=20)


class DraftSceneCheckConfig(BaseModel):
    """A draft scene that runs long or retells what the draft already says is written again once."""

    max_words_ratio: float = Field(gt=1.0, le=3.0)
    # Share of the scene's word trigrams that already stand earlier in the draft.
    max_trigram_overlap: float = Field(gt=0.0, le=1.0)


class DraftPackagingConfig(BaseModel):
    """How much of the evidence a draft call carries and how many beats it writes."""

    # "full": every call carries the whole evidence block. "act": a call carries the evidence of its act only
    # (its assigned facts and timeline entries, its contradictions with the facts that stand against them).
    mode: Literal["full", "act"]
    # Consecutive beats of one act written in one call (one "part" for the writer).
    scenes_per_call: int = Field(ge=1, le=3)
    # Facts nearest to a contradiction (by embedding) that go in the act package beside it.
    related_facts_per_contradiction: int = Field(ge=0, le=20)


class LengthCorrectionConfig(BaseModel):
    """The bounded length correction: one expansion of the acts that are too short, from the master's
    material for the same act. The count (once per chain) is not configurable."""

    enabled: bool
    # The master must hold at least this many times an act's shortfall in extra words.
    min_material_ratio: float = Field(gt=0.0, le=5.0)
    # The correction is told which passages were removed on purpose (at most this many sentences per
    # act); an answer that brings one back is rejected when this share of its word trigrams returns.
    max_removed_sentences: int = Field(ge=0, le=50)
    reintroduction_overlap: float = Field(gt=0.0, le=1.0)
    # The correction aims at the needed words plus this share, so a slightly short answer still passes.
    goal_margin_ratio: float = Field(ge=0.0, le=1.0)
    # An act that needs fewer words than this is not sent for them alone (a handful of words is not worth
    # a rewrite of the act); they count again only if the whole text is still below its minimum.
    min_needed_words: int = Field(ge=0, le=200)
    # An expanded act longer than this many times its target rejects the correction.
    max_act_overshoot: float = Field(gt=1.0, le=2.0)
    # An act whose repeated-trigram rate rose by more than this factor is flagged "likely padded".
    repetition_flag_factor: float = Field(gt=1.0, le=5.0)
    # After the one correction the length gate accepts a text this far (share of the range's minimum)
    # below the range; the first final is still held to final_length_tolerance, so the correction runs.
    accepted_shortfall: float = Field(ge=0.0, le=0.5)


class PostMasterFactReportConfig(BaseModel):
    """A deterministic comparison of the film text's numbers, dates and names with the master;
    it reports and never blocks or costs a model call."""

    enabled: bool


class EvidenceAuditConfig(BaseModel):
    """The one-pass audit of the English draft against the supplied facts and source excerpts. It
    is a report: it blocks nothing, repairs nothing and runs once per draft."""

    enabled: bool
    # Acts in one call: the evidence block is the same in every call (cached), the chunk is small.
    acts_per_call: int = Field(ge=1, le=20)
    max_items_per_call: int = Field(ge=1, le=100)
    # The strongest facts of the evidence pack the story must not misstate (omission checks).
    key_fact_count: int = Field(ge=0, le=500)
    # An answer that does not validate is asked for again this many times; then the call is a failure.
    invalid_output_retries: int = Field(ge=0, le=3)


class DraftReviewConfig(BaseModel):
    """The English inspector on the shared draft, once, before any language is told from it: an
    editorial report on the draft as a listening text, handed to the English rewrite. It blocks
    nothing and changes no text."""

    enabled: bool


class LanguagePipelineConfig(BaseModel):
    """One shared draft, then an independent finishing pipeline per language."""

    draft_language: str
    long_draft_minutes: int = Field(ge=1, le=240)
    requested_minutes_default: int = Field(ge=1, le=240)
    rewrite_headroom_minutes: int = Field(ge=0, le=60)
    language_concurrency: int = Field(ge=1, le=16)
    # Stage 2 of one language runs to the end first so the other languages' stage 2 reads the
    # shared prefix (plan, evidence, draft) from the provider's cache.
    stage2_cache_primer: bool
    final_length_tolerance: float = Field(ge=0.0, le=0.5)
    # How far an act may stray from its word target before the non-blocking per-act report marks it.
    act_length_tolerance: float = Field(ge=0.0, le=0.5)
    words_per_minute: dict[str, int]
    max_inspector_items: int = Field(ge=1, le=200)
    job_timeout_seconds: int = Field(ge=60)
    originality_min_words: int = Field(ge=1)
    gates: LanguagePipelineGatesConfig
    cross_language: LanguagePipelineCrossLanguageConfig
    estimate: LanguagePipelineEstimateConfig
    evidence_audit: EvidenceAuditConfig
    draft_review: DraftReviewConfig
    draft_scene_check: DraftSceneCheckConfig
    draft_packaging: DraftPackagingConfig
    length_correction: LengthCorrectionConfig
    review: ReviewConfig
    audit_fix: AuditFixConfig
    ledger: LanguagePipelineLedgerConfig
    # Stages 2-4 of every production language: its own agents and what its final text is held to.
    # A language without an entry is a config error (validated against the production languages).
    language_agents: dict[str, LanguageAgentsConfig]
    language_stages: dict[str, LanguageStageConfig]
    post_master_fact_report: PostMasterFactReportConfig

    def agents_for(self, language: str) -> LanguageAgentsConfig:
        try:
            return self.language_agents[language]
        except KeyError:
            raise KeyError(f"language_pipeline.language_agents has no entry for {language!r}") from None

    def stage_config_for(self, language: str) -> LanguageStageConfig:
        try:
            return self.language_stages[language]
        except KeyError:
            raise KeyError(f"language_pipeline.language_stages has no entry for {language!r}") from None

    def blocking_gates_for(self, language: str) -> list[str]:
        """The gates that block this language's final text."""
        return self.stage_config_for(language).blocking_gates

    def automatic_approval_for(self, language: str) -> bool:
        return self.stage_config_for(language).automatic_approval

    def minutes_range(self, requested: float | None = None) -> tuple[float, float]:
        """The film length stages 2-4 write to: the story's share of the requested minutes (all
        of them while the host's time comes on top, see host.time_accounting) up to the headroom
        above them."""
        low = ai_config.host.story_minutes(float(requested or self.requested_minutes_default))
        return low, low + self.rewrite_headroom_minutes

    def words_per_minute_for(self, language: str) -> int:
        try:
            return self.words_per_minute[language]
        except KeyError:
            raise KeyError(f"language_pipeline.words_per_minute has no entry for {language!r}") from None

    @model_validator(mode="after")
    def _validate(self):
        if self.long_draft_minutes < self.requested_minutes_default + self.rewrite_headroom_minutes:
            raise ValueError(
                "language_pipeline.long_draft_minutes must cover the longest final "
                "(requested_minutes_default + rewrite_headroom_minutes)")
        return self


class ScaledStageTimeBudgetConfig(BaseModel):
    base_seconds: float = Field(gt=0.0)
    seconds_per_target_minute: float = Field(ge=0.0)


class CostControlConfig(BaseModel):
    stage_time_budgets: dict[str, ScaledStageTimeBudgetConfig] = Field(default_factory=dict)


class BudgetConfig(BaseModel):
    """One case-wide spend policy, expressed in euros."""

    total_cap_eur: float = Field(default=20.0, gt=0.0)
    usd_to_eur: float = Field(default=0.92, gt=0.0)


class DiscoveryPolicies(BaseModel):
    """Discover-page (D4) soft-delete policy."""

    soft_delete_restore_days: int = Field(default=30, ge=0)


class ResearchDepthConfig(BaseModel):
    """Depth-weighted narrative capacity — deliberately conservative:
    full-text depth and evidence diversity drive the estimate, not raw
    source counts."""
    minutes_per_fulltext_source: float = Field(ge=0.0)
    minutes_per_partial_source: float = Field(ge=0.0)
    minutes_per_summary_source: float = Field(ge=0.0)
    minutes_per_metadata_source: float = Field(ge=0.0)
    minutes_per_evidence_item: float = Field(ge=0.0)
    minutes_per_human_detail: float = Field(ge=0.0)
    minutes_per_scene_detail: float = Field(ge=0.0)
    minutes_per_investigation_detail: float = Field(ge=0.0)
    minutes_per_quote: float = Field(ge=0.0)
    minutes_per_timeline_event: float = Field(ge=0.0)
    minutes_per_contradiction: float = Field(ge=0.0)
    minutes_per_independent_family: float = Field(ge=0.0)
    max_evidence_item_minutes: float = Field(ge=0.0)
    minimum_capacity_ratio: float = Field(gt=0.0, le=1.0)
    source_readiness_min_ratio: float = Field(default=0.5, gt=0.0, le=1.0)
    min_retrieved_sources: int = 1
    weak_area_minimums: dict[str, int] = {}


class SourceChunkingConfig(BaseModel):
    target_tokens: int = Field(gt=0)
    min_tokens: int = Field(gt=0)
    max_tokens: int = Field(gt=0)
    max_chunks_per_source_for_extraction: int = Field(ge=1, le=20)
    max_sources_per_extraction_call: int = Field(ge=1, le=20)
    # Extraction batches in flight at once; each uses its own session.
    extraction_concurrency: int = Field(ge=1, le=8)
    # Cosine similarity from which two extracted facts count as the same fact.
    merge_min_similarity: float = Field(ge=0.5, le=1.0)
    # Part 2(e): select before extracting. Only the top N sources per case
    # (ranked by reliability and content richness, after near-duplicate
    # removal) are ever sent to the fact extractor.
    max_sources_per_case_for_extraction: int = Field(ge=1, le=1000)
    # Cosine similarity from which two sources' content counts as a
    # near-duplicate/syndicated copy (one is skipped).
    duplicate_source_min_similarity: float = Field(ge=0.5, le=1.0)


class EvidenceStrengthConfig(BaseModel):
    weights: dict[str, float] = {}


class FollowUpResearchConfig(BaseModel):
    max_rounds: int = Field(ge=0, le=5)
    queries_per_gap: int = Field(ge=1, le=6)


class LanguageQualityConfig(BaseModel):
    minimum_target_script_ratio: float = Field(gt=0.0, le=1.0)
    max_foreign_token_ratio: float = Field(ge=0.0, lt=1.0)


class ResearchConfig(BaseModel):
    """Provider-neutral research controls (Part 21/23). The search and fetch limits of a research
    run are search_engine.* (connections.json); the spend limit is the case cap (policies.budget)."""
    provider: str
    dedupe_similarity_threshold: int = Field(ge=50, le=100)
    job_timeout_minutes: int = Field(ge=1)
    min_accept_score: float = Field(ge=0.0, le=1.0, default=0.2)
    default_profile: str = "DEEP_CASE_RESEARCH"


class VideoRelevanceConfig(BaseModel):
    """The only gate a discovered video passes: relevance to the case, never a count."""
    # Token-overlap score at or above this: relevant, no check needed.
    accept_from: float = Field(ge=0.0, le=1.0)
    # Below this: clearly unrelated, skipped with the reason "unrelated_to_case".
    unrelated_below: float = Field(ge=0.0, le=1.0)
    # In between: a cheap model reads title, channel and description and decides.
    check_batch_size: int = Field(ge=1, le=50)


class EvidenceSelectionConfig(BaseModel):
    """The fact cap shared by the story pack and the research stages."""
    # Facts the timeline builder and the contradiction analyzer receive.
    max_facts_per_research_stage: int = Field(ge=20, le=100)
    # A contradiction names sources, not facts: a fact stands in it when it
    # shares a source and at least this many terms (of this length) with it.
    term_min_length: int = Field(ge=2, le=10)
    contradiction_min_shared_terms: int = Field(ge=1, le=10)
    # A newly found contradiction whose topic is this similar (0-100) to a stored
    # one is the same contradiction and is not added again.
    same_contradiction_ratio: int = Field(ge=50, le=100)


class YouTubeDataAPIConfig(BaseModel):
    """P15d: quota settings for the YouTube Data API v3, read from its official docs
    (sources recorded in the field below) and verified against the project's live quota
    screen is the owner's own task — this records what the public docs say."""

    daily_quota_units: int = Field(default=10000, ge=1)
    daily_search_calls: int = Field(default=100, ge=1)
    reset_timezone: str = "America/Los_Angeles"
    cost_units: dict[str, int] = {}
    discovery_share_percent: float = Field(default=20.0, ge=0.0, le=100.0)
    uploads_reserved_percent: float = Field(default=10.0, ge=0.0, le=100.0)
    cache_expiry_hours: float = Field(default=24.0, gt=0.0)
    max_videos_per_channel_page: int = Field(default=50, ge=1, le=50)
    max_video_ids_per_call: int = Field(default=50, ge=1, le=50)
    clip_margin_seconds: float = Field(default=2.0, ge=0.0)
    max_candidates_per_sentence: int = Field(default=3, ge=1)
    # V2 clip wiring: how many search.list queries a case's clip discovery may send per run,
    # and how many results each asks for (search.list costs stay inside the discovery share).
    discovery_max_queries_per_case: int = Field(default=6, ge=0)
    discovery_max_results: int = Field(default=10, ge=1, le=50)


class OfficialSourcesConfig(BaseModel):
    """P2: automatic official-source discovery — no owner approval. A channel is proven
    official only when its score reaches proof_min_score AND (when proof_requires_domain_link)
    a real domain link exists either way: the channel links to the body's site, or the site
    links back to the channel. Everything else is skipped with the reason stored."""

    body_markers: dict[str, str] = {}
    standalone_bodies: list[str] = []
    channel_search_results: int = Field(default=3, ge=1, le=10)
    upload_titles_sampled: int = Field(default=20, ge=1, le=50)
    material_kinds: list[str] = []
    proof_scores: dict[str, int] = {}
    proof_min_score: int = Field(default=5, ge=1)
    proof_requires_domain_link: bool = True
    large_channel_subscribers: int = Field(default=50000, ge=0)
    official_upload_share: float = Field(default=0.3, ge=0.0, le=1.0)
    official_domain_suffixes: list[str] = []
    domain_fetch_seconds: float = Field(default=10.0, gt=0)
    # a channel's uploads playlist pages searched for case material (50 items per page)
    channel_upload_pages: int = Field(default=6, ge=1, le=20)


class YouTubeResearchConfig(BaseModel):
    languages: list[str] = ["en", "de", "fa", "ar"]
    max_results_per_language: int = Field(ge=1, le=50)
    discovery_provider: str = "auto"  # auto | youtube_api | engine
    # Discovery depth: query stems tried per language, results asked per query,
    # and the number of consecutive queries that add no new video before the
    # language is considered exhausted.
    max_queries_per_language: int = Field(ge=1, le=20)
    search_results_per_query: int = Field(ge=1, le=20)
    stop_after_empty_queries: int = Field(ge=1, le=10)
    relevance: VideoRelevanceConfig
    # Added to a candidate's relevance when ranking: the channel type matters.
    classification_rank_bonus: dict[str, float]


class SearchEngineConfig(BaseModel):
    """Brave Search Web API plus the local fetch/ranking pipeline."""
    brave_api_key_env: str = "BRAVE_SEARCH_API_KEY"
    brave_base_url: str = "https://api.search.brave.com"
    request_timeout_s: int = Field(ge=5, le=120, default=10)
    search_min_interval_s: float = Field(ge=0.0, le=60.0, default=1.0)
    # The health endpoint is a discovery-style probe, not a live search.  Keep
    # its result for ten minutes so polling and load balancers cannot create a
    # stream of billable Brave requests.
    health_cache_seconds: float = Field(ge=0, le=3600, default=600.0)
    # Iterative loop (Part 6/38)
    max_results_per_query: int = Field(ge=1, le=20)
    max_queries_per_language: int = Field(ge=1, le=20)
    max_rounds_per_language: int = Field(ge=1, le=8)
    max_fetches_per_language: int = Field(ge=0, le=50)
    consecutive_low_novelty_stop: int = Field(ge=1, le=5, default=2)
    max_duration_s: float = Field(ge=60, le=3600, default=900)
    # Ranking / acceptance (Parts 23-25)
    min_accept_score: float = Field(ge=0.0, le=1.0, default=0.2)
    fetch_quality_floor: float = Field(ge=0.0, le=1.0, default=0.15)
    rerank_band_low: float = Field(ge=0.0, le=1.0, default=0.35)
    rerank_band_high: float = Field(ge=0.0, le=1.0, default=0.6)
    max_rerank_candidates: int = Field(ge=0, le=30, default=10)
    enable_llm_rerank: bool = True
    enable_gap_analysis: bool = True
    # Extraction (Parts 13-15)
    min_full_text_chars: int = Field(ge=200)
    # Fetcher (Parts 10-12)
    fetch_timeout_s: int = Field(ge=5, le=120, default=30)
    fetch_max_bytes: int = Field(ge=100_000, le=50_000_000, default=8_000_000)
    fetch_cache_max_age_s: float = Field(ge=0, default=7 * 24 * 3600)
    fetch_resolve_dns: bool = True
    min_delay_per_domain_s: float = Field(ge=0.0, le=30.0, default=1.0)
    max_fetch_concurrency: int = Field(ge=1, le=20, default=4)
    # Internal index (Part 20)
    bm25_weight: float = Field(ge=0.0, le=1.0, default=0.55)
    dense_weight: float = Field(ge=0.0, le=1.0, default=0.45)


class TranscriptIngestionConfig(BaseModel):
    preferred_languages: list[str] = ["en"]
    max_transcript_seconds: int = Field(ge=60)
    max_segments_per_video: int = Field(ge=10)
    # One YouTube request may take at most this long; the library has no timeout of its own.
    request_timeout_seconds: float = Field(gt=0)


class TranscriptChunkingConfig(BaseModel):
    target_tokens: int = Field(gt=0)
    min_tokens: int = Field(gt=0)
    max_tokens: int = Field(gt=0)


class TranscriptQualityConfig(BaseModel):
    """Part 3(e) item 3: cheap, deterministic auto-caption quality gating
    — no model call. Manual captions skip this entirely (trusted by type,
    per app.providers.transcript.youtube's existing preference)."""
    max_segment_noise_ratio: float = Field(ge=0.0, le=1.0, default=0.15)
    degraded_garbled_rate: float = Field(ge=0.0, le=1.0, default=0.15)
    max_acceptable_garbled_rate: float = Field(ge=0.0, le=1.0, default=0.35)
    min_punctuation_ratio: float = Field(ge=0.0, le=1.0, default=0.2)


class TranscriptValueConfig(BaseModel):
    weights: dict[str, float] = {}


class ClaimClusteringConfig(BaseModel):
    near_duplicate_threshold: int = Field(ge=50, le=100)
    batch_size: int = Field(ge=1, le=500)
    # Clusters judged per evidence_verifier call: one call over every cluster
    # of a large case (1,626 on a 13-video case) is truncated by the model.
    verify_batch_size: int = Field(ge=1, le=200)
    min_independent_families_for_verified: int = Field(ge=1)


class EmbeddingsConfig(BaseModel):
    # A gateway 5xx/429 or network error is retried (the same request is
    # idempotent); a live run lost a whole research job to one transient 500.
    max_attempts: int = Field(ge=1, le=6)
    retry_backoff_s: float = Field(ge=0.0, le=60.0)


class SourceIndependenceConfig(BaseModel):
    shared_unusual_claim_ratio: float = Field(ge=0.0, le=1.0)
    min_shared_unusual_claims: int = Field(ge=1)


class SimilarityConfig(BaseModel):
    ngram_size: int = Field(ge=3, le=20)
    max_ngram_overlap_ratio: float = Field(ge=0.0, le=1.0)
    max_longest_sequence_ratio: float = Field(ge=0.0, le=1.0)
    structure_overlap_threshold: float = Field(ge=0.0, le=1.0)


class ReviewIndependenceConfig(BaseModel):
    """A reviewer must never be the model that wrote the text it judges.

    author_roles write narration; reviewer_roles score or validate it.
    When enabled: a reviewer's primary model may not be any author's
    model (checked at load), and a reviewer's fallback chain skips every
    author model — instead of silently letting the writer grade itself
    during a rate limit. reviewer_fallbacks optionally replaces the
    generic chain for reviewer roles (alias -> [aliases])."""

    enabled: bool = False
    author_roles: list[str] = []
    reviewer_roles: list[str] = []
    # Extra, independent pipelines: each group's reviewers judge only that
    # group's authors (e.g. the spoken adaptation may be written by the
    # model that reviews the master story, but never by its own critics).
    groups: list["ReviewGroup"] = []
    reviewer_fallbacks: dict[str, list[str]] = {}

    def all_groups(self) -> list["ReviewGroup"]:
        base = ReviewGroup(name="default", authors=self.author_roles,
                           reviewers=self.reviewer_roles)
        return [base, *self.groups]

    def all_reviewers(self) -> set[str]:
        return {r for g in self.all_groups() for r in g.reviewers}

    def authors_judged_by(self, reviewer: str) -> set[str]:
        """Author roles whose text this reviewer may judge."""
        return {a for g in self.all_groups() if reviewer in g.reviewers
                for a in g.authors}

    def strict_reviewers_of(self, author: str) -> set[str]:
        """Reviewer roles of strict groups that judge this author."""
        return {r for g in self.all_groups() if g.strict and author in g.authors
                for r in g.reviewers}


class ReviewGroup(BaseModel):
    name: str = ""
    authors: list[str] = []
    reviewers: list[str] = []
    # strict: the authors' fallback chain also skips the reviewers'
    # models, so a rate-limited writer never hands its text to the model
    # that will grade it.
    strict: bool = False
    # The owner explicitly allows a reviewer on the model of the authors it
    # judges, for THIS group only; owner_decision records the decision.
    same_model_allowed: bool = False
    owner_decision: str = ""

    @model_validator(mode="after")
    def _exception_is_recorded(self):
        if self.same_model_allowed and not self.owner_decision.strip():
            raise ValueError(
                f"review group {self.name!r} allows a reviewer on an author's model "
                "but records no owner_decision"
            )
        return self


ReviewIndependenceConfig.model_rebuild()


class VoiceBlocksConfig(BaseModel):
    """How narration is cut into text-to-speech blocks.

    Blocks never split a sentence, never cross an act boundary and prefer
    paragraph ends. Durations are estimated from the per-language
    language_pipeline.words_per_minute until real audio exists."""

    min_seconds: float = Field(default=30.0, gt=0)
    target_seconds: float = Field(default=60.0, gt=0)
    max_seconds: float = Field(default=90.0, gt=0)
    # Extra cost for ending a block mid-paragraph (between sentences)
    # instead of at a paragraph end. Relative to the squared-deviation
    # size cost, so 0.35 ≈ "accept ~60% size deviation to end a paragraph".
    sentence_break_penalty: float = Field(default=0.35, ge=0)
    # Cost for a block shorter than min_seconds when the act is long
    # enough to avoid one.
    short_block_penalty: float = Field(default=4.0, ge=0)
    # Characters of neighbouring text passed as TTS context
    # (ElevenLabs previous_text / next_text) for natural continuity.
    context_chars: int = Field(default=300, ge=0, le=2000)

    @model_validator(mode="after")
    def _validate(self):
        if not (self.min_seconds <= self.target_seconds <= self.max_seconds):
            raise ValueError(
                "voice_blocks requires min_seconds <= target_seconds <= max_seconds"
            )
        return self


class VoiceStyle(BaseModel):
    """Provider voice settings for one performance style. Small variation
    beats dramatic acting: styles differ mainly in speed and stability."""

    stability: float = Field(default=0.5, ge=0.0, le=1.0)
    similarity_boost: float = Field(default=0.75, ge=0.0, le=1.0)
    style: float = Field(default=0.0, ge=0.0, le=1.0)
    use_speaker_boost: bool = True
    speed: float = Field(default=1.0, ge=0.7, le=1.2)


class VoiceLanguageConfig(BaseModel):
    voice_id: str | None = None
    model_id: str = "eleven_multilingual_v2"
    # ISO 639-1 code sent to models that support language enforcement.
    language_code: str | None = None


class ElevenLabsConfig(BaseModel):
    secret_env: str = "TRUECRIME_ELEVENLABS_API_KEY"
    base_url: str = "https://api.elevenlabs.io"
    output_format: str = "mp3_44100_128"
    request_timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    # Models that reject previous_text / next_text (request stitching).
    no_context_models: list[str] = ["eleven_v4"]
    # Models that accept language_code (language enforcement).
    language_code_models: list[str] = ["eleven_v4", "eleven_turbo_v2_5",
                                       "eleven_flash_v2_5"]
    # Models that understand inline audio tags ([whispers], [pause] ...).
    audio_tag_models: list[str] = ["eleven_v4"]


class VoiceConfig(BaseModel):
    provider: str = "elevenlabs"
    elevenlabs: ElevenLabsConfig = Field(default_factory=ElevenLabsConfig)
    languages: dict[str, VoiceLanguageConfig] = {}
    default_style: str = "neutral_documentary"
    styles: dict[str, VoiceStyle] = Field(
        default_factory=lambda: {"neutral_documentary": VoiceStyle()}
    )
    # Base seed; a re-take after a failed check uses seed + attempt so a
    # retry is a genuinely different performance, reproducibly.
    seed: int = Field(default=1, ge=0)
    # Silence kept around the speech the provider reports (alignment),
    # so trimming never clips a breath or a word ending.
    lead_pad_ms: int = Field(default=60, ge=0, le=1000)
    tail_pad_ms: int = Field(default=150, ge=0, le=2000)
    # App-level pauses when blocks are assembled into one narration
    # track (dramatic pauses/silence come later from the director).
    between_blocks_ms: int = Field(default=200, ge=0, le=10000)
    between_sections_ms: int = Field(default=1400, ge=0, le=10000)
    work_dir: str = "data/cases"

    @model_validator(mode="after")
    def _validate(self):
        if self.default_style not in self.styles:
            raise ValueError(
                f"voice.default_style {self.default_style!r} is not in voice.styles"
            )
        return self

    def for_language(self, language: str) -> VoiceLanguageConfig:
        cfg = self.languages.get(language)
        if not cfg or not cfg.voice_id:
            raise ValueError(
                f"No narrator voice configured for language {language!r} "
                "(config: voice.languages.<lang>.voice_id)."
            )
        return cfg


class ASRCheckConfig(BaseModel):
    """Independent speech-to-text check of every rendered voice block:
    catches skipped, repeated, invented and badly mispronounced words."""

    enabled: bool = True
    provider: str = "faster_whisper"
    model: str = "small"
    compute_type: str = "int8"
    max_word_error_rate: float = Field(default=0.08, ge=0.0, le=1.0)
    # A run of this many consecutive missing (or extra) words fails the
    # block even when the overall error rate is low — that is a skipped
    # or invented phrase, not a transcription quirk.
    max_missing_run: int = Field(default=3, ge=1)
    max_extra_run: int = Field(default=3, ge=1)
    # Word pairs at least this similar (0–100, rapidfuzz ratio) count as
    # spelling variants, not errors — Whisper spells rare names its own
    # way ("Kadwill" -> "Cadwill"). Listed separately for review.
    spelling_variant_similarity: float = Field(default=80.0, ge=0.0, le=100.0)
    # Extra takes (different seed) when a block fails the check.
    auto_retakes: int = Field(default=1, ge=0, le=3)
    # "strict": every difference fails the block and earns a retake.
    # "warn": only differences in names and numbers are flagged, no retake.
    mode: Literal["strict", "warn"] = "strict"
    # Per-language overrides (e.g. a larger Whisper model and a more
    # lenient threshold where small Whisper models are weak, like Persian).
    languages: dict[str, "ASRLanguageOverride"] = {}

    def for_language(self, language: str | None) -> "ASRCheckConfig":
        o = self.languages.get(language or "")
        if not o:
            return self
        return self.model_copy(update=o.model_dump(exclude_none=True))


class ASRLanguageOverride(BaseModel):
    model: str | None = None
    max_word_error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    max_missing_run: int | None = Field(default=None, ge=1)
    max_extra_run: int | None = Field(default=None, ge=1)
    spelling_variant_similarity: float | None = Field(default=None, ge=0.0, le=100.0)
    mode: Literal["strict", "warn"] | None = None


ASRCheckConfig.model_rebuild()


class LoudnessConfig(BaseModel):
    narration_target_lufs: float = Field(default=-16.0, ge=-40.0, le=-5.0)
    true_peak_db: float = Field(default=-1.5, ge=-9.0, le=0.0)
    lra: float = Field(default=11.0, gt=0.0, le=50.0)
    # Flag a block whose loudness differs this much from the median.
    max_block_deviation_lu: float = Field(default=2.0, gt=0.0)
    sample_rate: int = Field(default=44100, ge=8000)


class DynamicEQBandConfig(BaseModel):
    """One harshness band: cut it only while it is brighter than this
    recording's own baseline + threshold_offset — never a static EQ."""
    name: str
    center_hz: float = Field(gt=0)
    bandwidth_hz: float = Field(gt=0)
    # Detection: band level must exceed baseline + this offset (dB)
    # before anything is reduced. The baseline is calibrated per file.
    threshold_offset_db: float = Field(default=12.0, ge=0.0, le=40.0)
    max_atten_db: float = Field(default=6.0, ge=0.0, le=24.0)
    # Share of the detected excess that is removed (0.5 = halve it).
    ratio: float = Field(default=0.5, gt=0.0, le=1.0)
    # Per-band envelope override; None uses the section defaults.
    attack_ms: float | None = Field(default=None, gt=0.0, le=500.0)
    release_ms: float | None = Field(default=None, gt=0.0, le=2000.0)
    strength: float = Field(default=1.0, ge=0.0, le=1.0)


class DeEsserConfig(BaseModel):
    """Independent dynamic de-esser for excessive sibilance."""
    enabled: bool = True
    center_hz: float = Field(default=6500.0, gt=0)
    bandwidth_hz: float = Field(default=4500.0, gt=0)
    threshold_offset_db: float = Field(default=14.0, ge=0.0, le=40.0)
    max_atten_db: float = Field(default=8.0, ge=0.0, le=24.0)
    ratio: float = Field(default=0.5, gt=0.0, le=1.0)
    attack_ms: float = Field(default=5.0, gt=0.0, le=500.0)
    release_ms: float = Field(default=60.0, gt=0.0, le=2000.0)
    strength: float = Field(default=1.0, ge=0.0, le=1.0)


class DynamicEQLanguageOverride(BaseModel):
    """Per-language nudges (deltas), not separate presets: the adaptive
    baseline already accounts for each voice and language."""
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    # Shift every harsh band's threshold (negative = more sensitive).
    threshold_offset_delta_db: float | None = Field(default=None, ge=-20, le=20)
    max_atten_delta_db: float | None = Field(default=None, ge=-24, le=24)
    deesser_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    deesser_threshold_offset_delta_db: float | None = Field(
        default=None, ge=-20, le=20)


class DynamicEQConfig(BaseModel):
    """Post-TTS dynamic EQ + de-esser (app/documentary/dynamic_eq.py)."""
    enabled: bool = True
    frame_ms: float = Field(default=20.0, gt=2.0, le=100.0)
    hop_ms: float = Field(default=5.0, gt=1.0, le=50.0)
    # Frames below this reference level are silence — never measured.
    gate_dbfs: float = Field(default=-55.0, ge=-120.0, le=0.0)
    # Speech-body reference the harsh bands are compared against.
    reference_band_hz: tuple[float, float] = (300.0, 2500.0)
    # Adaptive baseline: this percentile of the band-vs-reference excess
    # over active frames is "this voice's normal".
    baseline_percentile: float = Field(default=85.0, ge=1.0, le=99.0)
    # The baseline can never drop below this excess (dB): a voice whose
    # harsh band is basically silent must not get a hair-trigger
    # threshold that treats every mild consonant as a defect.
    baseline_floor_db: float = Field(default=-20.0, ge=-120.0, le=0.0)
    # …and the adaptive threshold can never rise above this absolute
    # excess (dB): a sustained resonance severe enough to become its own
    # baseline is still treated as harsh.
    threshold_ceiling_db: float = Field(default=10.0, ge=-20.0, le=30.0)
    attack_ms: float = Field(default=10.0, gt=0.0, le=500.0)
    release_ms: float = Field(default=120.0, gt=0.0, le=2000.0)
    # Global harshness-reduction strength (UI): scales every band.
    strength: float = Field(default=1.0, ge=0.0, le=1.0)
    bands: list[DynamicEQBandConfig] = Field(default_factory=lambda: [
        DynamicEQBandConfig(name="harsh_3k", center_hz=3100, bandwidth_hz=1500),
        DynamicEQBandConfig(name="harsh_4k7", center_hz=4700, bandwidth_hz=1700,
                            threshold_offset_db=15.0),
    ])
    deesser: DeEsserConfig = Field(default_factory=DeEsserConfig)
    languages: dict[str, DynamicEQLanguageOverride] = {}

    def for_language(self, language: str) -> "DynamicEQConfig":
        """Effective config for a language: base + its delta overrides."""
        o = self.languages.get(language or "")
        if not o:
            return self
        update: dict = {}
        if o.strength is not None:
            update["strength"] = o.strength
        if o.threshold_offset_delta_db or o.max_atten_delta_db:
            update["bands"] = [
                b.model_copy(update={
                    "threshold_offset_db": max(
                        0.0, b.threshold_offset_db
                        + (o.threshold_offset_delta_db or 0.0)),
                    "max_atten_db": max(
                        0.0, min(24.0, b.max_atten_db
                                 + (o.max_atten_delta_db or 0.0))),
                })
                for b in self.bands
            ]
        if o.deesser_strength is not None or o.deesser_threshold_offset_delta_db:
            update["deesser"] = self.deesser.model_copy(update={
                "strength": (o.deesser_strength
                             if o.deesser_strength is not None
                             else self.deesser.strength),
                "threshold_offset_db": max(
                    0.0, self.deesser.threshold_offset_db
                    + (o.deesser_threshold_offset_delta_db or 0.0)),
            })
        return self.model_copy(update=update)

    def resolved(self, language: str = "") -> dict:
        """Plain dict the DSP consumes (language profile applied). The
        override table itself is dropped so an edit for one language
        cannot invalidate another language's cached result."""
        cfg = self.for_language(language)
        d = cfg.model_dump()
        d.pop("languages", None)
        d["deesser"] = cfg.deesser.model_dump()
        d["bands"] = [b.model_dump() for b in cfg.bands]
        return d


class BlueprintConfig(BaseModel):
    """Editorial blueprint checks (deterministic, after the director)."""

    # More simultaneously open questions than this overloads a listener.
    max_open_questions: int = Field(default=3, ge=1)
    # Questions raised but never answered (beyond genuine unknowns).
    max_unanswered_questions: int = Field(default=2, ge=0)
    # Consecutive high-information beats before a recovery beat is due.
    max_dense_run: int = Field(default=3, ge=1)
    # One beat = one listener moment (~90 s at documentary pace).
    max_beat_words: int = Field(default=230, ge=50)
    max_repair_iterations: int = Field(default=1, ge=0, le=3)
    # Warnings worth one improvement round by the director. The revision
    # is kept only if it is error-free and has fewer such warnings.
    revise_on_warnings: list[str] = [
        "too_many_open_questions", "beat_too_long", "hook_not_at_act_start",
        "chapter_end_not_at_act_end", "many_unanswered_questions",
        "dense_run_without_recovery",
    ]
    max_revision_iterations: int = Field(default=1, ge=0, le=3)


class SpokenConfig(BaseModel):
    """Spoken storytelling adaptation: the approved script rewritten the
    way a person TELLS a true story (not how a newsreader reads it),
    natively per language, facts and uncertainty unchanged."""

    languages: list[str] = ["en", "de", "fa", "ar"]
    # Estimated speaking time of the spoken text vs. the source (per
    # language words-per-minute). Storytelling may breathe a little more.
    min_duration_ratio: float = Field(default=0.85, gt=0.0, le=1.0)
    max_duration_ratio: float = Field(default=1.3, ge=1.0, le=3.0)
    max_repair_iterations: int = Field(default=1, ge=0, le=3)
    # 0 = the whole story in one writer call (best flow); N = N beats per
    # call, each continuing from the previous chunk's spoken ending.
    beats_per_call: int = Field(default=0, ge=0)
    # Share of beats the native style critic must rate "storyteller"
    # ("mixed" beats are tolerated up to the rest; "newsreader" fails).
    min_storyteller_share: float = Field(default=0.7, ge=0.0, le=1.0)
    # Ear-friendly sentences: words per sentence above which a sentence is
    # "long" for the listener, and the share of long sentences allowed.
    max_sentence_words: dict[str, int] = {"en": 24, "de": 20, "fa": 24, "ar": 22}
    max_long_sentence_share: float = Field(default=0.12, ge=0.0, le=1.0)


class ChannelConfig(BaseModel):
    """One YouTube channel per language: its name (as the audience sees it)
    and the folder with its studio shots (data/studio/<lang>/: camera
    positions 01–06 of the design board and 07_floor_plan; manifest.json
    holds the shot list)."""

    name: str
    studio_dir: str | None = None  # repository-relative folder
    # The channel's studio profile in config/studio_registry.json
    # (default STUDIO_<LANG>). The voice stays in voice.languages — one
    # place per fact (see app/documentary/studio.channel_profile).
    studio_profile: str | None = None
    # Env var NAMES for this channel's avatar (default: the avatar section's).
    avatar_id_env: str | None = None
    avatar_key_env: str | None = None
    # The channel intro: its logo (repository-relative) and its concept
    # (app/documentary/intros.CONCEPTS); defaults per language there.
    logo: str | None = None
    intro_concept: str | None = None

    def profile_id(self, language: str) -> str:
        return self.studio_profile or f"STUDIO_{language.upper()}"


class AvatarConfig(BaseModel):
    """The on-screen host's avatar. Only the env-var NAMES live here; the
    key and the avatar id are read from .env. The id may name an avatar
    (a group of looks) or one look; a group resolves to its first look
    (HostScene.avatar_id records the look actually used)."""

    provider: str = "heygen"
    secret_env: str = "TRUECRIME_HEYGEN_API_KEY"
    avatar_id_env: str = "TRUECRIME_AVATAR_ID_HEYGEN"
    # Avatar videos cost provider credits: generated only when enabled
    # (voice and planning work without it).
    enabled: bool = False
    base_url: str = "https://api.heygen.com"
    # transparent avatar over our own studio (webm with alpha), else the
    # provider composites the studio image itself (mp4)
    output_format: str = "webm"
    resolution: str = "1080p"
    # How HeyGen fits the avatar's picture to the frame when it composites the studio itself
    # (create-video "fit": cover | contain). HeyGen has no avatar scale or position parameter:
    # the framing comes from the look's picture.
    fit: str = Field(default="contain", pattern="^(cover|contain)$")
    # HeyGen's rendering engine (create-video "engine.type"); None = the API default (avatar_iv).
    # avatar_iii takes no motion_prompt and no expressiveness (the API rejects them).
    engine: str | None = Field(default=None, pattern="^(avatar_iii|avatar_iv)$")
    request_timeout_s: float = Field(default=120.0, gt=0)
    poll_interval_s: float = Field(default=10.0, gt=0)
    max_poll_minutes: float = Field(default=20.0, gt=0)

    def configured(self) -> dict[str, bool]:
        from app.core.env import env_value

        return {"key_present": bool(env_value(self.secret_env)),
                "avatar_id_present": bool(env_value(self.avatar_id_env))}


class StudioConfig(BaseModel):
    """Channel studios: the registry (assets, safe zones, framing presets
    per channel) lives in config/studio_registry.json; the images stay in
    data/studio/<lang>/."""

    registry_path: str = "config/studio_registry.json"
    thumbs_dir: str = "data/studio_thumbs"
    thumb_width: int = Field(default=640, ge=64)
    # a framing preset that enlarges its background more than this is
    # flagged (the studio images are 1672x941; the film is 1920x1080)
    max_background_upscale: float = Field(default=1.6, ge=1.0)
    # which framing a host segment gets by where it sits in the film
    framing_by_position: dict[str, str] = Field(default_factory=lambda: {
        "opening": "HOST_MEDIUM", "mid": "HOST_CLOSE", "final": "HOST_WIDE"})


class HostRole(BaseModel):
    """One kind of host appearance: where it may stand and how many seconds it may take."""

    # opening | mid | final: where in the film the role stands.
    position: str
    base_seconds: float = Field(gt=0.0)
    min_seconds: float = Field(gt=0.0)
    max_seconds: float = Field(gt=0.0)
    # The appearance must ask the viewer something.
    requires_question: bool = False

    @model_validator(mode="after")
    def _validate(self):
        if self.position not in ("opening", "mid", "final"):
            raise ValueError("host role position must be opening, mid or final")
        if not (self.min_seconds <= self.base_seconds <= self.max_seconds):
            raise ValueError("host role seconds need min_seconds <= base_seconds <= max_seconds")
        return self


class HostLanguageRules(BaseModel):
    """What the deterministic checks hold the host's words of one language to."""

    # At least one must match: the host speaks to the viewer directly, informally.
    address_patterns: list[str]
    # None may match: the formal form of address.
    formal_patterns: list[str] = Field(default_factory=list)
    # None may match the start of an opening: greeting filler.
    greeting_patterns: list[str] = Field(default_factory=list)
    # None may match the LAST sentence of an opening: a handover line ("now let's go to the
    # story"); the opening ends with the fixed identity line instead.
    handover_patterns: list[str] = Field(default_factory=list)
    # Sentence-ending marks that make a sentence a question.
    question_marks: list[str] = Field(default_factory=lambda: ["?"])
    # Stock constructions the persona avoids (case-insensitive regexes).
    stock_phrases: list[str] = Field(default_factory=list)


class HostSpokenRules(BaseModel):
    min_sentences: int = Field(ge=2)
    short_sentence_words: int = Field(ge=1)
    min_short_sentences: int = Field(ge=0)
    min_length_variation: float = Field(ge=0.0, le=2.0)


class HandMotionConfig(BaseModel):
    """The measurement of how much the host's hands move in a finished avatar video: frames every
    `every_seconds`, the difference between neighbouring frames inside the hand region (fractions
    of the frame), a window counts as moving above `threshold` (mean grey-level difference)."""

    every_seconds: float = Field(default=2.0, gt=0)
    region: list[float] = [0.33, 0.60, 0.50, 0.72]    # x0, x1, y0, y1
    threshold: float = Field(default=6.0, gt=0)
    sample_width_px: int = Field(default=480, ge=64)


class HostConfig(BaseModel):
    """The recurring on-screen host (persona_master_prompt.md): when the
    host appears, how long, and the deterministic guard-rails on what the
    host may say (memories only from the archive, nothing the viewer has not
    heard yet, no stock phrases, nothing repeated from recent episodes)."""

    enabled: bool = True
    # The avatar is required only for the FINAL version (owner decision 2026-10-22, supersedes
    # 2026-10-21's "no film is built or rendered without it"): building a script and rendering a
    # draft never need it; publishing one does (app.lifecycle.videos.publish_blockers,
    # app.autopilot.runner._publish_inputs). Turning this off removes the publish-time gate too.
    required_for_film: bool = True
    # The persona and its rules (repository-relative markdown file).
    persona_file: str = "persona_master_prompt.md"
    # How the host's time relates to the requested film length: "added" (the story keeps the
    # whole requested length, the host comes on top) or "inside_requested" (the host's reserved
    # seconds are taken off the story's length).
    time_accounting: str = "added"
    # Appearances of one film, the opening and the outro included.
    min_appearances: int = Field(default=2, ge=0)
    max_appearances: int = Field(default=5, ge=1, le=8)
    # Acts of narration between two appearances, and the story seconds between them.
    min_acts_between: int = Field(default=2, ge=0)
    min_gap_seconds: float = Field(default=240.0, ge=0.0)
    # A film whose narration is shorter than this is a short film: the share limit is a
    # warning for it (an opening alone can exceed it) and the story seconds between two
    # appearances only have to reach short_film_min_gap_seconds.
    short_film_seconds: float = Field(default=600.0, ge=0.0)
    short_film_min_gap_seconds: float = Field(default=90.0, ge=0.0)
    # A short film still gets an opening, one bridge and an outro.
    short_film_min_appearances: int = Field(default=3, ge=0)
    hand_motion: HandMotionConfig = Field(default_factory=HandMotionConfig)
    # The kinds of appearance and their seconds [base, min, max] before the film's own weights.
    roles: dict[str, HostRole]
    # Seconds of an appearance grow with the weight of the act before it (its share of the
    # story against an even share) and with its place in the film (later = longer).
    act_weight_gain: float = Field(default=0.5, ge=0.0, le=2.0)
    position_gain: float = Field(default=0.3, ge=-1.0, le=1.0)
    # No appearance is longer than this (real voice seconds) ...
    max_appearance_seconds: float = Field(default=120.0, gt=0.0)
    # ... nor all of them together, nor more than this share of the story.
    max_total_seconds: float = Field(default=240.0, ge=0.0)
    max_total_share: float = Field(default=0.08, ge=0.0, le=0.5)
    # Level of the host's own audio against the narration track (Part 8a).
    audio_gain_db: float = Field(default=0.0, ge=-12.0, le=6.0)
    # The outro (role position "final") is off unless this switch is on.
    final_enabled: bool = True
    # Words the host speaks per minute, per language (the host is faster than the narrator).
    words_per_minute: dict[str, float]
    # A text may differ this much from its words for the planned seconds.
    word_tolerance: float = Field(default=0.12, ge=0.0, le=0.5)
    # The real voice may differ this much from the planned seconds before the text is
    # rewritten (and voiced again).
    duration_tolerance: float = Field(default=0.10, ge=0.0, le=0.5)
    # Rewrites (and new voices) allowed to bring a measured voice onto its planned seconds.
    max_retime_attempts: int = Field(default=3, ge=0, le=6)
    # The host's voice settings (a style of voice.styles): audibly a conversation, not the
    # narration's documentary delivery.
    voice_style: str = "host_conversational"
    # An act that answers at least this many questions is a payoff: no appearance stands
    # right before it (the answer belongs to the narration).
    payoff_min_resolved_loops: int = Field(default=2, ge=1)
    # Facts of the acts before an appearance that its writer and critic get, newest first.
    max_facts: int = Field(default=80, ge=1)
    # Context sizes sent to the writer.
    archive_cases: int = Field(default=30, ge=0)
    memory_items: int = Field(default=80, ge=0)
    recent_segments: int = Field(default=16, ge=0)
    max_repair_iterations: int = Field(default=2, ge=0, le=4)
    # Rewrites of one appearance's words after the code's or the critics' findings (the plan and
    # the stage direction keep max_repair_iterations).
    max_writer_repairs: int = Field(default=3, ge=0, le=5)
    # A segment sharing more than this share of its word trigrams with a
    # recent segment repeats it.
    max_trigram_overlap: float = Field(default=0.25, ge=0.0, le=1.0)
    # The first sentence of an opening is short enough to grab.
    opening_max_first_sentence_words: int = Field(default=16, ge=3)
    # Languages whose capitalised words are names (the second layer of the reveal check also
    # treats those as specific terms); the glossary and numbers apply to every language.
    spoiler_name_case_languages: list[str] = Field(default_factory=lambda: ["en"])
    # Languages whose host words go through the fact (meaning) check before the voice.
    meaning_check_languages: list[str] = Field(default_factory=list)
    # "Spoken, not written": a text of at least min_sentences sentences needs sentences of
    # varied length (coefficient of variation) and some very short ones.
    spoken: HostSpokenRules
    # Per-language rules for the deterministic checks (every channel language needs one).
    language_rules: dict[str, HostLanguageRules]
    # The agent (config/agents.json) that writes the host's words in each language.
    writer_agents: dict[str, str]

    def wpm(self, language: str) -> float:
        return self.words_per_minute[language]

    def is_short_film(self, narration_seconds: float) -> bool:
        return narration_seconds < self.short_film_seconds

    def min_appearances_for(self, narration_seconds: float) -> int:
        return (max(self.min_appearances, self.short_film_min_appearances)
                if self.is_short_film(narration_seconds) else self.min_appearances)

    def min_gap_for(self, narration_seconds: float) -> float:
        """Story seconds required between two appearances of a film of this narration length."""
        return (min(self.short_film_min_gap_seconds, self.min_gap_seconds)
                if self.is_short_film(narration_seconds) else self.min_gap_seconds)

    def story_minutes(self, requested_minutes: float) -> float:
        """The story's own length for a requested film length: all of it when the host comes on
        top, the requested length less the host's reserved seconds when it counts inside."""
        if self.time_accounting == "added":
            return float(requested_minutes)
        reserved = min(self.max_total_seconds, self.max_total_share * requested_minutes * 60)
        return float(requested_minutes) - reserved / 60

    @model_validator(mode="after")
    def _validate(self):
        if self.time_accounting not in ("added", "inside_requested"):
            raise ValueError("host.time_accounting must be added or inside_requested")
        if self.min_appearances > self.max_appearances:
            raise ValueError("host.min_appearances is above host.max_appearances")
        needed = {"opening", "outro"}
        if needed - set(self.roles):
            raise ValueError("host.roles needs an opening and an outro")
        if self.roles["opening"].position != "opening" or self.roles["outro"].position != "final":
            raise ValueError("the opening role stands at the opening, the outro at the end")
        for role, spec in self.roles.items():
            if spec.max_seconds > self.max_appearance_seconds:
                raise ValueError(f"host role {role} may take more than host.max_appearance_seconds")
        if not set(self.words_per_minute) == set(self.language_rules) == set(self.writer_agents):
            raise ValueError("host.words_per_minute, host.language_rules and host.writer_agents "
                             "need the same languages")
        return self


class AudioDirectionConfig(BaseModel):
    """Guard-rails for the audio director's plan (deterministic)."""

    # Most library tracks sent to the director with one film (spread over
    # the kinds and moods so a cap never starves one mood).
    library_max_tracks: int = Field(default=40, ge=1, le=200)

    # Allowed length (seconds) of each transition type after a beat.
    transitions: dict[str, list[float]] = Field(default_factory=lambda: {
        "breath": [1.0, 1.8],
        "music_bridge": [8.0, 16.0],
        "emotional_moment": [9.0, 18.0],
        "sting": [2.0, 4.0],
        "silence": [2.0, 4.0],
        "chapter_break": [12.0, 22.0],
    })
    # Pause between paragraphs inside a beat (a speaker's breath).
    paragraph_breath_ms: dict[str, int] = Field(default_factory=lambda: {
        "short": 600, "normal": 850, "long": 1150,
    })
    # Music-only moments may take at most this share of the runtime …
    max_music_only_share: float = Field(default=0.12, ge=0.0, le=0.5)
    # … and need this much narration in between (chapter breaks and the
    # moment after a reveal / chapter end are exempt).
    min_seconds_between_music_moments: float = Field(default=75.0, ge=0.0)
    # Music bed level under narration, dB relative to the narration.
    bed_levels_db: dict[str, float] = Field(default_factory=lambda: {
        "very_low": -26.0, "low": -21.0,
    })
    # Music-only moments relative to narration level.
    moment_level_db: float = Field(default=-7.0, le=0.0)
    sting_level_db: float = Field(default=-5.0, le=0.0)
    room_tone_level_db: float = Field(default=-34.0, le=0.0)
    max_repair_iterations: int = Field(default=1, ge=0, le=3)
    # A music moment starts this long before the beat's last word (fading
    # in under it) and keeps playing under the next beat's first words.
    music_lead_seconds: float = Field(default=2.0, ge=0.0, le=8.0)
    music_tail_seconds: float = Field(default=3.0, ge=0.0, le=10.0)
    # Clean narration: no music bed under the narrator's words. Music
    # lives in the gaps (between sections, before a reveal, after a strong
    # statement, at chapter turns, under silent picture sequences).
    beds_under_narration: bool = False
    # With beds off, a cue starts this long after the last word and is
    # faded out this long before the next word (no overlap with speech).
    music_start_after_word_seconds: float = Field(default=0.25, ge=0.0, le=3.0)
    music_end_before_word_seconds: float = Field(default=0.4, ge=0.0, le=3.0)
    # The emotional function of a cue (never "suspense because it is
    # true crime").
    moods: list[str] = ["suspense", "investigation", "mystery", "melancholy", "danger",
                        "discovery", "tension", "relief", "resolution", "uncertainty"]

    @model_validator(mode="after")
    def _validate(self):
        for kind, rng in self.transitions.items():
            if len(rng) != 2 or not (0 <= rng[0] <= rng[1]):
                raise ValueError(f"audio_direction.transitions.{kind} must be [min, max]")
        return self


class MusicCue(BaseModel):
    id: str
    kind: str          # bed | bridge | sting | room_tone
    mood: str          # see audio_direction.moods (+ neutral for room tone)
    seconds: float = Field(gt=0, le=30)
    loop: bool = False
    prompt: str


class MusicLibraryConfig(BaseModel):
    provider: str = "elevenlabs_sound"
    dir: str = "data/music_library"
    prompt_influence: float = Field(default=0.45, ge=0.0, le=1.0)
    # Every cue is normalized to this loudness when created, so mix
    # levels in audio_direction are predictable.
    reference_lufs: float = Field(default=-16.0)
    cues: list[MusicCue] = []
    # Variety across films: a track used in one of the last N films is
    # not chosen again while an alternative exists or can be generated.
    reuse_after_videos: int = Field(default=10, ge=0)
    max_variants_per_mood: int = Field(default=6, ge=1, le=30)
    # Where a film's music comes from. create: every film gets fresh
    # tracks while a variant can still be generated; library: only
    # tracks already in the database; create_if_missing: the database
    # first, a new track only when none exists for the (kind, mood).
    # The owner's choice (Settings) overrides this default.
    source_mode: Literal["create", "library", "create_if_missing"] = "create"
    # A new track whose kind and mood match an existing one and whose
    # situation and tags overlap at least this much is logged as a
    # near-duplicate (it is still created: the director found no fit).
    duplicate_similarity: float = Field(default=0.6, ge=0.0, le=1.0)
    # "Enough tracks" per "kind/mood" for the owner's traffic light; off
    # while both are empty. The mode never switches by itself.
    enough_target_default: int | None = Field(default=None, ge=1)
    enough_targets: dict[str, int] = Field(default_factory=dict)
    # How many of the latest films the owner's library_miss summary covers.
    overview_films: int = Field(default=10, ge=1, le=200)
    # Where in a film each kind of track is used; stored with every track
    # so agents and the owner can see in which situation to use it.
    kind_situations: dict[str, str] = Field(default_factory=lambda: {
        "bridge": "between two scenes, in the gap after a beat, to carry the listener into the next scene",
        "sting": "a short accent right after a turn, reveal or chapter end",
        "bed": "quietly under the narration (only when beds under narration are allowed)",
        "room_tone": "a deliberate silence: faint room air, no music",
    })
    # Base description per mood; a variant adds one of variant_styles.
    mood_prompts: dict[str, str] = Field(default_factory=lambda: {
        "suspense": "slow suspenseful documentary underscore, held low notes, a quiet pulse",
        "investigation": "measured investigative documentary underscore, steady soft pulse, curious",
        "mystery": "dark ambient mystery underscore, sparse notes, patient and unresolved",
        "melancholy": "melancholic documentary underscore, slow and intimate, gentle sadness",
        "danger": "ominous documentary underscore, low rumble, uneasy dissonance, restrained",
        "discovery": "documentary underscore for a discovery, rising soft swell, clarity",
        "tension": "tense documentary underscore, low pulse like a distant heartbeat",
        "relief": "warm documentary underscore, release of tension, calm resolution",
        "resolution": "documentary closing underscore, settled harmony, quiet dignity",
        "uncertainty": "uncertain documentary underscore, unresolved suspended chords, airy",
        "neutral": "quiet room tone, faint air, no music",
    })
    variant_styles: list[str] = [
        "low cello and felt piano", "warm synth pad and distant piano",
        "string quartet harmonics", "muted electric guitar swells and soft drone",
        "solo piano with long reverb", "low brass and soft timpani rolls",
        "glass harmonica textures and sub bass", "bowed vibraphone and low strings",
    ]

    def find(self, kind: str, mood: str | None = None) -> MusicCue | None:
        options = [c for c in self.cues if c.kind == kind]
        exact = [c for c in options if mood and c.mood == mood]
        return (exact or options or [None])[0]


class DocumentaryConfig(BaseModel):
    """Film-level rules shared by every stage."""

    languages: list[str] = ["en", "de", "fa", "ar"]
    # Approval gates: a result its auditor rejects is made again with the
    # reasons (and judged again) at most this many times; then it is left
    # out (a language, a host scene, a picture) or the job stops.
    max_redos: int = Field(default=2, ge=0, le=5)
    # Every finished documentary runs 45–120 minutes. Pilots are short
    # renders (pilot_seconds) of a full-length story, never short stories.
    min_film_minutes: float = Field(default=45.0, gt=0)
    max_film_minutes: float = Field(default=120.0, gt=0)
    pilot_seconds: float = Field(default=180.0, gt=0)
    # After the web research, a from-zero film also reads the case's YouTube
    # transcripts (extra claims, deeper evidence) unless the case already has them.
    video_research: bool = True
    # Ceiling of paid narration characters per documentary job (all languages
    # together); 0 = no ceiling. A language that would exceed it is not voiced.
    max_tts_characters_per_job: int = Field(default=0, ge=0)
    # Measured speed of the rendered narration per language (words per
    # minute), used to estimate film length before anything is rendered.
    speech_wpm: dict[str, int] = {"en": 150, "de": 128, "fa": 116, "ar": 104}
    storage_dir: str = "data/cases"
    dry_run: bool = False

    def wpm(self, language: str) -> int:
        return self.speech_wpm.get(language) or 140


class UsgsNaipConfig(BaseModel):
    """USGS/USDA NAIP aerial imagery (The National Map image service), public domain."""

    export_url: str
    page_url: str
    # Web Mercator extent of the service (x0, y0, x1, y1): outside it no image is asked for.
    extent: list[float] = Field(min_length=4, max_length=4)
    # Ground width in metres of each aerial image made around a place, and its size in pixels.
    view_meters: list[int] = Field(min_length=1)
    size_px: int = Field(ge=256, le=4000)
    license: str
    credit: str


class NasaConfig(BaseModel):
    """NASA Image and Video Library (images-api.nasa.gov): no key; public domain unless an item
    names a third party's copyright."""

    search_url: str
    asset_url: str
    page_url: str
    # File-name markers tried in this order to choose the image file of an item.
    size_markers: list[str] = Field(min_length=1)
    license: str
    credit: str


class EuropeanaConfig(BaseModel):
    """Europeana Search API (P12): a free key is required (none of this project's own); terms
    read 2026-10-21 (docs/open_sources_terms.md)."""

    search_url: str = "https://api.europeana.eu/record/v2/search.json"
    page_url: str = "https://www.europeana.eu/item{europeana_id}"
    api_key_env: str = "TRUECRIME_EUROPEANA_API_KEY"
    credit_suffix: str = "via Europeana"


class DplaConfig(BaseModel):
    """DPLA API (P12): a free key is required (none of this project's own); terms read
    2026-10-21 (docs/open_sources_terms.md). The response shape here is read from DPLA's own
    public documentation (github.com/dpla/platform), not a live-probed call — the live API
    itself is key-gated and none was available to probe it with."""

    search_url: str = "https://api.dp.la/v2/items"
    api_key_env: str = "TRUECRIME_DPLA_API_KEY"
    credit_suffix: str = "via DPLA"


class ArchivesConfig(BaseModel):
    """Structured dossier search shared by the archive providers (open_data_archives.py):
    alias fan-out, per-entity/per-case call caps, the open-rights-first threshold and the
    era-window padding applied to each API's date filter."""

    # one query per dossier alias, at most this many per entity per incoming search request
    max_aliases_per_entity: int = Field(default=4, ge=1)
    # HTTP calls a provider may make for one entity / for the whole run (both passes count)
    max_calls_per_entity: int = Field(default=10, ge=1)
    max_calls_per_case: int = Field(default=40, ge=1)
    # below this many open-rights candidates for an entity, an all-rights pass is allowed
    open_min_candidates: int = Field(default=3, ge=0)
    # the case/event year window is widened by this many years on each side
    era_padding_before_years: int = Field(default=50, ge=0)
    era_padding_after_years: int = Field(default=50, ge=0)


class OpenDataConfig(BaseModel):
    usgs_naip: UsgsNaipConfig
    nasa: NasaConfig
    europeana: EuropeanaConfig = Field(default_factory=EuropeanaConfig)
    dpla: DplaConfig = Field(default_factory=DplaConfig)
    archives: ArchivesConfig = Field(default_factory=ArchivesConfig)


class EntityDossierConfig(BaseModel):
    """P1: list-size limits for one entity dossier (entity_dossiers table)."""

    max_aliases: int = Field(default=12, ge=1)
    max_not_this: int = Field(default=8, ge=0)
    max_likely_sources: int = Field(default=6, ge=0)
    max_expected_captions: int = Field(default=6, ge=0)
    max_negative_terms: int = Field(default=8, ge=0)
    max_fact_ids: int = Field(default=40, ge=0)
    max_description_chars: int = Field(default=600, ge=50)


class VisualSearchConfig(BaseModel):
    def open_data_settings(self) -> OpenDataConfig:
        """The open-data providers' settings (visual_search.open_data in visuals.json)."""
        if self.open_data is None:
            raise RuntimeError("visual_search.open_data is missing in config/parameters/visuals.json")
        return self.open_data

    providers: list[str] = ["source_pages", "wikimedia"]
    # P3: which providers run for a modern case (incident year >= era_boundary_year) vs a
    # historical one. Empty for an era means "use `providers` unchanged" (old behaviour kept).
    era_boundary_year: int = Field(default=1980, ge=1800)
    providers_by_era: dict[str, list[str]] = {}
    # P4: query phrasing by era (the planner picks the slice named by the entity's dossier era).
    # Empty for an era means "use `expansion_templates` unchanged".
    expansion_templates_by_era: dict[str, dict[str, list[str]]] = {}
    # Part 4b: pages of the original owners named by a video's visual leads,
    # the Brave image search for the case's own people/places/documents, and
    # Brave video search results kept as LINKS for the owner (never downloaded).
    max_lead_sources: int = Field(default=6, ge=0)
    max_lead_pages_per_lead: int = Field(default=2, ge=1)
    max_video_link_queries: int = Field(default=3, ge=0)
    # People and places (by number of facts naming them) searched for at the
    # case level, before any blueprint exists.
    case_level_entities: int = Field(default=8, ge=0)
    video_links_per_query: int = Field(default=5, ge=1, le=20)
    user_agent: str = ("TrueCrimeStudio/1.0 (local documentary research tool; "
                       "https://github.com/Ferid1088/truecrime_story_app)")
    # Seconds between requests to public APIs (Wikimedia robot policy).
    min_request_interval_s: float = Field(default=1.0, ge=0.0)
    wikimedia_api: str = "https://commons.wikimedia.org/w/api.php"
    max_queries: int = Field(default=30, ge=1)
    max_candidates_per_query: int = Field(default=5, ge=1)
    # Extra searches per entity ("{name} historical photograph" ...), by
    # entity type, so a long film finds more than the planner's own queries.
    expansion_templates: dict[str, list[str]] = {}
    max_expansions_per_entity: int = Field(default=3, ge=0)
    # Only the entities with the most need get expanded searches.
    max_expansion_entities: int = Field(default=8, ge=0)
    # Wikimedia Commons category harvesting: the files of the category
    # named like an entity (and its direct subcategories).
    wikimedia_categories: bool = False
    category_max_files: int = Field(default=60, ge=1)
    max_category_entities: int = Field(default=12, ge=0)
    # The Wikipedia articles (these language editions) whose pictures are taken for an entity.
    wikipedia_languages: list[str] = []
    wikipedia_api: str = "https://{language}.wikipedia.org/w/api.php"
    max_source_pages: int = Field(default=25, ge=0)
    open_data: OpenDataConfig | None = None
    max_assets: int = Field(default=120, ge=1)
    # P5: at most this many DOWNLOADS per entity in one research run (candidates are already
    # ranked before the download loop, so this keeps one entity from using the whole budget).
    max_downloads_per_entity: int = Field(default=40, ge=1)
    # P6: a provider whose useful share (verified + needs_review of what was actually checked)
    # stays below this after at least this many checked pictures is skipped in later runs; its
    # query slots go to the other active providers instead.
    adaptive_stop_min_sample: int = Field(default=20, ge=1)
    adaptive_stop_min_share: float = Field(default=0.1, ge=0.0, le=1.0)
    # P11: model-guided query refinement for low-yield entities, budgeted per case.
    refinement_min_sample: int = Field(default=10, ge=1)
    refinement_min_share: float = Field(default=0.15, ge=0.0, le=1.0)
    refinement_max_calls_per_case: int = Field(default=5, ge=0)
    refinement_max_eur_per_case: float = Field(default=2.0, ge=0.0)
    refinement_sample_results: int = Field(default=15, ge=1)
    min_width: int = Field(default=600, ge=64)
    max_download_mb: float = Field(default=15.0, gt=0)
    request_timeout_s: float = Field(default=25.0, gt=0)
    # Stock libraries: found there means watermarked/licensed — never used.
    blocked_domains: list[str] = [
        "gettyimages.", "shutterstock.", "alamy.", "istockphoto.",
        "dreamstime.", "depositphotos.", "123rf.", "stock.adobe.",
    ]


class VideoAuditConfig(BaseModel):
    """The video auditor watches a piece frame by frame (in order): what
    it shows, cuts inside it, text/logos or faces that appear later,
    gore — and, before render, whether it fits the words."""

    frames_per_second: float = Field(default=1.0, gt=0.0, le=4.0)
    max_frames: int = Field(default=20, ge=3, le=40)
    frame_width: int = Field(default=384, ge=160, le=1280)


class ChaptersConfig(BaseModel):
    """Chapter cards and the running case timeline (on-screen cards made
    from the story's acts and the case's dated facts). Chapters are the
    master story's acts: the gap after the last beat of an act is a
    chapter_break (the audio plan makes sure of it) and the next
    chapter's card sits at the end of that gap. After a cold open (the
    film opens on a hook beat) the film's title card follows. Timeline
    cards move to the date the story reaches and show only the dates the
    viewer already knows. Texts are written by chapter_writer and approved
    by chapter_auditor (rejected: rewritten with the reasons, at most
    documentary.max_redos times, then left out — the card shows only the
    chapter number / the date). Dates come from the facts, never from a
    model."""

    enabled: bool = True
    # the film's title card after a cold open
    title_card: bool = True
    card_seconds: float = Field(default=4.5, ge=2.0, le=10.0)
    min_card_seconds: float = Field(default=3.0, ge=1.5, le=8.0)
    # the first picture of the new chapter appears this long before the
    # first word
    lead_out_seconds: float = Field(default=0.4, ge=0.0, le=2.0)
    max_title_chars: int = Field(default=48, ge=12, le=120)
    max_label_chars: int = Field(default=48, ge=12, le=120)
    # events on one timeline card (the current one and its neighbours)
    # events labelled by the writer (the ones the story tells, in order)
    max_labelled_events: int = Field(default=24, ge=1, le=80)
    # the marker slides to the new date in this time
    # The channel intro (app/documentary/intros.py): rendered once per
    # channel into intro_dir/<lang>/ and the same in every film — after
    # the cold open (in its chapter break), else before the first word.
    intro_enabled: bool = True
    intro_dir: str = "data/intros"
    # the film's sound under the intro (the intro has its own)
    intro_duck_db: float = Field(default=-18.0, le=0.0)


class AutopilotScheduleConfig(BaseModel):
    """Where the dry-run publish queue puts the first slot and how far apart the next ones are."""

    first_slot_hour_utc: int = Field(ge=0, le=23)
    slot_spacing_hours: int = Field(ge=1)
    film_before_shorts_hours: int = Field(ge=0)


class AutopilotConfig(BaseModel):
    """The Start button: which languages and platforms a run covers and who is recorded as the
    approver of automatic text approvals."""

    languages: list[str] = Field(min_length=1)
    platforms: list[str] = Field(min_length=1)
    automatic_approver: str = Field(min_length=1)
    tick_seconds: float = Field(gt=0)
    # A paid phase call (research, texts, titles) is tried at most this often per run; after
    # that the owner decides, the autopilot does not keep paying for the same failure.
    max_attempts_per_phase: int = Field(ge=1)
    render_profile: Literal["preview", "publish"]
    schedule: AutopilotScheduleConfig


class DetectiveBoardConfig(BaseModel):
    """The detective board: a growing road of short-title cards, one per
    key narrated fact, pasted onto the board as the film reaches it and
    never forgotten afterwards. Rendered by our own code from facts the
    script already contains — no paid provider call besides one writer
    call for the native-language titles."""

    enabled: bool = True
    title_max_words: int = Field(default=5, ge=2, le=10)
    # The board is planned to the end of the case: at least one card lies in this last share of the
    # film's beats (never in its very last beat, which has no room left to show it).
    final_stretch_share: float = Field(default=0.25, gt=0.0, le=1.0)
    min_cards: int = Field(default=4, ge=1, le=20)
    max_cards: int = Field(default=14, ge=1, le=40)
    # beats between two selected card-beats (a turning point is not
    # followed by another one right away)
    min_beats_between_cards: int = Field(default=2, ge=0, le=20)
    # the reading hold after a new card is pasted: base seconds plus this
    # many seconds per word of the title, bounded to [min,max]
    hold_base_seconds: float = Field(default=12.0, ge=0.0, le=30.0)
    hold_per_word_seconds: float = Field(default=1.8, ge=0.0, le=6.0)
    hold_min_seconds: float = Field(default=15.0, ge=0.5, le=45.0)
    hold_max_seconds: float = Field(default=24.0, ge=0.5, le=60.0)
    # the board never carries more than this share of the film's picture
    # time (same spirit as fill_ways/coverage caps)
    max_share: float = Field(default=0.15, ge=0.0, le=1.0)
    min_card_seconds: float = Field(default=12.0, ge=0.5, le=30.0)
    # cards per row on the cork board; the note's size as a fraction of W/H
    cards_per_row: int = Field(default=4, ge=2, le=12)
    card_width_fraction: float = Field(default=0.215, ge=0.05, le=0.4)
    card_height_fraction: float = Field(default=0.2, ge=0.05, le=0.4)
    # a note's drift off its grid cell (fraction of the cell) and tilt (degrees)
    jitter_fraction: float = Field(default=0.12, ge=0.0, le=0.3)
    max_tilt_degrees: float = Field(default=5.0, ge=0.0, le=15.0)
    # a note flies in, then its strings are pulled tight to the notes it
    # is really related to; several new notes follow each other
    paste_seconds: float = Field(default=1.0, ge=0.1, le=3.0)
    thread_seconds: float = Field(default=1.0, ge=0.1, le=3.0)
    stagger_seconds: float = Field(default=3.6, ge=0.0, le=10.0)
    thread_sag_fraction: float = Field(default=0.06, ge=0.0, le=0.3)
    thread_color: str = "#a3201c"
    pin_colors: list[str] = Field(default_factory=lambda: [
        "#2b5fd0", "#c9302c", "#d03fa0", "#2f9e5b"])
    kind_colors: dict[str, str] = Field(default_factory=lambda: {
        "person": "#c98a3a", "place": "#3a7fc9", "time": "#7a7a7a",
        "object": "#8a6ad6", "event": "#c92c28", "contradiction": "#d6414a",
        "open_question": "#b8aa4a",
    })
    # paper colour of a note by kind (index card, sticky note, clipping)
    paper_colors: dict[str, str] = Field(default_factory=lambda: {
        "person": "#efe6d2", "place": "#efe6d2", "time": "#f4efe4",
        "object": "#efe6d2", "event": "#f4efe4", "contradiction": "#f2c9c4",
        "open_question": "#f6e58a",
    })


class VisualVerificationConfig(BaseModel):
    # Candidates vision-checked per visual need (more checked = more
    # usable pictures = less repetition).
    per_requirement: int = Field(default=5, ge=1, le=20)
    verified_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    reject_below_confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    # A lower bar for these kinds of subject only (never a person: identity keeps the general bar).
    verified_min_confidence_by_subject: dict[str, float] = {}
    # A context verdict (a real picture of the same kind, not the thing itself) needs at least this for
    # these subjects, where a lookalike of another kind is easy to mistake for the right kind.
    context_min_confidence_by_subject: dict[str, float] = {}
    thumbnail_px: int = Field(default=768, ge=128)
    # A picture of the right real place or thing in the wrong period (a modern photo of the
    # house) is kept, for these kinds of subject; the auditor still judges it per sentence.
    modern_view_subjects: list[str] = []
    # A picture that is not what it was found for but shows another known thing of the case
    # (for these kinds of subject) is kept for that thing instead of being rejected.
    other_entity_subjects: list[str] = []
    # Every unchecked photo found at this relevance tier or closer (1 exact case ... 5 generic) is
    # vision-checked, not only the best-ranked ones of each need.
    check_all_up_to_tier: int = Field(default=3, ge=1, le=5)
    # P7: tiered checking. The cheap tier (role visual_verifier_cheap) looks first; its verdict
    # is trusted only when decisive (verified or rejected, never needs_review/unverified), never
    # for a person (identity always goes to the strong tier) and only at this extra-safety
    # confidence margin (stricter than verified_min_confidence/reject_below_confidence, since a
    # cheaper model is less reliable) — otherwise the strong verifier (visual_verifier) decides,
    # as before.
    cheap_tier_enabled: bool = True
    cheap_tier_decisive_confidence: float = Field(default=0.85, ge=0.0, le=1.0)


class StorageBudgetConfig(BaseModel):
    """Disk use of a case's pictures and clips."""

    # Searching stops taking new files once a case holds this much (MB).
    max_case_mb: float = Field(default=3000.0, gt=0)
    # Files of a rejected picture are removed this many days after it was found; the metadata and
    # the reason stay.
    rejected_retention_days: int = Field(default=14, ge=0)


class PlannerLimits(BaseModel):
    """Queries and aliases the visual planner may keep per entity."""

    max_exact_queries: int = Field(ge=1)
    max_context_queries: int = Field(ge=0)
    max_aliases: int = Field(ge=0)


class AudioLimits(BaseModel):
    max_miss_tags: int = Field(ge=0)


class LanguagePipelineLimits(BaseModel):
    # An inspector whose answer does not validate is asked once more; a second failure fails the stage.
    inspector_attempts: int = Field(ge=1)


class ResearchJobLimits(BaseModel):
    # How often a film job looks at its research job, and how long it waits for it at most.
    poll_seconds: float = Field(gt=0)
    max_hours: float = Field(gt=0)


class DiscoveryLimits(BaseModel):
    # Earlier discovery runs the cost estimate is averaged over.
    cost_estimate_sample_size: int = Field(ge=1)


class SearchLimits(BaseModel):
    max_query_words: int = Field(ge=1)


class LimitsConfig(BaseModel):
    """Volume and retry limits that bound how much a run searches, checks, asks and pays for
    (config/parameters/limits.json). The EUR limits are policies.budget and visuals.supply."""

    planner: PlannerLimits
    audio: AudioLimits
    language_pipeline: LanguagePipelineLimits
    research_job: ResearchJobLimits
    discovery: DiscoveryLimits
    search: SearchLimits


class MusicProviderConfig(BaseModel):
    """The generated-music provider as a swappable abstraction: callers read this, never a
    hardcoded provider name, so the provider can change without touching code. ``licence_cleared``
    is the single gate a caller must check before generating or using any track; it starts
    false and is only set true by an explicit owner decision recorded in
    docs/shorts_host_led_checklist.md."""

    name: str = "elevenlabs_music"
    api_base: str = "https://api.elevenlabs.io"
    endpoint: str = "/v1/music"
    model_id: str = "music_v2"
    tier_required: str = "unconfirmed"
    licence_cleared: bool = False
    # Owner switch: when false, no new track may ever be generated — selection and library
    # building may only reuse already-stored host_led_music_tracks rows, and must fail or skip
    # cleanly (never silently fall back to a paid call) when none fits.
    allow_generation: bool = True


class HostLedMusicLibraryConfig(BaseModel):
    """The real, once-generated host-led-reel music library per channel: one short mood family
    description per language (never an artist/band/song/film name), a handful of named moods,
    and the banned-phrase guard a prompt must pass before any paid call is made. Distinct from
    the long-form documentary's ``AIConfig.music_library`` (a different feature, different
    schema) despite the similar name."""

    channel_mood_family: dict[str, str] = {}
    moods: list[str] = []
    track_length_seconds: float = Field(default=20.0, gt=0)
    min_tracks_per_language: int = Field(default=3, ge=1)
    max_tracks_per_language: int = Field(default=4, ge=1)
    banned_prompt_phrases: list[str] = []
    plan_name: str | None = None
    no_repeat_window_reels: int = Field(default=5, ge=1)
    pinned_mood: str | None = None


class ClosingLibraryConfig(BaseModel):
    """The stored closing clips (the fixed call-to-action and identity line spoken by the host):
    how many variants a language should have, how many recent uses are avoided, and the default
    of the two owner switches (the Settings page stores the live value)."""

    target_variants: int = Field(default=6, ge=1)
    # The same variant never twice within this many recent uses of a language (at least the
    # last one, whatever the library holds).
    recent_history: int = Field(default=3, ge=1)
    # A seed makes the random choice repeatable (tests); None picks freely.
    seed: int | None = None
    default_use_stored: bool = True
    default_create_new: bool = True


class HostLedShortsConfig(BaseModel):
    """Host-led Shorts/Reels cut from the finished film: Fereidoun opens every reel, a fixed
    outro and end card close it, cover labels and outro sentences are owner-approved and never
    rewritten by an agent."""

    avatar_max_seconds: float = Field(default=14.0, gt=0)
    hook_seconds_min: float = Field(default=4.0, gt=0)
    hook_seconds_max: float = Field(default=8.0, gt=0)
    hook_max_words: int = Field(default=20, ge=1)
    outro_seconds_min: float = Field(default=5.0, gt=0)
    outro_seconds_max: float = Field(default=7.0, gt=0)
    # Owner feedback 2026-10-20: "the logo must be shown 2 seconds longer" — raised from the
    # spec's original ~2s default.
    end_card_seconds: float = Field(default=4.0, gt=0)
    # Owner feedback 2026-10-20: the hook's opening avatar shot needs a subtle camera move
    # ("not too fast but not too slowly"). A slow push-in from 1.0x to this end scale over the
    # hook's own duration — ~1.3%/second for a ~6s hook, imperceptible frame to frame but
    # visibly progressed by the end, the standard low end of a Ken Burns push-in.
    hook_camera_zoom_end: float = Field(default=1.08, ge=1.0, le=1.3)
    hard_duration_max_seconds: float = Field(default=60.0, gt=0)
    template_version: int = Field(default=1, ge=1)
    asset_store_dir: str = "data/shared_assets"
    reading_hold_min_seconds: float = Field(default=3.0, ge=0)
    reading_hold_max_seconds: float = Field(default=4.0, ge=0)
    cover_label: dict[str, str] = {}
    outro_sentence: dict[str, str] = {}
    channel_name: dict[str, str] = {}
    channel_logo: dict[str, str] = {}
    music_provider: MusicProviderConfig = Field(default_factory=MusicProviderConfig)
    music_library: HostLedMusicLibraryConfig = Field(default_factory=HostLedMusicLibraryConfig)
    music_attribution_text: dict[str, str] = {}
    music_duck_db_under_speech: float = Field(default=-14.0, le=0)
    music_level_db_visual_only: float = Field(default=-8.0, le=0)
    music_duck_attack_seconds: float = Field(default=0.35, ge=0)
    music_duck_release_seconds: float = Field(default=0.6, ge=0)
    target_lufs: float = Field(default=-14.0, le=0)
    banned_hook_phrases: list[str] = []
    pose_outfit_variants_min_per_language: int = Field(default=3, ge=1)
    # The fixed identity line, spoken by the host in the viewer's informal address. The same
    # words close every short and end the host's opening of every long film; owner-approved,
    # never rewritten by an agent.
    identity_line: dict[str, str] = {}
    # Shorts closing: the outro sentence then the identity line, or the reverse.
    closing_order: Literal["cta_then_identity", "identity_then_cta"] = "cta_then_identity"
    closing_library: ClosingLibraryConfig = Field(default_factory=ClosingLibraryConfig)


class FillWaysConfig(BaseModel):
    """The ways, besides another real picture, a gap in the picture track is filled with: they use only
    what the film and the case already hold (its own pictures, words and facts)."""

    # Tried in this order for every gap that no unused real picture fills.
    order: list[Literal["reframe", "split", "text"]] = ["reframe", "split", "text"]
    # A real picture shown again as a close-up: seconds since it was last on screen, and how
    # often one picture may come back.
    reuse_gap_seconds: float = Field(default=45.0, ge=0)
    max_reuse_per_picture: int = Field(default=2, ge=0)
    max_per_film: dict[str, int] = {"reframe": 8, "split": 3, "text": 6}
    # The regions of a picture (x0, y0, x1, y1 as fractions) a later appearance may be cropped to,
    # in the order they are used; a picture never shows the same region twice.
    crop_regions: list[list[float]] = Field(min_length=1, default_factory=lambda: [
        [0.05, 0.05, 0.55, 0.55], [0.45, 0.05, 0.95, 0.55], [0.05, 0.45, 0.55, 0.95],
        [0.45, 0.45, 0.95, 0.95], [0.25, 0.25, 0.75, 0.75]])
    # A spoken sentence becomes a text scene when it holds a quotation mark or a number and has
    # a length within these limits.
    text_min_chars: int = Field(default=20, ge=1)
    text_max_chars: int = Field(default=160, ge=20)
    quote_marks: str = "\"«»“”„"


class CoverageConfig(BaseModel):
    """What the film's picture track must satisfy (checked before and after the render)."""

    max_card_stretch_seconds: float = Field(default=6.0, gt=0)
    max_card_share: float = Field(default=0.12, ge=0, le=1.0)
    max_black_seconds: float = Field(default=0.0, ge=0)
    # One still (a photo, a map) is never shown longer than this; a longer stretch gets other pictures.
    max_still_seconds: float = Field(default=20.0, gt=0)
    max_repair_rounds: int = Field(default=3, ge=1, le=10)
    # A black screen in the finished file is black for at least this long (shorter dips are the fades
    # of a card or of the film's start and end).
    black_screen_min_seconds: float = Field(default=1.0, gt=0)
    # Per gap: unused real pictures the auditor may look at, and the length of the pictures a gap is
    # cut into (the picture track's editing rhythm).
    pool_tries: int = Field(default=2, ge=0, le=6)
    fill_piece_seconds: float = Field(default=9.5, gt=0)
    fill_concurrency: int = Field(default=3, ge=1, le=8)


class RightsConfig(BaseModel):
    """FOUND does not mean USABLE. Which rights statuses each render
    profile may show; 'preview' renders are internal review copies."""

    allowed_for_render: dict[str, list[str]] = Field(default_factory=lambda: {
        "preview": ["owned", "licensed", "public_domain", "creative_commons",
                    "editorial_review_required"],
        "publish": ["owned", "licensed", "public_domain", "creative_commons"],
    })
    attribution_required: list[str] = ["creative_commons"]
    # Providers whose pictures are public domain but ask for a credit: the film shows it.
    credit_providers: list[str] = []
    # False: rights are no filter. True-crime pictures and footage are taken as public material
    # (owner decision); only relevance to the case, the point in the story and the spoken words
    # decides (the verifier and the auditor). Credits for attribution_required licences stay.
    # True: the allowed_for_render lists and the editorial approval apply as before.
    enforce: bool = True


class MotionConfig(BaseModel):
    """Subtle camera moves on stills — never a visible slideshow effect."""

    push_scale: float = Field(default=1.07, ge=1.0, le=1.3)
    pan_fraction: float = Field(default=0.05, ge=0.0, le=0.3)
    emotional_slowdown: float = Field(default=0.6, gt=0.0, le=1.0)
    parallax_enabled: bool = True
    parallax_shift_fraction: float = Field(default=0.012, ge=0.0, le=0.05)
    min_hold_seconds: float = Field(default=5.0, gt=0.0)
    max_still_seconds: float = Field(default=16.0, gt=0.0)
    # One picture (with its reframes) is never on screen longer than this:
    # a longer hold becomes a sequence of earned pictures.
    max_hold_seconds: float = Field(default=20.0, gt=0.0)
    # A map is orientation, not a backdrop: after this many seconds the
    # story's pictures take over (zoom levels only when nothing else may
    # be shown).
    max_map_seconds: float = Field(default=12.0, gt=0.0)
    crossfade_seconds: list[float] = [0.7, 1.4]
    # The same motion is not used more than this many shots in a row.
    max_same_motion_run: int = Field(default=2, ge=1)


class MapsConfig(BaseModel):
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    attribution: str = "© OpenStreetMap contributors"
    geocoder_url: str = "https://nominatim.openstreetmap.org/search"
    tile_size: int = 256
    # Zoom steps of an orientation sequence (wide -> close).
    darken: float = Field(default=0.55, ge=0.0, le=1.0)
    # A place with the same name as one of the case, but this far from it, is asked for again with the
    # geocoder preferring the area (viewbox: this many degrees around the case's place).
    near_retry_km: float = Field(default=30.0, gt=0)
    near_viewbox_degrees: float = Field(default=1.5, gt=0)
    near_limit: int = Field(default=20, ge=5, le=40)


class ZoomImagery(BaseModel):
    """One open imagery source of the zoom-in map. Every entry names its licence and the
    attribution shown on screen; an entry that is not allowed, or comes from a forbidden
    provider or host, never loads."""

    id: str
    provider: str
    kind: Literal["xyz", "wms"]
    url: str
    params: dict[str, str] = {}
    coverage: Literal["global"] | list[list[float]]
    zoom_min: int = Field(ge=0, le=22)
    zoom_max: int = Field(ge=0, le=22)
    resolution_m: float = Field(gt=0)
    # composites are black (or nearly: open water) outside their swath: pixels this dark or darker become
    # transparent and the layer below shows (0 = off)
    nodata_below: int = Field(default=0, ge=0, le=255)
    # drawn over the water of the vector map (the globe's oceans); other imagery lies under the water, so that
    # the swath edges and dark fills of composites never show in the sea
    over_water: bool = False
    licence: str
    licence_url: str
    attribution: str
    allowed: bool

    @model_validator(mode="after")
    def _validate(self):
        for field in ("licence", "licence_url", "attribution", "provider", "url"):
            if not getattr(self, field).strip():
                raise ValueError(f"imagery {self.id}: {field} is empty")
        if not self.allowed:
            raise ValueError(f"imagery {self.id}: allowed must be true (an entry that is not allowed is removed)")
        if self.zoom_max < self.zoom_min:
            raise ValueError(f"imagery {self.id}: zoom_max below zoom_min")
        if self.coverage != "global" and any(len(b) != 4 for b in self.coverage):
            raise ValueError(f"imagery {self.id}: coverage boxes are [west, south, east, north]")
        return self


class ZoomVariant(BaseModel):
    seconds: float = Field(gt=0)              # the camera move; the shot then holds on the place for hold_seconds
    hold_seconds: float = Field(default=0.0, ge=0.0)
    pitch_end: float = Field(default=0.0, ge=0.0, le=85.0)
    line_share: float = Field(default=0.7, gt=0.0, le=1.0)
    extrude: bool = False


class ZoomLabels(BaseModel):
    """Which names a map label shows per film language (first one the place has), and the right-to-left languages."""

    chains: dict[str, list[str]]
    rtl_languages: list[str]


class MvpSearchConfig(BaseModel):
    """Bounded search rounds per unmet visual requirement."""

    initial_rounds: int = Field(ge=1, le=3)
    extra_rounds_max: int = Field(ge=0, le=5)
    min_new_candidates_to_continue: int = Field(ge=0)


class MvpShotsConfig(BaseModel):
    min_seconds: float = Field(gt=0.0)
    max_still_seconds: float = Field(gt=0.0)
    target_shots_per_minute: float = Field(gt=0.0)


class MvpCoverageConfig(BaseModel):
    min_temporal_coverage: float = Field(gt=0.0, le=1.0)
    max_uncovered_gap_seconds: float = Field(ge=0.0)
    max_black_frame_seconds: float = Field(ge=0.0)
    evidence_requirements_need_evidence_roles: bool


class MvpVerificationConfig(BaseModel):
    escalate_conflicts_to_second_verifier: bool
    transient_failure_retries: int = Field(ge=0)
    min_confidence_exact_person: float = Field(gt=0.0, le=1.0)
    min_confidence_other: float = Field(gt=0.0, le=1.0)
    min_quality: float = Field(ge=0.0, le=1.0)
    max_candidates_per_requirement: int = Field(ge=1)
    max_requirements_per_asset: int = Field(ge=1)
    rights_profile: str
    approving_statuses: list[str] = Field(min_length=1)
    never_approving_statuses: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _disjoint(self):
        if set(self.approving_statuses) & set(self.never_approving_statuses):
            raise ValueError("main_visual_planner.verification: a status cannot both approve and never approve")
        return self


class MvpTopupConfig(BaseModel):
    """Coverage top-up: stored pictures no requirement named are judged for the place and context needs of
    the beats the shot list left bare, before the film is directed again."""

    max_assets: int = Field(ge=0)
    passes: int = Field(ge=0, le=3)
    min_relevance: float = Field(ge=0.0)


class MvpMomentsConfig(BaseModel):
    """Moment cutting: for a moment of the film that no picture, video or graphic covers, the part of a long
    video of the case that shows what is said is found, cut by meaning, checked and offered to the planner."""

    enabled: bool
    min_clip_seconds: float = Field(gt=0.0)
    max_clip_seconds: float = Field(gt=0.0)
    # a clip is cut this much longer than the moment, so a shot never runs past its clip
    margin_seconds: float = Field(ge=0.0)
    max_moments: int = Field(ge=1)
    regions_per_moment: int = Field(ge=1)
    picks_per_call: int = Field(ge=1)
    frames_per_region: int = Field(ge=3)
    frame_width: int = Field(ge=128)
    # a video counts as the case's own when at least this many of its pieces are approved
    trusted_min_approved: int = Field(ge=1)
    accept_fit: list[str] = Field(min_length=1)
    review_fit: list[str]
    # share of a moment's words a stored moment clip must share with a bare stretch to be placed on it
    min_overlap: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate(self):
        if self.max_clip_seconds <= self.min_clip_seconds:
            raise ValueError("main_visual_planner.moments: max_clip_seconds must exceed min_clip_seconds")
        if set(self.accept_fit) & set(self.review_fit):
            raise ValueError("main_visual_planner.moments: a fit level cannot be both accepted and sent to review")
        return self


class MvpTopicalConfig(BaseModel):
    """Topical fill: a moment still bare after every picture, clip and graphic may show an approved picture or video
    that is related to what is said by at least `min_score` (0-1, judged against the owner's examples)."""

    enabled: bool
    min_score: float = Field(gt=0.0, le=1.0)
    max_per_moment: int = Field(ge=1)
    moments_per_call: int = Field(ge=1)


class MvpNeighbourCoverConfig(BaseModel):
    """The owner-approved last resort for moments that are still bare: each half is covered by the neighbouring
    picture held longer, or by a photo of its own when the neighbour is not a still picture or is already held as
    long as `shots.max_still_seconds` allows. No piece of a photo is shorter than `min_piece_seconds`."""

    min_piece_seconds: float = Field(gt=0.0)


class MvpOverlaysConfig(BaseModel):
    """Other channels' and agencies' marks in the pictures and footage the film uses (a logo, a watermark, a caption
    strip, a channel name, a bar with a logo in it). Each approved picture and clip is scanned; the film shows the
    largest part of it that holds none of them (at least `min_keep_area` of the picture), and a picture or clip
    that cannot be cut free of them is not used."""

    enabled: bool
    # sampled frames of a clip and their width; a photo is scanned at `photo_width`
    frames_per_video: int = Field(ge=1)
    video_frame_width: int = Field(ge=256)
    photo_width: int = Field(ge=256)
    # the least share of the picture's own area (its bars left out) that the cut must keep
    min_keep_area: float = Field(gt=0.0, le=1.0)
    # the cut's width/height stays between these; a 16:9 cut is taken when it keeps at least `frame_preference` of
    # the area the largest clean cut keeps (a cut of another shape is shown on a blurred fill of itself)
    min_aspect: float = Field(gt=0.0, le=1.0)
    max_aspect: float = Field(ge=16 / 9)
    frame_preference: float = Field(ge=0.5, le=1.0)
    # the cut must hold the centre of the picture: a mark that can only be avoided off-centre (a divider through the
    # middle of a split picture, a caption over the subject) leaves the picture unusable
    must_hold_centre: bool
    # every mark is widened by this share of the picture's width and height before the cut avoids it
    margin: float = Field(ge=0.0, lt=0.2)
    kinds: list[str] = Field(min_length=1)
    max_marks: int = Field(ge=1)
    pictures_per_call: int = Field(ge=1)
    version: int = Field(ge=1)


class MvpTimingConfig(BaseModel):
    """Story timing of the pictures and clips: each approved picture or clip is labelled with what it shows (its
    phase of the story) and the first beat in which the story has told of that; the film shows it from that beat
    on and never before (a criminal complaint is not shown while the officers still search the garage)."""

    enabled: bool
    version: int = Field(ge=1)
    assets_per_call: int = Field(ge=1)
    summary_chars: int = Field(ge=40)
    # phases that always belong to a later part of the story: never labelled "any beat"
    phases: list[str] = Field(min_length=3)
    always_ok: list[str] = Field(min_length=1)


class MvpOwnerNoteConfig(BaseModel):
    """The owner's note beside a picture or clip in the review (who or what it shows, how it belongs to the story)."""

    max_chars: int = Field(ge=40)


class LogoLapConfig(BaseModel):
    """Every `every_seconds` of the film the logo leaves its corner, goes once around the screen and returns, taking
    `seconds` (it eases out of and into its place); it is on screen for the whole lap, over any kind of shot. The
    route runs along the screen's edges inset by the logo's margin, its lower edge `bottom_clear_fraction` of the
    height above the bottom (the subtitles and the safe zone stay free; 0 = the full perimeter)."""

    every_seconds: float = Field(gt=0.0)
    seconds: float = Field(gt=0.0)
    bottom_clear_fraction: float = Field(ge=0.0, lt=0.5)


class LogoConfig(BaseModel):
    """The channel logo shown in a corner of the film (config/parameters/branding.json): on pictures and clips, not on
    graphics, maps, host scenes, chapter or title cards (they have their own layouts, or the logo already plays)."""

    enabled: bool
    shot_kinds: list[str] = Field(min_length=1)
    height_fraction: float = Field(gt=0.0, le=0.3)
    margin_fraction: float = Field(ge=0.0, le=0.2)
    opacity: float = Field(gt=0.0, le=1.0)
    fade_seconds: float = Field(gt=0.0)
    min_window_seconds: float = Field(gt=0.0)
    # a logo file without transparency has a black background: below this brightness it becomes see-through
    black_key_below: int = Field(ge=0, le=255)
    # a faint dark plate under the logo keeps it readable on a bright picture
    plate_alpha: float = Field(ge=0.0, le=1.0)
    lap: LogoLapConfig


class BrandingConfig(BaseModel):
    logo: LogoConfig


class MvpReviewConfig(BaseModel):
    """The persisted owner-review workflow; the film is a candidate until APPROVED."""

    state_machine: list[str] = Field(min_length=2)
    # Full films in the other languages wait for the owner's approval of the exact English render.
    require_english_first: bool


class MainVisualPlannerConfig(BaseModel):
    """The Main Visual Planner's limits (config/parameters/main_visual_planner.json). Its
    agents' models and sampling parameters are the mvp_* roles in config/models.json."""

    # One model call of the planner (a picture check, a query round, a beat's direction) that has not
    # answered by then counts as a transient failure and is retried, never waited for indefinitely.
    call_timeout_s: float = Field(gt=0.0)
    search: MvpSearchConfig
    shots: MvpShotsConfig
    coverage: MvpCoverageConfig
    verification: MvpVerificationConfig
    topup: MvpTopupConfig
    moments: MvpMomentsConfig
    topical: MvpTopicalConfig
    neighbour_cover: MvpNeighbourCoverConfig
    overlays: MvpOverlaysConfig
    timing: MvpTimingConfig
    owner_note: MvpOwnerNoteConfig
    review: MvpReviewConfig


class StreamConfig(BaseModel):
    """How the text of a graphic is revealed: word by word, at a pace slower than reading, in silence. An alternative
    with its own page (a conflict, a quote, a recap) first shows its empty page and then streams the text in the order
    it is read; a board that continues (the timeline, the board of conflicts, a document whose quote is
    highlighted at its cue) keeps what it shows, lets the new card in and streams only the new text. At the end the whole page stays on screen for `end_hold_seconds`."""

    enabled: bool
    words_per_second: float = Field(gt=0.0)
    # one word fades in over this long
    word_fade_seconds: float = Field(gt=0.0)
    # the empty page (or the first page of a board) is shown this long before the first word
    start_seconds: float = Field(ge=0.0)
    # a new card of a continuing board fades in this long before its words; a page of its own waits this long
    appear_seconds: float = Field(ge=0.0)
    lead_seconds: float = Field(ge=0.0)
    # after the words of a step, before the next step
    pause_seconds: float = Field(ge=0.0)
    end_hold_seconds: float = Field(ge=0.0)
    # in a shorter shot than its schedule the words go faster, but not beyond this share of the schedule
    min_speed_scale: float = Field(gt=0.0, le=1.0)
    # the planner holds a graphic this much longer than its English schedule (other languages need more words)
    plan_margin: float = Field(ge=1.0)
    continued: list[str]


class GraphicsConfig(BaseModel):
    """The code-made graphics (config/parameters/graphics.json): look, fixed labels per language and the
    defaults the planner uses; the planner, not a graphic, decides use, place, length and cues."""

    canvas: dict[str, int]
    colors: dict[str, str]
    type_scale: dict[str, int]
    margin: int = Field(gt=0)
    # the strip at the bottom of every page that stays empty for the subtitles: content is shrunk to end above it
    safe_zone: dict[str, float]
    transition_seconds: float = Field(gt=0.0)
    line_draw_seconds: float = Field(gt=0.0)
    stream: StreamConfig
    hold_seconds: dict[str, float]
    cue_fractions: dict[str, list[float]]
    timeline: dict
    document_card: dict
    conflict: dict
    conflict_board: dict
    recap: dict
    floor_plan: dict
    key_facts: dict
    headline: dict
    planner: dict
    engines: dict
    labels: dict[str, dict[str, str]]

    @model_validator(mode="after")
    def _validate(self):
        if set(self.labels) != {"en", "de", "fa", "ar"}:
            raise ValueError("graphics.labels needs exactly en, de, fa and ar")
        keys = set(self.labels["en"])
        for language, table in self.labels.items():
            if set(table) != keys:
                raise ValueError(f"graphics.labels.{language} differs from en: "
                                 f"{sorted(set(table) ^ keys)}")
        for kind, fractions in self.cue_fractions.items():
            if kind not in self.hold_seconds:
                raise ValueError(f"graphics.cue_fractions.{kind} has no hold_seconds")
            if fractions != sorted(fractions) or not all(0.0 <= f < 1.0 for f in fractions):
                raise ValueError(f"graphics.cue_fractions.{kind} must rise within [0, 1)")
        return self


class MonitoringConfig(BaseModel):
    """What the Monitoring tab watches (config/parameters/monitoring.json): when something counts as stuck or
    unhealthy and which workspace tab belongs to each pipeline stage."""

    refresh_seconds: float = Field(gt=0.0)
    stuck_stage_minutes: float = Field(gt=0.0)
    queued_wait_seconds: float = Field(gt=0.0)
    budget_warn_fraction: float = Field(gt=0.0, le=1.0)
    provider_window_minutes: float = Field(gt=0.0)
    provider_min_calls: int = Field(gt=0)
    provider_error_rate: float = Field(gt=0.0, le=1.0)
    recent_jobs: int = Field(gt=0)
    recent_days: float = Field(gt=0.0)
    recent_provider_errors: int = Field(gt=0)
    stage_tabs: dict[str, str]


class ZoomMapConfig(BaseModel):
    """The cinematic zoom-in map (globe -> region -> street) built from open data, offline:
    a Protomaps/OpenStreetMap extract, open imagery and a MapLibre scene rendered frame by
    frame. Nothing here calls a paid or forbidden service."""

    enabled: bool = True
    data: dict
    hosts: dict[str, list[str]]
    imagery: list[ZoomImagery]
    forbidden_providers: list[str]
    imagery_fade_levels: float = Field(gt=0)
    imagery_dim: float = Field(ge=0.0, le=1.0)
    style: dict[str, str | float]
    labels: ZoomLabels
    # The page around the zoom video: the map takes `map_share` of the frame width, a panel lists the places
    # the camera passes (country, region, town, the place); a step is current from the zoom level in step_zooms.
    panel: dict
    camera: dict
    variants: dict[str, ZoomVariant]
    # which variant a map beat gets: first mention, a move from the place shown before, a place coming back
    variant_rules: dict[str, str]
    marker: dict[str, float]
    limits: dict[str, float | int]
    render: dict

    @model_validator(mode="after")
    def _validate(self):
        forbidden = [f.lower() for f in self.hosts.get("forbidden", [])]
        providers = [f.lower() for f in self.forbidden_providers]
        ids = [e.id for e in self.imagery]
        if len(ids) != len(set(ids)):
            raise ValueError("zoom_map.imagery: duplicate id")
        for e in self.imagery:
            host = urlparse(e.url).netloc.lower()   # a layer name such as GoogleMapsCompatible is no provider
            bad = [x for x in providers if x in e.provider.lower() or x in host]
            bad += [x for x in forbidden if x in host]
            if bad:
                raise ValueError(f"imagery {e.id}: forbidden provider or host {bad}")
        missing = {"zoom_in", "route", "pitched_3d"} - set(self.variants)
        if missing:
            raise ValueError(f"zoom_map.variants missing {sorted(missing)}")
        if set(self.variant_rules) != {"first", "move", "return"} or set(self.variant_rules.values()) - set(self.variants):
            raise ValueError("zoom_map.variant_rules names first, move and return, each a configured variant")
        return self


class AttentionConfig(BaseModel):
    """Cognitive load: the viewer is never asked to read, look and follow
    dense narration at the same time."""

    max_overlay_words: int = Field(default=14, ge=1)
    max_read_items_per_beat: int = Field(default=1, ge=0)
    max_changes_per_minute: float = Field(default=7.0, gt=0)
    min_changes_per_minute: float = Field(default=1.0, ge=0)
    # Reveal firewall: a visual may not show evidence that a LATER beat
    # with one of these purposes discloses.
    firewall_purposes: list[str] = ["reveal", "contradiction", "evidence",
                                    "false_lead", "chapter_end"]
    # A black screen is a short dramatic pause, never a whole beat: after
    # this many seconds a picture the story has already earned takes over.
    max_black_seconds: float = Field(default=6.0, ge=0.0)
    # Pictures of the investigation (searches, police, rescue teams) are
    # not shown before the story's first incident (it would reveal it).
    investigation_terms: list[str] = [
        "search", "police", "officer", "rescue", "investigat", "helicopter",
        "sniffer", "dog handler", "firefighter", "volunteers", "missing poster",
        "cordon", "forensic"]
    # Pictures of custody or court (what the vision check saw, not the
    # article headline) are not shown before the beat that tells of the
    # arrest — the first beat whose summary contains one of arrest_terms.
    custody_terms: list[str] = [
        "inmate", "jumpsuit", "prison uniform", "jail uniform", "courtroom", "in court",
        "sentencing", "verdict", "handcuff", "mugshot", "booking photo", "defense attorney",
        "defence attorney", "witness stand", "on trial", "arraign", "gefängnis", "gerichtssaal",
        "handschellen", "angeklagte"]
    arrest_terms: list[str] = [
        "arrest", "charged with", "in custody", "indicted", "taken into custody",
        "festgenommen", "verhaftet", "festnahme", "verhaftung", "angeklagt",
        "بازداشت", "دستگیر"]
    # The same date or place card is not shown again within this time.
    repeat_overlay_seconds: float = Field(default=150.0, ge=0.0)


class RenderConfig(BaseModel):
    profile: str = "preview"
    width: int = Field(default=1920, ge=320)
    height: int = Field(default=1080, ge=240)
    fps: int = Field(default=25, ge=10, le=60)
    crf: int = Field(default=20, ge=10, le=40)
    preset: str = "veryfast"
    burn_subtitles: bool = False
    subtitle_max_chars: int = Field(default=42, ge=10)
    subtitle_max_seconds: float = Field(default=6.0, gt=0)


class DocumentaryCriticsConfig(BaseModel):
    critics: list[str] = ["automation_feel", "attention", "visual_accuracy", "production"]
    max_fix_iterations: int = Field(default=1, ge=0, le=3)
    max_asset_reuse: int = Field(default=3, ge=1)


class PerformanceConfig(BaseModel):
    """Voice direction: blueprint intents -> voice styles and pauses.

    Pause classes: micro pauses inside sentences belong to the voice
    engine; 'dramatic' and 'silence' are inserted by the app between
    blocks (silence = no narrator, no music, only room tone later)."""

    style_for_intent: dict[str, str] = Field(default_factory=lambda: {
        "neutral": "neutral_documentary",
        "factual": "factual",
        "investigative": "factual",
        "controlled_tension": "controlled_tension",
        "urgent_but_controlled": "controlled_tension",
        "reveal": "reveal",
        "reflective": "reflective",
        "emotional_restraint": "reflective",
    })
    pause_ms: dict[str, int] = Field(default_factory=lambda: {
        "none": 0, "short": 350, "dramatic": 1400, "silence": 2800,
    })
    # ± variation of long pauses so they never sound machine-identical.
    pause_jitter: float = Field(default=0.15, ge=0.0, le=0.5)
    # At most this share of beats may end in a dramatic pause/silence.
    max_long_pause_share: float = Field(default=0.2, ge=0.0, le=1.0)
    # These beat purposes keep their long pause when caps apply.
    protected_purposes: list[str] = ["reveal", "chapter_end"]

    @model_validator(mode="after")
    def _validate(self):
        missing = {"none", "short", "dramatic", "silence"} - set(self.pause_ms)
        if missing:
            raise ValueError(f"performance.pause_ms missing {sorted(missing)}")
        return self


class VoicePerformanceConfig(BaseModel):
    """Voice performance director (ElevenLabs v4 audio tags).

    Levels form the narrator's arc: 0 neutral (nothing has happened
    yet), 1 first unease, 2 dark and crime-specific, 3 breath-taking
    moments (slow, measured, plain: never whispered). Every tag must be on
    the palette of its level (levels are cumulative); everything else is
    removed by the validator."""

    enabled: bool = True
    sentences_per_call: int = Field(default=70, ge=10)
    # Voice style per level (names in voice.styles).
    level_styles: dict[str, str] = {
        "0": "v3_neutral", "1": "v3_unease", "2": "v3_tension", "3": "v3_climax"}
    level_tags: dict[str, list[str]] = Field(default_factory=lambda: {
        "0": ["pause", "thoughtful", "calm"],
        "1": ["slowly", "slow", "serious", "concerned"],
        "2": ["low", "somber", "measured", "deliberate"],
        "3": [],
    })
    # Never used by a documentary narrator (taste, credibility): no whispering, no
    # theatrics, no emotion played at the listener.
    forbidden_tags: list[str] = [
        "whisper", "whispers", "whispering", "hushed", "barely audible", "breathless",
        "gasps", "fearful", "softly", "quietly", "sighs", "anxious", "ominous", "tense",
        "uneasy", "distant", "hesitant", "drawn out",
        "laughs", "laughing", "giggles", "chuckles", "crying", "sobbing",
        "shouts", "shouting", "loudly", "yelling", "excited", "playful",
        "amazed", "proud", "optimistic", "mad", "aggressive", "bitter",
        "sarcastic", "stammers", "singing", "accent"]
    max_tags_per_sentence: int = Field(default=2, ge=1, le=4)
    # Share of sentences (per level) that may carry any tag.
    max_tagged_share: dict[str, float] = {"0": 0.2, "1": 0.4, "2": 0.55, "3": 0.85}
    # Share of all sentences allowed at level 3 (climaxes stay rare).
    max_climax_share: float = Field(default=0.12, ge=0.0, le=1.0)
    # Emphasis by capitals (one word per sentence, not at level 0) —
    # only where the script has letter case and it reads naturally.
    caps_languages: list[str] = ["en"]
    max_ellipses_per_sentence: int = Field(default=2, ge=0)
    # Breath between paragraphs grows with the level (x (1 + f*level)).
    breath_per_level: float = Field(default=0.15, ge=0.0, le=1.0)

    def allowed_tags(self, level: int) -> set[str]:
        out: set[str] = set()
        for lv in range(0, max(0, min(level, 3)) + 1):
            out |= {t.lower() for t in self.level_tags.get(str(lv), [])}
        return out

    def style_for_level(self, level: int) -> str | None:
        return self.level_styles.get(str(max(0, min(level, 3))))


class PronunciationConfig(BaseModel):
    """Listening check for words a voice can misread (pronunciation loop).

    Whisper writes Persian without short vowels (ملک is melk, molk, malek
    or malak), so it cannot hear a wrong vowel. A phoneme recognizer
    (wav2vec2, IPA) listens to every risky word at its exact place in the
    audio; the vowels heard are compared with the reading the meaning
    needs (pronunciation key). A wrong word gets harakat (then full
    harakat, then an unambiguous spelling), the block is spoken again and
    checked again — up to max_rounds."""

    enabled: bool = True
    languages: list[str] = ["fa"]
    phoneme_model: str = "facebook/wav2vec2-xlsr-53-espeak-cv-ft"
    max_rounds: int = Field(default=3, ge=0, le=6)
    # seconds of context around a word when reading its phonemes
    # (the word's own consonants then cut out the neighbours' sounds)
    pad_seconds: float = Field(default=0.12, ge=0.0, le=0.5)
    # a vowel heard as another vowel class costs 1.0 (o for a: malk/molk);
    # close pairs cost less (a/ā, e/i, o/u). A word is wrong when one hard
    # vowel error occurs or the normalized distance passes this value.
    max_vowel_distance: float = Field(default=0.6, ge=0.0, le=2.0)
    # Fixes in order: minimal harakat, full harakat, unambiguous spelling,
    # last a synonym (changes the subtitles too).
    fix_order: list[str] = ["vowelled", "full", "respell", "synonym"]
    # Pronunciation-key sentences per model call.
    sentences_per_call: int = Field(default=60, ge=5)


RESOLUTION_STATUSES = ("SOLVED", "UNSOLVED", "UNKNOWN", "STATUS_UNDER_REVIEW")


class CaseSelectionConfig(BaseModel):
    """Which cases discovery recommends: RECENT + SOLVED + NEVER USED.

    rank = recency (exponential decay on the newest known date: latest
    development, else incident date) x status weight. UNSOLVED cases are
    only suggested when a request explicitly includes them."""

    recency_half_life_days: float = Field(default=365.0, gt=0)
    # How new the case is: weight of the incident date vs. the newest
    # development (verdict, arrest) in the recency score.
    incident_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    # Cases without any known date get this recency.
    undated_recency: float = Field(default=0.15, ge=0.0, le=1.0)
    status_weights: dict[str, float] = Field(default_factory=lambda: {
        "SOLVED": 1.0, "STATUS_UNDER_REVIEW": 0.35, "UNKNOWN": 0.35, "UNSOLVED": 0.2})
    solved_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    # Best candidates checked by the status verifier (search + LLM).
    verify_top_n: int = Field(default=8, ge=0)
    # Time range of Brave discovery searches (day|week|month|year|"").
    discovery_time_range: str = "year"
    # Search queries per language; {year} / {last_year} are filled in.
    seed_queries: dict[str, list[str]] = Field(default_factory=lambda: {
        "en": ["murder trial verdict convicted {year}", "found guilty of murder sentenced {year}",
               "charged with murder after disappearance {year}",
               "cold case solved arrest DNA {year}", "killer sentenced life in prison {last_year}"],
        "de": ["Mordprozess Urteil lebenslange Haft {year}", "wegen Mordes verurteilt {year}",
               "Cold Case aufgeklärt Festnahme {year}", "Vermisste tot aufgefunden Täter verurteilt"],
        "fa": ["دادگاه قاتل محکوم شد {year}", "پرونده قتل حل شد دستگیری"],
        "ar": ["الحكم على قاتل في قضية {year}", "القبض على قاتل بعد اختفاء"],
    })
    # Duplicate checker thresholds (rapidfuzz 0..100).
    title_threshold: int = Field(default=88, ge=50, le=100)
    person_threshold: int = Field(default=90, ge=50, le=100)
    # A shared person counts with a place match or dates this close.
    date_window_years: int = Field(default=2, ge=0)
    # URLs on these hosts are not case-specific (search/aggregator pages).
    generic_url_hosts: list[str] = ["google.", "bing.", "duckduckgo.", "youtube.com/results",
                                    "facebook.com", "twitter.com", "x.com", "instagram.com"]


class CaseMonitorConfig(BaseModel):
    """Twice-weekly check of every UNSOLVED case for meaningful news.

    Stage 1 (fast, cheap): a few searches + deterministic signal words.
    No signal -> stop (no fetch, no LLM). Stage 2 (deep) only after a
    signal: fetch the pages, the case_status_verifier judges, and a case
    becomes SOLVED only with enough confidence AND independent or
    official sources."""

    enabled: bool = True
    # Start the in-app scheduler with the API server.
    autostart: bool = True
    interval_hours: float = Field(default=84.0, gt=0)   # ~ twice a week
    poll_minutes: float = Field(default=30.0, gt=0)
    statuses: list[str] = ["UNSOLVED", "STATUS_UNDER_REVIEW"]
    fast_queries_per_case: int = Field(default=2, ge=1, le=6)
    fast_results_per_query: int = Field(default=8, ge=1, le=30)
    # time range of the fast searches when a case was never checked
    first_check_time_range: str = "year"
    # signal -> words (casefolded substring match) per language
    signal_terms: dict[str, dict[str, list[str]]] = Field(default_factory=lambda: {
        "en": {
            "arrest": ["arrested", "arrest of", "taken into custody", "detained a"],
            "suspect_identified": ["suspect identified", "identified as the suspect",
                                   "named as a suspect", "suspect has been named"],
            "remains_identified": ["remains identified", "remains were identified",
                                   "body was identified", "identified the remains"],
            "charges": ["charged with", "charges filed", "indicted", "faces charges"],
            "confession": ["confessed", "confession", "pleaded guilty", "admitted killing"],
            "conviction": ["convicted", "found guilty", "guilty verdict", "sentenced to"],
            "official_update": ["police said", "police announced", "prosecutors said",
                                "press conference", "police statement"],
            "case_closed": ["case closed", "case solved", "solved the case", "cold case solved"],
            "forensic": ["dna match", "dna breakthrough", "genetic genealogy",
                         "forensic breakthrough", "new dna"],
            "disappearance_resolved": ["found alive", "found dead", "body found", "remains found"],
        },
        "de": {
            "arrest": ["festgenommen", "verhaftet", "festnahme", "untersuchungshaft"],
            "suspect_identified": ["tatverdächtig", "mutmaßliche täter", "mutmaßlicher täter"],
            "remains_identified": ["leiche identifiziert", "überreste identifiziert",
                                   "identität geklärt"],
            "charges": ["anklage erhoben", "angeklagt"],
            "confession": ["gestanden", "geständnis"],
            "conviction": ["verurteilt", "schuldig gesprochen", "urteil gefallen"],
            "official_update": ["polizei teilte mit", "staatsanwaltschaft teilte mit",
                                "pressekonferenz"],
            "case_closed": ["fall gelöst", "fall geklärt", "aufgeklärt"],
            "forensic": ["dna-treffer", "dna-spur", "dna-analyse"],
            "disappearance_resolved": ["tot aufgefunden", "lebend gefunden", "leiche gefunden"],
        },
        "fa": {
            "arrest": ["دستگیر شد", "بازداشت شد", "دستگیری"],
            "conviction": ["محکوم شد", "حکم صادر شد", "به اعدام محکوم"],
            "confession": ["اعتراف کرد", "اعتراف"],
            "charges": ["کیفرخواست", "متهم شد"],
            "case_closed": ["پرونده حل شد", "معما حل شد"],
            "disappearance_resolved": ["جسد پیدا شد", "پیدا شد"],
        },
        "ar": {
            "arrest": ["القبض على", "اعتقال", "توقيف"],
            "conviction": ["أدين", "حكم على", "الحكم بالإعدام"],
            "confession": ["اعترف", "اعتراف"],
            "charges": ["وجهت إليه تهمة", "اتهام"],
            "case_closed": ["حل لغز", "إغلاق القضية"],
            "disappearance_resolved": ["العثور على جثة", "العثور عليها"],
        },
    })
    deep_max_pages: int = Field(default=6, ge=1, le=20)
    deep_extra_queries: int = Field(default=2, ge=0, le=6)
    solved_min_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    min_independent_sources: int = Field(default=2, ge=1)
    # One source on an official host is enough (police/prosecutor/court).
    official_source_patterns: list[str] = [
        "polizei", "police", ".gov", "staatsanwaltschaft", "justiz", "gericht",
        "court", "prosecutor", "justice.", "bka.de", "fbi.gov"]


class VideoIdentityConfig(BaseModel):
    """Public identity of an episode: status words and the YouTube title
    shape. The channel names live in `channels` (one place per fact)."""

    # Public words for the only two statuses shown to the audience.
    status_labels: dict[str, dict[str, str]] = Field(default_factory=lambda: {
        "en": {"solved": "Solved", "unsolved": "Unsolved"},
        "de": {"solved": "Gelöst", "unsolved": "Ungelöst"},
        "fa": {"solved": "حل\u200cشده", "unsolved": "حل\u200cنشده"},
        "ar": {"solved": "محلولة", "unsolved": "غير محلولة"},
    })
    title_format: str = "{title} ({status}){channel_suffix}"
    channel_separator: str = " | "
    include_channel_suffix: bool = True
    include_episode_number: bool = False
    max_title_chars: int = Field(default=100, ge=30)


class CaseNamingConfig(BaseModel):
    """Title naming: counts, length and collision thresholds. Collision
    checks read stored data only — no search backend is ever called."""

    candidates_per_language: int = Field(default=7, ge=1)
    # the documentary job drafts the title candidates (an editor approves one;
    # a film cannot be marked published without an approved title)
    generate_in_pipeline: bool = True
    max_rounds: int = Field(default=4, ge=1)
    max_words: int = Field(default=6, ge=2)
    preferred_words: tuple[int, int] = (2, 5)
    # lexical (rapidfuzz 0-100) — a candidate this close to a stored title is rejected
    near_token_threshold: float = Field(default=88.0, ge=0, le=100)
    near_char_threshold: float = Field(default=90.0, ge=0, le=100)
    # embedding-model cosine against other cases' titles; calibrate on real data
    semantic_threshold: float = Field(default=0.82, ge=0, le=1)
    semantic_enabled: bool = True
    semantic_max_entries: int = Field(default=3000, ge=1)
    # corpus kinds the semantic check compares against
    semantic_kinds: list[str] = ["case_title", "episode_title", "video_title", "candidate",
                                 "discovery_title"]
    extra_per_round: int = Field(default=4, ge=0)
    max_chars: int = Field(default=60, ge=10)
    reject_questions: bool = True
    # critic gates (scores 0-1; risks: lower is better)
    min_native_quality: float = Field(default=0.7, ge=0, le=1)
    min_specificity: float = Field(default=0.4, ge=0, le=1)
    max_spoiler_risk: float = Field(default=0.5, ge=0, le=1)
    max_epistemic_risk: float = Field(default=0.4, ge=0, le=1)
    max_sensationalism_risk: float = Field(default=0.5, ge=0, le=1)
    # ranking weights
    weights: dict[str, float] = {"memorability": 0.25, "curiosity": 0.25, "specificity": 0.25,
                                 "brevity": 0.1, "documentary_tone": 0.15}
    generic_phrases: dict[str, list[str]] = {
        "en": ["the dark secret", "the final night", "hidden truth", "the hidden truth",
               "vanished without trace", "vanished without a trace", "the mystery",
               "the last day", "the last night", "the missing", "dark secrets", "cold case",
               "unsolved mystery", "the disappearance", "the murder", "a mother s fear"],
        "de": ["das dunkle geheimnis", "die letzte nacht", "die wahrheit", "die verborgene wahrheit",
               "spurlos verschwunden", "das ratsel", "der letzte tag", "dunkle geheimnisse",
               "ungeloster fall", "der fall", "das verschwinden"],
        "fa": ["راز تاریک", "شب آخر", "حقیقت پنهان", "بدون ردپا", "معما", "روز آخر",
               "ناپدید شدن", "پرونده مرموز", "راز پنهان"],
        "ar": ["السر المظلم", "الليلة الاخيرة", "الحقيقة الخفية", "اختفاء بلا اثر", "اللغز",
               "اليوم الاخير", "اختفاء غامض", "سر دفين"],
    }
    # stems (compared as token prefixes) that state guilt or killing as fact
    accusation_terms: dict[str, list[str]] = {
        "en": ["kill", "murder", "slain", "slay", "guilty", "culprit", "butcher", "strangl",
               "monster", "psychopath", "executioner"],
        "de": ["morder", "mord", "getotet", "ermordet", "tater", "schuldig", "killer",
               "monster", "henker"],
        "fa": ["قاتل", "کشت", "جنایتکار", "مقصر", "گناهکار", "مجرم", "هیولا"],
        "ar": ["قاتل", "قتل", "قتلة", "مجرم", "جاني", "مذنب", "وحش"],
    }


class ThumbnailConfig(BaseModel):
    """Channel thumbnails: one recognisable host, one real case picture, a
    small localized status badge. Composed deterministically (no image
    model), so text and badge are always exact."""

    width: int = 1280
    height: int = 720
    max_file_bytes: int = 2_000_000          # YouTube limit
    default_outfit_id: str | None = None
    # images: 1 primary, an optional secondary; more is rejected
    max_case_images: int = Field(default=2, ge=1, le=3)
    max_text_words: int = Field(default=4, ge=0)
    min_image_px: tuple[int, int] = (480, 320)
    min_verification_confidence: float = Field(default=0.6, ge=0, le=1)
    # rights: found is not usable — assets whose rights are not in the
    # publish list need `human_override` (editorial approval) when enabled
    rights_profile: str = "publish"
    allow_editorial_override: bool = True
    # layout (fractions of the canvas)
    host_height: float = 0.94
    host_height_xl: float = 1.0
    image_width: float = 0.46
    margin: float = 0.045
    badge_height: float = 0.075
    badge_max_area: float = 0.06
    badge_min_height_px: int = 40
    # per-language accent (badge + frame); the room stays dark and neutral
    accent: dict[str, str] = {"en": "#d9a441", "de": "#6fa8dc", "fa": "#d9694f", "ar": "#58a582"}
    background: str = "#101114"
    style: str = "minimal_documentary"
    forbidden: list[str] = ["red_arrows", "circles", "fake_blood", "flames", "crowded_collage",
                            "invented_crime_scene", "misleading_relationship", "spoiler_asset",
                            "extreme_glow", "multiple_fonts"]
    # image ranking by what it shows (lower = preferred)
    subject_priority: dict[str, int] = {"victim": 0, "person": 1, "suspect": 2, "object": 3,
                                        "vehicle": 3, "place": 4, "building": 4, "landscape": 4,
                                        "document": 5}
    # critic gates
    min_host_height: float = Field(default=0.8, ge=0, le=1)
    min_host_width: float = Field(default=0.15, ge=0, le=1)
    max_elements: int = Field(default=5, ge=1)
    min_contrast: float = Field(default=4.5, ge=1)
    max_automation_feel: float = Field(default=0.5, ge=0, le=1)
    max_spoiler_risk: float = Field(default=0.4, ge=0, le=1)
    max_misleading_risk: float = Field(default=0.4, ge=0, le=1)
    min_scores: dict[str, float] = {"host_visibility": 0.6, "case_visual_clarity": 0.6,
                                    "brand_consistency": 0.6, "cleanliness": 0.6,
                                    "authenticity": 0.5, "readability": 0.6}
    storage_dir: str = "data/thumbnails"
    # a picture whose vision description contains these is never used
    graphic_terms: list[str] = ["blood", "corpse", "dead body", "body bag", "crime scene tape",
                                "gore", "wound", "mutilat", "autopsy", "naked", "nude"]
    opening_beats: int = Field(default=2, ge=0)


class YouTubeMetadataConfig(BaseModel):
    """Update-video title templates (the public title shape of originals
    lives in `video_identity`)."""

    titles: dict[str, dict[str, str]] = Field(default_factory=lambda: {
        "en": {"follow_up": "The {name} Case — What Happened After Our Original Video"},
        "de": {"follow_up": "Der Fall {name} – was nach unserem ersten Video geschah"},
        "fa": {"follow_up": "پرونده‌ی {name} — بعد از ویدیوی قبلی ما چه شد"},
        "ar": {"follow_up": "قضية {name} — ماذا حدث بعد حلقتنا الأولى"},
    })
    # On-screen status card (unsolved films, follow-ups).
    status_labels: dict[str, dict[str, str]] = Field(default_factory=lambda: {
        "en": {"UNSOLVED": "UNSOLVED CASE", "follow_up": "CASE NOW SOLVED"},
        "de": {"UNSOLVED": "UNGEKLÄRTER FALL", "follow_up": "FALL INZWISCHEN GELÖST"},
        "fa": {"UNSOLVED": "پرونده‌ی حل‌نشده", "follow_up": "این پرونده حل شده است"},
        "ar": {"UNSOLVED": "قضية لم تُحل", "follow_up": "القضية حُلّت"},
    })
    status_card_seconds: list[float] = [1.5, 7.5]
    # Seconds before the end where the status card returns.
    status_card_end_seconds: float = Field(default=12.0, ge=0.0)
    # Opening of every follow-up film (master language; the spoken
    # versions carry it into every language).
    follow_up_intro: str = (
        "We first told this story{episode}, \"{original_title}\"{published}, when the "
        "investigation was still unresolved. The case has now been solved. Today we return "
        "to it to explain what happened.")


class OpeningConfig(BaseModel):
    """Openings vary with the case: the story director chooses one and
    avoids the ones used by the most recent films."""

    strategies: dict[str, str] = Field(default_factory=lambda: {
        "critical_moment": "start inside the decisive moment of the case, then step back",
        "mysterious_statement": "start with a documented statement that does not add up",
        "victim_introduction": "start with the person at the centre, their life just before",
        "evidence_discovery": "start with the moment a piece of evidence was found",
        "emergency_call": "start with the call or report that set everything in motion",
        "important_location": "start at the place that holds the story",
        "contradiction": "start with two facts that cannot both be true",
        "last_sighting": "start with the last time the person was seen",
        "courtroom_outcome": "start with the verdict, then ask how it came to this",
        "unanswered_question": "start with the question the case still leaves open",
        "timeline_anomaly": "start with a gap or impossibility in the timeline",
    })
    # Follow-up films always open with the earlier coverage.
    follow_up_strategy: str = "previous_coverage"
    avoid_recent: int = Field(default=3, ge=0)


class VisualDirectionConfig(BaseModel):
    """The Visual Director: what the viewer sees while each sentence is
    spoken — case material first, low repetition, maps by geography."""

    # Relevance tiers 1 (exact case evidence) .. 5 (generic atmosphere).
    tier_weights: dict[str, float] = Field(default_factory=lambda: {
        "1": 1.0, "2": 0.92, "3": 0.78, "4": 0.55, "5": 0.3})
    # Appearances per film: every picture is shown ONCE (holding it on
    # screen, with its camera move, is one appearance — never a return).
    max_generic_appearances: int = Field(default=2, ge=1)
    max_context_appearances: int = Field(default=2, ge=1)
    max_person_appearances: int = Field(default=2, ge=1)
    max_evidence_appearances: int = Field(default=3, ge=1)
    # Part 4 coverage metric: a beat whose share of case-specific picture
    # time is below this is listed as a gap.
    min_beat_coverage: float = Field(default=0.5, ge=0.0, le=1.0)
    min_repeat_gap_seconds: float = Field(default=45.0, ge=0.0)
    # A verified clip of the same need outranks a photo by this factor
    # (and within a tier a fill takes a clip before a photo).
    video_bonus: float = Field(default=1.15, ge=1.0, le=2.0)
    # Picture changes (seconds) — snapped to sentence starts.
    cut_pattern: list[float] = [7.0, 9.0, 6.0, 8.5, 5.5, 8.0]
    min_cut_seconds: float = Field(default=4.0, gt=0)
    # A beat gets up to beat_seconds / seconds_per_shot shots.
    seconds_per_shot: float = Field(default=7.0, gt=0)
    max_shots_per_beat: int = Field(default=14, ge=2)
    # Maps: never the very first picture (unless the opening is about the
    # place), one map per place and film.
    first_map_not_before_seconds: float = Field(default=20.0, ge=0.0)
    # Production-time search when the visuals of a sentence are weak.
    production_search: bool = True
    max_search_requests: int = Field(default=12, ge=0)
    queries_per_request: int = Field(default=3, ge=1, le=6)
    # A sentence is weak when its best candidate is above this tier.
    weak_tier: int = Field(default=3, ge=1, le=5)
    # Best-ranked candidates the director is offered per requirement of a beat.
    candidates_per_requirement: int = Field(default=12, ge=1)
    # A verified picture whose match score reaches this is offered as a fill for a beat (related
    # tier 3 and context tier 4 pictures count, before anything else is used).
    fill_min_score: float = Field(default=0.35, ge=0.0, le=1.0)


class FootageConfig(BaseModel):
    """Real moving pictures of the case/places — always muted."""

    enabled: bool = True
    providers: list[str] = ["wikimedia_video", "internet_archive"]
    internet_archive_api: str = "https://archive.org/advancedsearch.php"
    # Words in a video's title, caption or credit that mean fiction or a stage show.
    fiction_markers: list[str] = []
    # Words of a search that say what kind of footage is wanted, not what it shows.
    generic_query_words: list[str] = []
    max_queries: int = Field(default=8, ge=0)
    max_clips_per_case: int = Field(default=12, ge=0)
    max_candidates_per_query: int = Field(default=3, ge=1)
    max_download_mb: float = Field(default=250.0, gt=0)
    # Longer sources are not downloaded (feature films, full broadcasts).
    max_source_seconds: float = Field(default=1800.0, gt=0)
    # Stored window of a clip and its quality floor.
    clip_seconds: float = Field(default=24.0, gt=1)
    min_height: int = Field(default=360, ge=120)
    # An uploaded video keeps up to this many seconds (from its start or
    # the chosen start) — muted like every clip.
    upload_max_seconds: float = Field(default=600.0, gt=1)
    # Video pieces: a video is kept whole (muted, at most max_keep_seconds,
    # at most proxy_height lines) and cut at its scene changes into pieces
    # of piece_min..piece_max seconds — a long scene is split into pieces
    # that overlap by piece_overlap_seconds so each stays meaningful.
    pieces: bool = True
    max_keep_seconds: float = Field(default=600.0, gt=1)
    proxy_height: int = Field(default=720, ge=240)
    piece_min_seconds: float = Field(default=3.0, gt=0.5)
    piece_max_seconds: float = Field(default=20.0, gt=2)
    piece_overlap_seconds: float = Field(default=2.0, ge=0.0)
    max_pieces_per_source: int = Field(default=30, ge=1, le=60)
    # Footage from other people (the providers below): no clip plays longer than this, and
    # all of it together fills at most this share of the film; our own and licensed
    # pictures carry the story.
    max_third_party_clip_seconds: float = Field(default=24.0, gt=1)
    max_third_party_share: float = Field(default=0.30, ge=0.0, le=1.0)
    third_party_providers: list[str] = ["wikimedia_video", "internet_archive", "film_candidate", "youtube_clip"]
    # ffmpeg scene-change score above which a cut is detected (0..1)
    scene_threshold: float = Field(default=0.32, gt=0.0, lt=1.0)
    # The segmenter watches the video in windows of this many seconds
    # (frames at segment_fps, segment_frame_width wide) and cuts by meaning;
    # its cut points snap to a detected scene change within snap_seconds.
    segment_window_seconds: float = Field(default=90.0, gt=10)
    segment_fps: float = Field(default=0.5, gt=0.0, le=2.0)
    segment_frame_width: int = Field(default=256, ge=128, le=768)
    snap_seconds: float = Field(default=0.6, ge=0.0, le=3.0)


class ConcurrencyConfig(BaseModel):
    """How much runs at the same time (per process). Two levels:
    inside one documentary (languages, critics, image checks and voice
    blocks in parallel) and several documentaries at once (jobs)."""

    llm: int = Field(default=10, ge=1, le=64)
    vision: int = Field(default=4, ge=1, le=32)
    # ElevenLabs allows 5 concurrent requests on this plan; keep one free.
    elevenlabs_tts: int = Field(default=4, ge=1, le=32)
    elevenlabs_sound: int = Field(default=2, ge=1, le=16)
    asr: int = Field(default=1, ge=1, le=8)
    render: int = Field(default=2, ge=1, le=8)
    downloads: int = Field(default=4, ge=1, le=32)
    # Documentary jobs running at the same time, worker-wide — including the
    # autopilot's one-job-per-language films (checked live 2026-10-23: this
    # machine's 8 cores/16GB fit 4; it is the per-provider semaphores below,
    # not this count, that keep any one external service within its limit).
    # More jobs than this wait in "queued".
    jobs: int = Field(default=4, ge=1, le=16)
    # Language branches of one documentary running at the same time (only
    # matters for a job given several languages at once; the autopilot's
    # one-job-per-language films are capped by `jobs` instead).
    languages: int = Field(default=4, ge=1, le=8)


class AgentDef(BaseModel):
    """One agent: which model role it uses, which prompt file it loads and
    what it returns. No model name, no prompt text."""

    role: str
    prompt: str | None = None      # prompts/<prompt>.md; None = the stage builds the prompt
    output: Literal["json", "text"] = "json"
    description: str = ""
    used_by: str = ""


class ShortFormPlatformConfig(BaseModel):
    enabled: bool = True
    count: int = Field(ge=0, le=20)
    min_seconds: int = Field(ge=1, le=60)
    target_seconds: int = Field(ge=1, le=60)
    max_seconds: int = Field(ge=1, le=60)

    @model_validator(mode="after")
    def _validate(self):
        if not (self.min_seconds <= self.target_seconds <= self.max_seconds):
            raise ValueError(
                "short_form platform length requires "
                "min_seconds <= target_seconds <= max_seconds"
            )
        return self


class ShortFormDynamicCountConfig(BaseModel):
    weak: tuple[int, int] = (4, 5)
    normal: tuple[int, int] = (6, 8)
    strong: tuple[int, int] = (9, 12)

    @model_validator(mode="after")
    def _validate(self):
        for label, bounds in self.model_dump().items():
            lo, hi = bounds
            if lo < 0 or hi < lo:
                raise ValueError(f"short_form.dynamic_count.{label} is invalid")
        return self


class CaptionFont(BaseModel):
    file: str
    family: str


class ShortFormProductionConfig(BaseModel):
    """Per-finished-film production defaults; publishing is configured separately."""

    min_count: int = Field(default=3, ge=1, le=20)
    max_count: int = Field(default=5, ge=1, le=20)
    target_count: int = Field(default=4, ge=1, le=20)
    min_seconds: int = Field(default=20, ge=1, le=60)
    max_seconds: int = Field(default=60, ge=1, le=60)
    target_seconds: int = Field(default=35, ge=1, le=60)
    width: int = Field(default=1080, ge=1)
    height: int = Field(default=1920, ge=1)
    captions_burned_in: bool = True
    # Burned-in captions, in pixels of the 1080x1920 frame (libass is told the frame size, so
    # the numbers mean pixels): letter height, side margins and distance from the bottom edge.
    caption_font_px: int = Field(default=72, ge=20, le=200)
    caption_side_margin_px: int = Field(default=90, ge=0, le=400)
    caption_bottom_margin_px: int = Field(default=340, ge=0, le=900)
    default_visibility: Literal["private", "draft"] = "private"
    languages: list[str] = ["en", "de", "fa", "ar"]
    max_redos: int = Field(default=2, ge=0, le=2)
    max_cost_usd_per_case: float = Field(default=2.0, gt=0.0)
    # The ffmpeg/ffprobe builds to use, the first that exists (a Mac's keg-only ffmpeg-full has libass);
    # none of them there: the tool on PATH. FFMPEG_BIN / FFPROBE_BIN in the environment win over both.
    ffmpeg_candidates: list[str] = []
    ffprobe_candidates: list[str] = []
    # Font of the burned-in captions per language: the first file that exists (absolute, or relative to the
    # repository), with the family name libass asks for. The last entry of every language is a font that ships
    # with the repository, so a machine without the system fonts (CI, Linux) still has Persian/Arabic glyphs.
    caption_fonts: dict[str, list[CaptionFont]] = {}

    @model_validator(mode="after")
    def _validate(self):
        missing = [lang for lang in self.languages if not self.caption_fonts.get(lang)]
        if missing:
            raise ValueError(f"short_form.production.caption_fonts has no font for {missing}")
        if self.min_count > self.target_count or self.target_count > self.max_count:
            raise ValueError("short_form.production requires min_count <= target_count <= max_count")
        if self.min_seconds > self.target_seconds or self.target_seconds > self.max_seconds:
            raise ValueError("short_form.production requires min_seconds <= target_seconds <= max_seconds")
        if self.width != 1080 or self.height != 1920:
            raise ValueError("short_form.production is a vertical 1080x1920 profile")
        return self


class ShortFormConfig(BaseModel):
    production: ShortFormProductionConfig = Field(default_factory=ShortFormProductionConfig)
    candidate_multiplier: float = Field(default=1.75, gt=0.0, le=10.0)
    publish_target: int = Field(default=8, ge=0, le=20)
    distribution: dict[str, ShortFormPlatformConfig] = Field(default_factory=lambda: {
        "youtube_short": ShortFormPlatformConfig(
            enabled=True, count=5, min_seconds=20, target_seconds=35, max_seconds=60
        ),
        "instagram_reel": ShortFormPlatformConfig(
            enabled=True, count=6, min_seconds=20, target_seconds=30, max_seconds=45
        ),
        "facebook_reel": ShortFormPlatformConfig(
            enabled=True, count=5, min_seconds=25, target_seconds=35, max_seconds=60
        ),
        "tiktok_video": ShortFormPlatformConfig(
            enabled=True, count=8, min_seconds=15, target_seconds=28, max_seconds=45
        ),
    })
    hard_max_seconds: int = Field(default=60, ge=1, le=60)
    dynamic_count: ShortFormDynamicCountConfig = Field(
        default_factory=ShortFormDynamicCountConfig
    )
    release_window_days: tuple[int, int] = (10, 14)
    learning_min_published_shorts: int = Field(default=30, ge=1)
    learning_min_episodes: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def _validate(self):
        enabled = [p for p in self.distribution.values() if p.enabled]
        if not enabled:
            raise ValueError("short_form requires at least one enabled platform")
        for name, platform in self.distribution.items():
            if platform.max_seconds > self.hard_max_seconds:
                raise ValueError(
                    f"short_form.distribution.{name}.max_seconds exceeds hard_max_seconds"
                )
        lo, hi = self.release_window_days
        if lo <= 0 or hi < lo:
            raise ValueError("short_form.release_window_days must be positive ascending bounds")
        return self

    def candidate_count(self) -> int:
        max_count = max(
            p.count for p in self.distribution.values() if p.enabled
        )
        return round(max_count * self.candidate_multiplier)


class ArtifactsConfig(BaseModel):
    shadow_mode: bool = False


class AIConfig(BaseModel):
    production_policies: ProductionPolicies
    providers: ProviderSelection
    research_providers: dict[str, ResearchProviderSection]
    generation_providers: dict[str, GenerationProviderSection]
    generation: dict[str, GenerationSettings]
    agents: dict[str, AgentDef] = Field(default_factory=dict)
    story: StoryConfig
    story_quality: StoryQualityConfig = Field(default_factory=lambda: StoryQualityConfig(
        minimum_grounding_score=0.85,
        max_unsupported_claims=2,
        max_grounding_repair_iterations=1,
        consistency_max_high_severity=0,
        disputed_confidence_threshold=0.7,
    ))
    language_quality: dict[str, LanguageQualityConfig] = {}
    multilingual: MultilingualConfig = Field(default_factory=MultilingualConfig)
    language_pipeline: LanguagePipelineConfig
    video_rights: VideoRightsConfig
    youtube_clips: YouTubeClipsConfig = Field(default_factory=YouTubeClipsConfig)
    cost_control: CostControlConfig
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    discovery: DiscoveryPolicies = Field(default_factory=DiscoveryPolicies)
    research_depth: ResearchDepthConfig
    source_chunking: SourceChunkingConfig
    evidence_strength: EvidenceStrengthConfig
    follow_up_research: FollowUpResearchConfig
    research: ResearchConfig
    search_engine: SearchEngineConfig
    youtube_research: YouTubeResearchConfig
    youtube_data_api: YouTubeDataAPIConfig = Field(default_factory=YouTubeDataAPIConfig)
    official_sources: OfficialSourcesConfig = Field(default_factory=OfficialSourcesConfig)
    evidence_selection: EvidenceSelectionConfig
    transcript_ingestion: TranscriptIngestionConfig
    transcript_chunking: TranscriptChunkingConfig
    transcript_quality: TranscriptQualityConfig
    transcript_value: TranscriptValueConfig
    claim_clustering: ClaimClusteringConfig
    story_plan: StoryPlanConfig
    stage_direction: StageDirectionConfig
    host_looks: HostLooksConfig
    writer_metrics: WriterMetricsConfig
    embeddings: EmbeddingsConfig
    source_independence: SourceIndependenceConfig
    similarity: SimilarityConfig
    review_independence: ReviewIndependenceConfig = Field(
        default_factory=ReviewIndependenceConfig
    )
    voice_blocks: VoiceBlocksConfig = Field(default_factory=VoiceBlocksConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    asr_check: ASRCheckConfig = Field(default_factory=ASRCheckConfig)
    loudness: LoudnessConfig = Field(default_factory=LoudnessConfig)
    dynamic_eq: DynamicEQConfig = Field(default_factory=DynamicEQConfig)
    blueprint: BlueprintConfig = Field(default_factory=BlueprintConfig)
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    spoken: SpokenConfig = Field(default_factory=SpokenConfig)
    audio_direction: AudioDirectionConfig = Field(default_factory=AudioDirectionConfig)
    host: HostConfig
    channels: dict[str, ChannelConfig] = {}
    avatar: AvatarConfig = Field(default_factory=AvatarConfig)
    studio: StudioConfig = Field(default_factory=StudioConfig)
    music_library: MusicLibraryConfig = Field(default_factory=MusicLibraryConfig)
    documentary: DocumentaryConfig = Field(default_factory=DocumentaryConfig)
    visual_search: VisualSearchConfig = Field(default_factory=VisualSearchConfig)
    entity_dossier: EntityDossierConfig = Field(default_factory=EntityDossierConfig)
    coverage: CoverageConfig = Field(default_factory=CoverageConfig)
    storage_budget: StorageBudgetConfig = Field(default_factory=StorageBudgetConfig)
    limits: LimitsConfig | None = None
    zoom_map: ZoomMapConfig | None = None
    graphics: GraphicsConfig | None = None
    branding: BrandingConfig | None = None
    monitoring: MonitoringConfig | None = None
    main_visual_planner: MainVisualPlannerConfig | None = None
    fill_ways: FillWaysConfig = Field(default_factory=FillWaysConfig)
    shorts_host_led: HostLedShortsConfig = Field(default_factory=HostLedShortsConfig)
    visual_verification: VisualVerificationConfig = Field(
        default_factory=VisualVerificationConfig)
    video_audit: VideoAuditConfig = Field(default_factory=VideoAuditConfig)
    chapters: ChaptersConfig = Field(default_factory=ChaptersConfig)
    detective_board: DetectiveBoardConfig = Field(default_factory=DetectiveBoardConfig)
    autopilot: AutopilotConfig | None = None
    rights: RightsConfig = Field(default_factory=RightsConfig)
    motion: MotionConfig = Field(default_factory=MotionConfig)
    maps: MapsConfig = Field(default_factory=MapsConfig)
    attention: AttentionConfig = Field(default_factory=AttentionConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    voice_performance: VoicePerformanceConfig = Field(default_factory=VoicePerformanceConfig)
    pronunciation: PronunciationConfig = Field(default_factory=PronunciationConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)
    short_form: ShortFormConfig = Field(default_factory=ShortFormConfig)
    documentary_critics: DocumentaryCriticsConfig = Field(
        default_factory=DocumentaryCriticsConfig)
    case_selection: CaseSelectionConfig = Field(default_factory=CaseSelectionConfig)
    case_monitor: CaseMonitorConfig = Field(default_factory=CaseMonitorConfig)
    youtube_metadata: YouTubeMetadataConfig = Field(default_factory=YouTubeMetadataConfig)
    video_identity: VideoIdentityConfig = Field(default_factory=VideoIdentityConfig)
    case_naming: CaseNamingConfig = Field(default_factory=CaseNamingConfig)
    thumbnail: ThumbnailConfig = Field(default_factory=ThumbnailConfig)
    opening: OpeningConfig = Field(default_factory=OpeningConfig)
    visual_direction: VisualDirectionConfig = Field(default_factory=VisualDirectionConfig)
    footage: FootageConfig = Field(default_factory=FootageConfig)
    artifacts: ArtifactsConfig = Field(default_factory=ArtifactsConfig)

    @model_validator(mode="after")
    def _validate(self):
        if self.providers.research not in self.research_providers:
            raise ValueError(
                f"providers.research={self.providers.research!r} has no entry "
                "in research_providers"
            )
        if self.providers.generation not in self.generation_providers:
            raise ValueError(
                f"providers.generation={self.providers.generation!r} has no entry "
                "in generation_providers"
            )
        if self.research.provider != self.providers.research:
            raise ValueError(
                f"research.provider={self.research.provider!r} does not match "
                f"providers.research={self.providers.research!r}"
            )
        missing = REQUIRED_ROLES - set(self.generation)
        if missing:
            raise ValueError(f"generation settings missing roles: {sorted(missing)}")
        unknown = set(self.generation) - REQUIRED_ROLES
        if unknown:
            raise ValueError(f"generation settings define unknown roles: {sorted(unknown)}")
        rp = self.research_provider()
        if rp.type == "llm":
            # Capability-vs-routing check (Part 20): roles that must
            # search or fetch the web need the capability on the routed
            # RESEARCH model or one of its configured fallbacks.
            for role in _WEB_SEARCH_ROLES:
                self._require_capability(rp, role, "web_search")
            for role in _WEB_FETCH_ROLES:
                self._require_capability(rp, role, "web_fetch")
        self._validate_review_independence()
        self._validate_language_agents()
        self._validate_host_voice_style()
        gp = self.generation_provider()
        for role in VISION_ROLES:
            model = gp.models[gp.routing[role]]
            if not gp.capabilities.get(model, ModelCapabilities()).vision:
                raise ValueError(
                    f"role {role!r} sends images but its model {model!r} has "
                    "no vision capability"
                )
        unknown_styles = set(self.performance.style_for_intent.values()) - set(self.voice.styles)
        if unknown_styles:
            raise ValueError(
                f"performance.style_for_intent uses unknown voice styles: {sorted(unknown_styles)}"
            )
        return self

    def _validate_host_voice_style(self) -> None:
        if self.host.voice_style not in self.voice.styles:
            raise ValueError(f"host.voice_style {self.host.voice_style!r} is not in voice.styles")

    def _validate_language_agents(self) -> None:
        lp = self.language_pipeline
        named = {a for entry in lp.language_agents.values()
                 for a in (entry.rewrite, entry.inspect, entry.final, entry.length_correction, entry.fidelity)
                 if a}
        unknown = named - set(self.agents)
        if unknown:
            raise ValueError(f"language_pipeline names agents that are not defined: {sorted(unknown)}")
        languages = set(self.multilingual.production_languages)
        for table in ("language_agents", "language_stages"):
            entries = set(getattr(lp, table))
            if entries != languages:
                raise ValueError(
                    f"language_pipeline.{table} must have exactly one entry per production language "
                    f"{sorted(languages)}; missing {sorted(languages - entries)}, "
                    f"unknown {sorted(entries - languages)}")
        for language, stage in lp.language_stages.items():
            if (stage.source == "draft") != (language == lp.draft_language):
                raise ValueError(
                    f"language_pipeline.language_stages.{language}.source must be 'draft' for the draft "
                    f"language ({lp.draft_language}) and 'english_final' for every other language")
            if stage.check == "fidelity" and not lp.language_agents[language].fidelity:
                raise ValueError(f"language_pipeline.language_agents.{language} needs a fidelity agent")

    def _validate_review_independence(self) -> None:
        ri = self.review_independence
        if not ri.enabled:
            return
        gp = self.generation_provider()
        groups = ri.all_groups()
        listed = {r for g in groups for r in [*g.authors, *g.reviewers]}
        unknown = listed - set(gp.routing)
        if unknown:
            raise ValueError(
                f"review_independence lists unknown roles: {sorted(unknown)}"
            )
        all_authors = {a for g in groups for a in g.authors}
        both = all_authors & ri.all_reviewers()
        if both:
            raise ValueError(
                f"roles cannot be both author and reviewer: {sorted(both)}"
            )
        for alias, chain in ri.reviewer_fallbacks.items():
            for a in [alias, *chain]:
                if a not in gp.models:
                    raise ValueError(
                        f"review_independence.reviewer_fallbacks uses unknown "
                        f"alias {a!r}"
                    )
        for g in groups:
            if g.same_model_allowed:
                continue
            author_models = {gp.models[gp.routing[r]] for r in g.authors}
            for role in g.reviewers:
                model = gp.models[gp.routing[role]]
                if model in author_models:
                    raise ValueError(
                        f"reviewer role {role!r} routes to {model!r}, which also "
                        "writes the narration it would judge"
                    )

    @staticmethod
    def _require_capability(
        provider: "ResearchProviderSection", role: str, cap: str
    ) -> None:
        alias = provider.routing[role]
        chain = [alias] + provider.fallbacks.get(alias, [])
        if not any(
            getattr(
                provider.capabilities.get(
                    provider.models[a], ModelCapabilities()
                ),
                cap,
            )
            != "none"
            for a in chain
        ):
            raise ValueError(
                f"role {role!r} requires {cap} capability but neither "
                f"its model nor its fallbacks support it (chain: {chain})"
            )

    # ------------------------------------------------------------------
    # Provider lookups
    # ------------------------------------------------------------------

    def research_provider(self) -> ResearchProviderSection:
        return self.research_providers[self.providers.research]

    def generation_provider(self) -> GenerationProviderSection:
        return self.generation_providers[self.providers.generation]

    # ------------------------------------------------------------------
    # Role-based lookups used by agents — provider-aware (Part 3):
    # web-retrieval roles resolve through the research provider, all
    # other roles through the generation provider. Agents never choose.
    # ------------------------------------------------------------------

    @staticmethod
    def provider_for_role(role: str) -> str:
        """Which provider family owns a role: 'research' or 'generation'."""
        return "research" if role in RESEARCH_ROLES else "generation"

    def _section_for_role(self, role: str):
        """The provider section (research or generation) owning a role."""
        if self.provider_for_role(role) == "research":
            return self.research_provider()
        return self.generation_provider()

    def alias_for(self, role: str) -> str:
        section = self._section_for_role(role)
        try:
            return section.routing[role]
        except KeyError:
            raise KeyError(
                f"Unknown {self.provider_for_role(role)} role: {role!r}"
            ) from None

    def prompt_of(self, agent: str) -> str:
        """The prompt file of an agent that has one (agents that only call a model have none)."""
        name = self.agents[agent].prompt
        if not name:
            raise KeyError(f"agent {agent} has no prompt")
        return name

    def mvp(self) -> MainVisualPlannerConfig:
        """The Main Visual Planner's settings (config/parameters/main_visual_planner.json)."""
        if self.main_visual_planner is None:
            raise RuntimeError("config/parameters/main_visual_planner.json is missing")
        return self.main_visual_planner

    def branding_settings(self) -> BrandingConfig:
        """The channel logo's settings (config/parameters/branding.json)."""
        if self.branding is None:
            raise RuntimeError("config/parameters/branding.json is missing")
        return self.branding

    def monitoring_settings(self) -> MonitoringConfig:
        """What the Monitoring tab watches (config/parameters/monitoring.json)."""
        if self.monitoring is None:
            raise RuntimeError("config/parameters/monitoring.json is missing")
        return self.monitoring

    def graphics_settings(self) -> GraphicsConfig:
        """The code-made graphics' settings (config/parameters/graphics.json)."""
        if self.graphics is None:
            raise RuntimeError("config/parameters/graphics.json is missing")
        return self.graphics

    def zoom(self) -> ZoomMapConfig:
        """The zoom-in map's settings (config/parameters/zoom_map.json)."""
        if self.zoom_map is None:
            raise RuntimeError("config/parameters/zoom_map.json is missing")
        return self.zoom_map

    def limits_settings(self) -> LimitsConfig:
        """The volume and retry limits (config/parameters/limits.json)."""
        if self.limits is None:
            raise RuntimeError("config/parameters/limits.json is missing")
        return self.limits

    def autopilot_settings(self) -> AutopilotConfig:
        """The Start button's settings (config/parameters/autopilot.json)."""
        if self.autopilot is None:
            raise RuntimeError("config/parameters/autopilot.json is missing")
        return self.autopilot

    def model_for(self, role: str) -> str:
        """The concrete model ID assigned to a logical role, resolved
        on the provider that owns the role."""
        section = self._section_for_role(role)
        return section.models[self.alias_for(role)]

    def fallback_models_for(self, role: str) -> list[str]:
        """Ordered fallback model IDs within the OWNING provider only —
        there is deliberately no cross-provider fallback (Part 13/14)."""
        section = self._section_for_role(role)
        alias = self.alias_for(role)
        # Host dialogue is optional for a film, but a provider content filter or
        # transient quota error must not silently remove an entire language's
        # host layer when a safe writer model is available.  Keep this narrow:
        # adding a global sol/luna fallback would let protected review roles
        # fall back to their own authors and violate reviewer independence.
        if role in {"host_writer", "host_writer_fa_ar"}:
            writer_model = section.models.get("writer")
            primary_model = section.models.get(alias)
            if writer_model and writer_model != primary_model:
                return [writer_model]
        chain = section.fallbacks.get(alias) or []
        ri = self.review_independence
        if ri.enabled and role in ri.all_reviewers():
            # A reviewer never falls back to a model that writes the
            # narration it judges — a rate limit must not turn a critic
            # into the author grading its own text.
            chain = ri.reviewer_fallbacks.get(alias, chain)
            gp = self.generation_provider()
            authors = {gp.models[gp.routing[r]] for r in ri.authors_judged_by(role)}
            primary = section.models[alias]
            out: list[str] = []
            for a in chain:
                m = section.models[a]
                if a != alias and m != primary and m not in authors and m not in out:
                    out.append(m)
            return out
        if ri.enabled and ri.strict_reviewers_of(role):
            gp = self.generation_provider()
            critics = {gp.models[gp.routing[r]]
                       for r in ri.strict_reviewers_of(role)}
            return [section.models[a] for a in chain
                    if a != alias and section.models[a] not in critics]
        return [section.models[a] for a in chain if a != alias]

    def generation_for(self, role: str) -> GenerationSettings:
        try:
            return self.generation[role]
        except KeyError:
            raise KeyError(f"No generation settings for role: {role!r}") from None

    def capabilities_of(
        self, model: str, *, role: str | None = None
    ) -> ModelCapabilities:
        """Declared capabilities for a concrete model ID, resolved on the
        provider that owns it — the same model ID may exist on both
        providers with different declared capabilities."""
        if role is not None:
            section = self._section_for_role(role)
        else:
            section = (
                self.research_provider()
                if model in set(self.research_provider().capabilities)
                else self.generation_provider()
            )
        return section.capabilities.get(model) or ModelCapabilities()

    def language_quality_for(self, language: str) -> LanguageQualityConfig | None:
        return self.language_quality.get(language)

    def words_per_minute_for(self, language: str) -> int:
        """Per-language narration rate."""
        return self.language_pipeline.words_per_minute_for(language)


def config_files(config_dir: Path = CONFIG_DIR) -> list[Path]:
    return [config_dir / f for f in CONFIG_FILES] + sorted((config_dir / PARAMETER_DIR).glob("*.json"))


def _merge(into: dict, add: dict, where: str, source: Path) -> None:
    for key, value in add.items():
        if key not in into:
            into[key] = value
        elif isinstance(into[key], dict) and isinstance(value, dict):
            _merge(into[key], value, f"{where}{key}.", source)
        else:
            raise RuntimeError(f"config key {where}{key} is defined twice (again in {source.name})")


def load_raw_config(config_dir: Path = CONFIG_DIR) -> dict:
    """All layers merged into one dict. A key may be split over files only
    at dict level; defining the same value twice is an error."""
    raw: dict = {}
    for f in config_files(config_dir):
        try:
            part = json.loads(f.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RuntimeError(f"AI config file not found: {f}") from None
        except json.JSONDecodeError as e:
            raise RuntimeError(f"AI config file {f.name} is not valid JSON: {e}") from e
        _merge(raw, part, "", f)
    return raw


def load_ai_config(config_dir: Path = CONFIG_DIR) -> AIConfig:
    return AIConfig.model_validate(load_raw_config(config_dir))


ai_config = load_ai_config()


def _schema_node(schema: dict, root: dict) -> dict:
    ref = schema.get("$ref")
    if not ref:
        return schema
    return root.get("$defs", {}).get(ref.rsplit("/", 1)[-1], {})


def _unknown_keys(value: object, schema: dict, root: dict, path: str) -> list[str]:
    """Return JSON keys not represented by the typed configuration schema.

    Maps such as agents, channels and model aliases intentionally have dynamic
    keys; their values are checked against the declared value schema instead.
    """
    if not isinstance(value, dict):
        return []
    schema = _schema_node(schema, root)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        out: list[str] = []
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key not in properties:
                out.append(child_path)
            else:
                out.extend(_unknown_keys(child, properties[key], root, child_path))
        return out
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        out = []
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            out.extend(_unknown_keys(child, additional, root, child_path))
        return out
    return []


def unknown_config_keys(config_dir: Path = CONFIG_DIR) -> list[str]:
    """List unknown JSON keys without making startup fail."""
    schema = AIConfig.model_json_schema()
    out: list[str] = []
    for source in config_files(config_dir):
        data = json.loads(source.read_text(encoding="utf-8"))
        for key in _unknown_keys(data, schema, schema, ""):
            out.append(f"{source.name}:{key}")
    return sorted(out)


def unknown_truecrime_env_vars(raw: dict | None = None) -> list[str]:
    """List configured-prefix environment variables with no known consumer."""
    from app.core.env import known_environment_names

    raw = raw or load_raw_config()
    known = {name.upper() for name in known_environment_names()}

    def walk(value: object, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, child_key)
        elif isinstance(value, str) and key.endswith("_env"):
            known.add(value.upper())

    walk(raw)
    return sorted(
        name for name in os.environ
        if name.upper().startswith("TRUECRIME_") and name.upper() not in known
    )


def warn_unknown_configuration() -> None:
    """Log configuration drift while retaining permissive startup behavior."""
    log = logging.getLogger(__name__)
    unknown_json = unknown_config_keys()
    unknown_env = unknown_truecrime_env_vars()
    if unknown_json:
        log.warning("Unknown AI config keys (ignored): %s", ", ".join(unknown_json))
    if unknown_env:
        for name in unknown_env:
            log.warning("Environment variable %s is unused, remove it.", name)


warn_unknown_configuration()


def save_dynamic_eq(cfg: DynamicEQConfig) -> DynamicEQConfig:
    """Persist the dynamic_eq section into the layer file that holds it and
    make it effective immediately (the running process keeps its other
    sections). Atomic: a crash mid-write can never corrupt the file."""
    for f in config_files(CONFIG_DIR):
        raw = json.loads(f.read_text(encoding="utf-8"))
        if "dynamic_eq" in raw:
            raw["dynamic_eq"] = cfg.model_dump(mode="json")
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(f)
            break
    else:
        raise RuntimeError("no config file holds the dynamic_eq section")
    ai_config.dynamic_eq = cfg
    return cfg
