"""Central, typed loader for config/ai_config.json.

Every non-secret AI parameter lives in that one file — agents request
models and generation settings by logical role and never hardcode
model IDs, temperatures, token limits or thresholds. The config is
validated at import time so a malformed file fails fast on startup
instead of degrading silently mid-pipeline.
"""

import json
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

# Provider secrets are referenced by env-var NAME in the config below
# (secret_env). Loading .env here — once, alongside the module that
# defines those names — guarantees os.environ lookups resolve in any
# process that imports this module, whichever import order runs first.
load_dotenv()

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "ai_config.json"

# Every role the pipeline uses must have a routed model alias AND
# per-role generation settings in the config file. Research-side roles
# (search/planning/verification/normalization) route through the same
# table — agents never name a model ID.
REQUIRED_ROLES = {
    # Research intelligence roles — the ENGINE searches/fetches; these
    # LLM roles only plan and analyze retrieved content (search-engine
    # architecture: search is not an LLM task).
    "case_discovery_agent",
    "youtube_discovery_agent",
    "research_verifier",
    "research_query_planner",
    "result_reranker",
    "research_normalizer",
    "fact_extractor",
    "timeline_builder",
    "contradiction_analyzer",
    "discovery",
    "story_director",
    "writer",
    "rewriter",
    "engagement_critic",
    "final_editor",
    "section_critic",
    "grounding_validator",
    "consistency_checker",
    "evidence_normalizer",
    "semantic_consistency_checker",
    "localized_grounding_validator",
    "master_story_director",
    "master_writer",
    "master_rewriter",
    "localization_writer",
    "master_engagement_critic",
    "master_final_editor",
    "native_language_critic",
    "localized_engagement_critic",
    "localized_final_editor",
    "transcript_normalizer",
    "transcript_intelligence_extractor",
    "claim_clusterer",
    "source_dependence_analyzer",
    "evidence_verifier",
    "narrative_research_synthesizer",
    "source_similarity_critic",
    "structure_originality_critic",
    # Research-engine internals: result scoring + final assembly use
    # role-routed generation too — no model IDs hardcoded anywhere.
    "research_evaluator",
    "research_assembler",
    # Documentary engine: editorial blueprint (beats, reveals, intents).
    "narrative_director",
    # Spoken storytelling adaptation (author) + its independent checks.
    "spoken_writer",
    "spoken_meaning_checker",
    "spoken_style_critic",
    # Pronunciation key: for words a voice can misread (Persian homographs
    # like ملک = melk / molk / malek / malak) the reading the MEANING needs
    # and the harakat that force it — used by the listening check.
    "pronunciation_editor",
    # Voice performance: ElevenLabs v3 audio tags, emphasis and the
    # narrator's tension arc (neutral -> dark -> whispered climaxes).
    "voice_performance_director",
    # Professional audio direction: breaths, music moments, silences.
    "audio_director",
    # Solved / unsolved: judges search evidence for discovery suggestions
    # and for the unsolved-case monitor's deep verification.
    "case_status_verifier",
    # Visual intelligence: needs per beat, image verification (vision),
    # shot direction, on-screen text per language.
    "visual_planner",
    "visual_verifier",
    "visual_director",
    "overlay_localizer",
    # Documentary critics (independent of the visual director).
    "automation_feel_critic",
    "attention_critic",
    "visual_accuracy_critic",
    "production_critic",
}

# Roles that send images and need a vision-capable model.
VISION_ROLES = {"visual_verifier"}

# Strict provider split: the research PROVIDER is now infrastructure
# (the TrueCrime Search Engine — SearXNG + fetcher + index), not an LLM
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


class ModelCapabilities(BaseModel):
    """What a model can actually do — never send unsupported parameters.

    web_search / web_fetch: "native" (model retrieves on its own, e.g.
    Sonar), "plugin" (needs the provider's web plugin), or "none"."""

    web_search: str = "none"  # native | plugin | none
    web_fetch: str = "none"  # native | plugin | none
    structured_output: bool = True
    tool_calling: bool = False
    vision: bool = False

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
        Search Engine: SearXNG + fetcher + index). No LLM credentials,
        no model routing; intelligence roles live in the generation
        provider instead.
    type="llm"            — an LLM-vendor research provider; requires
        model aliases, routing and declared capabilities (kept for
        backward compatibility with the removed OpenRouter section).
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


