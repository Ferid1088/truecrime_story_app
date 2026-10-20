"""The recurring on-screen host Fereidoun (persona_master_prompt.md).

The narration tells the story; the host appears at a few chosen moments and talks to the viewer
like a close friend — never to retell the case.

Pipeline:
  1. host director (role host_director), once per film and for all languages: decides the
     STRUCTURE only — how many appearances, after which act, in which role (opening, transition,
     reflection, viewer question, outro). Seconds and knowledge cutoffs are computed from the
     story (host_planning); the director writes no content, so none can leak through the plan;
  2. host writer (role host_writer), per language and per appearance, in film order: the dialogue
     written natively — never translated — from the plan entry and ONLY the story up to the
     appearance's cutoff (host_firewall), the memories it may use and the host's earlier words;
  3. host critic (role host_critic, an independent model): the persona's quality check and the
     direct-address, register, hook and no-spoiler checks, on the same cutoff input. Failing
     segments are rewritten (host.max_repair_iterations), otherwise left out.
Deterministic guard-rails: placements and spacing, the length for the planned seconds, direct and
informal address per language, the opening's hook, the reveal check on the host's words, memories
only by reference to existing rows, stock phrases and repetition.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.story import build_evidence_pack, stored_sections
from app.core.ai_config import HostConfig, ai_config
from app.core.prompts import prompt
from app.db.models import (
    Case, Contradiction, EditorialBlueprint, Fact, HostMemory, HostPlan, HostSegments,
    Source, StoryVersion,
)
from app.documentary.blueprint import version_sections
from app.documentary.host_story import HostStory
from app.documentary.identity_line import with_identity

ROOT = Path(__file__).resolve().parents[2]

LANG_NAMES = {"en": "English", "de": "German", "fa": "Persian (Farsi)", "ar": "Arabic"}
MEMORY_KINDS = ("opinion", "reaction", "correction", "open_question", "theme")
DIMENSIONS = (
    "intellectual_humility", "empathy", "curiosity", "patience",
    "discomfort_with_weak_evidence", "sensitivity_to_victims_and_families",
    "respect_for_due_process", "questioning_assumptions", "holding_two_explanations",
    "awareness_of_bias", "emotional_honesty", "changing_an_opinion",
    "memory_of_previous_cases", "restrained_humor",
)
CHECKS = ("adds_value", "conversational", "native", "fresh", "shows_not_tells",
          "emotion_justified", "verified", "respectful", "short_enough",
          "direct_address", "informal_form", "friend_tone", "hook", "no_spoiler",
          "builds_on_story")


def persona_prompt(cfg: HostConfig | None = None) -> str:
    cfg = cfg or ai_config.host
    p = Path(cfg.persona_file)
    p = p if p.is_absolute() else ROOT / p
    return p.read_text(encoding="utf-8").strip()


def persona_sha256(cfg: HostConfig | None = None) -> str:
    """sha256 of the persona text exactly as it is inserted into the prompts."""
    return hashlib.sha256(persona_prompt(cfg).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# length follows time
# ---------------------------------------------------------------------------


def estimate_seconds(text: str, language: str) -> float:
    return round(len((text or "").split()) / ai_config.host.wpm(language) * 60, 1)


def target_words(seconds: float, language: str, cfg: HostConfig | None = None) -> int:
    cfg = cfg or ai_config.host
    return max(int(round(seconds * cfg.wpm(language) / 60)), 1)


def word_range(seconds: float, language: str, cfg: HostConfig | None = None) -> tuple[int, int]:
    """Words that fill `seconds` of host speech in `language`, within the word tolerance."""
    cfg = cfg or ai_config.host
    words = seconds * cfg.wpm(language) / 60
    return int(words * (1 - cfg.word_tolerance)), int(words * (1 + cfg.word_tolerance)) + 1


# ---------------------------------------------------------------------------
# context: archive, memory, recent segments
# ---------------------------------------------------------------------------


def archive(db: Session, case_id: int, cfg: HostConfig | None = None) -> list[dict]:
    """Previously covered cases: every other case with a usable blueprint
    (central question and thesis of its newest one)."""
    cfg = cfg or ai_config.host
    rows = (db.query(EditorialBlueprint)
            .filter(EditorialBlueprint.case_id != case_id,
                    EditorialBlueprint.status != "invalid")
            .order_by(EditorialBlueprint.id.desc()).all())
    out, seen = [], set()
    for bp in rows:
        if bp.case_id in seen:
            continue
        seen.add(bp.case_id)
        case = db.get(Case, bp.case_id)
        if case is None:
            continue
        out.append({"ref": f"A{case.id}", "case": case.canonical_title,
                    "summary": (case.summary or "")[:400] or None,
                    "central_question": bp.central_question,
                    "editorial_thesis": bp.editorial_thesis,
                    "human_thread": bp.human_thread})
        if len(out) >= cfg.archive_cases:
            break
    return out


def memories(db: Session, case_id: int, cfg: HostConfig | None = None) -> list[dict]:
    """Host memory from OTHER cases (this case's own notes are rewritten
    with every plan)."""
    cfg = cfg or ai_config.host
    rows = (db.query(HostMemory)
            .filter(HostMemory.case_id != case_id, HostMemory.active.is_(True))
            .order_by(HostMemory.id.desc()).limit(cfg.memory_items).all())
    titles: dict[int, str] = {}
    out = []
    for m in rows:
        if m.case_id not in titles:
            c = db.get(Case, m.case_id)
            titles[m.case_id] = c.canonical_title if c else f"case {m.case_id}"
        out.append({"ref": f"M{m.id}", "case": titles[m.case_id], "kind": m.kind,
                    "text": m.text})
    return out


def recent_segments(db: Session, case_id: int, language: str | None = None,
                    cfg: HostConfig | None = None) -> list[dict]:
    """The newest host segments of other cases (for anti-repetition);
    with a language, only segments in that language."""
    cfg = cfg or ai_config.host
    q = db.query(HostSegments).filter(HostSegments.case_id != case_id)
    if language:
        q = q.filter(HostSegments.language == language)
    out: list[dict] = []
    seen_cases: set[tuple[int, str]] = set()
    for row in q.order_by(HostSegments.id.desc()).limit(200):
        key = (row.case_id, row.language)
        if key in seen_cases:  # only the newest version per case/language
            continue
        seen_cases.add(key)
        for s in json.loads(row.segments_json or "[]"):
            if not s.get("avatar_dialogue"):
                continue
            out.append({"language": row.language, "position": s.get("position"),
                        "dimensions": s.get("personality_dimension"),
                        "dialogue": s["avatar_dialogue"]})
            if len(out) >= cfg.recent_segments:
                return out
    return out


def beat_texts(version: StoryVersion, blueprint: dict) -> dict[str, str]:
    """Narration per beat: a spoken version's sections ARE the beats; a
    master is cut by the blueprint's paragraph ranges."""
    from app.documentary.spoken import beat_sections

    secs = stored_sections(version) or []
    ids = [b["id"] for b in blueprint.get("beats") or []]
    if secs and [s["id"] for s in secs] == ids:
        return {s["id"]: s["text"] for s in secs}
    try:
        return {s["id"]: s["text"] for s in beat_sections(version_sections(version), blueprint)}
    except (KeyError, IndexError, ValueError):
        return {}


