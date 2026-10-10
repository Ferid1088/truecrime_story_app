from datetime import datetime
from sqlalchemy import (Boolean, CheckConstraint, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint,
                        event, select)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.db.types import UTCDateTime
from app.utils import new_case_uid, utc_now


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_title: Mapped[str] = mapped_column(String(500), index=True)
    slug: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    # Stable technical identity, independent of any public title.
    case_uid: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(50), default="new")
    language: Mapped[str] = mapped_column(String(20), default="fa")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    # Whether the real-world case is solved — first-class, never only
    # text in research notes: SOLVED | UNSOLVED | UNKNOWN | STATUS_UNDER_REVIEW.
    # History of every change: CaseStatusHistory.
    resolution_status: Mapped[str] = mapped_column(String(30), default="UNKNOWN", index=True)
    resolution_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Identity of the real-world incident (duplicate detection).
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    people_json: Mapped[str] = mapped_column(Text, default="[]")
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    incident_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latest_development_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    identifiers_json: Mapped[str] = mapped_column(Text, default="[]")
    # discovery | manual | follow_up
    origin: Mapped[str | None] = mapped_column(String(30), nullable=True)

    sources = relationship("Source", back_populates="case", cascade="all, delete-orphan")
    facts = relationship("Fact", back_populates="case", cascade="all, delete-orphan")
    contradictions = relationship("Contradiction", back_populates="case", cascade="all, delete-orphan")
    stories = relationship("StoryVersion", back_populates="case", cascade="all, delete-orphan")
    original_media_segments = relationship(
        "OriginalMediaSegment", back_populates="case", cascade="all, delete-orphan"
    )
    reveal_graphs = relationship(
        "RevealGraph", back_populates="case", cascade="all, delete-orphan"
    )
    epistemic_contract_sets = relationship(
        "EpistemicContractSet", back_populates="case", cascade="all, delete-orphan"
    )


@event.listens_for(Case, "before_insert")
def _assign_case_uid(_mapper, connection, case) -> None:
    """Every new case gets a case_uid that no other case has (retry on the
    rare collision of the 6-hex id)."""
    uid = case.case_uid or new_case_uid()
    for _ in range(50):
        taken = connection.execute(select(Case.id).where(Case.case_uid == uid)).first()
        if not taken:
            break
        uid = new_case_uid()
    case.case_uid = uid


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
    # --- the suggestion record (CaseSuggestion) -----------------------
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # suggested | accepted | ignored | duplicate | filtered
    state: Mapped[str] = mapped_column(String(20), default="suggested", index=True)
    resolution_status: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    resolution_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latest_development_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    people_json: Mapped[str] = mapped_column(Text, default="[]")
    source_urls_json: Mapped[str] = mapped_column(Text, default="[]")
    recency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggestion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_of_case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duplicate_of_candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duplicate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovery_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


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


class OriginalMediaSegment(Base):
    """A selected time window from a case's original media asset.

    This maps the pre-existing table exactly. It is intentionally separate
    from VisualAsset: one asset may have multiple transcript/story windows.
    """

    __tablename__ = "original_media_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("visual_assets.id"), index=True)
    beat_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_start: Mapped[float] = mapped_column(Float)
    source_end: Mapped[float] = mapped_column(Float)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_use: Mapped[str | None] = mapped_column(Text, nullable=True)
    translation_strategy_json: Mapped[str] = mapped_column(Text)
    selected: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)

    case = relationship("Case", back_populates="original_media_segments")
    asset = relationship("VisualAsset", back_populates="original_media_segments")