class GenerationSettings(BaseModel):
    temperature: float = Field(ge=0.0, le=2.0)
    max_tokens: int = Field(ge=1, le=262144)


class StoryConfig(BaseModel):
    default_language: str
    default_target_minutes: int = Field(ge=1, le=120)
    # Allowed request range for story length. A low minimum enables short
    # pilot segments (e.g. a 3–5 minute documentary test) without code
    # changes; defaults keep the historical 10–90 minute range.
    min_target_minutes: int = Field(default=10, ge=1, le=120)
    max_target_minutes: int = Field(default=90, ge=1, le=240)
    words_per_minute: int = Field(ge=50, le=300)
    minimum_length_ratio: float = Field(gt=0.0, le=1.0)
    maximum_length_ratio: float = Field(ge=1.0, le=3.0)
    max_rewrite_iterations: int = Field(ge=1, le=10)
    max_length_repair_iterations: int = Field(ge=0, le=5)
    engagement_threshold: float = Field(ge=0, le=100)
    similarity_threshold: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate(self):
        if self.minimum_length_ratio > self.maximum_length_ratio:
            raise ValueError(
                "story.minimum_length_ratio must not exceed maximum_length_ratio"
            )
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
    section_engagement_threshold: float = Field(ge=0, le=100)
    max_section_rewrites: int = Field(ge=0, le=10)
    consistency_max_high_severity: int = Field(ge=0)
    disputed_confidence_threshold: float = Field(ge=0.0, le=1.0)
    strip_markdown_artifacts: bool = True
    section_marker_prefix: str = "[[ACT:"


class MultilingualConfig(BaseModel):
    research_languages: list[str] = ["en", "de", "fa", "ar"]
    canonical_language: str = "en"
    localization_languages: list[str] = ["de", "fa", "ar"]

    @model_validator(mode="after")
    def _validate(self):
        if self.canonical_language in self.localization_languages:
            raise ValueError(
                "canonical_language must not also be a localization target"
            )
        return self


class LocalizationConfig(BaseModel):
    require_master_ready: bool = True
    # Refuse to localize a master whose evidence fingerprint no longer
    # matches the case's current facts (research re-ran since).
    require_current_evidence: bool = True
    words_per_minute: dict[str, int] = {}
    minimum_native_quality_score: float = Field(ge=0, le=100)
    minimum_semantic_consistency_score: float = Field(ge=0, le=100)
    minimum_factual_consistency_score: float = Field(ge=0.0, le=1.0)
    minimum_engagement_score: float = Field(ge=0, le=100)
    max_localization_repair_iterations: int = Field(ge=0, le=5)
    minimum_duration_ratio: float = Field(gt=0.0, le=1.0)
    maximum_duration_ratio: float = Field(ge=1.0, le=3.0)

    @model_validator(mode="after")
    def _validate(self):
        if self.minimum_duration_ratio > self.maximum_duration_ratio:
            raise ValueError(
                "localization.minimum_duration_ratio must not exceed "
                "maximum_duration_ratio"
            )
        return self


class MasterGenerationConfig(BaseModel):
    section_based_generation: bool = True
    section_word_tolerance: float = Field(gt=0.0, le=1.0)
    rewrite_length_tolerance: float = Field(gt=0.0, le=1.0)
    max_sections_per_revision_cycle: int = Field(ge=1, le=10)
    max_revision_cycles: int = Field(ge=0, le=6)
    minimum_engagement_improvement: float = Field(ge=0.0, le=50.0)
    min_act_budget_share: float = Field(gt=0.0, le=0.5)
    max_span_repairs_per_pass: int = Field(ge=1, le=50)
    narrative_value_hints: dict[str, float] = {}


