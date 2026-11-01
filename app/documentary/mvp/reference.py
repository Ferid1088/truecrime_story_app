"""The English film is the reference for every other language. A language other than English has no pictures or videos of its own: its planner
uses only what was approved in the English film's plan, and nothing is checked, searched or reviewed again for it. The English pictures are
given to the other film's beats by their place in the story (each film's beats cover the same story, cut differently): a beat of the other
film gets the pictures of the English beats that cover the same stretch of the story."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import EditorialBlueprint, StoryVersion, VisualPlan


def draft_language() -> str:
    return ai_config.language_pipeline.draft_language


def language_of_plan(db: Session, plan: VisualPlan) -> str | None:
    """The language of the story the plan's blueprint was made for."""
    blueprint = db.get(EditorialBlueprint, plan.blueprint_id) if plan.blueprint_id else None
    version = db.get(StoryVersion, blueprint.story_version_id) if blueprint is not None else None
    return version.language if version is not None else None


def is_reference_film(db: Session, plan: VisualPlan) -> bool:
    """True for the plan of a film in a language other than English: it follows the English film's pictures."""
    language = language_of_plan(db, plan)
    return language is not None and language != draft_language()


def english_needs_plan(db: Session, case_id: int) -> VisualPlan | None:
    """The English film's needs plan: the newest plan of a blueprint made for an English story (not a language row of it)."""
    rows = (db.query(VisualPlan).join(EditorialBlueprint, EditorialBlueprint.id == VisualPlan.blueprint_id)
            .join(StoryVersion, StoryVersion.id == EditorialBlueprint.story_version_id)
            .filter(VisualPlan.case_id == case_id, StoryVersion.language == draft_language())
            .order_by(VisualPlan.id.desc()).all())
    return next((r for r in rows if "needs_plan_id" not in (json.loads(r.validation_json or "{}").get("mvp") or {})), None)


def beat_spans(blueprint_json: str | None) -> dict[str, tuple[float, float]]:
    """beat id -> (start, end) as fractions of the story, from the words each beat has."""
    beats = json.loads(blueprint_json or "{}").get("beats") or []
    total = sum(max(int(b.get("words") or 0), 0) for b in beats) or 1
    spans, reached = {}, 0
    for beat in beats:
        words = max(int(beat.get("words") or 0), 0)
        spans[beat["id"]] = (reached / total, (reached + words) / total)
        reached += words
    return spans


def matching_beats(spans: tuple[float, float], english: dict[str, tuple[float, float]]) -> list[str]:
    """The English beats whose stretch of the story overlaps this one's."""
    start, end = spans
    return [bid for bid, (a, b) in english.items() if a < end and b > start]


def english_plan_ids(db: Session, case_id: int) -> set[int]:
    """Every plan of the case made for an English story: the only plans whose approvals count for a film (a picture approved for another
    language's plan alone is not approved)."""
    return {row.id for row in (db.query(VisualPlan).join(EditorialBlueprint, EditorialBlueprint.id == VisualPlan.blueprint_id)
                               .join(StoryVersion, StoryVersion.id == EditorialBlueprint.story_version_id)
                               .filter(VisualPlan.case_id == case_id, StoryVersion.language == draft_language()))}
