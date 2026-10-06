from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.db.types import UTCDateTime
from app.utils import utc_now


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_title: Mapped[str] = mapped_column(String(500), index=True)
    slug: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(50), default="new")
    language: Mapped[str] = mapped_column(String(20), default="fa")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    sources = relationship("Source", back_populates="case", cascade="all, delete-orphan")
    facts = relationship("Fact", back_populates="case", cascade="all, delete-orphan")
    contradictions = relationship("Contradiction", back_populates="case", cascade="all, delete-orphan")
    stories = relationship("StoryVersion", back_populates="case", cascade="all, delete-orphan")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)

    title: Mapped[str] = mapped_column(String(1000))
    url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(20), default="unknown")
    publisher: Mapped[str | None] = mapped_column(String(500), nullable=True)

    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reliability_score: Mapped[float] = mapped_column(Float, default=0.5)
    is_authorized_text: Mapped[bool] = mapped_column(Boolean, default=False)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_status: Mapped[str] = mapped_column(String(30), default="summary_only")
    value_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_family: Mapped[str | None] = mapped_column(String(300), nullable=True)
    retrieval_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    retrieval_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    research_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Language is measured, not trusted: declared = model-reported,
    # detected = deterministic detector output; `language` stays the
    # effective value used for normalization routing.
    declared_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    language_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    language_detection_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="active")

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    case = relationship("Case", back_populates="sources")
    chunks = relationship(
        "SourceChunk", back_populates="source", cascade="all, delete-orphan"
    )


class SourceChunk(Base):
    __tablename__ = "source_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    section_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_or_location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(20), default="unknown")

    source = relationship("Source", back_populates="chunks")


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)

    claim: Mapped[str] = mapped_column(Text)  # canonical English representation
    original_claim: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_language: Mapped[str] = mapped_column(String(20), default="en")
    category: Mapped[str] = mapped_column(String(100), default="general")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    source_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    disputed: Mapped[bool] = mapped_column(Boolean, default=False)
    event_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    narrative_value: Mapped[str | None] = mapped_column(String(40), nullable=True)
    evidence_strength: Mapped[str | None] = mapped_column(String(40), nullable=True)
    supporting_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    transcript_claim_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    quote_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    speaker: Mapped[str | None] = mapped_column(String(300), nullable=True)
    people_json: Mapped[str] = mapped_column(Text, default="[]")
    locations_json: Mapped[str] = mapped_column(Text, default="[]")

    case = relationship("Case", back_populates="facts")


class Contradiction(Base):
    __tablename__ = "contradictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)

    topic: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    source_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    severity: Mapped[str] = mapped_column(String(50), default="medium")

    case = relationship("Case", back_populates="contradictions")


class StoryVersion(Base):
    __tablename__ = "story_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)

    version: Mapped[int] = mapped_column(Integer)
    # "master" = canonical English story; "localized" = derived narration;
    # "direct" = legacy single-language generation.
    kind: Mapped[str] = mapped_column(String(20), default="direct")
    master_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("story_versions.id"), nullable=True
    )
    derived_from_master_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    narrative_angle: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(20), default="fa")
    native_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    semantic_consistency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    factual_consistency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    story_text: Mapped[str] = mapped_column(Text)
    engagement_score: Mapped[float] = mapped_column(Float, default=0.0)
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    similarity_status: Mapped[str] = mapped_column(String(30), default="not_evaluated")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    is_best: Mapped[bool] = mapped_column(Boolean, default=False)
    text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    narrative_structure: Mapped[str | None] = mapped_column(Text, nullable=True)
    critic_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    generation_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    case = relationship("Case", back_populates="stories")


class DiscoveryCandidate(Base):
    __tablename__ = "discovery_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    query: Mapped[str] = mapped_column(String(500))
    rationale: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(200), index=True)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)

    agent_name: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(50), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    generation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    case = relationship("Case")