class CostControlConfig(BaseModel):
    max_master_tokens_per_case: int = Field(gt=0)
    max_master_cost_usd: float = Field(gt=0.0)


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
    overlap_sentences: int = Field(ge=0, le=5)
    max_chunks_per_source_for_extraction: int = Field(ge=1, le=20)


class EvidenceStrengthConfig(BaseModel):
    weights: dict[str, float] = {}
    preferred_for_scene: list[str] = []


class FollowUpResearchConfig(BaseModel):
    max_rounds: int = Field(ge=0, le=5)
    queries_per_gap: int = Field(ge=1, le=6)


class LanguageQualityConfig(BaseModel):
    minimum_target_script_ratio: float = Field(gt=0.0, le=1.0)
    max_foreign_token_ratio: float = Field(ge=0.0, lt=1.0)


class ResearchConfig(BaseModel):
    """Provider-neutral research controls (Part 21/23).

    Hard call-count caps always apply; the cost cap is enforced on
    provider-reported spend only — never estimated."""
    provider: str
    default_candidate_count: int = Field(ge=1, le=20)
    max_sources_per_case: int = Field(ge=1)
    minimum_independent_sources: int = Field(ge=1)
    dedupe_similarity_threshold: int = Field(ge=50, le=100)
    job_timeout_minutes: int = Field(ge=1)
    max_search_calls_per_language: int = Field(ge=1, default=6)
    max_fetch_calls_per_language: int = Field(ge=0, default=5)
    max_research_cost_per_case: float = Field(gt=0.0, default=1.5)
    max_followup_rounds: int = Field(ge=0, default=2)
    minimum_result_novelty: float = Field(ge=0.0, le=1.0, default=0.15)
    min_fetch_score: float = Field(ge=0.0, le=1.0, default=0.45)
    # Fairness: each language gets a reserved minimum share of the case
    # budget; whatever it doesn't use is redistributed adaptively
    # (Part 2 — no hardcoded equal cost).
    minimum_language_budget_share: float = Field(
        ge=0.0, le=0.5, default=0.10
    )
    adaptive_redistribution: bool = True
    # Below this evaluator relevance a result is rejected outright —
    # a search result is not automatically a Source (Part 14).
    min_accept_score: float = Field(ge=0.0, le=1.0, default=0.2)
    default_profile: str = "DEEP_CASE_RESEARCH"


class ResearchProfileConfig(BaseModel):
    """A named research strategy — depth, spend and breadth knobs that
    research jobs select by name (never hardcoded in code)."""

    model_alias: str
    search_context_size: str = "medium"
    max_search_calls_per_language: int = Field(ge=1)
    max_results_per_search: int = Field(ge=1, le=25)
    max_fetch_calls_per_language: int = Field(ge=0)
    max_followup_rounds: int = Field(ge=0)
    max_cost_usd: float = Field(gt=0.0)
    minimum_result_novelty: float = Field(ge=0.0, le=1.0, default=0.15)


class YouTubeResearchConfig(BaseModel):
    languages: list[str] = ["en", "de", "fa", "ar"]
    max_results_per_language: int = Field(ge=1, le=50)
    max_videos_per_language: int = Field(ge=1, le=50)
    min_duration_seconds: int = Field(ge=0)
    discovery_provider: str = "auto"  # auto | youtube_api | engine


class SearchEngineConfig(BaseModel):
    """TrueCrime Search Engine — the infrastructure research provider.

    All bounds are config, never magic numbers in code: SearXNG
    endpoint, iterative-loop budgets, fetcher limits, cache ages and
    ranking thresholds."""
    # SearXNG backend
    searxng_url_env: str = "TRUECRIME_SEARXNG_URL"
    searxng_url: str | None = None
    request_timeout_s: int = Field(ge=5, le=120, default=25)
    # Iterative loop (Part 6/38)
    max_results_per_query: int = Field(ge=1, le=25)
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


class TranscriptChunkingConfig(BaseModel):
    target_tokens: int = Field(gt=0)
    min_tokens: int = Field(gt=0)
    max_tokens: int = Field(gt=0)