def _pack(db: Session, case_id: int) -> dict:
    return build_evidence_pack(
        db.query(Fact).filter(Fact.case_id == case_id).all(),
        db.query(Contradiction).filter(Contradiction.case_id == case_id).all(),
        db.query(Source).filter(Source.case_id == case_id).all())


def evidence_items(pack: dict) -> list[dict]:
    items = [{"id": f["id"], "claim": (f.get("claim") or "")[:300],
              "category": f.get("category"), "uncertain": bool(f.get("uncertain")),
              "speaker": f.get("speaker")} for f in pack.get("facts") or []]
    items += [{"id": t["id"], "date": t.get("event_date"), "claim": (t.get("claim") or "")[:200]}
              for t in pack.get("timeline") or []]
    items += [{"id": c["id"], "contradiction": (c.get("description") or "")[:300]}
              for c in pack.get("contradictions") or []]
    return items


def _memory_index(arch: list[dict], mem: list[dict]) -> dict[str, dict]:
    return {x["ref"]: x for x in [*arch, *mem]}


# ---------------------------------------------------------------------------
# 1. director: the structure of the appearances
# ---------------------------------------------------------------------------


def director_system_prompt(cfg: HostConfig | None = None,
                           narration_seconds: float | None = None) -> str:
    cfg = cfg or ai_config.host
    short = narration_seconds is not None and cfg.is_short_film(narration_seconds)
    gap = cfg.min_gap_for(narration_seconds) if narration_seconds is not None else cfg.min_gap_seconds
    least = cfg.min_appearances_for(narration_seconds) if narration_seconds is not None else cfg.min_appearances
    share = f"{int(cfg.max_total_share * 100)} % of the film" + (
        " (this is a short film: going over the share is only a warning; the appearances "
        "required above are still planned)" if short else "")
    roles = "\n".join(
        f'- "{name}" (position "{spec.position}"): {spec.min_seconds:g}–{spec.max_seconds:g} s'
        for name, spec in cfg.roles.items())
    return prompt("documentary/host/director_system_prompt").format(
        persona_prompt=persona_prompt(cfg), roles=roles, min_appearances=least,
        max_appearances=cfg.max_appearances, min_acts_between=cfg.min_acts_between,
        min_gap_seconds=int(gap), max_total_seconds=int(cfg.max_total_seconds),
        max_total_share=share)


