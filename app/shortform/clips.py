"""The short clips of a film: what a script of one clip must be (four parts, about 25 seconds, one idea) and the checks of a script. A clip's parts: the hook (the
avatar says it from the first frame), the context (just enough to understand the moment), the detail (the case pictures show it while the narration tells one
compelling detail) and the reaction (the avatar's reaction or question), then the invitation to watch the full story. A clip may tell only what the acts before the
story's answer have told: the answer itself is the long film's."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ShortClipsConfig, ai_config
from app.db.models import ShortClip
from app.documentary.host import _SENTENCE_END
from app.documentary.spoken import digits_left, latin_fix, latin_left

TEXT_PARTS = ("hook", "context", "detail", "reaction", "cta")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)


def _scale(language: str) -> float:
    wpm = ai_config.host.words_per_minute
    return wpm.get(language, wpm["en"]) / wpm["en"]


def words_for(part: str, language: str, cfg: ShortClipsConfig | None = None) -> tuple[int, int]:
    """The words of a language that fill a part of a clip, within the word tolerance."""
    cfg = cfg or ai_config.short_clips_settings()
    words = cfg.segments[part].seconds * ai_config.host.words_per_minute.get(language, ai_config.host.words_per_minute["en"]) / 60
    if part == "hook":
        per_second = ai_config.host.words_per_minute.get(language, ai_config.host.words_per_minute["en"]) / 60
        return max(2, round(cfg.hook_min_seconds * per_second)), max(3, round(cfg.hook_max_words * _scale(language)))
    return max(1, int(words * (1 - cfg.word_tolerance))), int(words * (1 + cfg.word_tolerance)) + 1


def clip_issues(clip: dict, language: str, cfg: ShortClipsConfig | None = None) -> list[dict]:
    """What is wrong with one clip's script, each with the fix the writer is told."""
    cfg = cfg or ai_config.short_clips_settings()
    issues: list[dict] = []
    if clip.get("angle") not in cfg.angles:
        issues.append({"type": "unknown_angle", "quote": str(clip.get("angle")), "fix": f"The angle is one of: {', '.join(cfg.angles)}."})
    for part in TEXT_PARTS:
        if not str(clip.get(part) or "").strip():
            return [*issues, {"type": f"empty_{part}", "fix": f"Write the {part}."}]
    for part in ("hook", "context", "detail"):
        lo, hi = words_for(part, language, cfg)
        n = len(str(clip[part]).split())
        if n < lo or n > hi:
            issues.append({"type": f"{part}_length", "words": n, "min": lo, "max": hi, "fix": f"The {part} has {lo} to {hi} words ({cfg.segments[part].seconds:g} seconds spoken)."})
    lo, hi = words_for("reaction", language, cfg)
    spoken = len(str(clip["reaction"]).split()) + len(str(clip["cta"]).split())
    if spoken < lo or spoken > hi:
        issues.append({"type": "reaction_length", "words": spoken, "min": lo, "max": hi, "fix": f"The reaction and the invitation together have {lo} to {hi} words ({cfg.segments['reaction'].seconds:g} seconds spoken)."})
    if len(str(clip["cta"]).split()) > cfg.cta_max_words or _URL.search(str(clip["cta"])):
        issues.append({"type": "cta", "fix": f"The invitation to watch the full story has at most {cfg.cta_max_words} words and no web address."})
    rules = ai_config.host.language_rules.get(language)
    if rules is not None:
        if not any(q in str(clip["reaction"]) for q in rules.question_marks):
            issues.append({"type": "reaction_no_question", "fix": "The reaction asks the viewer a question."})
        if any(re.search(p, str(clip["hook"]), re.IGNORECASE) for p in rules.greeting_patterns):
            issues.append({"type": "greeting", "fix": "No greeting and no introduction: the hook is the case itself."})
    for part in TEXT_PARTS:
        text = str(clip[part])
        if rules is not None and (found := next((m.group(0) for p in rules.formal_patterns if (m := re.search(p, text))), None)):
            issues.append({"type": "formal_address", "part": part, "quote": found, "fix": "Speak to the viewer informally, as a friend does (the host never says 'Sie' or the formal you of the language)."})
        if generic := generic_phrase_in(text, language, cfg):
            issues.append({"type": "generic_phrase", "part": part, "quote": generic, "fix": "This is an empty suspense phrase that fits dozens of cases: say the concrete, unusual, case-specific fact instead."})
        if digits := digits_left(text, language):
            issues.append({"type": "numbers_as_digits", "part": part, "quote": ", ".join(digits[:4]), "fix": "Write numbers as words."})
        if latin := latin_left(text, language):
            issues.append({"type": "foreign_script", "part": part, "quote": ", ".join(latin[:4]), "fix": latin_fix(latin[0])})
    if len([s for s in _SENTENCE_END.split(str(clip["hook"])) if s.strip()]) > cfg.hook_max_sentences:
        issues.append({"type": "hook_not_one_statement", "fix": f"The hook is at most {cfg.hook_max_sentences} sentence(s): one whole opening sentence, or two short ones."})
    return issues


