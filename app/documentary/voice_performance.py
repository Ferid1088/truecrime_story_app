"""Voice performance director — how a real storyteller would SAY it.

ElevenLabs v3 performs audio tags (words in square brackets that are not
spoken but change how the next words are said: [whispers], [slowly],
[pause] ...) and reads punctuation as timing. This stage turns the
spoken text into the narrator's performance:

1. Arc (one call, role voice_performance_director): for every beat a
   normal level and a peak level of tension, and the beat where the
   story's first incident happens. Levels:
     0 neutral   — nothing has happened yet; calm, even, informative
     1 unease    — the first thing that does not fit
     2 dark      — the crime is present; lower, graver, deliberate
     3 climax    — breath-taking moments; slow, measured, near-whisper
   Deterministic rules then hold the arc to the brief: everything before
   the incident is level 0, the opening beat stays neutral, climaxes are
   rare and the tension never jumps without a step.
2. Sentences (chunks in parallel): per sentence its level and the text
   for the voice — tags placed right before the 4–5 words they colour,
   ellipses and dashes for timing, (English only) one emphasised word in
   capitals.
3. Validator: tags only from the palette of the sentence's level, never
   forbidden ones (laughter, crying, shouting, sound effects, accents),
   at most N per sentence, density per level, never a trailing tag, the
   words themselves unchanged (only tags, punctuation and allowed
   emphasis may differ) and the sentence type (? !) kept. Anything that
   fails falls back to the plain sentence with its valid tags.

The voice renderer sends `tts` (tags + punctuation) to the voice and
uses `display` (no tags) for the speech-to-text check and subtitles. Level -> voice style (speed,
stability) per block: config voice_performance.level_styles.
"""

from __future__ import annotations


from app.core.prompts import prompt

import json
import re

from sqlalchemy.orm import Session

from app.agents.story import _paragraphs, stored_sections
from app.core.ai_config import VoicePerformanceConfig, ai_config
from app.db.models import StoryVersion, VoicePerformance
from app.documentary.voice_blocks import split_sentences

TAG = re.compile(r"\[([^\[\]\n]{1,48})\]")
_TERMINAL = re.compile(r"([.?!…؟]+)[\s\"'»”“’)\]]*$")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

LEVEL_NAMES = {0: "neutral", 1: "unease", 2: "dark", 3: "climax"}


# ---------------------------------------------------------------------------
# speech structure: what the narrator says, sentence by sentence
# ---------------------------------------------------------------------------


def speech_structure(version: StoryVersion) -> list[dict]:
    """Per beat (section): paragraphs of sentence records
    {"speech", "display"} — what the narrator says and what people read
    (subtitles, speech-to-text check; the same text)."""
    language = version.language or "en"
    sections = stored_sections(version) or [{"id": "full", "text": version.story_text or ""}]
    out = []
    for sec in sections:
        paragraphs = [[{"speech": x, "display": x} for x in split_sentences(para, language)]
                      for para in _paragraphs(sec["text"])]
        out.append({"beat_id": sec["id"], "paragraphs": paragraphs})
    return out


# ---------------------------------------------------------------------------
# validation (deterministic)
# ---------------------------------------------------------------------------


def strip_tags(text: str) -> str:
    return " ".join(TAG.sub(" ", text or "").split())


def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _terminal(text: str) -> str:
    m = _TERMINAL.search((text or "").strip())
    if not m:
        return ""
    mark = m.group(1)
    if "?" in mark or "؟" in mark:
        return "?"
    if "!" in mark:
        return "!"
    return "."


def _tag_parts(inner: str) -> list[str]:
    return [p.strip().lower() for p in inner.split(",") if p.strip()]