def director_input(story: HostStory, narration_seconds: float) -> dict:
    """The film's structure for the director: acts with their purpose, weight and the
    questions they raise and answer — no narration text, no evidence."""
    return {
        "case": story.case_title,
        "film_seconds": round(narration_seconds),
        "acts": [{"id": a.id, "number": a.index, "title": a.title, "purpose": a.purpose,
                  "words": a.words,
                  "seconds": round(a.words / max(story.total_words(), 1) * narration_seconds),
                  "opens_questions": len(a.open_loops), "answers_questions": len(a.resolved_loops),
                  "is_hook": a.hook, "is_payoff": a.payoff} for a in story.acts],
    }


# ---------------------------------------------------------------------------
# 2. writer: the dialogue, natively per language, one appearance at a time
# ---------------------------------------------------------------------------


def writer_system_prompt(language: str, cfg: HostConfig | None = None) -> str:
    cfg = cfg or ai_config.host
    name = LANG_NAMES.get(language, language)
    channel = ai_config.channels.get(language)
    brand = (prompt("documentary/host/writer_system_prompt_2")
             .format(name_2=channel.name, name=name)) if channel else ""
    return prompt("documentary/host/writer_system_prompt").format(
        persona_prompt=persona_prompt(cfg), name=name, brand=brand,
        opening_first_sentence=cfg.opening_max_first_sentence_words,
        dimensions=", ".join(DIMENSIONS))


def writer_input(story: HostStory, seg: dict, language: str, arch_mem: dict[str, dict],
                 recent: list[dict], earlier: list[str], cfg: HostConfig | None = None) -> dict:
    """Everything the writer of ONE appearance gets: its plan entry, the story up to its
    cutoff (host_firewall), the memories, the host's earlier words in this film and recent
    episodes. Nothing after the cutoff."""
    from app.documentary.host_firewall import segment_context

    cfg = cfg or ai_config.host
    lo, hi = word_range(seg["target_seconds"], language, cfg)
    return {
        "language": language,
        "segment": {"id": seg["id"], "position": seg["position"], "role": seg["role"],
                    "after_act": seg["beat_id"], "acts_heard": seg["cutoff"],
                    "target_seconds": seg["target_seconds"],
                    "target_words": target_words(seg["target_seconds"], language, cfg),
                    "allowed_words": [lo, hi],
                    "requires_question": cfg.roles[seg["role"]].requires_question},
        **segment_context(story, seg, cfg),
        "memories": list(arch_mem.values()),
        "earlier_in_this_film": earlier,
        "recent_segments": [r["dialogue"] for r in recent],
        "avoid_phrases": cfg.language_rules[language].stock_phrases,
    }