class TranscriptValueConfig(BaseModel):
    weights: dict[str, float] = {}
    high_value_threshold: float = Field(ge=0.0, le=1.0)
    skip_process_threshold: float = Field(ge=0.0, le=1.0)


class ClaimClusteringConfig(BaseModel):
    near_duplicate_threshold: int = Field(ge=50, le=100)
    batch_size: int = Field(ge=1, le=500)
    min_independent_families_for_verified: int = Field(ge=1)


class SourceIndependenceConfig(BaseModel):
    shared_unusual_claim_ratio: float = Field(ge=0.0, le=1.0)
    min_shared_unusual_claims: int = Field(ge=1)


class SimilarityConfig(BaseModel):
    ngram_size: int = Field(ge=3, le=20)
    max_ngram_overlap_ratio: float = Field(ge=0.0, le=1.0)
    max_longest_sequence_ratio: float = Field(ge=0.0, le=1.0)
    structure_overlap_threshold: float = Field(ge=0.0, le=1.0)


class ResearchStopConditionsConfig(BaseModel):
    max_transcripts_per_case: int = Field(ge=1)
    minimum_novel_information_ratio: float = Field(ge=0.0, le=1.0)
    consecutive_low_value_stop: int = Field(ge=1)


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


ReviewIndependenceConfig.model_rebuild()


class VoiceBlocksConfig(BaseModel):
    """How narration is cut into text-to-speech blocks.

    Blocks never split a sentence, never cross an act boundary and prefer
    paragraph ends. Durations are estimated from the per-language
    localization.words_per_minute until real audio exists."""

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
    secret_env: str = "TrueCrime_ELEVENLABS_API_KEY"
    base_url: str = "https://api.elevenlabs.io"
    output_format: str = "mp3_44100_128"
    request_timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    # Models that reject previous_text / next_text (request stitching).
    no_context_models: list[str] = ["eleven_v3"]
    # Models that accept language_code (language enforcement).
    language_code_models: list[str] = ["eleven_v3", "eleven_turbo_v2_5",
                                       "eleven_flash_v2_5"]
    # Models that understand inline audio tags ([whispers], [pause] ...).
    audio_tag_models: list[str] = ["eleven_v3"]


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


ASRCheckConfig.model_rebuild()


class LoudnessConfig(BaseModel):
    narration_target_lufs: float = Field(default=-16.0, ge=-40.0, le=-5.0)
    true_peak_db: float = Field(default=-1.5, ge=-9.0, le=0.0)
    lra: float = Field(default=11.0, gt=0.0, le=50.0)
    # Flag a block whose loudness differs this much from the median.
    max_block_deviation_lu: float = Field(default=2.0, gt=0.0)
    sample_rate: int = Field(default=44100, ge=8000)


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


class AudioDirectionConfig(BaseModel):
    """Guard-rails for the audio director's plan (deterministic)."""

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
    # Every finished documentary runs 45–120 minutes. Pilots are short
    # renders (pilot_seconds) of a full-length story, never short stories.
    min_film_minutes: float = Field(default=45.0, gt=0)
    max_film_minutes: float = Field(default=120.0, gt=0)
    pilot_seconds: float = Field(default=180.0, gt=0)
    # Measured speed of the rendered narration per language (words per
    # minute), used to estimate film length before anything is rendered.
    speech_wpm: dict[str, int] = {"en": 150, "de": 128, "fa": 116, "ar": 104}
    storage_dir: str = "data/cases"

    def wpm(self, language: str) -> int:
        return self.speech_wpm.get(language) or 140


class VisualSearchConfig(BaseModel):
    providers: list[str] = ["source_pages", "wikimedia", "searxng_images"]
    user_agent: str = ("TrueCrimeStudio/1.0 (local documentary research tool; "
                       "https://github.com/Ferid1088/truecrime_story_app)")
    # Seconds between requests to public APIs (Wikimedia robot policy).
    min_request_interval_s: float = Field(default=1.0, ge=0.0)
    wikimedia_api: str = "https://commons.wikimedia.org/w/api.php"
    max_queries: int = Field(default=30, ge=1)
    max_candidates_per_query: int = Field(default=5, ge=1)
    max_source_pages: int = Field(default=25, ge=0)
    max_assets: int = Field(default=120, ge=1)
    min_width: int = Field(default=600, ge=64)
    max_download_mb: float = Field(default=15.0, gt=0)
    request_timeout_s: float = Field(default=25.0, gt=0)
    # Stock libraries: found there means watermarked/licensed — never used.
    blocked_domains: list[str] = [
        "gettyimages.", "shutterstock.", "alamy.", "istockphoto.",
        "dreamstime.", "depositphotos.", "123rf.", "stock.adobe.",
    ]