def validate_line(speech: str, tts: str | None, level: int, language: str,
                  cfg: VoicePerformanceConfig | None = None) -> tuple[str, list[str]]:
    """Clean `tts` for one sentence. Returns (tts, issues). The words of
    `speech` never change; on any doubt the plain sentence (plus its valid
    leading tags) is used."""
    cfg = cfg or ai_config.voice_performance
    issues: list[str] = []
    speech = " ".join((speech or "").split())
    tts = " ".join((tts or "").split()) or speech
    allowed = cfg.allowed_tags(level)
    forbidden = {t.lower() for t in cfg.forbidden_tags}

    kept = 0
    good_tags: list[str] = []

    def keep(m: re.Match) -> str:
        nonlocal kept
        parts = _tag_parts(m.group(1))
        bad = [p for p in parts if p in forbidden or any(f in p for f in forbidden)
               or p not in allowed]
        if not parts or bad:
            issues.append(f"tag_removed:{m.group(1)}")
            return " "
        if kept >= cfg.max_tags_per_sentence:
            issues.append(f"too_many_tags:{m.group(1)}")
            return " "
        kept += 1
        tag = "[" + ", ".join(parts) + "]"
        good_tags.append(tag)
        return f" {tag} "

    tts = " ".join(TAG.sub(keep, tts).split())
    # a tag must colour the words AFTER it: drop trailing tags
    while True:
        found = list(TAG.finditer(tts))
        if not found or _words(tts[found[-1].end():]):
            break
        last = found[-1]
        issues.append(f"trailing_tag:{last.group(1)}")
        tts = " ".join((tts[:last.start()] + " " + tts[last.end():]).split())
        tts = re.sub(r"\s+([.?!…؟,;:])", r"\1", tts)
        if good_tags:
            good_tags.pop()

    plain = strip_tags(tts)
    caps_ok = language in cfg.caps_languages and level >= 1

    def fallback(reason: str) -> tuple[str, list[str]]:
        issues.append(reason)
        lead = " ".join(good_tags[:1])
        return (f"{lead} {speech}".strip() if lead else speech), issues

    a, b = _words(speech), _words(plain)
    if len(a) != len(b):
        return fallback("words_changed")
    caps_added = 0
    for x, y in zip(a, b):
        if x == y:
            continue
        if caps_ok and x.lower() == y.lower() and y.isupper() and len(y) > 1:
            caps_added += 1
            continue
        return fallback("words_changed")
    if caps_added > 1:
        return fallback("too_much_emphasis")
    if plain.count("...") + plain.count("…") > cfg.max_ellipses_per_sentence + (
            speech.count("...") + speech.count("…")):
        return fallback("too_many_ellipses")
    t0, t1 = _terminal(speech), _terminal(plain)
    if t0 in ("?", "!") and t1 != t0:
        return fallback("sentence_type_changed")
    if t0 == "." and t1 in ("?", "!"):
        return fallback("sentence_type_changed")
    return tts, issues


def validate_arc(beats: list[dict], arc: dict, cfg: VoicePerformanceConfig | None = None
                 ) -> tuple[dict[str, dict], str | None, list[str]]:
    """beats: blueprint beats (ordered). Returns ({beat_id: {level, peak}},
    incident beat id, log)."""
    cfg = cfg or ai_config.voice_performance
    log: list[str] = []
    ids = [b["id"] for b in beats]
    got = {str(x.get("beat_id")): x for x in arc.get("beats") or [] if isinstance(x, dict)}
    incident = arc.get("incident_beat")
    if incident not in ids:
        # first beat that is not pure set-up
        incident = next((b["id"] for b in beats[1:] if b.get("purpose") not in (
            "orientation", "human_introduction", "transition")), ids[min(1, len(ids) - 1)]
            if ids else None)
        log.append("incident_guessed")
    out: dict[str, dict] = {}
    before = True
    prev_level = 0
    for k, b in enumerate(beats):
        x = got.get(b["id"]) or {}
        try:
            level = max(0, min(3, int(x.get("level", 1))))
            peak = max(0, min(3, int(x.get("peak", level))))
        except (TypeError, ValueError):
            level, peak = 1, 1
            log.append(f"bad_arc:{b['id']}")
        if b["id"] == incident:
            before = False
        if before:
            level, peak = 0, 0
        if k == 0:  # the film always opens neutral
            level, peak = 0, min(peak, 1)
        if b.get("purpose") == "recovery":
            peak = min(peak, 1)
            level = min(level, 1)
        if level > prev_level + 2:
            log.append(f"arc_jump_softened:{b['id']}")
            level = prev_level + 2
        peak = max(peak, level)
        out[b["id"]] = {"level": level, "peak": peak}
        prev_level = level
    # climax beats are rare
    peaks = [bid for bid in ids if out[bid]["peak"] == 3]
    allowed = max(1, round(len(ids) * 0.2))
    if len(peaks) > allowed:
        def weight(bid: str) -> tuple:
            b = next(x for x in beats if x["id"] == bid)
            lv = {"low": 0, "medium": 1, "high": 2}
            return (b.get("purpose") in ("reveal", "contradiction", "chapter_end", "evidence"),
                    lv.get(b.get("emotional_load"), 1) + lv.get(b.get("mystery_intensity"), 1))
        keep = set(sorted(peaks, key=weight, reverse=True)[:allowed])
        for bid in peaks:
            if bid not in keep:
                out[bid]["peak"] = 2
                out[bid]["level"] = min(out[bid]["level"], 2)
                log.append(f"climax_capped:{bid}")
    return out, incident, log


def enforce_levels(records: list[dict], arc: dict[str, dict],
                   cfg: VoicePerformanceConfig | None = None) -> list[str]:
    """records: flat ordered sentence records with beat_id and level
    (mutated). Clamp to the beat's arc, cap the share and runs of
    climax sentences."""
    cfg = cfg or ai_config.voice_performance
    log: list[str] = []
    for r in records:
        a = arc.get(r["beat_id"], {"level": 0, "peak": 0})
        lo, hi = max(0, a["level"] - 1), a["peak"]
        try:
            lv = int(r.get("level", a["level"]))
        except (TypeError, ValueError):
            lv = a["level"]
        r["level"] = max(lo, min(hi, lv))
    climax = [k for k, r in enumerate(records) if r["level"] == 3]
    allowed = max(1 if climax else 0, int(cfg.max_climax_share * len(records)))
    if len(climax) > allowed:
        # keep climaxes spread over the film: every n-th
        step = len(climax) / max(allowed, 1)
        keep = {climax[int(j * step)] for j in range(allowed)}
        for k in climax:
            if k not in keep:
                records[k]["level"] = 2
        log.append(f"climax_share_capped:{len(climax)}->{allowed}")
    run = 0
    for r in records:
        run = run + 1 if r["level"] == 3 else 0
        if run > 4:
            r["level"] = 2
            run = 0
            log.append("climax_run_broken")
    return log