def parse_writer(raw: dict) -> dict:
    """The writer's answer: dialogue, the memory it used, and its English notes."""
    raw = raw if isinstance(raw, dict) else {}
    dims = [d for d in raw.get("dimensions") or [] if d in DIMENSIONS][:2]
    note = raw.get("memory_note") if isinstance(raw.get("memory_note"), dict) else None
    if note and (note.get("kind") not in MEMORY_KINDS or not str(note.get("text") or "").strip()):
        note = None
    ref = str(raw.get("memory_used") or "").strip() or None
    return {"dialogue": str(raw.get("dialogue") or "").strip().strip('"“”«»'),
            "memory_used": ref, "dimensions": dims,
            "delivery": str(raw.get("delivery") or "")[:300],
            "memory_note": {"kind": note["kind"], "text": str(note["text"]).strip()[:500]}
            if note else None}


# What the writer is told to do about each deterministic finding (the finding itself stays
# machine-readable; the fix is the sentence the writer reads).
ISSUE_FIXES = {
    "no_direct_address": "Speak to the viewer directly, informally, with the address form of the "
                         "language (several sentences must contain it, e.g. a question to them).",
    "formal_address": "Replace the formal address with the informal one, as a friend would say it.",
    "no_question_to_the_viewer": "End a sentence with a real question to the viewer.",
    "greeting_filler": "Start without a greeting or welcome: the first sentence grabs.",
    "handover_line": "Do not end with a line that hands over to the story (\"now let's go to the "
                     "story\", \"let's begin\"): end on your last thought or question; the app adds "
                     "the closing identity line itself.",
    "first_sentence_too_long": "Make the first sentence short, one breath.",
    "stock_phrase": "Drop this stock phrase and say it in your own words.",
    "no_short_sentences": "Add one or two very short sentences of at most {short} words (a "
                          "reaction, a pause, a doubt) between the longer ones, the way people "
                          "talk. Count the words.",
    "flat_rhythm": "The sentences are about the same length. Make some clearly longer and some "
                   "clearly shorter (at most {short} words): short, long, a sentence that breaks "
                   "off.",
    "same_sentence_opening": "Two sentences in a row start alike: start them differently.",
    "too_short": "Add one more thought from the acts you were given.",
    "too_long": "Cut the least important sentences.",
}

_WORD = re.compile(r"\w+", re.UNICODE)
_META = re.compile(r"\[|\]|\(pause\)|\bsegment\s*\w*\d|https?://|www\.|^\s*(S\d+|Host)\s*:",
                   re.IGNORECASE | re.MULTILINE)
_EPISODE_META = re.compile(r"\b(case|episode|folge|fall)\s*(id|#|nr\.?)?\s*\d+", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?؟…])\s+")


def _trigrams(text: str) -> set[tuple[str, ...]]:
    w = [x.lower() for x in _WORD.findall(text or "")]
    return {tuple(w[i:i + 3]) for i in range(len(w) - 2)}


def _first_words(text: str, n: int = 3) -> tuple[str, ...]:
    return tuple(x.lower() for x in _WORD.findall(text or "")[:n])


def spoken_issues(sentences: list[str], cfg: HostConfig) -> list[dict]:
    """Spoken, not written: sentences of varied length with some very short ones, and no two
    sentences in a row that start alike."""
    rules = cfg.spoken
    if len(sentences) < rules.min_sentences:
        return []
    lengths = [len(s.split()) for s in sentences]
    issues: list[dict] = []
    if sum(1 for n in lengths if n <= rules.short_sentence_words) < rules.min_short_sentences:
        issues.append({"type": "no_short_sentences", "shortest": min(lengths)})
    mean = sum(lengths) / len(lengths)
    spread = (sum((n - mean) ** 2 for n in lengths) / len(lengths)) ** 0.5
    if mean and spread / mean < rules.min_length_variation:
        issues.append({"type": "flat_rhythm", "variation": round(spread / mean, 2)})
    for before, after in zip(sentences, sentences[1:]):
        if _first_words(before, 2) == _first_words(after, 2):
            issues.append({"type": "same_sentence_opening", "quote": after[:60]})
            break
    return issues


