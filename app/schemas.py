from pydantic import BaseModel, Field, field_validator
from typing import Literal


class TopicDiscoveryRequest(BaseModel):
    count: int = Field(default=5, ge=1, le=20)
    languages: list[str] = ["en", "de", "ar", "fa"]
    theme: str = "true crime"
    prefer_undercovered: bool = True
    require_multiple_sources: bool = False
    search_web: bool = True
    search_youtube: bool = True
    avoid_existing: bool = True
    # The standard pipeline suggests SOLVED cases; set to also get UNSOLVED.
    include_unsolved: bool = False


ResolutionStatus = Literal["SOLVED", "UNSOLVED", "UNKNOWN", "STATUS_UNDER_REVIEW"]


class CreateCaseRequest(BaseModel):
    canonical_title: str
    language: str = "fa"
    summary: str | None = None
    # Solved/unsolved is known from the start (UNKNOWN until verified).
    resolution_status: ResolutionStatus = "UNKNOWN"
    # Identity (duplicate detection): victims/suspects, place, dates.
    aliases: list[str] = []
    people: list[str] = []
    location: str | None = None
    incident_date: str | None = None
    # Create even though the duplicate checker found the same case.
    force: bool = False


class UpdateCaseRequest(BaseModel):
    status: Literal[
        "new", "researching", "researched", "writing", "story_ready",
        "producing", "rendered", "published", "completed", "rejected", "archived",
    ] | None = None
    canonical_title: str | None = None
    summary: str | None = None
    aliases: list[str] | None = None
    people: list[str] | None = None
    location: str | None = None
    incident_date: str | None = None


class InvestigateCandidateRequest(BaseModel):
    language: str = "fa"
    # Investigate even though the duplicate checker matched an existing case.
    force: bool = False


class AddSourceRequest(BaseModel):
    title: str
    url: str
    source_type: Literal["news", "court", "police", "youtube", "book", "documentary", "article", "other"]
    language: str = "unknown"
    publisher: str | None = None
    raw_text: str | None = None
    notes: str | None = None
    reliability_score: float = Field(default=0.5, ge=0, le=1)
    is_authorized_text: bool = False


class VoiceRenderRequest(BaseModel):
    # Render only the opening (whole blocks) — e.g. 180 for a 3-minute
    # pilot. None renders the full story.
    max_seconds: float | None = Field(default=None, gt=0)
    # Voice style from config voice.styles (default: voice.default_style).
    style: str | None = None
    # Blocks to re-record as a new take (different seed), ignoring cache.
    force_block_ids: list[str] = []
    # Mix music beds, bridges, stings and room tone when the story has an
    # audio plan (directed performance).
    with_music: bool = True


class DynamicEQPreviewRequest(BaseModel):
    """Overrides for a pre-rendered dynamic-EQ preview; any field left
    out keeps the configured value. Nothing is persisted."""
    enabled: bool | None = None
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    max_atten_db: float | None = Field(default=None, ge=0.0, le=24.0)
    deesser_enabled: bool | None = None
    deesser_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    # Seconds of narration to process (from the top).
    seconds: float = Field(default=60.0, gt=1.0, le=600.0)
