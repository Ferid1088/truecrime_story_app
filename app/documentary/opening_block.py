"""The film's first seconds: a hook, the context text that follows it and the music brief (the opening block).

The English opening is made once from the English story by the opening director; every other language has its own writer that
writes the block again in that language (it is not a translation). What these texts may use is cut like the host's: only what the
first `opening_block.knowledge_acts` acts have told, plus the story's riddles. This module holds what needs no model: the checks of a
text, the limits scaled to a language, the voice settings of the block and the readers of what a row holds."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import OpeningBlockConfig, ai_config
from app.db.models import FilmOpening
from app.documentary.host import _SENTENCE_END
from app.documentary.host_firewall import reveal_issues
from app.documentary.host_story import HostStory, words_of
from app.documentary.spoken import digits_left, latin_left

HOOK_KINDS = ("quote", "moment", "question")
_HOOK_SENTENCE_END = re.compile(r"(?<=[.!?؟…])[\"”»’“]*\s+")          # a sentence ends on its mark, even when a closing quotation mark follows it


def _scale(language: str) -> float:
    """How many words of this language fill the time of the same English text."""
    wpm = ai_config.host.words_per_minute
    return wpm.get(language, wpm["en"]) / wpm["en"]


def hook_max_words(language: str, cfg: OpeningBlockConfig | None = None) -> int:
    cfg = cfg or ai_config.opening_block_settings()
    return max(3, round(cfg.hook_max_words * _scale(language)))


def context_limits(language: str, cfg: OpeningBlockConfig | None = None) -> tuple[int, int]:
    cfg = cfg or ai_config.opening_block_settings()
    scale = _scale(language)
    return max(1, round(cfg.context_min_words * scale)), max(2, round(cfg.context_max_words * scale))


def copied_run(text: str, source: str, run: int) -> str | None:
    """The first `run` words in a row of `text` that stand word for word in `source` (the narration the opening must not repeat), or None."""
    mine, theirs = words_of(text), words_of(source)
    if len(mine) < run or len(theirs) < run:
        return None
    seen = {tuple(theirs[i:i + run]) for i in range(len(theirs) - run + 1)}
    for i in range(len(mine) - run + 1):
        if tuple(mine[i:i + run]) in seen:
            return " ".join(mine[i:i + run])
    return None


def opening_issues(opening: dict, story: HostStory, language: str, cfg: OpeningBlockConfig | None = None) -> list[dict]:
    """What is wrong with a hook and a context text: empty or too long or too short, a greeting, digits written as digits, something only a later act
    tells, or words copied from the narration that follows. Every issue carries the fix the writer is told."""
    cfg = cfg or ai_config.opening_block_settings()
    issues: list[dict] = []
    hook = str(opening.get("hook") or "").strip()
    context = str(opening.get("context") or "").strip()
    if not hook:
        return [{"type": "empty_hook", "fix": "Write the hook."}]
    if not context:
        return [{"type": "empty_context", "fix": "Write the context text."}]
    if len([s for s in _HOOK_SENTENCE_END.split(hook) if s.strip()]) > 1:
        issues.append({"type": "hook_not_one_statement", "fix": "The hook is ONE statement, one sentence: no narrator's words around it ('he said', 'the man on the phone'), nothing added."})
    limit = hook_max_words(language, cfg)
    if len(hook.split()) > limit:
        issues.append({"type": "hook_too_long", "words": len(hook.split()), "max": limit, "fix": f"The hook has at most {limit} words: one strong statement."})
    lo, hi = context_limits(language, cfg)
    n = len(context.split())
    if n < lo:
        issues.append({"type": "context_too_short", "words": n, "min": lo, "fix": f"The context text has at least {lo} words."})
    elif n > hi:
        issues.append({"type": "context_too_long", "words": n, "max": hi, "fix": f"The context text has at most {hi} words."})
    rules = ai_config.host.language_rules.get(language)
    for label, text in (("hook", hook), ("context", context)):
        if rules and any(re.search(p, text, re.IGNORECASE) for p in rules.greeting_patterns):
            issues.append({"type": "greeting", "part": label, "fix": "No greeting, no 'welcome', no 'today we': start with the case itself."})
        if digits := digits_left(text, language):
            issues.append({"type": "numbers_as_digits", "part": label, "quote": ", ".join(digits[:4]), "fix": "Write numbers as words."})
        if latin := latin_left(text, language):
            issues.append({"type": "foreign_script", "part": label, "quote": ", ".join(latin[:4]), "fix": f"The word {latin[0]!r} is written in Latin letters and is not a word of this language: delete it and say that thought in ordinary words of the language, or write the sentence another way. No Latin letters anywhere."})
        for issue in reveal_issues(story, cfg.knowledge_acts, text):
            issues.append({**issue, "part": label})
    if story.acts and cfg.knowledge_acts:
        told = " ".join(a.text for a in story.visible(cfg.knowledge_acts))
        for label, text in (("hook", hook), ("context", context)):
            run = copied_run(text, told, ai_config.host.narration_copy_words)
            if run and label == "context":      # the hook may be the very quote the story tells; the context may not repeat the narration
                issues.append({"type": "repeats_the_narration", "part": label, "quote": run,
                               "fix": "Do not repeat the narration that follows: say what makes the case strange, in new words."})
    sentences = [s for s in _SENTENCE_END.split(context) if s.strip()]
    if sentences and not any(len(s.split()) <= 6 for s in sentences):
        issues.append({"type": "no_short_sentence", "part": "context", "fix": "Use at least one very short sentence: it is spoken, not written."})
    return issues


def scene_problems(scenes: list[str], cfg: OpeningBlockConfig) -> list[dict]:
    """What is wrong with the scene descriptions of the block's pictures: not the configured number, too short or too long, or a person, victim or weapon in one."""
    from app.covers.images import scene_issues

    pictures = cfg.pictures
    problems: list[dict] = []
    if len(scenes) != pictures.count:
        problems.append({"type": "scene_count", "words": len(scenes), "fix": f"Give exactly {pictures.count} scene descriptions."})
    for i, scene in enumerate(scenes):
        if not pictures.scene_min_words <= len(scene.split()) <= pictures.scene_max_words:
            problems.append({"type": "scene_length", "part": f"scene {i + 1}", "fix": f"A scene has {pictures.scene_min_words} to {pictures.scene_max_words} words."})
        problems += [{"type": "scene_not_symbolic", "part": f"scene {i + 1}", "fix": p} for p in scene_issues(scene)]
    return problems