def dialogue_issues(seg: dict, dialogue: str, language: str, recent: list[dict],
                    others: list[str], cfg: HostConfig | None = None) -> list[dict]:
    """Deterministic checks of one segment's dialogue."""
    cfg = cfg or ai_config.host
    rules = cfg.language_rules[language]
    issues: list[dict] = []
    text = (dialogue or "").strip()
    if not text:
        return [{"type": "empty"}]
    words = len(text.split())
    lo, hi = word_range(seg["target_seconds"], language, cfg)
    if words < lo:
        issues.append({"type": "too_short", "words": words, "min": lo})
    elif words > hi:
        issues.append({"type": "too_long", "words": words, "max": hi})
    for pat in rules.stock_phrases:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            issues.append({"type": "stock_phrase", "quote": m.group(0)})
    if not any(re.search(p, text, re.IGNORECASE) for p in rules.address_patterns):
        issues.append({"type": "no_direct_address"})
    for pat in rules.formal_patterns:
        m = re.search(pat, text)
        if m:
            issues.append({"type": "formal_address", "quote": m.group(0)})
    sentences = [s for s in _SENTENCE_END.split(text) if s.strip()]
    if seg["position"] == "opening" and sentences and any(
            re.search(p, sentences[-1], re.IGNORECASE) for p in rules.handover_patterns):
        issues.append({"type": "handover_line", "quote": sentences[-1]})
    if cfg.roles[seg["role"]].requires_question and not any(
            s.strip().endswith(tuple(rules.question_marks)) for s in sentences):
        issues.append({"type": "no_question_to_the_viewer"})
    if seg["position"] == "opening":
        for pat in rules.greeting_patterns:
            if re.search(pat, text, re.IGNORECASE):
                issues.append({"type": "greeting_filler"})
                break
        first = len(sentences[0].split()) if sentences else 0
        if first > cfg.opening_max_first_sentence_words:
            issues.append({"type": "first_sentence_too_long", "words": first,
                           "max": cfg.opening_max_first_sentence_words})
    issues += spoken_issues(sentences, cfg)
    if _META.search(text):
        issues.append({"type": "not_plain_speech"})
    if _EPISODE_META.search(text):
        issues.append({"type": "metadata_callback", "quote": _EPISODE_META.search(text).group(0)})
    mine = _trigrams(text)
    for r in recent:
        theirs = _trigrams(r["dialogue"])
        if mine and theirs and len(mine & theirs) / len(mine) > cfg.max_trigram_overlap:
            issues.append({"type": "repeats_recent_segment", "quote": r["dialogue"][:120]})
            break
    start = _first_words(text)
    if seg["position"] == "opening" and any(
            r.get("position") == "opening" and _first_words(r["dialogue"]) == start
            for r in recent):
        issues.append({"type": "repeated_opening", "quote": " ".join(start)})
    if any(_first_words(o, 2) == _first_words(text, 2) for o in others):
        issues.append({"type": "same_start_as_other_segment"})
    for o in others:
        theirs = _trigrams(o)
        if mine and theirs and len(mine & theirs) / len(mine) > cfg.max_trigram_overlap:
            issues.append({"type": "repeats_another_segment", "quote": o[:120]})
            break
    return [{**i, "fix": ISSUE_FIXES[i["type"]].format(short=cfg.spoken.short_sentence_words)}
            if i["type"] in ISSUE_FIXES else i for i in issues]


# ---------------------------------------------------------------------------
# 3. critic: the persona's quality check, address, hook and spoilers
# ---------------------------------------------------------------------------


def critic_system_prompt(language: str, cfg: HostConfig | None = None) -> str:
    """The critic judges against the persona itself (its Never list, Evidence
    Discipline, Memory and Anti-Repetition rules and its own Quality Check),
    inserted whole after its instructions."""
    name = LANG_NAMES.get(language, language)
    return (prompt("documentary/host/critic_system_prompt").format(name=name)
            + "\n\nTHE PERSONA YOU JUDGE AGAINST (the host must follow all of it; its Quality "
              "Check list is your checklist):\n\n" + persona_prompt(cfg))


