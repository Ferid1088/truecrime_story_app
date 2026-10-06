from pydantic import BaseModel, Field, HttpUrl, field_validator
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


class TopicCandidate(BaseModel):
    title: str
    rationale: str
    suggested_queries: list[str]


class CreateCaseRequest(BaseModel):
    canonical_title: str
    language: str = "fa"
    summary: str | None = None


class UpdateCaseRequest(BaseModel):
    status: Literal[
        "new", "researching", "researched", "writing", "story_ready",
        "completed", "rejected", "archived",
    ] | None = None
    canonical_title: str | None = None
    summary: str | None = None


class InvestigateCandidateRequest(BaseModel):
    language: str = "fa"


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


def check_target_minutes(value: int) -> int:
    """Story length must lie in the configured range (story.min/max_
    target_minutes) — a low minimum enables short pilot segments."""
    from app.core.ai_config import ai_config

    lo = ai_config.story.min_target_minutes
    hi = ai_config.story.max_target_minutes
    if not lo <= value <= hi:
        raise ValueError(f"target_minutes must be between {lo} and {hi}")
    return value


class GenerateStoryRequest(BaseModel):
    target_minutes: int = Field(default=45, ge=1)
    language: str = "fa"
    tone: str = "cinematic, suspenseful, investigative, respectful"
    iterations: int = Field(default=2, ge=1, le=5)

    @field_validator("target_minutes")
    @classmethod
    def _target_in_configured_range(cls, v: int) -> int:
        return check_target_minutes(v)


class ImproveStoryRequest(BaseModel):
    story_version_id: int
    instruction: str = Field(min_length=3, max_length=2000)
