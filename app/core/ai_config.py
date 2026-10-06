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
}

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
    reviewer_fallbacks: dict[str, list[str]] = {}


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


class ElevenLabsConfig(BaseModel):
    secret_env: str = "TrueCrime_ELEVENLABS_API_KEY"
    base_url: str = "https://api.elevenlabs.io"
    output_format: str = "mp3_44100_128"
    request_timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)


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


class LoudnessConfig(BaseModel):
    narration_target_lufs: float = Field(default=-16.0, ge=-40.0, le=-5.0)
    true_peak_db: float = Field(default=-1.5, ge=-9.0, le=0.0)
    lra: float = Field(default=11.0, gt=0.0, le=50.0)
    # Flag a block whose loudness differs this much from the median.
    max_block_deviation_lu: float = Field(default=2.0, gt=0.0)
    sample_rate: int = Field(default=44100, ge=8000)


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
        return self

    def _validate_review_independence(self) -> None:
        ri = self.review_independence
        if not ri.enabled:
            return
        gp = self.generation_provider()
        listed = set(ri.author_roles) | set(ri.reviewer_roles)
        unknown = listed - set(gp.routing)
        if unknown:
            raise ValueError(
                f"review_independence lists unknown roles: {sorted(unknown)}"
            )
        both = set(ri.author_roles) & set(ri.reviewer_roles)
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
        author_models = {gp.models[gp.routing[r]] for r in ri.author_roles}
        for role in ri.reviewer_roles:
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
        if ri.enabled and role in ri.reviewer_roles:
            # A reviewer never falls back to a model that writes the
            # narration — a rate limit must not turn a critic into the
            # author grading its own text.
            chain = ri.reviewer_fallbacks.get(alias, chain)
            gp = self.generation_provider()
            authors = {gp.models[gp.routing[r]] for r in ri.author_roles}
            primary = section.models[alias]
            out: list[str] = []
            for a in chain:
                m = section.models[a]
                if a != alias and m != primary and m not in authors and m not in out:
                    out.append(m)
            return out
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
