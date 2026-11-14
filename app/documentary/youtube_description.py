"""The YouTube description of a film. No model call here, ever.

The narrative (a hook, what the documentary investigates, an invitation) is written by the chapter writer inside its own call (agents/chapters.py, `ChapterPlan.plan_json["descriptions"]`)
from what the first part of the story tells. This module checks it with deterministic rules and assembles the whole description around it from what the application already holds: the
chapters, the disclosure of AI narration and presenter, 2 to 3 hashtags and the channel. A description holds no references at all: no sources, no links to articles or videos, no picture credits. A text the owner wrote replaces the automatic one and is never
changed by the app. A film without a usable narrative keeps the plain description (the caller's fallback).

The checks cannot make a description true: spoilers and facts are prevented by what the writer is given (only the first part of the story) and by the chapter auditor that reads the text next;
here only what code can see is checked (length, script, the words of an outcome or an accusation, claims about a fictional case, repeated words, the title said whole, unknown names)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections import Counter

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, EditorialBlueprint, EpisodeIdentity, ProductionScript, StoryVersion, Video, VideoDescription
from app.documentary import youtube_chapters as YT
from app.documentary.chapters import _loads, latest_chapter_plan
from app.identity.titles import channel, get_identity
from app.shortform.release_plan import load_config as social_config
from app.tags import dossier as TD
from app.tags import validate as TV
from app.utils import utc_now

log = logging.getLogger(__name__)

LATIN_TAG = re.compile(r"[A-Za-zÀ-ɏ]+")
RTL = {"fa", "ar"}


def stem_in(text: str, language: str, stems: list[str]) -> str | None:
    folded = unicodedata.normalize("NFC", text).casefold()
    return next((s for s in stems if re.search(rf"(?<!\w){re.escape(s.casefold())}", folded)), None)


def reveal_issue(text: str, language: str) -> str | None:
    """Why a text must not be published before the film: it names an outcome (a culprit, a confession, a verdict, a twist) or casts doubt on an account or hints at staging, or None."""
    if term := stem_in(text, language, ai_config.chapters.youtube.outcome_stems.get(language, [])):
        return f"«{term}» names an outcome or a reveal; a description gives none."
    if term := stem_in(text, language, ai_config.description_settings().hint_stems.get(language, [])):
        return f"«{term}» hints that an account does not hold or that something was staged; ask only neutral questions about what happened."
    return None


def clean_narrative(text: str) -> str:
    """The text as YouTube takes it: NFC, no `#` or angle brackets, trimmed lines, one blank line between paragraphs."""
    text = unicodedata.normalize("NFC", str(text or "")).replace("#", "").replace("<", "").replace(">", "")
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n|\n", text)]
    return "\n\n".join(p for p in paragraphs if p)


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def check(narrative: str, case: Case, language: str, title: str | None, known: set[str]) -> list[dict]:
    """What code can see wrong with a narrative: {type, severity, message}."""
    cfg = ai_config.description_settings()
    tags = ai_config.tags_settings()
    issues: list[dict] = []

    def add(kind: str, severity: str, message: str) -> None:
        issues.append({"type": kind, "severity": severity, "message": message})

    count = len(narrative.split())
    if count < cfg.min_words or count > cfg.max_words:
        add("length", "error", f"{count} words; between {cfg.min_words} and {cfg.max_words} are accepted (about {cfg.target_words[0]} to {cfg.target_words[1]} are intended).")
    if re.search(r"[#<>]", narrative):
        add("characters", "error", "A `#` or an angle bracket in the narrative.")
    if re.search(r"https?://|www\.", narrative, re.IGNORECASE):
        add("reference", "error", "A link in the narrative; a description holds no references.")
    letters = re.findall(r"[^\W\d_]", narrative)
    arabic = [c for c in letters if TV.ARABIC_SCRIPT.match(c)]
    if language in RTL and letters and len(arabic) / len(letters) < 0.5:
        add("wrong_script", "error", f"Less than half of the letters are in the script of {language}.")
    if language not in RTL and (arabic or TV.OTHER_SCRIPT.search(narrative)):
        add("wrong_script", "error", f"The script does not belong to the language {language}.")
    if reason := reveal_issue(narrative, language):
        add("outcome", "error", reason)
    if case.resolution_status != "SOLVED" and (term := TV._term_in(narrative, tags.accusation_terms.get(language, []))):
        add("unsupported_accusation", "error", f"«{term}» accuses someone in a case that is not solved.")
    if TD._fictional(case) and (term := TV._term_in(narrative, tags.real_claim_terms.get(language, []))):
        add("fictional_real_claim", "error", f"«{term}» claims a real case; this case is fictional.")
    repeated = [w for w, n in Counter(w for w in _words(narrative) if len(w) >= 5).items() if n > cfg.repeat_word_limit]
    if repeated:
        add("keyword_repetition", "warning", f"Repeated more than {cfg.repeat_word_limit} times: {', '.join(sorted(repeated)[:4])}.")
    if language not in cfg.common_noun_capitals:
        inside = {m.group(1) for m in re.finditer(r"(?<![.!?¿¡\n]\s)(?<!^)\b([A-ZÀ-Ö][A-Za-zÀ-ɏ'’-]{2,})", narrative) if not re.search(r"[.!?¿¡]\s*$|\n\s*$", narrative[:m.start()])}
        unknown = sorted(w for w in inside if w.casefold() not in known)
        if unknown:
            add("name_unverified", "warning", f"Capitalised words not found in the case record or the story: {', '.join(unknown[:6])}. Check the spelling.")
    hook = narrative.split("\n\n")[0].casefold()
    if title and len(title.split()) >= 3 and title.casefold() in hook:
        add("repeats_title", "warning", "The opening repeats the whole title; the hook should add to it.")
    return issues


def hashtags(case: Case, language: str) -> list[str]:
    """The case's own hashtag (its first person, else the case name), the language's genre hashtag and the channel's when its name is written in Latin letters."""
    def camel(text: str) -> str:
        return "".join(w.capitalize() if w.islower() else w for w in LATIN_TAG.findall(text))

    people = TD._list(case.people_json)
    subject = camel(people[0]) if people and camel(people[0]) else camel(re.sub(r"^(the\s+)?(murder|killing|death|disappearance|case|mystery)\s+of\s+", "", case.canonical_title, flags=re.I))
    channel_tag = camel(channel(language)["name"])
    found = [f"#{subject}"] if subject else []
    found.append(ai_config.description_settings().genre_hashtags.get(language) or "#TrueCrime")
    if channel_tag and channel(language)["name"].isascii():
        found.append(f"#{channel_tag}")
    return list(dict.fromkeys(found))[:3]


