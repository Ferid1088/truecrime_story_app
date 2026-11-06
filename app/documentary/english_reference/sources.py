"""The approved sources of a review, verified and never guessed: the narration's spoken version (never a voice-prepare rewrite), the approved final
story of its language it was made from, and the approved English final story the app's English film tells. A missing or unapproved source stops the
review; nothing is substituted."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import EditorialBlueprint, StoryVersion
from app.documentary.mvp import reference as REF


class SourceError(RuntimeError):
    """A source of the review is missing, unapproved or ambiguous."""


@dataclass(frozen=True)
class Sources:
    narration: StoryVersion          # the spoken version the narration speaks (its blocks come from it)
    final: StoryVersion              # the approved final story of the narration's language it was made from
    english: StoryVersion            # the approved English final story
    notes: list[str] = field(default_factory=list)

    def describe(self) -> dict:
        def one(v: StoryVersion) -> dict:
            return {"id": v.id, "kind": v.kind, "language": v.language, "status": v.status, "is_best": v.is_best,
                    "approved_at": v.approved_at.isoformat() if v.approved_at else None, "sha256": sha(v.story_text or "")}

        return {"narration": one(self.narration), "final": one(self.final), "english": one(self.english), "notes": self.notes}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def approved_sources(db: Session, narration: StoryVersion) -> Sources:
    if narration.kind != "spoken":
        raise SourceError(f"version {narration.id} is not a spoken version ({narration.language}, {narration.kind})")
    if (json.loads(narration.narrative_structure or "{}")).get("prepared"):
        raise SourceError(f"version {narration.id} is a voice-prepare rewrite, not the approved text")
    final = db.get(StoryVersion, narration.master_version_id) if narration.master_version_id else None
    if final is None or final.language != narration.language or final.kind != "final" or final.approved_at is None:
        raise SourceError(f"the {narration.language} final story of version {narration.id} is missing or not approved")
    english = english_final(db, narration.case_id)
    if english is None or english.language != "en" or english.approved_at is None:
        raise SourceError(f"case {narration.case_id} has no approved English final story")
    notes = []
    if narration.approved_at is None:
        notes.append(f"the spoken version {narration.id} has no approval date of its own; it is the spoken form of the approved final "
                     f"{final.id} (is_best: {narration.is_best})")
    return Sources(narration, final, english, notes)


def english_final(db: Session, case_id: int) -> StoryVersion | None:
    """The English final story the app's English film tells (the story of the English needs plan's blueprint)."""
    plan = REF.english_needs_plan(db, case_id)
    blueprint = db.get(EditorialBlueprint, plan.blueprint_id) if plan is not None else None
    return db.get(StoryVersion, blueprint.story_version_id) if blueprint is not None else None