class RevealGraph(Base):
    """One persisted reveal dependency graph for an EditorialBlueprint."""

    __tablename__ = "reveal_graphs"
    __table_args__ = (
        UniqueConstraint("case_id", "blueprint_id", "version", name="uq_reveal_graph_version"),
        CheckConstraint("status IN ('draft', 'validated', 'invalid')", name="ck_reveal_graph_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    blueprint_id: Mapped[int] = mapped_column(ForeignKey("editorial_blueprints.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    case = relationship("Case", back_populates="reveal_graphs")
    blueprint = relationship("EditorialBlueprint")
    nodes = relationship("RevealNode", back_populates="graph", cascade="all, delete-orphan")
    edges = relationship("RevealEdge", back_populates="graph", cascade="all, delete-orphan")
    exposures = relationship("RevealExposure", back_populates="graph", cascade="all, delete-orphan")
    asset_links = relationship("RevealAssetLink", back_populates="graph", cascade="all, delete-orphan")


class RevealNode(Base):
    """A stable, queryable reveal in a graph."""

    __tablename__ = "reveal_nodes"
    __table_args__ = (UniqueConstraint("graph_id", "node_key", name="uq_reveal_node_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_id: Mapped[int] = mapped_column(ForeignKey("reveal_graphs.id"), index=True)
    node_key: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(60), index=True)
    first_revealed_beat_id: Mapped[str] = mapped_column(String(20))
    first_revealed_order: Mapped[int] = mapped_column(Integer)
    first_revealed_at_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    graph = relationship("RevealGraph", back_populates="nodes")
    prerequisite_edges = relationship(
        "RevealEdge", foreign_keys="RevealEdge.dependent_node_id",
        back_populates="dependent_node", cascade="all, delete-orphan"
    )
    dependent_edges = relationship(
        "RevealEdge", foreign_keys="RevealEdge.prerequisite_node_id",
        back_populates="prerequisite_node", cascade="all, delete-orphan"
    )
    exposures = relationship("RevealExposure", back_populates="node", cascade="all, delete-orphan")
    asset_links = relationship("RevealAssetLink", back_populates="node", cascade="all, delete-orphan")


class RevealEdge(Base):
    """A directed prerequisite -> dependent relationship."""

    __tablename__ = "reveal_edges"
    __table_args__ = (
        UniqueConstraint("graph_id", "prerequisite_node_id", "dependent_node_id",
                         name="uq_reveal_edge"),
        CheckConstraint("prerequisite_node_id != dependent_node_id", name="ck_reveal_no_self_edge"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_id: Mapped[int] = mapped_column(ForeignKey("reveal_graphs.id"), index=True)
    prerequisite_node_id: Mapped[int] = mapped_column(ForeignKey("reveal_nodes.id"), index=True)
    dependent_node_id: Mapped[int] = mapped_column(ForeignKey("reveal_nodes.id"), index=True)

    graph = relationship("RevealGraph", back_populates="edges")
    prerequisite_node = relationship(
        "RevealNode", foreign_keys=[prerequisite_node_id], back_populates="dependent_edges"
    )
    dependent_node = relationship(
        "RevealNode", foreign_keys=[dependent_node_id], back_populates="prerequisite_edges"
    )


class RevealExposure(Base):
    """A node's exposure or deliberate withholding in one blueprint beat."""

    __tablename__ = "reveal_exposures"
    __table_args__ = (
        UniqueConstraint("graph_id", "beat_id", "node_id", name="uq_reveal_exposure"),
        CheckConstraint("exposure_type IN ('exposed', 'withheld')", name="ck_reveal_exposure_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_id: Mapped[int] = mapped_column(ForeignKey("reveal_graphs.id"), index=True)
    beat_id: Mapped[str] = mapped_column(String(20), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("reveal_nodes.id"), index=True)
    exposure_type: Mapped[str] = mapped_column(String(20))
    source_field: Mapped[str] = mapped_column(String(30), default="manual")

    graph = relationship("RevealGraph", back_populates="exposures")
    node = relationship("RevealNode", back_populates="exposures")


class RevealAssetLink(Base):
    """A visual or original-media artifact that can expose a reveal."""

    __tablename__ = "reveal_asset_links"
    __table_args__ = (
        CheckConstraint(
            "visual_asset_id IS NOT NULL OR original_media_segment_id IS NOT NULL",
            name="ck_reveal_asset_target",
        ),
        CheckConstraint("asset_kind IN ('visual', 'audio')", name="ck_reveal_asset_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_id: Mapped[int] = mapped_column(ForeignKey("reveal_graphs.id"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("reveal_nodes.id"), index=True)
    visual_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_assets.id"), nullable=True, index=True
    )
    original_media_segment_id: Mapped[int | None] = mapped_column(
        ForeignKey("original_media_segments.id"), nullable=True, index=True
    )
    asset_kind: Mapped[str] = mapped_column(String(20), default="visual")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    graph = relationship("RevealGraph", back_populates="asset_links")
    node = relationship("RevealNode", back_populates="asset_links")
    visual_asset = relationship("VisualAsset")
    original_media_segment = relationship("OriginalMediaSegment")


class EpistemicContractSet(Base):
    """A versioned, reviewable claim ledger for one long-form script."""

    __tablename__ = "epistemic_contract_sets"
    __table_args__ = (
        UniqueConstraint("case_id", "story_version_id", "version", name="uq_epistemic_contract_set"),
        CheckConstraint("status IN ('draft', 'in_review', 'approved', 'invalid')",
                        name="ck_epistemic_contract_set_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    blueprint_id: Mapped[int] = mapped_column(ForeignKey("editorial_blueprints.id"), index=True)
    story_version_id: Mapped[int] = mapped_column(ForeignKey("story_versions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    coverage_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    case = relationship("Case", back_populates="epistemic_contract_sets")
    blueprint = relationship("EditorialBlueprint")
    story_version = relationship("StoryVersion")
    claims = relationship("EpistemicClaim", back_populates="contract_set", cascade="all, delete-orphan")


class EpistemicClaim(Base):
    """One factual script assertion and its editorial modality contract."""

    __tablename__ = "epistemic_claims"
    __table_args__ = (
        UniqueConstraint("contract_set_id", "claim_key", name="uq_epistemic_claim_key"),
        CheckConstraint(
            "modality IN ('ESTABLISHED', 'BELIEVED_BY_INVESTIGATORS', 'ALLEGED', "
            "'ABSENCE_OF_EVIDENCE', 'DISPUTED')",
            name="ck_epistemic_claim_modality",
        ),
        CheckConstraint(
            "review_status IN ('proposed', 'approved', 'rejected')",
            name="ck_epistemic_claim_review_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_set_id: Mapped[int] = mapped_column(ForeignKey("epistemic_contract_sets.id"), index=True)
    claim_key: Mapped[str] = mapped_column(String(120))
    claim_text: Mapped[str] = mapped_column(Text)
    source_sentence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beat_id: Mapped[str] = mapped_column(String(20), index=True)
    modality: Mapped[str] = mapped_column(String(40), index=True)
    assertion_role: Mapped[str] = mapped_column(String(40), default="NARRATOR_ASSERTION", index=True)
    speaker: Mapped[str | None] = mapped_column(String(300), nullable=True)
    parent_claim_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    source_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    review_status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(String(20), default="extracted")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    contract_set = relationship("EpistemicContractSet", back_populates="claims")


class ChapterPlan(Base):
    """On-screen cards of one blueprint, for all its languages: the film
    title, a title per chapter (= act of the master story) and a short
    label per dated event the story tells (the running timeline). Every
    text was approved by the chapter auditor; texts it still rejected
    after the redos are left out (listed in audit_json)."""

    __tablename__ = "chapter_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    blueprint_id: Mapped[int] = mapped_column(
        ForeignKey("editorial_blueprints.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    # approved | partial (some texts left out) | no_texts (writer failed)
    status: Mapped[str] = mapped_column(String(20), default="approved", index=True)
    languages_json: Mapped[str] = mapped_column(Text, default="[]")
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    audit_json: Mapped[str] = mapped_column(Text, default="{}")
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
    # Media library: how directly the asset belongs to the case.
    # 1 exact case evidence/footage, 2 exact person/place/object,
    # 3 exact city/building/area, 4 contextual licensed, 5 generic.
    relevance_tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    case_relevance: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # research | production_search | upload | generated
    found_during: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Footage: the usable window of the (muted) clip, seconds.
    clip_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    clip_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_media_segments = relationship(
        "OriginalMediaSegment", back_populates="asset", cascade="all, delete-orphan"
    )


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
    # the visual auditor's report: every picture/clip on screen approved
    # for the words spoken over it (what was replaced or left out, and why)
    audit_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    render_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class VisualAudit(Base):
    """One verdict of the visual auditor: may this picture/clip be shown
    while these words are spoken? Keyed by the asset (code + file hash)
    and the exact English sentences, so every language reuses it and a
    changed picture or changed words are audited again."""

    __tablename__ = "visual_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer, index=True)
    asset_code: Mapped[str] = mapped_column(String(20), index=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    sentences_json: Mapped[str] = mapped_column(Text, default="[]")
    verdict: Mapped[str] = mapped_column(String(20))            # approved | rejected
    shown_as: Mapped[str | None] = mapped_column(String(20), nullable=True)  # evidence|context|symbolic
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ShortFormConcept(Base):
    """A candidate short-form concept derived from real documentary beats.

    Gate outputs are stored here as data, not as prose hidden in a prompt:
    hard gates must be deterministic and reviewable before any platform
    variant or render can move forward.
    """

    __tablename__ = "short_form_concepts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    episode_identity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    concept_type: Mapped[str] = mapped_column(String(60), index=True)
    source_beat_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    asset_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    allowed_reveals_json: Mapped[str] = mapped_column(Text, default="[]")
    forbidden_reveals_json: Mapped[str] = mapped_column(Text, default="[]")
    claims_json: Mapped[str] = mapped_column(Text, default="[]")
    gate_results_json: Mapped[str] = mapped_column(Text, default="{}")
    soft_scores_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class ShortFormScript(Base):
    __tablename__ = "short_form_scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("short_form_concepts.id"), index=True)
    language: Mapped[str] = mapped_column(String(10), index=True)
    hook: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    cta: Mapped[str] = mapped_column(Text)
    target_duration: Mapped[float] = mapped_column(Float)
    visual_requirements_json: Mapped[str] = mapped_column(Text, default="[]")
    original_audio_opportunity_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class PlatformVariant(Base):
    __tablename__ = "platform_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("short_form_concepts.id"), index=True)
    script_id: Mapped[int | None] = mapped_column(ForeignKey("short_form_scripts.id"), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(40), index=True)
    language: Mapped[str] = mapped_column(String(10), index=True)
    hook: Mapped[str] = mapped_column(Text)
    caption: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    cta: Mapped[str] = mapped_column(Text, default="")
    end_card: Mapped[str] = mapped_column(Text, default="")
    subtitle_layout_json: Mapped[str] = mapped_column(Text, default="{}")
    cover_frame_json: Mapped[str] = mapped_column(Text, default="{}")
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    destination_json: Mapped[str] = mapped_column(Text, default="{}")
    disclosure_flags_json: Mapped[str] = mapped_column(Text, default="{}")
    rights_flags_json: Mapped[str] = mapped_column(Text, default="{}")
    policy_flags_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class CrossPlatformFunnel(Base):
    __tablename__ = "cross_platform_funnels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(40), index=True)
    source_episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    destination_type: Mapped[str] = mapped_column(String(40))
    destination_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    campaign_id: Mapped[str] = mapped_column(String(120), index=True)
    utm_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attribution_status: Mapped[str] = mapped_column(String(40), default="unavailable", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class ShortFormPublishingPlan(Base):
    __tablename__ = "short_form_publishing_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    source_episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    plan_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class ShortFormMetric(Base):
    __tablename__ = "short_form_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("platform_variants.id"), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(40), index=True)
    metric_date: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    unique_viewers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    average_watch_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    rewatches: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    profile_visits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    link_clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    youtube_clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_subscribers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    destination_clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    youtube_episode_visits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_conversion_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    attribution_status: Mapped[str] = mapped_column(String(40), default="unavailable", index=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class VoicePerformance(Base):
    """How the narrator performs one spoken version: the tension arc
    (levels 0–3 per beat and sentence) and per sentence the text sent to
    the voice — with ElevenLabs v3 audio tags and timing punctuation —
    next to the plain speech and its display text (subtitles)."""

    __tablename__ = "voice_performances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    story_version_id: Mapped[int] = mapped_column(
        ForeignKey("story_versions.id"), index=True
    )
    language: Mapped[str] = mapped_column(String(20), default="en")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    performance_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    generation_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class DocumentaryJob(Base):
    """One run of the documentary pipeline for a case: blueprint, audio
    plan, spoken versions, visuals, voice, production script, critique
    and render — per language, as a pilot or the full film."""

    __tablename__ = "documentary_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    # None until a "from zero" job has researched and written its master.
    master_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("story_versions.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(10), default="pilot")
    languages_json: Mapped[str] = mapped_column(Text, default='["en"]')
    pilot_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    render_profile: Mapped[str] = mapped_column(String(20), default="preview")
    # Re-plan visuals (new research, verification and shot direction)
    # even when a visual plan already exists.
    refresh_visuals: Mapped[bool] = mapped_column(Boolean, default=False)
    # "From zero": research the case and write the master story first.
    from_zero: Mapped[bool] = mapped_column(Boolean, default=False)
    target_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Jobs started together (a batch of documentaries).
    batch_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(60), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stages_json: Mapped[str] = mapped_column(Text, default="[]")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # original | follow_up (an update video about a case covered before)
    production_type: Mapped[str] = mapped_column(String(20), default="original")
    follow_up_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# Case lifecycle: status history, monitor, videos, follow-ups
# ---------------------------------------------------------------------------


class CaseStatusHistory(Base):
    """Every change of a case's resolution status: who, why, from which
    sources (auditability)."""

    __tablename__ = "case_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    previous_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    new_status: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_json: Mapped[str] = mapped_column(Text, default="[]")
    # discovery | verifier | monitor | research | user
    changed_by: Mapped[str] = mapped_column(String(30), default="user")
    status_check_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class MonitorRun(Base):
    """One run of the unsolved-case monitor (scheduled or manual)."""

    __tablename__ = "monitor_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20), default="scheduled")
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cases_checked: Mapped[int] = mapped_column(Integer, default=0)
    deep_checks: Mapped[int] = mapped_column(Integer, default=0)
    status_changes: Mapped[int] = mapped_column(Integer, default=0)
    follow_ups_created: Mapped[int] = mapped_column(Integer, default=0)
    search_calls: Mapped[int] = mapped_column(Integer, default=0)
    fetch_calls: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CaseStatusCheck(Base):
    """One monitor check of one case: fast signal scan, and when a signal
    appears, the deep verification."""

    __tablename__ = "case_status_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    monitor_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # fast | deep
    stage: Mapped[str] = mapped_column(String(10), default="fast")
    # no_signal | signal | confirmed_change | not_confirmed | unchanged | error
    outcome: Mapped[str] = mapped_column(String(30), default="no_signal")
    previous_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    current_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    queries_json: Mapped[str] = mapped_column(Text, default="[]")
    signals_json: Mapped[str] = mapped_column(Text, default="[]")
    sources_json: Mapped[str] = mapped_column(Text, default="[]")
    new_facts_json: Mapped[str] = mapped_column(Text, default="[]")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_calls: Mapped[int] = mapped_column(Integer, default=0)
    fetch_calls: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class Video(Base):
    """A finished film for one language (rendered, later published):
    what the channel has covered, with its YouTube metadata and the case
    status at production/publication."""

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    production_script_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="en")
    mode: Mapped[str] = mapped_column(String(10), default="full")
    # preview | publish — the rights profile the film was rendered with
    render_profile: Mapped[str] = mapped_column(String(10), default="preview")
    # original | follow_up
    production_type: Mapped[str] = mapped_column(String(20), default="original")
    original_video_id: Mapped[int | None] = mapped_column(
        ForeignKey("videos.id"), nullable=True, index=True)
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    youtube_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    youtube_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    youtube_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    status_at_production: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    status_at_publication: Mapped[str | None] = mapped_column(String(30), nullable=True)
    opening_strategy: Mapped[str | None] = mapped_column(String(40), nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # rendered | published | archived
    state: Mapped[str] = mapped_column(String(20), default="rendered", index=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    youtube_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class FollowUpCandidate(Base):
    """A case covered while UNSOLVED that has since been solved: waits
    for the user's decision; nothing is produced before approval."""

    __tablename__ = "follow_up_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    original_video_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_check_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_status: Mapped[str] = mapped_column(String(30), default="UNSOLVED")
    new_status: Mapped[str] = mapped_column(String(30), default="SOLVED")
    development: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # pending | approved | dismissed | in_production | produced
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    follow_up_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    follow_up_video_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


# ---------------------------------------------------------------------------
# Production memory: which picture / which music, where and why
# ---------------------------------------------------------------------------


class MediaUsage(Base):
    """One appearance of a visual asset in a production script."""

    __tablename__ = "media_usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("visual_assets.id"), index=True)
    production_script_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    film_key: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    shot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beat_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    start: Mapped[float | None] = mapped_column(Float, nullable=True)
    end: Mapped[float | None] = mapped_column(Float, nullable=True)
    seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sentence: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    appearance: Mapped[int] = mapped_column(Integer, default=1)
    repeat_justified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    repeat_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class MusicTrack(Base):
    """One generated music cue in the shared library (several variants
    per kind and mood, so films do not share one soundtrack)."""

    __tablename__ = "music_tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)   # bridge | sting | bed | room_tone
    mood: Mapped[str] = mapped_column(String(30), index=True)
    variant: Mapped[int] = mapped_column(Integer, default=1)
    seconds: Mapped[float] = mapped_column(Float, default=20.0)
    loop: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt: Mapped[str] = mapped_column(Text, default="")
    style: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider: Mapped[str] = mapped_column(String(30), default="elevenlabs_sound")
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    characters_paid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class MusicUsage(Base):
    """One placement of a music track (or chosen silence) in a film."""

    __tablename__ = "music_usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("music_tracks.id"), nullable=True,
                                                 index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    film_key: Mapped[str] = mapped_column(String(60), index=True)
    production_script_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    beat_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(30), nullable=True)
    mood: Mapped[str | None] = mapped_column(String(30), nullable=True)
    start: Mapped[float | None] = mapped_column(Float, nullable=True)
    end: Mapped[float | None] = mapped_column(Float, nullable=True)
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class HostPlan(Base):
    """The on-screen host's plan for one blueprint — language-independent:
    which moments the host appears at (opening / after a beat / final),
    why, which personality dimension shows, the verified memory used and
    the delivery. The words themselves are written natively per language
    (HostSegments)."""

    __tablename__ = "host_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    blueprint_id: Mapped[int] = mapped_column(
        ForeignKey("editorial_blueprints.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="invalid", index=True)
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    generation_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class HostSegments(Base):
    """The host's on-camera dialogue for one spoken version (one
    language), written natively from the HostPlan, checked for facts,
    memory, repetition and length."""

    __tablename__ = "host_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    story_version_id: Mapped[int] = mapped_column(
        ForeignKey("story_versions.id"), index=True
    )
    host_plan_id: Mapped[int] = mapped_column(ForeignKey("host_plans.id"), index=True)
    language: Mapped[str] = mapped_column(String(20), default="en", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    segments_json: Mapped[str] = mapped_column(Text, default="[]")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    generation_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class HostMemory(Base):
    """What the host remembers across episodes: opinions, reactions,
    corrections, unresolved questions and recurring themes — each tied to
    the case it came from. Only these rows (and the archive of covered
    cases) may be referred to as memories. kind: opinion | reaction |
    correction | open_question | theme. origin: host_plan (written by the
    pipeline) | editor (added or confirmed by a person)."""

    __tablename__ = "host_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="opinion", index=True)
    text: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(20), default="host_plan")
    host_plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class HostScene(Base):
    """One host segment on its way to the screen, step by step. Each step's
    result is saved before the next step starts, so a failure (ElevenLabs,
    HeyGen) is retried from where it stopped — the text, the voice and an
    accepted provider job are never produced twice.

    status: planned → voice_ready → avatar_uploaded → avatar_requested →
    avatar_ready (→ composited, timeline insertion). failed_step/last_error
    describe the last failure; the status stays at the last good step.
    history_json: every attempt of every step ({at, step, outcome, detail})."""

    __tablename__ = "host_scenes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    story_version_id: Mapped[int] = mapped_column(Integer, index=True)
    host_segments_id: Mapped[int] = mapped_column(Integer, index=True)
    host_segment_id: Mapped[str] = mapped_column(String(20))      # S1, S2 …
    language: Mapped[str] = mapped_column(String(10), index=True)
    channel: Mapped[str] = mapped_column(String(100))
    position: Mapped[str | None] = mapped_column(String(20), nullable=True)
    beat_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # the words — frozen when the scene is planned (sha256 ties every
    # later artifact to exactly this text)
    text: Mapped[str] = mapped_column(Text, default="")
    text_sha256: Mapped[str] = mapped_column(String(64), default="")
    # studio and framing
    studio_profile_id: Mapped[str] = mapped_column(String(50))
    studio_asset_id: Mapped[str] = mapped_column(String(80))
    framing_preset: Mapped[str] = mapped_column(String(20))
    host_position_json: Mapped[str] = mapped_column(Text, default="{}")
    host_scale: Mapped[float | None] = mapped_column(Float, nullable=True)
    background_mode: Mapped[str] = mapped_column(String(40),
                                                 default="transparent_avatar_over_studio")
    planned_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    planned_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    # voice step
    voice_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    voice_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    voice_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    voice_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    voice_meta_json: Mapped[str] = mapped_column(Text, default="{}")
    # avatar steps
    avatar_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    avatar_id: Mapped[str | None] = mapped_column(String(100), nullable=True)   # look used
    provider_asset_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_asset_voice_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_job_voice_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_generation: Mapped[int] = mapped_column(Integer, default=0)
    avatar_video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    avatar_video_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_meta_json: Mapped[str] = mapped_column(Text, default="{}")
    # bookkeeping
    status: Mapped[str] = mapped_column(String(30), default="planned", index=True)
    failed_step: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts_json: Mapped[str] = mapped_column(Text, default="{}")
    history_json: Mapped[str] = mapped_column(Text, default="[]")
    running_since: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class EpisodeIdentity(Base):
    """The public identity of one case in one channel language: editorial
    title, localized status and the YouTube title built from them. The
    episode number is internal (`episode_sequence`) and never part of the
    default public title. Once published, the title only changes through
    an explicit revision (`title_version`)."""

    __tablename__ = "episode_identities"
    __table_args__ = (UniqueConstraint("case_id", "language", name="uq_episode_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    case_uid: Mapped[str | None] = mapped_column(String(20), index=True)
    episode_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel_id: Mapped[str] = mapped_column(String(40))
    language: Mapped[str] = mapped_column(String(10))
    editorial_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    resolution_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    resolution_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    youtube_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title_family_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    thumbnail_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    thumbnail_status_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    host_outfit_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    host_reference_asset: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # an editor approved this title (naming flow); a title copied from the
    # story's own draft is not approved
    title_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    published_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    title_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class CaseTitleCandidate(Base):
    """One proposed editorial title for a case in one language. Rejected
    candidates are kept so the same title is never proposed again."""

    __tablename__ = "case_title_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    language: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(String(300))
    title_norm: Mapped[str] = mapped_column(String(300), index=True)
    title_family_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    editorial_concept: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_round: Mapped[int] = mapped_column(Integer, default=1)
    # candidate | selected | rejected
    status: Mapped[str] = mapped_column(String(20), default="candidate", index=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    exact_collision: Mapped[bool] = mapped_column(Boolean, default=False)
    near_collision_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    semantic_collision_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    collision_with: Mapped[str | None] = mapped_column(Text, nullable=True)
    memorability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    curiosity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    specificity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    brevity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sensationalism_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    spoiler_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    epistemic_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    critic_json: Mapped[str] = mapped_column(Text, default="{}")
    # generated | manual
    origin: Mapped[str] = mapped_column(String(20), default="generated")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class Thumbnail(Base):
    """One composed thumbnail of an episode identity (case + language).
    The brief, the critic report and the human decision are kept with it."""

    __tablename__ = "thumbnails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    language: Mapped[str] = mapped_column(String(10), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # draft | approved | rejected
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    brief_json: Mapped[str] = mapped_column(Text, default="{}")
    critic_json: Mapped[str] = mapped_column(Text, default="{}")
    layout_json: Mapped[str] = mapped_column(Text, default="{}")
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outfit_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    host_asset_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    primary_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    secondary_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