class VideoSource(Base):
    """A YouTube (or other platform) video treated as a research source.

    Linked 1:1 to a Source row (source_type="youtube_video") so videos join
    the canonical source/evidence layer; video-specific metadata lives here.
    Transcript text itself is NOT stored here — it lives in
    TranscriptSegment rows with timestamps preserved.
    """
    __tablename__ = "video_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id"), nullable=True, index=True
    )

    platform: Mapped[str] = mapped_column(String(50), default="youtube")
    video_id: Mapped[str] = mapped_column(String(100), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(1000))
    channel_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    channel_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(20), default="unknown")
    published_at: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovery_query: Mapped[str | None] = mapped_column(String(500), nullable=True)
    research_language: Mapped[str | None] = mapped_column(String(20), nullable=True)

    transcript_status: Mapped[str] = mapped_column(
        String(40), default="discovered", index=True
    )  # discovered|pending|available|unavailable|processed|failed
    transcript_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # manual|auto_caption|creator_supplied|external_authorized|user_supplied

    classification: Mapped[str] = mapped_column(String(60), default="unknown")
    # documentary|news|creator_narration|interview|official|archive|analysis
    source_independence: Mapped[str] = mapped_column(String(40), default="unknown")
    # primary|independent_secondary|likely_derivative|unknown
    likely_derivative_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
    creator_source_family: Mapped[str | None] = mapped_column(String(300), nullable=True)
    duplicate_group: Mapped[str | None] = mapped_column(String(200), nullable=True)

    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    novel_information_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_cache_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)

    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    case = relationship("Case")
    source = relationship("Source")
    segments = relationship(
        "TranscriptSegment", back_populates="video_source",
        cascade="all, delete-orphan",
    )
    claims = relationship(
        "TranscriptClaim", back_populates="video_source",
        cascade="all, delete-orphan",
    )


class TranscriptSegment(Base):
    """One timed caption segment. Timestamps and original language text are
    never discarded — canonical English is added alongside, not instead."""
    __tablename__ = "transcript_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_source_id: Mapped[int] = mapped_column(
        ForeignKey("video_sources.id"), index=True
    )

    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    segment_index: Mapped[int] = mapped_column(Integer)

    text_original: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(20), default="unknown")
    canonical_text_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    video_source = relationship("VideoSource", back_populates="segments")


class TranscriptClaim(Base):
    """A factual claim extracted from a transcript, with full provenance:
    which video, which segments, which timestamps, which language."""
    __tablename__ = "transcript_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    video_source_id: Mapped[int] = mapped_column(
        ForeignKey("video_sources.id"), index=True
    )
    segment_ids_json: Mapped[str] = mapped_column(Text, default="[]")

    original_language: Mapped[str] = mapped_column(String(20), default="en")
    original_claim: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_claim_en: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(60), default="fact")
    # fact|timeline_event|human_detail|scene_detail|investigation_detail|
    # physical_evidence|legal|location|person|relationship|quote|
    # reported_statement|theory|disputed|unverified|question|contradiction|
    # historical_context
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    certainty: Mapped[str] = mapped_column(String(40), default="unverified")
    speaker: Mapped[str | None] = mapped_column(String(300), nullable=True)
    quote_classification: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # source_document_quote|interview_quote|reported_quote|creator_narration|uncertain

    timestamp_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp_end: Mapped[float | None] = mapped_column(Float, nullable=True)

    verification_status: Mapped[str] = mapped_column(
        String(40), default="unverified", index=True
    )
    # unverified|supported|strongly_supported|contradicted|disputed|
    # false_or_unreliable|unknown
    supporting_source_ids_json: Mapped[str] = mapped_column(Text, default="[]")

    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("claim_clusters.id"), nullable=True, index=True
    )
    promoted_fact_id: Mapped[int | None] = mapped_column(
        ForeignKey("facts.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    video_source = relationship("VideoSource", back_populates="claims")


class ClaimCluster(Base):
    """Semantic deduplication: many transcript claims (across videos and
    languages) that assert the same underlying fact collapse into one
    cluster. Independent-family count — not member count — drives trust."""
    __tablename__ = "claim_clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)

    canonical_claim_en: Mapped[str] = mapped_column(Text)
    member_claim_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    independent_source_family_count: Mapped[int] = mapped_column(Integer, default=1)
    languages_json: Mapped[str] = mapped_column(Text, default="[]")

    claim_type: Mapped[str] = mapped_column(String(60), default="fact")
    narrative_value: Mapped[str | None] = mapped_column(String(40), nullable=True)
    support_status: Mapped[str] = mapped_column(String(40), default="unverified")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)

    promoted_fact_id: Mapped[int | None] = mapped_column(
        ForeignKey("facts.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    case = relationship("Case")


class NarrativeInsight(Base):
    """Narrative intelligence mined from transcripts — questions, framing,
    emphasized details. NOT factual evidence; never promoted into the Fact
    layer. Stored separately so writers can see *what creators found
    interesting* without inheriting their structure or wording."""
    __tablename__ = "narrative_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    video_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("video_sources.id"), nullable=True, index=True
    )

    insight_type: Mapped[str] = mapped_column(String(60), default="important_question")
    # important_question|mystery_element|emotional_context|turning_point|
    # reveal_candidate|frequently_emphasized_detail|commonly_omitted_detail|
    # viewer_context|narrative_transition_topic
    text_original: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_text_en: Mapped[str] = mapped_column(Text)
    segment_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    language: Mapped[str] = mapped_column(String(20), default="unknown")

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    case = relationship("Case")


class ResearchJob(Base):
    __tablename__ = "research_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)

    provider: Mapped[str] = mapped_column(String(50), default="truecrime")
    external_job_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    job_type: Mapped[str] = mapped_column(String(50), default="research", index=True)
    status: Mapped[str] = mapped_column(String(50), default="queued", index=True)
    # Stage is live-updated from provider progress — planning /
    # searching:en / fetching:de / normalizing / extracting / completed …
    current_stage: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    profile: Mapped[str | None] = mapped_column(String(60), nullable=True)
    input_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # First-class telemetry (promoted from result_json._telemetry).
    search_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetch_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    search_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetch_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    sources_discovered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources_accepted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources_rejected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    languages_completed: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    case = relationship("Case")
    queries = relationship(
        "ResearchQuery", back_populates="job", cascade="all, delete-orphan"
    )


class ResearchQuery(Base):
    """One executed search query — relational research provenance (Part 5)."""

    __tablename__ = "research_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_job_id: Mapped[int] = mapped_column(
        ForeignKey("research_jobs.id"), index=True
    )
    language: Mapped[str] = mapped_column(String(20))
    query_text: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str | None] = mapped_column(String(60), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(20), nullable=True)
    round: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    job = relationship("ResearchJob", back_populates="queries")
    results = relationship(
        "ResearchResult", back_populates="query", cascade="all, delete-orphan"
    )