def thin_tags(records: list[dict], cfg: VoicePerformanceConfig | None = None) -> int:
    """Density per level: a narrator who performs every line sounds fake.
    Removes tags from evenly spread sentences above the share."""
    cfg = cfg or ai_config.voice_performance
    removed = 0
    for lv in range(4):
        group = [r for r in records if r["level"] == lv]
        tagged = [r for r in group if TAG.search(r.get("tts") or "")]
        allowed = int(cfg.max_tagged_share.get(str(lv), 0.5) * len(group) + 0.999)
        if len(tagged) <= allowed:
            continue
        step = len(tagged) / max(allowed, 1)
        keep = {id(tagged[int(j * step)]) for j in range(allowed)}
        for r in tagged:
            if id(r) not in keep:
                r["tts"] = " ".join(TAG.sub(" ", r["tts"]).split())
                removed += 1
    return removed


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------


def _palette(cfg: VoicePerformanceConfig) -> str:
    return "\n".join(
        f"  level {lv} ({LEVEL_NAMES[lv]}): " + ", ".join(
            f"[{t}]" for t in cfg.level_tags.get(str(lv), []))
        for lv in range(4))


ARC_SYSTEM = prompt("documentary/voice_performance/arc_system")


def director_system(language: str, cfg: VoicePerformanceConfig) -> str:
    caps = (
        "- English only: you may write ONE word of a sentence in CAPITALS where a\n"
        "  storyteller would lean on it (never at level 0, only a few per beat)."
        if language in cfg.caps_languages else
        "- Never change letter case (no capitals for emphasis in this language)."
    )
    return prompt("documentary/voice_performance/director_system").format(palette=_palette(cfg), max_tags_per_sentence=cfg.max_tags_per_sentence, max_ellipses_per_sentence=cfg.max_ellipses_per_sentence, caps=caps, caps_note=" (and English emphasis capitals)" if language in cfg.caps_languages else "")


# ---------------------------------------------------------------------------
# director
# ---------------------------------------------------------------------------


def latest_performance(db: Session, version_id: int) -> VoicePerformance | None:
    return (db.query(VoicePerformance)
            .filter(VoicePerformance.story_version_id == version_id,
                    VoicePerformance.status != "invalid")
            .order_by(VoicePerformance.version.desc()).first())


def performance_records(row: VoicePerformance | None) -> dict[str, list[list[dict]]]:
    """{beat_id: paragraphs of sentence records} from a stored performance."""
    if row is None:
        return {}
    data = json.loads(row.performance_json or "{}")
    return {b["beat_id"]: b["paragraphs"] for b in data.get("beats") or []}


def _beat_sources(db: Session, version: StoryVersion, blueprint: dict) -> dict[str, str]:
    """English source text of every beat (what the meaning is checked
    against)."""
    try:
        from app.documentary.blueprint import version_sections
        from app.documentary.spoken import beat_sections

        struct = json.loads(version.narrative_structure or "{}")
        master = db.get(StoryVersion, struct.get("source_version_id") or version.master_version_id)
        if master is None:
            return {}
        from app.db.models import EditorialBlueprint

        row = db.get(EditorialBlueprint, struct.get("blueprint_id") or -1)
        bp = json.loads(row.blueprint_json or "{}") if row else blueprint
        return {s["id"]: s["text"] for s in beat_sections(version_sections(master), bp)}
    except Exception:  # meaning context is a help, never a blocker
        return {}


def _tag_counts(records: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        for m in TAG.finditer(r.get("tts") or ""):
            out[m.group(1)] = out.get(m.group(1), 0) + 1
    return dict(sorted(out.items(), key=lambda x: -x[1]))


def performance_dict(row: VoicePerformance) -> dict:
    return {
        "id": row.id, "story_version_id": row.story_version_id, "language": row.language,
        "version": row.version, "status": row.status, "model": row.generation_model,
        "performance": json.loads(row.performance_json or "{}"),
        "validation": json.loads(row.validation_json or "{}"),
        "created_at": row.created_at,
    }


def __getattr__(name: str):
    # the agent class lives in app/agents/voice_performance.py (imported when first asked for)
    if name == "VoicePerformanceDirector":
        from app.agents.voice_performance import VoicePerformanceDirector

        return VoicePerformanceDirector
    raise AttributeError(name)