class VisualVerificationConfig(BaseModel):
    verified_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    reject_below_confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    thumbnail_px: int = Field(default=768, ge=128)


class RightsConfig(BaseModel):
    """FOUND does not mean USABLE. Which rights statuses each render
    profile may show; 'preview' renders are internal review copies."""

    allowed_for_render: dict[str, list[str]] = Field(default_factory=lambda: {
        "preview": ["owned", "licensed", "public_domain", "creative_commons",
                    "editorial_review_required"],
        "publish": ["owned", "licensed", "public_domain", "creative_commons"],
    })
    attribution_required: list[str] = ["creative_commons"]


class MotionConfig(BaseModel):
    """Subtle camera moves on stills — never a visible slideshow effect."""

    push_scale: float = Field(default=1.07, ge=1.0, le=1.3)
    pan_fraction: float = Field(default=0.05, ge=0.0, le=0.3)
    emotional_slowdown: float = Field(default=0.6, gt=0.0, le=1.0)
    parallax_enabled: bool = True
    parallax_shift_fraction: float = Field(default=0.012, ge=0.0, le=0.05)
    min_hold_seconds: float = Field(default=5.0, gt=0.0)
    max_still_seconds: float = Field(default=16.0, gt=0.0)
    crossfade_seconds: list[float] = [0.7, 1.4]
    # The same motion is not used more than this many shots in a row.
    max_same_motion_run: int = Field(default=2, ge=1)