class ResearchResult(Base):
    """One raw search result — discovery metadata only, never a copy of
    Source content (Part 6). Acceptance/rejection is recorded here."""

    __tablename__ = "research_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_query_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_queries.id"), index=True, nullable=True
    )
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(50), default="truecrime")
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    result_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    credibility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    novelty_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetch_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    query = relationship("ResearchQuery", back_populates="results")
    source_links = relationship(
        "ResearchSourceLink", back_populates="result",
        cascade="all, delete-orphan",
    )


class ResearchSourceLink(Base):
    """Many-to-one: every discovery path to a canonical Source is kept —
    a Source may be found by several queries/languages (Part 7)."""

    __tablename__ = "research_source_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_result_id: Mapped[int] = mapped_column(
        ForeignKey("research_results.id"), index=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    # Column keeps the name "relationship"; the attribute is link_type so
    # it doesn't shadow sqlalchemy.relationship in class scope.
    link_type: Mapped[str] = mapped_column("relationship", String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    result = relationship("ResearchResult", back_populates="source_links")

class EditorialBlueprint(Base):
    """Language-independent documentary strategy for one story version:
    beats (which paragraphs, what they reveal, which questions they open
    or answer, how they should feel, sound and look). Every later stage —
    voice performance, visuals, music, the reveal firewall — reads this.
    The narration text itself stays in StoryVersion."""

    __tablename__ = "editorial_blueprints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    story_version_id: Mapped[int] = mapped_column(
        ForeignKey("story_versions.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    # valid | needs_review (warnings) | invalid (structural errors)
    status: Mapped[str] = mapped_column(String(20), default="invalid", index=True)
    evidence_fingerprint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    story_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    central_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    editorial_thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_thread: Mapped[str | None] = mapped_column(Text, nullable=True)
    blueprint_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    generation_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    generation_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class AudioPlan(Base):
    """The audio director's plan for one blueprint — language-independent:
    per beat the breath between paragraphs, a music bed (or none) and the
    transition after it (breath, music bridge, emotional moment, sting,
    silence, chapter break). Exact times are computed per language from
    the real narration audio."""

    __tablename__ = "audio_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    blueprint_id: Mapped[int] = mapped_column(
        ForeignKey("editorial_blueprints.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="invalid", index=True)
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    generation_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    generation_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class VisualAsset(Base):
    """One visual in the case library: a real photo/document/footage
    found by visual research, a user upload, or a generated card/map.

    Semantic meaning lives here, never in the filename (Part 19).
    asset_role: evidence (real case material) | context (real place or
    period, not evidence) | illustration (atmospheric, generic).
    rights_status: owned | licensed | public_domain | creative_commons |
    editorial_review_required | permission_required | unknown | do_not_use.
    verification_status: unverified | verified | needs_review | rejected."""

    __tablename__ = "visual_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    asset_code: Mapped[str] = mapped_column(String(20), index=True)
    asset_type: Mapped[str] = mapped_column(String(20), default="photo", index=True)
    subject_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    entities_json: Mapped[str] = mapped_column(Text, default="[]")
    date_start: Mapped[str | None] = mapped_column(String(20), nullable=True)
    date_end: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    asset_role: Mapped[str] = mapped_column(String(20), default="illustration", index=True)
    story_functions_json: Mapped[str] = mapped_column(Text, default="[]")
    provider: Mapped[str] = mapped_column(String(30), default="upload")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    found_for: Mapped[str | None] = mapped_column(String(300), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    license: Mapped[str | None] = mapped_column(String(200), nullable=True)
    credit: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rights_status: Mapped[str] = mapped_column(String(40), default="unknown", index=True)
    rights_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(20), default="unverified", index=True)
    verification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verification_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    historical_accuracy_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Evidence ids this visual would reveal: never shown before the beat
    # that reveals them (reveal firewall).
    reveals_json: Mapped[str] = mapped_column(Text, default="[]")
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phash: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_original_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    original_audio_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Generated assets (maps, document/date/quote cards) keep their spec.
    spec_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_override: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class VisualPlan(Base):
    """Language-independent visual direction for one blueprint: per beat
    the visual requirements and the shots (command, asset, share of the
    beat, motion, overlay). Exact times are computed per language."""

    __tablename__ = "visual_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    blueprint_id: Mapped[int] = mapped_column(
        ForeignKey("editorial_blueprints.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    requirements_json: Mapped[str] = mapped_column(Text, default="{}")
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    generation_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class ProductionScript(Base):
    """Render-ready timeline for ONE language: voice, silences, visuals,
    overlays, music, subtitles — from the real narration audio."""

    __tablename__ = "production_scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    story_version_id: Mapped[int] = mapped_column(
        ForeignKey("story_versions.id"), index=True
    )
    language: Mapped[str] = mapped_column(String(10), index=True)
    blueprint_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visual_plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String(10), default="pilot")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    script_json: Mapped[str] = mapped_column(Text, default="{}")
    critique_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    render_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class OriginalMediaSegment(Base):
    """A selected piece of real footage/audio (interview, news clip) that
    may play with its own sound, with its language handling."""

    __tablename__ = "original_media_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("visual_assets.id"), index=True)
    beat_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_start: Mapped[float] = mapped_column(Float, default=0.0)
    source_end: Mapped[float] = mapped_column(Float, default=0.0)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_use: Mapped[str | None] = mapped_column(Text, nullable=True)
    # per target language: original_with_subtitles | voiceover | skip
    translation_strategy_json: Mapped[str] = mapped_column(Text, default="{}")
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class DocumentaryJob(Base):
    """One run of the documentary pipeline for a case: blueprint, audio
    plan, spoken versions, visuals, voice, production script, critique
    and render — per language, as a pilot or the full film."""

    __tablename__ = "documentary_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    master_version_id: Mapped[int] = mapped_column(ForeignKey("story_versions.id"))
    mode: Mapped[str] = mapped_column(String(10), default="pilot")
    languages_json: Mapped[str] = mapped_column(Text, default='["en"]')
    pilot_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    render_profile: Mapped[str] = mapped_column(String(20), default="preview")
    # Re-plan visuals (new research, verification and shot direction)
    # even when a visual plan already exists.
    refresh_visuals: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(60), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stages_json: Mapped[str] = mapped_column(Text, default="[]")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
