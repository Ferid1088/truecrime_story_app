"""The film inspector's answer, validated. An answer that does not fit is an error and fails the
stage; nothing is repaired and nothing is asked again."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

FILM_SCORES = ("native_naturalness", "spoken_quality", "narrative_flow", "engagement")


class FilmItem(BaseModel):
    """One listening problem the film inspector found."""

    id: int
    severity: Literal["high", "medium", "low"]
    kind: Literal["native", "spoken", "flow", "engagement", "repetition", "selection", "ending",
                  "production"]
    act: str = ""
    quote: str = ""
    problem: str
    instruction: str

    @field_validator("act", "quote", mode="before")
    @classmethod
    def _blank(cls, value):
        return "" if value is None else str(value)


class FilmInspection(BaseModel):
    verdict: Literal["pass", "revise"]
    scores: dict[str, int]
    items: list[FilmItem]
    summary: str

    @field_validator("scores")
    @classmethod
    def _scores(cls, scores: dict[str, int]) -> dict[str, int]:
        missing = set(FILM_SCORES) - set(scores)
        if missing:
            raise ValueError(f"scores missing: {sorted(missing)}")
        if not all(0 <= v <= 100 for v in scores.values()):
            raise ValueError("scores must be 0-100")
        return scores


class FidelityItem(BaseModel):
    """One difference in meaning between a film text and the approved English text."""

    id: int
    severity: Literal["high", "medium", "low"]
    kind: Literal["changed_fact", "missing_fact", "added_claim", "attribution", "certainty",
                  "relationship", "value", "name"]
    act: str = ""
    quote: str = ""
    english: str = ""
    problem: str
    correction: str

    @field_validator("act", "quote", "english", mode="before")
    @classmethod
    def _blank(cls, value):
        return "" if value is None else str(value)


class FidelityReport(BaseModel):
    verdict: Literal["pass", "issues"]
    items: list[FidelityItem]
    summary: str

    @model_validator(mode="after")
    def _verdict_follows_items(self):
        if (self.verdict == "issues") != bool(self.items):
            raise ValueError('verdict is "issues" exactly when there are items')
        return self