def set_issues(clips: list[dict], cfg: ShortClipsConfig | None = None) -> list[dict]:
    """What is wrong with a set of clips: too few or too many, one angle twice, one key twice."""
    cfg = cfg or ai_config.short_clips_settings()
    issues: list[dict] = []
    if len(clips) < cfg.min_clips or len(clips) > cfg.max_clips:
        issues.append({"type": "clip_count", "count": len(clips), "fix": f"Plan {cfg.min_clips} to {cfg.max_clips} clips."})
    angles = [c.get("angle") for c in clips]
    if len(set(angles)) != len(angles):
        issues.append({"type": "angle_twice", "fix": "Every clip has its own angle."})
    keys = [c.get("clip_key") for c in clips]
    if len(set(keys)) != len(keys):
        issues.append({"type": "key_twice", "fix": "Every clip has its own clip_key."})
    return issues


def parse_clips(raw: dict) -> list[dict]:
    """The clips of an answer, each as a dict with the fields of a script (anything else is dropped)."""
    fields = ("clip_key", "angle", "idea", *TEXT_PARTS, "evidence_ids", "act_ids", "selection")
    return [{k: (v if k in ("evidence_ids", "act_ids", "selection") else (str(v).strip() if v is not None else "")) for k, v in c.items() if k in fields}
            for c in (raw.get("clips") or []) if isinstance(c, dict)]


def _plain(text: str) -> str:
    """The text as the generic-phrase check reads it: lower case, no punctuation, half-spaces and apostrophes as plain spaces."""
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold().replace("\u200c", " ").replace("'", " ")).split())


def generic_phrase_in(text: str, language: str, cfg: ShortClipsConfig | None = None) -> str | None:
    """The empty suspense phrase a sentence of `text` essentially is (the phrase plus at most three more words), or None. A phrase inside a longer sentence that states a concrete fact is no empty teaser."""
    cfg = cfg or ai_config.short_clips_settings()
    phrases = [(_plain(p), p) for p in cfg.generic_phrases.get(language, [])]
    for sentence in _SENTENCE_END.split(text):
        plain = _plain(sentence)
        for key, original in phrases:
            if key and key in plain and len(plain.split()) <= len(key.split()) + 3:
                return original
    return None


def spoken_seconds(clip: dict, language: str, cfg: ShortClipsConfig | None = None) -> float:
    """The local estimate of the spoken duration: each part takes the seconds of its slot, scaled by its words against the middle of the words its slot holds."""
    cfg = cfg or ai_config.short_clips_settings()
    total = 0.0
    for part in ("hook", "context", "detail", "reaction"):
        low, high = words_for(part, language, cfg)
        words = len((clip.get(part) or "").split()) + (len((clip.get("cta") or "").split()) if part == "reaction" else 0)
        total += cfg.segments[part].seconds * words / ((low + high) / 2)
    return round(total, 1)


def with_estimate(clip: dict, language: str) -> dict:
    """The clip with the writer's own selection note (the concepts it weighed, the hook span, the tease question, the fact references) and its duration, honestly labelled: at this point only an
    ESTIMATE from the words (no speech has been made); `duration_of` gives the measured one once the clip has been voiced."""
    note = clip.get("selection") if isinstance(clip.get("selection"), dict) else {}
    return {**clip, "selection": {**note, "duration": {"seconds": spoken_seconds(clip, language), "kind": "estimate"}}}


def duration_of(row: ShortClip) -> dict:
    """{seconds, kind} of a stored clip: the real length of its synthesized voice when it has been voiced ("measured"), else the estimate from its words ("estimate")."""
    media = json.loads(row.media_json or "{}")
    if media.get("voice_seconds"):
        return {"seconds": media["voice_seconds"], "kind": "measured"}
    return {"seconds": spoken_seconds(json.loads(row.script_json), row.language), "kind": "estimate"}


def seconds_of(clip: dict, cfg: ShortClipsConfig | None = None) -> dict[str, float]:
    """The parts of a clip with their start second: the hook, the context, the detail, the reaction (with the invitation)."""
    cfg = cfg or ai_config.short_clips_settings()
    out, t = {}, 0.0
    for part in ("hook", "context", "detail", "reaction"):
        out[part] = round(t, 3)
        t += cfg.segments[part].seconds
    return out


def clips_of(db: Session, case_id: int, language: str, status: str | None = "valid") -> list[ShortClip]:
    """The newest version of every clip of a language (those that passed their checks, unless `status` is None), in the order of their keys."""
    rows = db.query(ShortClip).filter(ShortClip.case_id == case_id, ShortClip.language == language).order_by(ShortClip.id.desc()).all()
    newest: dict[str, ShortClip] = {}
    for r in rows:
        newest.setdefault(r.clip_key, r)
    return sorted((r for r in newest.values() if status is None or r.status == status), key=lambda r: r.clip_key)


def clip_dict(row: ShortClip) -> dict:
    return {"id": row.id, "case_id": row.case_id, "language": row.language, "clip_key": row.clip_key, "kind": row.kind, "status": row.status, "version": row.version,
            **json.loads(row.script_json or "{}"), "media": json.loads(row.media_json or "{}"), "validation": json.loads(row.validation_json or "{}")}
