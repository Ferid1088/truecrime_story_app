"""The cover planner's proposals: what a title and a scene of a cover must be, the checks of an answer, and the rows.

A proposal is a title (a hook of the configured number of words that can be set large enough to read, spoilers and greetings refused like the host's) and a scene description (what the image model paints: symbolic, never a person or a
weapon). The planner states its certainty that the scene carries the title: a cover whose scene is missing or uncertain stays open for the owner. Nothing here approves."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.covers.compose import CoverError, display_title, fit_title, title_issues
from app.covers.images import scene_issues
from app.db.models import CoverProposal
from app.naming import selection as SEL
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
                    "scene": "" if scene.lower() in ("none", "null") else scene, "reason": str(item.get("reason") or "").strip(),
                    "title_pick": str(item.get("title_pick") or "").strip().upper() or None, "pair_reason": str(item.get("pair_reason") or "").strip()})
    return out


def pairing_issues(item: dict, options: dict[str, dict]) -> list[dict]:
    """What is wrong with the film cover's pairing with the YouTube title options: no valid pick, no reason, or a cover title that repeats the picked title."""
    cfg = ai_config.covers_settings().proposal
    if item["title_pick"] not in options:
        return [{"type": "title_pick", "fix": f"Choose the id ({', '.join(options)}) of the title option your cover pairs best with."}]
    issues = []
    if not item["pair_reason"] or len(item["pair_reason"].split()) > cfg["reason_max_words"]:
        issues.append({"type": "pair_reason", "fix": f"Say in at most {cfg['reason_max_words']} words why this title and this cover belong together."})
    if SEL.repeat_share(item["title"], options[item["title_pick"]]["title"]) > ai_config.case_naming.cover_max_repeat:
        issues.append({"type": "repeats_title", "fix": "The cover title is the thumbnail text and must not repeat the picked YouTube title: say something else that makes the same promise visible."})
    return issues


def item_issues(item: dict, story: HostStory, cutoff: int, language: str, options: dict[str, dict] | None = None) -> list[dict]:
    """What is wrong with one proposal, each with the fix the planner is told. The film cover is also checked against the title `options` when there are some."""
    cfg = ai_config.covers_settings().proposal
    issues: list[dict] = []
    if problems := title_issues(item["title"]):
        issues.append({"type": "title_length", "fix": problems[0]})
    elif item["key"]:
        try:
            fit_title(display_title(item["title"], language), language, "long" if item["key"] == "long" else "short")
        except CoverError as e:
            issues.append({"type": "title_unreadable", "fix": f"{e}; keep the idea and use fewer or shorter words"})
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
    if options and item["key"] == "long":
        issues += pairing_issues(item, options)
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