def critic_input(wi: dict, dialogue: str) -> dict:
    """The writer's input (the cutoff context included) plus the dialogue to judge."""
    return {**wi, "dialogue": dialogue}


def meaning_input(wi: dict, dialogue: str) -> dict:
    """What the host meaning check gets: the source (the story up to the cutoff and its facts,
    the case title and the open questions) and what the host says."""
    parts = [f"CASE: {wi['case']['title']}"]
    parts += [f"[{a['act']}] {a['narration']}" for a in wi["story_so_far"]]
    parts += [f"FACT {f['id']}: {f.get('claim') or f.get('contradiction') or ''}"
              for f in wi["facts"]]
    parts += [f"OPEN QUESTION: {q}" for q in wi["open_questions"]]
    parts += [f"MEMORY {m['ref']}: {m.get('text') or m.get('central_question') or ''}"
              for m in wi["memories"]]
    return {"spoken_language": wi["language"],
            "segments": [{"segment_id": wi["segment"]["id"], "source": "\n\n".join(parts),
                          "spoken": dialogue}]}


def meaning_problems(raw: dict, segment_id: str) -> list[dict]:
    """The checker's items for one segment as writer problems; a segment it does not return
    counts as unchecked and fails."""
    for entry in (raw or {}).get("segments") or []:
        if isinstance(entry, dict) and str(entry.get("segment_id")) == segment_id:
            return [{"check": f"meaning_{kind}", "quote": str(item)[:300],
                     "fix": "Say only what the source says: remove or correct this."}
                    for kind in ("added", "changed", "certainty")
                    for item in entry.get(kind) or [] if item]
    return [{"check": "meaning_unchecked", "quote": "",
             "fix": "The fact check returned nothing for this segment."}]


