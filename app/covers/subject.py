"""Whose photograph a cover shows. The rule of the owner: the cover shows the photograph of the victim; when the victim or the culprit is a woman, the woman's photograph is on every cover
(the victim's when both are women). The roles and genders are read from the case's story by a model (never guessed from a name, 'unknown' when the story does not show it); the rule is code."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.shorts import CoverSubjectPicker
from app.db.models import Case, StoryVersion, VisualAsset

STORY_CHARS = 3000


def apply_rule(roles: list[dict], available: list[str]) -> tuple[str | None, str]:
    """(entity key, why): the woman among the victim and the culprit (the victim first), else the victim, else nobody (the caller falls back). Only people with a usable photograph count."""
    by_role = {r["role"]: r for r in roles if r.get("entity_key") in available and r.get("role") in ("victim", "culprit")}
    for role in ("victim", "culprit"):
        found = by_role.get(role)
        if found and found.get("gender") == "female":
            return found["entity_key"], f"the {role} is a woman: her photograph is on every cover"
    if "victim" in by_role:
        return by_role["victim"]["entity_key"], "the victim's photograph"
    return None, "no victim with a usable photograph is known"


def story_opening(db: Session, case: Case) -> str:
    version = (db.query(StoryVersion).filter(StoryVersion.case_id == case.id, StoryVersion.language == "en").order_by(StoryVersion.id.desc()).first())
    return (version.story_text or "")[:STORY_CHARS] if version else ""


async def choose(db: Session, case: Case, people: list[dict], gen=None) -> tuple[str | None, str, list[dict]]:
    """(entity key, why, the roles the model found) for the people that have photographs; the key is None when the rule finds nobody."""
    if not people:
        return None, "the story has no photograph of a person", []
    view = [{"entity_key": p["entity_key"], "name": p["name"], "photo_descriptions": [(db.get(VisualAsset, ph["asset_id"]).description or "")[:200] for ph in p["photos"][:2]]} for p in people]
    roles = await CoverSubjectPicker(gen).run(case={"title": case.canonical_title, "story": story_opening(db, case)}, people=view)
    key, why = apply_rule(roles, [p["entity_key"] for p in people])
    return key, why, roles
