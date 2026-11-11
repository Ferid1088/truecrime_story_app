"""The cover planner's proposals: what a title and a scene of a cover must be, the checks of an answer, and the rows.

A proposal is a title (1 to 4 words, spoilers and greetings refused like the host's) and a scene description (what the image model paints: symbolic, never a person or a
weapon). The planner states its certainty that the scene carries the title: a cover whose scene is missing or uncertain stays open for the owner. Nothing here approves."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.covers.compose import title_issues
from app.covers.images import scene_issues
from app.db.models import CoverProposal
from app.documentary.host_firewall import reveal_issues
from app.documentary.host_story import HostStory

STATUSES = ("proposed", "open", "approved", "skipped")


class ProposalError(RuntimeError):
    pass


def keys_of(clip_keys: list[str]) -> list[str]:
    """The covers of a language: the long film's and one for each short clip."""
    return ["long", *sorted(clip_keys)]


def parse_items(raw: dict) -> list[dict]:
    """The items of an answer, each as {key, title, scene, confidence, reason} (anything else is dropped)."""
    out = []
    for item in raw.get("covers") or []:
        if not isinstance(item, dict):
            continue
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence"))))
        except (TypeError, ValueError):
            confidence = None
        scene = str(item.get("scene") or "").strip()
        out.append({"key": str(item.get("key") or "").strip(), "title": str(item.get("title") or "").strip(), "confidence": confidence,
                    "scene": "" if scene.lower() in ("none", "null") else scene, "reason": str(item.get("reason") or "").strip()})
    return out


def item_issues(item: dict, story: HostStory, cutoff: int, language: str) -> list[dict]:
    """What is wrong with one proposal, each with the fix the planner is told."""
    cfg = ai_config.covers_settings().proposal
    issues: list[dict] = []
    if problems := title_issues(item["title"]):
        issues.append({"type": "title_length", "fix": problems[0]})
    for issue in reveal_issues(story, cutoff, item["title"]):
        issues.append({**issue, "part": "title"})
    rules = ai_config.host.language_rules.get(language)
    if rules and any(re.search(p, item["title"], re.IGNORECASE) for p in rules.greeting_patterns):
        issues.append({"type": "greeting", "fix": "A cover title is no greeting."})
    if item["confidence"] is None:
        issues.append({"type": "no_confidence", "fix": "State your certainty (0 to 1) that the scene fits the title."})
    if item["scene"]:
        if not (cfg["scene_min_words"] <= len(item["scene"].split()) <= cfg["scene_max_words"]):
            issues.append({"type": "scene_length", "fix": f"Describe the scene in {cfg['scene_min_words']} to {cfg['scene_max_words']} words."})
        for problem in scene_issues(item["scene"]):
            issues.append({"type": "scene_not_symbolic", "fix": problem})
        if not item["reason"] or len(item["reason"].split()) > cfg["reason_max_words"]:
            issues.append({"type": "reason", "fix": f"Say in at most {cfg['reason_max_words']} words why this scene carries this title."})
    return issues


def set_issues(items: list[dict], keys: list[str]) -> list[dict]:
    """What is wrong with the covers together: a missing or unknown key, the same title twice, the same scene twice."""
    issues: list[dict] = []
    found = [i["key"] for i in items]
    for key in keys:
        if key not in found:
            issues.append({"type": "missing_cover", "quote": key, "fix": f"Propose the cover {key} too."})
    for key in set(found) - set(keys):
        issues.append({"type": "unknown_cover", "quote": key, "fix": f"There is no cover {key}; the covers are {', '.join(keys)}."})
    titles = [i["title"].lower() for i in items if i["title"]]
    if len(set(titles)) < len(titles):
        issues.append({"type": "same_title", "fix": "Every cover has its own title."})
    scenes = [i["scene"].lower() for i in items if i["scene"]]
    if len(set(scenes)) < len(scenes):
        issues.append({"type": "same_scene", "fix": "Every cover has its own scene."})
    return issues


def status_of(item: dict, issues: list[dict]) -> str:
    """proposed only when there is a scene the planner is sure of and nothing is wrong with the item; otherwise the cover stays open."""
    if issues or not item["scene"] or (item["confidence"] or 0) < ai_config.covers_settings().proposal["min_confidence"]:
        return "open"
    return "proposed"


def current(db: Session, case_id: int, language: str) -> dict[str, CoverProposal]:
    """The newest row of every cover of a language."""
    rows = db.query(CoverProposal).filter(CoverProposal.case_id == case_id, CoverProposal.language == language).order_by(CoverProposal.id.desc()).all()
    found: dict[str, CoverProposal] = {}
    for row in rows:
        found.setdefault(row.cover_key, row)
    return {key: found[key] for key in keys_of([k for k in found if k != "long"]) if key in found}


def view(row: CoverProposal) -> dict:
    return {"id": row.id, "case_id": row.case_id, "language": row.language, "key": row.cover_key, "kind": row.kind, "version": row.version, "source": row.source,
            "status": row.status, "title": row.title, "scene_prompt": row.scene_prompt, "image_id": row.image_id, "confidence": row.confidence, "reason": row.reason,
            "validation": json.loads(row.validation_json or "{}")}