def critic_verdict(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    checks = raw.get("checks") if isinstance(raw.get("checks"), dict) else {}
    checks = {k: bool(checks.get(k, True)) for k in CHECKS}
    problems = [p for p in raw.get("problems") or [] if isinstance(p, dict)]
    return {"checks": checks, "problems": problems[:8], "pass": all(checks.values())}


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def _placement_text(seg: dict, story: HostStory) -> str:
    if seg["position"] == "opening":
        first = story.acts[0].title if story.acts else ""
        return f"Opening — before {first}" if first else "Opening"
    act = story.act(seg["beat_id"])
    title = f" ({act.title})" if act else ""
    return ("Outro — after" if seg["position"] == "final" else "After") + f" {seg['beat_id']}{title}"


def segment_output(seg: dict, written: dict, language: str, story: HostStory,
                   verdict: dict | None, arch_mem: dict[str, dict]) -> dict:
    """One segment in the persona's output format (plus what later stages need: position, act,
    cutoff, planned and estimated seconds, the quality check)."""
    dialogue = written.get("dialogue") or ""
    if seg["position"] == "opening" and dialogue:
        dialogue = with_identity(dialogue, language)
    ref = written.get("memory_used")
    memory = "none"
    if ref and ref in arch_mem:
        m = arch_mem[ref]
        memory = f"{ref} — {m.get('case')}: {m.get('text') or m.get('central_question') or ''}"
    verdict = verdict or {"checks": {}, "problems": [], "deterministic": [],
                          "pass": bool(dialogue)}
    return {
        "segment_id": seg["id"],
        "placement": _placement_text(seg, story),
        "position": seg["position"], "role": seg["role"], "beat_id": seg["beat_id"],
        "cutoff": seg["cutoff"],
        "purpose": seg["reason"],
        "personality_dimension": written.get("dimensions") or [],
        "memory_reference": memory,
        "delivery_direction": written.get("delivery") or "",
        "avatar_dialogue": dialogue,
        "target_duration": seg["target_seconds"],
        "target_seconds": seg["target_seconds"],
        "estimated_seconds": estimate_seconds(dialogue, language),
        "quality": {"pass": bool(verdict.get("pass")) and bool(dialogue),
                    "checks": verdict.get("checks", {}),
                    "problems": verdict.get("problems", []),
                    "deterministic": verdict.get("deterministic", [])},
    }


def remember(db: Session, case_id: int, plan_row: HostPlan, updates: list[dict]) -> int:
    """Replace what earlier plans of this case made the host remember
    (editor-made memories stay)."""
    # Delete mapped rows through the session. A bulk delete with
    # synchronize_session=False leaves an old HostMemory object in the
    # identity map; SQLite can then reuse its id for the replacement and
    # SQLAlchemy warns that two instances have the same identity.
    old = (db.query(HostMemory)
           .filter(HostMemory.case_id == case_id, HostMemory.origin == "host_plan")
           .all())
    for row in old:
        db.delete(row)
    db.flush()
    for u in updates:
        db.add(HostMemory(case_id=case_id, kind=u["kind"], text=u["text"],
                          origin="host_plan", host_plan_id=plan_row.id))
    db.commit()
    return len(updates)


def latest_host_plan(db: Session, blueprint_id: int) -> HostPlan | None:
    return (db.query(HostPlan)
            .filter(HostPlan.blueprint_id == blueprint_id, HostPlan.status != "invalid")
            .order_by(HostPlan.version.desc()).first())


def latest_story_host_plan(db: Session, story_plan_id: int) -> HostPlan | None:
    """The newest usable host plan of a story plan (a story without a blueprint)."""
    return (db.query(HostPlan)
            .filter(HostPlan.story_plan_id == story_plan_id, HostPlan.status != "invalid")
            .order_by(HostPlan.version.desc()).first())


def latest_host_segments(db: Session, version_id: int) -> HostSegments | None:
    return (db.query(HostSegments).filter(HostSegments.story_version_id == version_id)
            .order_by(HostSegments.version.desc()).first())


def persona_state(row) -> dict:
    """Which persona a result was made under, and whether the file changed
    since: unknown (made before the hash was stored), current or changed."""
    stored = getattr(row, "persona_sha256", None)
    current = persona_sha256()
    return {"persona_file": getattr(row, "persona_file", None), "persona_sha256": stored,
            "persona_state": "unknown persona" if not stored
            else ("current" if stored == current else "persona changed")}


def host_plan_dict(row: HostPlan) -> dict:
    return {**persona_state(row), "id": row.id, "case_id": row.case_id, "blueprint_id": row.blueprint_id,
            "story_plan_id": row.story_plan_id,
            "version": row.version, "status": row.status,
            "generation_model": row.generation_model, "created_at": row.created_at,
            "plan": json.loads(row.plan_json or "{}"),
            "validation": json.loads(row.validation_json or "{}")}


def host_segments_dict(row: HostSegments) -> dict:
    return {**persona_state(row), "id": row.id, "case_id": row.case_id, "story_version_id": row.story_version_id,
            "host_plan_id": row.host_plan_id, "language": row.language,
            "version": row.version, "status": row.status,
            "generation_model": row.generation_model, "created_at": row.created_at,
            "segments": json.loads(row.segments_json or "[]"),
            "validation": json.loads(row.validation_json or "{}")}


def memory_usage(db: Session) -> dict[str, list[dict]]:
    """Which host-plan segments used which memory (by its M<id> reference)."""
    used: dict[str, list[dict]] = {}
    for row in db.query(HostSegments).all():
        for seg in json.loads(row.segments_json or "[]"):
            ref = str(seg.get("memory_reference") or "").split(" ")[0]
            if ref and ref != "none":
                used.setdefault(ref, []).append(
                    {"plan_id": row.host_plan_id, "case_id": row.case_id,
                     "segment_id": seg.get("segment_id")})
    return used


def memory_dict(m: HostMemory, usage: dict[str, list[dict]] | None = None) -> dict:
    return {"used_by": (usage or {}).get(f"M{m.id}", []), "id": m.id, "ref": f"M{m.id}", "case_id": m.case_id, "kind": m.kind,
            "text": m.text, "origin": m.origin, "host_plan_id": m.host_plan_id,
            "active": m.active, "created_at": m.created_at}


def __getattr__(name: str):
    # the agent class lives in app/agents/host.py (imported when first asked for)
    if name == "HostDirector":
        from app.agents.host import HostDirector

        return HostDirector
    raise AttributeError(name)