def channel_line(language: str) -> str:
    name = channel(language)["name"]
    url = ((social_config().get("accounts") or {}).get("youtube_short") or {}).get(language, {}).get("channel_url")
    return f"{name} — {url}" if url else name


def assemble(language: str, narrative: str, chapters: str, presenter: bool, tags: list[str], channel_text: str) -> tuple[str | None, list[str]]:
    """The whole description within YouTube's limit, and what was dropped to fit (the channel line). The narrative, chapters, disclosure and hashtags are never dropped; when they do not fit alone the
    result is (None, ...)."""
    cfg = ai_config.description_settings()
    disclosure = cfg.disclosure.get(language) or cfg.disclosure["en"]
    notice = " ".join([disclosure["narration"], *([disclosure["presenter"]] if presenter else [])])
    dropped: list[str] = []
    for keep_channel in (True, False):
        text = "\n\n".join(p for p in (narrative, chapters, notice, channel_text if keep_channel else "", " ".join(tags)) if p)
        if len(text) <= cfg.max_chars:
            return text, dropped
        dropped.append("the channel line")
    return None, dropped


def _fingerprint(parts: dict) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def stored(db: Session, case_id: int, language: str) -> VideoDescription | None:
    return db.query(VideoDescription).filter(VideoDescription.case_id == case_id, VideoDescription.language == language).first()


def final_text(row: VideoDescription | None) -> str:
    """The description to publish: the owner's text, else the assembled one, else nothing (the caller keeps the plain description)."""
    if row is None:
        return ""
    return row.edited_text or (row.auto_text if row.status == "ok" else "")


