from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TopicDiscoveryRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=10)
    languages: list[str] = ["en", "de", "ar", "fa"]
    theme: str = "true crime"
    prefer_undercovered: bool = True
    require_multiple_sources: bool = False
    search_web: bool = True
    search_youtube: bool = True
    avoid_existing: bool = True
    # "solved_only" (default): the standard pipeline, UNSOLVED filtered out.
    # "unsolved_only": only UNSOLVED is suggested, SOLVED filtered out.
    # "both": no filtering by resolution status.
    resolution_filter: Literal["solved_only", "unsolved_only", "both"] = "solved_only"
    # None = any country. A config/parameters/countries.json code (D1).
    country_code: str | None = None
    # None = any year, for either bound (D1). The year of the incident,
    # never the latest development.
    incident_year_from: int | None = Field(default=None, ge=1700)
    incident_year_to: int | None = Field(default=None, ge=1700)

    @field_validator("country_code")
    @classmethod
    def _known_country(cls, v: str | None) -> str | None:
        if v is None:
            return None
        from app.lifecycle.geography import country_name

        code = v.upper()
        if country_name(code) is None:
            raise ValueError(f"unknown country_code {v!r}")
        return code

    @model_validator(mode="after")
    def _year_range_ordered(self) -> "TopicDiscoveryRequest":
        if (self.incident_year_from is not None and self.incident_year_to is not None
                and self.incident_year_from > self.incident_year_to):
            raise ValueError("incident_year_from must not be after incident_year_to")
        return self


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


class RenameSearchRequest(BaseModel):
    # None (or "") clears the override, reverting to the generated name.
    display_name: str | None = None


class BulkCandidateActionRequest(BaseModel):
    candidate_ids: list[int]


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
