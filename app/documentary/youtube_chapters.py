"""The YouTube chapters of a film: the list of times in its description. No model call here, ever.

The chapter writer that already writes the film's cards picks the chapters (beat ids and a native title each) in the same call (`ChapterPlan.plan_json["story_chapters"]`,
agents/chapters.py). This module finds each chosen beat's time in the finished film of the language (the production script's beats), checks the list against YouTube's
rules and the spoiler words of the language, and stores the result per case and language. Each language is a native film with its own blueprint, so a beat id
means something only inside its own language's film: nothing is ever carried from one language to another.

YouTube's rules: the first chapter at 00:00, at least `min_chapters`, ascending, each at least `min_chapter_seconds` long. A list that cannot keep them is omitted, never
published half-true. A title that is empty, too long, repeated or names an outcome (a culprit, a confession, a verdict, a twist) gets the language's neutral title instead."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import unicodedata

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, ChapterPlan, ProductionScript, Video, VideoChapters
from app.documentary.chapters import _FA_DIGITS, _loads, latest_chapter_plan
from app.services.cost_model import model_cost_eur
from app.tags.validate import same_tag_key
from app.utils import utc_now

log = logging.getLogger(__name__)

METHOD = "the chapter writer picks the beats and titles them in its own call; code finds the times"


def extra_call(agent: str, run, res, extra_in, extra_out) -> dict | None:
    """What the YouTube chapters added to one existing call: the characters of the extra prompt and output as an estimated number of tokens and its price (the provider reports one
    total per call, so the share is an estimate, labelled so)."""
    if not extra_in and not extra_out:
        return None
    per = ai_config.chapters.youtube.chars_per_token
    tokens_in = math.ceil(len(json.dumps(extra_in, ensure_ascii=False)) / per) if extra_in else 0
    tokens_out = math.ceil(len(json.dumps(extra_out, ensure_ascii=False)) / per) if extra_out else 0
    model = getattr(res, "model", None)
    return {"agent": agent, "run_id": getattr(run, "id", None), "model": model, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost_eur": model_cost_eur(model, tokens_in, tokens_out), "estimate": True}


def format_time(seconds: float) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60:02d}:{rest % 60:02d}"


def _outcome(title: str, language: str) -> bool:
    """A title that names an outcome or casts doubt on an account (the stems of both lists) is replaced by a neutral one."""
    folded = unicodedata.normalize("NFC", title).casefold()
    stems = [*ai_config.chapters.youtube.outcome_stems.get(language, []), *ai_config.description_settings().hint_stems.get(language, [])]
    return any(re.search(rf"(?<!\w){re.escape(stem.casefold())}", folded) for stem in stems)


def _fallback(index: int, total: int, language: str) -> str:
    titles = ai_config.chapters.youtube.fallback_titles.get(language) or ai_config.chapters.youtube.fallback_titles["en"]
    if index == 0:
        return titles["first"]
    if index == total - 1:
        return titles["last"]
    number = str(index + 1)
    return titles["middle"].format(n=number.translate(_FA_DIGITS) if language == "fa" else number)


def build(anchors: list[list[str]], beats: list[dict], duration: float, language: str) -> dict:
    """{status, reason, chapters:[{start, time, title, beat, source}], dropped:[...]} for the anchors [[beat id, title]] on the film's beats [{beat_id, start}] and its duration.
    Pure and deterministic."""
    cfg = ai_config.chapters.youtube
    starts = {b["beat_id"]: float(b["start"]) for b in beats if "beat_id" in b and "start" in b}
    dropped: list[str] = []
    found = []
    for beat, title in anchors:
        if beat in starts and starts[beat] < duration:
            found.append({"beat": beat, "start": starts[beat], "title": title})
        else:
            dropped.append(f"{beat}: not on the film's timeline")
    found.sort(key=lambda c: c["start"])
    unique: list[dict] = []
    for chapter in found:
        if unique and int(chapter["start"]) <= int(unique[-1]["start"]):
            dropped.append(f"{chapter['beat']}: same second as {unique[-1]['beat']}")
        else:
            unique.append(chapter)
    first_beat = beats[0]["beat_id"] if beats else None
    if unique and unique[0]["beat"] != first_beat:
        unique.insert(0, {"beat": first_beat, "start": 0.0, "title": ""})
    elif unique:
        unique[0] = {**unique[0], "start": 0.0}
    changed = True
    while changed and len(unique) > 1:
        changed = False
        for i, chapter in enumerate(unique):
            end = unique[i + 1]["start"] if i + 1 < len(unique) else duration
            if end - chapter["start"] < cfg.min_chapter_seconds:
                victim = i if i > 0 else 1
                dropped.append(f"{unique[victim]['beat']}: shorter than {cfg.min_chapter_seconds:g} s")
                unique.pop(victim)
                changed = True
                break
    while len(unique) > cfg.max_chapters:
        lengths = [(((unique[i + 1]["start"] if i + 1 < len(unique) else duration) - c["start"]), i) for i, c in enumerate(unique) if i > 0]
        dropped.append(f"{unique[min(lengths)[1]]['beat']}: more than {cfg.max_chapters} chapters")
        unique.pop(min(lengths)[1])
    if len(unique) < cfg.min_chapters:
        return {"status": "omitted", "reason": f"fewer than {cfg.min_chapters} valid chapters ({len(unique)})", "chapters": [], "dropped": dropped}
    seen: set[str] = set()
    chapters = []
    for i, chapter in enumerate(unique):
        title, source = chapter["title"].strip(), "writer"
        key = same_tag_key(title)
        if not title or len(title) > ai_config.chapters.max_title_chars or _outcome(title, language) or key in seen:
            if title:
                dropped.append(f"{chapter['beat']}: the title «{title}» was replaced by a neutral one")
            title, source = _fallback(i, len(unique), language), "fallback"
            key = same_tag_key(title)
        seen.add(key)
        chapters.append({"start": int(chapter["start"]), "time": format_time(chapter["start"]), "title": title, "beat": chapter["beat"], "source": source})
    return {"status": "ok", "reason": None, "chapters": chapters, "dropped": dropped}


def block(chapters: list[dict]) -> str:
    """The lines of the description: `00:00 Title`, one per chapter."""
    return "\n".join(f"{c['time']} {c['title']}" for c in chapters)


def _cost(plan: dict, language: str) -> dict:
    calls = (plan.get("story_chapters_cost") or {}).get(language) or []
    costs = [c["cost_eur"] for c in calls if c.get("cost_eur") is not None]
    return {"calls": calls, "tokens_in": sum(c["tokens_in"] for c in calls), "tokens_out": sum(c["tokens_out"] for c in calls),
            "cost_eur": round(sum(costs), 6) if costs else None, "estimate": True,
            "extra_llm_requests": 0 if not plan.get("story_chapters_backfilled", {}).get(language) else len(calls)}


def resolve(db: Session, case: Case, video: Video) -> VideoChapters:
    """The chapters of one rendered film, stored per case and language. Recomputed only when the render (its production script and timeline) or the chapter plan changed; a
    new narration timing therefore moves the times without a model call. Never raises: a failure is stored as the status."""
    language = video.language
    row = stored(db, case.id, language) or VideoChapters(case_id=case.id, language=language)
    ps = db.get(ProductionScript, video.production_script_id) if video.production_script_id else None
    plan_row = latest_chapter_plan(db, ps.blueprint_id) if ps else None
    plan = _loads(plan_row.plan_json, {}) if plan_row else {}
    anchors = (plan.get("story_chapters") or {}).get(language) or []
    script = _loads(ps.script_json, {}) if ps else {}
    duration = float(script.get("duration") or video.duration_seconds or 0)
    fingerprint = hashlib.sha256(json.dumps([ps.id if ps else None, duration, plan_row.id if plan_row else None, anchors, [b.get("start") for b in script.get("beats") or []]],
                                            sort_keys=True).encode()).hexdigest()
    if row.id and row.fingerprint == fingerprint and row.video_id == video.id:
        return row
    try:
        if ps is None:
            result = {"status": "no_render", "reason": "the film has no production script", "chapters": [], "dropped": []}
        elif not anchors:
            result = {"status": "no_plan", "reason": "the chapter plan of this film has no YouTube chapters yet", "chapters": [], "dropped": []}
        else:
            result = build(anchors, script.get("beats") or [], duration, language)
    except Exception as exc:  # noqa: BLE001 - a failing chapter list never blocks the film
        log.exception("chapters of %s %s failed", case.id, language)
        result = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:300], "chapters": [], "dropped": []}
    row.video_id, row.production_script_id, row.plan_id = video.id, ps.id if ps else None, plan_row.id if plan_row else None
    row.status, row.reason, row.chapters_json = result["status"], result["reason"], json.dumps({"chapters": result["chapters"], "dropped": result["dropped"]}, ensure_ascii=False)
    row.duration_s, row.fingerprint, row.method, row.resolved_at = duration or None, fingerprint, METHOD, utc_now()
    row.cost_json = json.dumps(_cost(plan, language), ensure_ascii=False)
    row.generation_model = next((c["model"] for c in reversed((plan.get("story_chapters_cost") or {}).get(language) or []) if c.get("agent") == "chapter writer"), None)
    db.add(row)
    db.flush()
    return row


def stored(db: Session, case_id: int, language: str) -> VideoChapters | None:
    return db.query(VideoChapters).filter(VideoChapters.case_id == case_id, VideoChapters.language == language).first()


def view(row: VideoChapters | None, language: str) -> dict:
    data = _loads(row.chapters_json, {}) if row else {}
    return {"language": language, "status": row.status if row else "no_render", "reason": row.reason if row else "the film is not rendered yet",
            "chapters": data.get("chapters", []), "dropped": data.get("dropped", []), "text": block(data.get("chapters", [])),
            "duration_s": row.duration_s if row else None, "duration": format_time(row.duration_s) if row and row.duration_s else None,
            "plan_id": row.plan_id if row else None, "production_script_id": row.production_script_id if row else None, "fingerprint": row.fingerprint if row else None,
            "method": row.method if row else None, "model": row.generation_model if row else None, "resolved_at": row.resolved_at.isoformat() if row and row.resolved_at else None,
            "cost": _loads(row.cost_json, {}) if row else {}}