def parse_master(raw: dict, cfg: OpeningBlockConfig | None = None) -> tuple[dict, list[dict]]:
    """The director's answer as an opening: the hook it chose among its candidates, the context text and the music brief; with the problems of the
    answer itself (a missing candidate, a choice that is no candidate, a hook of an unknown kind)."""
    cfg = cfg or ai_config.opening_block_settings()
    problems: list[dict] = []
    candidates = [c for c in (raw.get("candidates") or []) if isinstance(c, dict) and str(c.get("text") or "").strip()]
    if len(candidates) < min(2, cfg.hook_candidates):
        problems.append({"type": "too_few_candidates", "fix": f"Weigh {cfg.hook_candidates} different hooks."})
    for c in candidates:
        if c.get("kind") not in HOOK_KINDS:
            problems.append({"type": "unknown_hook_kind", "quote": str(c.get("kind")), "fix": "A hook is a quote, a moment or a question."})
            break
    chosen = raw.get("chosen")
    hook = ""
    if isinstance(chosen, int) and 0 <= chosen < len(candidates):
        hook = str(candidates[chosen]["text"]).strip()
    else:
        problems.append({"type": "no_chosen_hook", "fix": "Name the index of the hook you choose among your candidates."})
    scenes = [str(x).strip() for x in (raw.get("scenes") or []) if str(x).strip()] if isinstance(raw.get("scenes"), list) else []
    problems += scene_problems(scenes, cfg)
    music = raw.get("music") if isinstance(raw.get("music"), dict) else {}
    opening = {"scenes": scenes, "hook": hook, "context": str(raw.get("context") or "").strip(),
               "candidates": [{"text": str(c["text"]).strip(), "kind": c.get("kind"), "why": str(c.get("why") or "")} for c in candidates],
               "chosen": chosen if isinstance(chosen, int) else None,
               "music": {"mood": str(music.get("mood") or cfg.music.mood_fallback).strip().lower(), "prompt": str(music.get("prompt") or "").strip()}}
    return opening, problems


def parse_native(raw: dict, master: dict) -> dict:
    """A native writer's answer: its own hook and context; the music brief, the candidates and the scenes of the pictures stay the English opening's."""
    return {"hook": str(raw.get("hook") or "").strip(), "context": str(raw.get("context") or "").strip(), "music": master.get("music") or {}, "scenes": master.get("scenes") or []}


def post_speed_for(language: str, cfg: OpeningBlockConfig | None = None) -> float:
    """The local speed of the opening's voice: the language's own post speed times the factor (and the language's extra), never above the maximum."""
    cfg = cfg or ai_config.opening_block_settings()
    base = ai_config.voice.post_speed.get(language, 1.0)
    return round(min(base * cfg.voice.post_speed_factor + cfg.voice.post_speed_extra.get(language, 0.0), cfg.voice.post_speed_max), 3)


def voice_settings_for(language: str, cfg: OpeningBlockConfig | None = None) -> dict:
    """The ElevenLabs settings of the opening's voice: the language's own style, with the stability `stability_drop` lower when it is `stability_threshold` or more."""
    cfg = cfg or ai_config.opening_block_settings()
    settings = ai_config.voice.settings_for(cfg.voice.style, language)
    if settings["stability"] >= cfg.voice.stability_threshold:
        settings["stability"] = round(settings["stability"] - cfg.voice.stability_drop, 2)
    return settings


def music_prompt(opening: dict, cfg: OpeningBlockConfig | None = None) -> str:
    """The text the music is generated from: the director's brief, instrumental and without a melody that competes with the voice."""
    cfg = cfg or ai_config.opening_block_settings()
    brief = (opening.get("music") or {}).get("prompt") or ""
    base = brief.strip() or f"{cfg.music.mood_fallback}, slow, dark, restrained"
    return f"Instrumental film score for a true crime documentary opening, no vocals, no drums that compete with a speaking voice: {base}"


def latest_opening(db: Session, case_id: int, language: str, story_version_id: int | None = None) -> FilmOpening | None:
    q = db.query(FilmOpening).filter(FilmOpening.case_id == case_id, FilmOpening.language == language)
    if story_version_id is not None:
        q = q.filter(FilmOpening.story_version_id == story_version_id)
    return q.order_by(FilmOpening.id.desc()).first()


def said_before(db: Session, case_id: int, language: str) -> list[str]:
    """What the opening block says in this language, as text the host's writer must not repeat (nothing when the language has no valid block)."""
    row = latest_opening(db, case_id, language)
    if row is None or row.status != "valid":
        return []
    opening = json.loads(row.opening_json or "{}")
    return [f"{opening.get('hook', '')} {opening.get('context', '')}".strip()]


def opening_dict(row: FilmOpening) -> dict:
    return {"id": row.id, "case_id": row.case_id, "language": row.language, "kind": row.kind, "status": row.status, "version": row.version,
            **json.loads(row.opening_json or "{}"), "media": json.loads(row.media_json or "{}"), "validation": json.loads(row.validation_json or "{}")}