def resolve(db: Session, case: Case, video: Video) -> VideoDescription:
    """The description of one film, stored per case and language. Recomputed only when something it is made from changed (the narrative, chapters, title, channel); never raises."""
    language = video.language
    row = stored(db, case.id, language) or VideoDescription(case_id=case.id, language=language)
    try:
        chapters = YT.resolve(db, case, video)
        ps = db.get(ProductionScript, video.production_script_id) if video.production_script_id else None
        script = _loads(ps.script_json, {}) if ps else {}
        plan_row = latest_chapter_plan(db, ps.blueprint_id) if ps else None
        plan = _loads(plan_row.plan_json, {}) if plan_row else {}
        narrative = clean_narrative((plan.get("descriptions") or {}).get(language) or "")
        ident: EpisodeIdentity | None = get_identity(db, case.id, language)
        title = ident.editorial_title if ident else None
        chapter_text = YT.block(_loads(chapters.chapters_json, {}).get("chapters", [])) if chapters.status == "ok" else ""
        presenter = bool(script.get("host"))
        tags, channel_text = hashtags(case, language), channel_line(language)
        fingerprint = _fingerprint({"rules": [ai_config.description_settings().model_dump(), ai_config.chapters.youtube.outcome_stems], "plan": plan_row.id if plan_row else None, "narrative": narrative, "chapters": chapter_text, "title": title, "presenter": presenter, "tags": tags,
                                    "channel": channel_text, "status": case.resolution_status})
        if row.id and row.fingerprint == fingerprint and row.video_id == video.id:
            return row
        issues: list[dict] = []
        text, dropped = None, []
        if not narrative:
            status, reason = "no_plan", "the chapter plan of this film has no description yet"
        else:
            dossier = TD.build(db, case, language)
            story = db.get(StoryVersion, ps.story_version_id) if ps else None
            blueprint = db.get(EditorialBlueprint, ps.blueprint_id) if ps and ps.blueprint_id else None
            texts = [*dossier["evidence_text"].values(), story.story_text if story else "", *(b.get("summary", "") for b in _loads(blueprint.blueprint_json, {}).get("beats", []) if blueprint)]
            known_words = {w.casefold() for t in texts for w in LATIN_TAG.findall(t or "")} | dossier["known_words"]
            issues = check(narrative, case, language, title, known_words)
            if any(i["severity"] == "error" for i in issues):
                status, reason = "invalid", "; ".join(i["message"] for i in issues if i["severity"] == "error")[:400]
            else:
                text, dropped = assemble(language, narrative, chapter_text, presenter, tags, channel_text)
                status, reason = ("ok", None) if text else ("invalid", "the description does not fit into YouTube's limit even without the channel line")
    except Exception as exc:  # noqa: BLE001 - an optional description never blocks the film
        log.exception("description of %s %s failed", case.id, language)
        row.status, row.reason, row.resolved_at = "failed", f"{type(exc).__name__}: {exc}"[:300], utc_now()
        db.add(row)
        db.flush()
        return row
    row.video_id, row.plan_id = video.id, plan_row.id if plan_row else None
    row.status, row.reason, row.narrative, row.auto_text = status, reason, narrative, text or ""
    row.validation_json = json.dumps({"issues": issues, "dropped": dropped}, ensure_ascii=False)
    row.fingerprint, row.resolved_at = fingerprint, utc_now()
    calls = (plan.get("story_chapters_cost") or {}).get(language) or []
    costs = [c["cost_eur"] for c in calls if c.get("cost_eur") is not None]
    row.cost_json = json.dumps({"calls": calls, "tokens_in": sum(c["tokens_in"] for c in calls), "tokens_out": sum(c["tokens_out"] for c in calls),
                                "cost_eur": round(sum(costs), 6) if costs else None, "estimate": True,
                                "extra_llm_requests": len(calls) if (plan.get("story_chapters_backfilled") or {}).get(language) else 0}, ensure_ascii=False)
    row.generation_model = next((c["model"] for c in reversed(calls) if c.get("agent") == "chapter writer"), None)
    db.add(row)
    db.flush()
    return row


def save_edit(db: Session, row: VideoDescription, text: str | None) -> VideoDescription:
    """The owner's own text, or None to go back to the automatic one."""
    cleaned = unicodedata.normalize("NFC", text).replace("<", "").replace(">", "").strip() if text else ""
    row.edited_text = cleaned or None
    row.edited_at = utc_now() if cleaned else None
    db.flush()
    return row


def view(row: VideoDescription | None, language: str) -> dict:
    cfg = ai_config.description_settings()
    validation = _loads(row.validation_json, {}) if row else {}
    text = final_text(row)
    return {"language": language, "status": row.status if row else "no_render", "reason": row.reason if row else "the film is not rendered yet",
            "text": text, "auto_text": row.auto_text if row else "", "edited": bool(row and row.edited_text), "chars": len(text), "max_chars": cfg.max_chars,
            "narrative": row.narrative if row else "", "hook": (row.narrative.split("\n\n")[0] if row and row.narrative else None),
            "issues": validation.get("issues", []), "dropped": validation.get("dropped", []), "plan_id": row.plan_id if row else None,
            "model": row.generation_model if row else None, "resolved_at": row.resolved_at.isoformat() if row and row.resolved_at else None,
            "cost": _loads(row.cost_json, {}) if row else {}}