class MapsConfig(BaseModel):
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    attribution: str = "© OpenStreetMap contributors"
    geocoder_url: str = "https://nominatim.openstreetmap.org/search"
    tile_size: int = 256
    # Zoom steps of an orientation sequence (wide -> close).
    zoom_levels: list[int] = [4, 7, 11]
    darken: float = Field(default=0.55, ge=0.0, le=1.0)


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
    show_credits: bool = True


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
    """Voice performance director (ElevenLabs v3 audio tags).

    Levels form the narrator's arc: 0 neutral (nothing has happened
    yet), 1 first unease, 2 dark and crime-specific, 3 breath-taking
    moments (slow, measured, close to a whisper). Every tag must be on
    the palette of its level (levels are cumulative); everything else is
    removed by the validator."""

    enabled: bool = True
    sentences_per_call: int = Field(default=70, ge=10)
    # Voice style per level (names in voice.styles).
    level_styles: dict[str, str] = {
        "0": "v3_neutral", "1": "v3_unease", "2": "v3_tension", "3": "v3_climax"}
    level_tags: dict[str, list[str]] = Field(default_factory=lambda: {
        "0": ["pause", "thoughtful", "calm"],
        "1": ["softly", "quietly", "slowly", "slow", "sighs", "drawn out",
              "hesitant", "serious", "concerned", "distant"],
        "2": ["low", "tense", "uneasy", "anxious", "somber", "ominous",
              "measured", "deliberate"],
        "3": ["whispers", "whispering", "hushed", "barely audible",
              "breathless", "gasps", "fearful"],
    })
    # Never used by a documentary narrator (taste, credibility).
    forbidden_tags: list[str] = [
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
    include_unsolved_default: bool = False
    # The status verifier must reach this confidence before a suggestion
    # is labelled SOLVED (otherwise STATUS_UNDER_REVIEW / UNKNOWN).
    solved_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    # Best candidates checked by the status verifier (search + LLM).
    verify_top_n: int = Field(default=8, ge=0)
    # SearXNG time range of discovery searches (day|week|month|year|"").
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


class YouTubeMetadataConfig(BaseModel):
    """Deterministic title rules: an unsolved case and a follow-up are
    recognisable from the title alone, in every language."""

    max_title_chars: int = Field(default=100, ge=20)
    titles: dict[str, dict[str, str]] = Field(default_factory=lambda: {
        "en": {"original": "{title}", "unsolved": "UNSOLVED: {title}",
               "follow_up": "SOLVED: The {name} Case — What Happened After Our Original Video"},
        "de": {"original": "{title}", "unsolved": "UNGEKLÄRT: {title}",
               "follow_up": "GELÖST: Der Fall {name} – was nach unserem ersten Video geschah"},
        "fa": {"original": "{title}", "unsolved": "حل‌نشده: {title}",
               "follow_up": "حل شد: پرونده‌ی {name} — بعد از ویدیوی قبلی ما چه شد"},
        "ar": {"original": "{title}", "unsolved": "لم تُحل: {title}",
               "follow_up": "حُلّت: قضية {name} — ماذا حدث بعد حلقتنا الأولى"},
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
    # Appearances per film: generic/contextual pictures and maps rarely
    # repeat; central people may recur, with room in between.
    max_generic_appearances: int = Field(default=1, ge=1)
    max_context_appearances: int = Field(default=2, ge=1)
    max_person_appearances: int = Field(default=5, ge=1)
    max_evidence_appearances: int = Field(default=3, ge=1)
    min_repeat_gap_seconds: float = Field(default=45.0, ge=0.0)
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


class FootageConfig(BaseModel):
    """Real moving pictures of the case/places — always muted."""

    enabled: bool = True
    providers: list[str] = ["wikimedia_video", "internet_archive"]
    internet_archive_api: str = "https://archive.org/advancedsearch.php"
    max_queries: int = Field(default=8, ge=0)
    max_clips_per_case: int = Field(default=12, ge=0)
    max_candidates_per_query: int = Field(default=3, ge=1)
    max_download_mb: float = Field(default=250.0, gt=0)
    # Longer sources are not downloaded (feature films, full broadcasts).
    max_source_seconds: float = Field(default=1800.0, gt=0)
    # Stored window of a clip and its quality floor.
    clip_seconds: float = Field(default=24.0, gt=1)
    min_height: int = Field(default=360, ge=120)


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
    # Documentaries running at the same time; more wait in "queued".
    jobs: int = Field(default=2, ge=1, le=16)
    # Language branches of one documentary running at the same time.
    languages: int = Field(default=4, ge=1, le=8)


class AIConfig(BaseModel):
    providers: ProviderSelection
    research_providers: dict[str, ResearchProviderSection]
    generation_providers: dict[str, GenerationProviderSection]
    generation: dict[str, GenerationSettings]
    story: StoryConfig
    story_quality: StoryQualityConfig = Field(default_factory=lambda: StoryQualityConfig(
        minimum_grounding_score=0.85,
        max_unsupported_claims=2,
        max_grounding_repair_iterations=1,
        section_engagement_threshold=55,
        max_section_rewrites=2,
        consistency_max_high_severity=0,
        disputed_confidence_threshold=0.7,
    ))
    language_quality: dict[str, LanguageQualityConfig] = {}
    multilingual: MultilingualConfig = Field(default_factory=MultilingualConfig)
    localization: LocalizationConfig
    master_generation: MasterGenerationConfig
    cost_control: CostControlConfig
    research_depth: ResearchDepthConfig
    source_chunking: SourceChunkingConfig
    evidence_strength: EvidenceStrengthConfig
    follow_up_research: FollowUpResearchConfig
    research: ResearchConfig
    research_profiles: dict[str, ResearchProfileConfig] = {}
    search_engine: SearchEngineConfig
    youtube_research: YouTubeResearchConfig
    transcript_ingestion: TranscriptIngestionConfig
    transcript_chunking: TranscriptChunkingConfig
    transcript_value: TranscriptValueConfig
    claim_clustering: ClaimClusteringConfig
    source_independence: SourceIndependenceConfig
    similarity: SimilarityConfig
    research_stop_conditions: ResearchStopConditionsConfig
    review_independence: ReviewIndependenceConfig = Field(
        default_factory=ReviewIndependenceConfig
    )
    voice_blocks: VoiceBlocksConfig = Field(default_factory=VoiceBlocksConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    asr_check: ASRCheckConfig = Field(default_factory=ASRCheckConfig)
    loudness: LoudnessConfig = Field(default_factory=LoudnessConfig)
    blueprint: BlueprintConfig = Field(default_factory=BlueprintConfig)
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    spoken: SpokenConfig = Field(default_factory=SpokenConfig)
    audio_direction: AudioDirectionConfig = Field(default_factory=AudioDirectionConfig)
    music_library: MusicLibraryConfig = Field(default_factory=MusicLibraryConfig)
    documentary: DocumentaryConfig = Field(default_factory=DocumentaryConfig)
    visual_search: VisualSearchConfig = Field(default_factory=VisualSearchConfig)
    visual_verification: VisualVerificationConfig = Field(
        default_factory=VisualVerificationConfig)
    rights: RightsConfig = Field(default_factory=RightsConfig)
    motion: MotionConfig = Field(default_factory=MotionConfig)
    maps: MapsConfig = Field(default_factory=MapsConfig)
    attention: AttentionConfig = Field(default_factory=AttentionConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    voice_performance: VoicePerformanceConfig = Field(default_factory=VoicePerformanceConfig)
    pronunciation: PronunciationConfig = Field(default_factory=PronunciationConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)
    documentary_critics: DocumentaryCriticsConfig = Field(
        default_factory=DocumentaryCriticsConfig)
    case_selection: CaseSelectionConfig = Field(default_factory=CaseSelectionConfig)
    case_monitor: CaseMonitorConfig = Field(default_factory=CaseMonitorConfig)
    youtube_metadata: YouTubeMetadataConfig = Field(default_factory=YouTubeMetadataConfig)
    opening: OpeningConfig = Field(default_factory=OpeningConfig)
    visual_direction: VisualDirectionConfig = Field(default_factory=VisualDirectionConfig)
    footage: FootageConfig = Field(default_factory=FootageConfig)

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
            aliases = set(rp.models)
            bad_profiles = {
                name: p.model_alias
                for name, p in self.research_profiles.items()
                if p.model_alias not in aliases
            }
            if bad_profiles:
                raise ValueError(
                    f"research_profiles route to unknown model aliases: "
                    f"{bad_profiles}. Known aliases: {sorted(aliases)}"
                )
            # Capability-vs-routing check (Part 20): roles that must
            # search or fetch the web need the capability on the routed
            # RESEARCH model or one of its configured fallbacks.
            for role in _WEB_SEARCH_ROLES:
                self._require_capability(rp, role, "web_search")
            for role in _WEB_FETCH_ROLES:
                self._require_capability(rp, role, "web_fetch")
        self._validate_review_independence()
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

    def capabilities_for(self, role: str) -> ModelCapabilities:
        """Declared capabilities for the model routed to a role —
        the research layer consults this before attaching server tools.
        Undeclared models default to all-none (safe: no tools sent)."""
        return self.capabilities_of(
            self.model_for(role), role=role
        )

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

    def research_profile(
        self, name: str | None = None
    ) -> ResearchProfileConfig | None:
        """Named research strategy (Part 28); None when not configured."""
        return self.research_profiles.get(name or self.research.default_profile)

    def language_quality_for(self, language: str) -> LanguageQualityConfig | None:
        return self.language_quality.get(language)

    def words_per_minute_for(self, language: str) -> int:
        """Per-language narration rate; falls back to the story default."""
        return self.localization.words_per_minute.get(
            language, self.story.words_per_minute
        )


def load_ai_config(path: Path = CONFIG_PATH) -> AIConfig:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise RuntimeError(f"AI config file not found: {path}") from None
    except json.JSONDecodeError as e:
        raise RuntimeError(f"AI config file is not valid JSON: {e}") from e
    return AIConfig.model_validate(raw)


ai_config = load_ai_config()
