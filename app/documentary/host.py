"""The recurring on-screen host (persona_master_prompt.md).

The narration tells the story; the host appears at a few chosen moments
to add perspective, clarity and continuity — never to retell the case.

Pipeline:
  1. host director (role host_director), once per blueprint and
     language-independent: decides IF and WHERE the host appears
     (opening / after a beat / final), why, which one or two personality
     dimensions show, the verified memory used (archive of covered cases
     or host memory — nothing else), the delivery, the claims the host
     will make (each with its kind of information and evidence ids) and
     what the host now remembers about this case (memory updates);
  2. host writer (role host_writer), per language: the dialogue written
     natively — never translated — from the plan and that language's own
     narration around the placement;
  3. host critic (role host_critic, an independent model): the quality
     check of the persona prompt per segment (adds value, conversational,
     native, fresh, shows not tells, emotion justified, verified,
     respectful, short) plus facts, spoilers and memories; failing
     segments are rewritten (host.max_repair_iterations).
Deterministic guard-rails: placements and spacing, reveal firewall (the
host never mentions what a later beat reveals), memories only by
reference to existing rows, durations per placement, host share of the
film, stock phrases and repetition of recent episodes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.story import build_evidence_pack, stored_sections
from app.core.ai_config import HostConfig, ai_config
from app.db.models import (
    Case, Contradiction, EditorialBlueprint, Fact, HostMemory, HostPlan, HostSegments,
    Source, StoryVersion,
)
from app.documentary.blueprint import version_sections
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

ROOT = Path(__file__).resolve().parents[2]

LANG_NAMES = {"en": "English", "de": "German", "fa": "Persian (Farsi)", "ar": "Arabic"}
POSITIONS = ("opening", "mid", "final")
CLAIM_KINDS = (
    "confirmed_fact", "witness_statement", "official_finding", "media_report",
    "disputed_claim", "expert_interpretation", "speculation", "personal_reaction",
)
MEMORY_KINDS = ("opinion", "reaction", "correction", "open_question", "theme")
DIMENSIONS = (
    "intellectual_humility", "empathy", "curiosity", "patience",
    "discomfort_with_weak_evidence", "sensitivity_to_victims_and_families",
    "respect_for_due_process", "questioning_assumptions", "holding_two_explanations",
    "awareness_of_bias", "emotional_honesty", "changing_an_opinion",
    "memory_of_previous_cases", "restrained_humor",
)
# Beats the host must not interrupt right after (momentum) or right
# before (the payoff belongs to the narration).
NO_SEGMENT_AFTER = {"hook"}
NO_SEGMENT_BEFORE = {"reveal"}
CHECKS = ("adds_value", "conversational", "native", "fresh", "shows_not_tells",
          "emotion_justified", "verified", "respectful", "short_enough")


def persona_prompt(cfg: HostConfig | None = None) -> str:
    cfg = cfg or ai_config.host
    p = Path(cfg.persona_file)
    p = p if p.is_absolute() else ROOT / p
    return p.read_text(encoding="utf-8").strip()


def host_wpm(language: str, cfg: HostConfig | None = None) -> float:
    cfg = cfg or ai_config.host
    return ai_config.documentary.wpm(language) * cfg.pace_factor


def estimate_seconds(text: str, language: str) -> float:
    return round(len((text or "").split()) / host_wpm(language) * 60, 1)


def word_range(position: str, language: str, cfg: HostConfig | None = None) -> tuple[int, int]:
    cfg = cfg or ai_config.host
    lo, hi = cfg.seconds[position]
    wpm = host_wpm(language, cfg)
    return int(lo * wpm / 60), int(hi * wpm / 60) + 1


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
                        "pattern": s.get("pattern"),
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


def _evidence_items(pack: dict) -> list[dict]:
    items = [{"id": f["id"], "claim": (f.get("claim") or "")[:300],
              "category": f.get("category"), "uncertain": bool(f.get("uncertain")),
              "speaker": f.get("speaker")} for f in pack.get("facts") or []]
    items += [{"id": t["id"], "date": t.get("event_date"), "claim": (t.get("claim") or "")[:200]}
              for t in pack.get("timeline") or []]
    items += [{"id": c["id"], "contradiction": (c.get("description") or "")[:300]}
              for c in pack.get("contradictions") or []]
    return items


# ---------------------------------------------------------------------------
# 1. director: when and why the host appears
# ---------------------------------------------------------------------------


def director_system_prompt(cfg: HostConfig | None = None) -> str:
    cfg = cfg or ai_config.host
    sec = {k: f"{v[0]:g}–{v[1]:g} s" for k, v in cfg.seconds.items()}
    return f"""{persona_prompt(cfg)}

---

# Your task now: the host plan for this episode (steps 1–5 of the Procedure)

You receive the complete verified narration beat by beat (the editorial
blueprint: purpose, reveals, listener questions, emotional load), the
evidence list (F… facts, T… timeline, C… contradictions), the ARCHIVE of
previously covered cases (refs A…), HOST MEMORY (refs M…) and the host's
RECENT SEGMENTS from earlier episodes. Do not write dialogue yet; decide
where the host appears and what each appearance must do. The dialogue is
written later, natively in every language, from your plan.

Placement:
- position "opening" ({sec['opening']}): before the first beat. At most one.
- position "mid" ({sec['mid']}): after a beat ("beat_id"). At most
  {cfg.max_mid_segments}, usually one or two, only at a meaningful moment.
  Never right after a hook beat, never right before a reveal beat (the
  payoff belongs to the narration), at least {cfg.min_beats_between} beats
  between two appearances.
- position "final" ({sec['final']}): after the last beat, optional.
- Fewer is better than forced. An appearance that only repeats the
  narration is worse than none.

Reveal firewall: at its placement the host knows only what the viewer has
heard so far. Never use evidence a LATER beat reveals (the opening may
only tease what the first beat reveals).

Memory: "memory_reference" is the ref (A… or M…) of a GENUINE, specific
connection, or null. No ref, no memory — never invent one; a weak
similarity is left out.

Claims: list every factual statement the host will make with its kind
({" | ".join(CLAIM_KINDS)}) and the evidence ids that support it.
Speculation and personal reactions are allowed only labelled as such.

Variety: look at the recent segments' patterns and dimensions and choose
differently (another opening pattern, other dimensions).

memory_updates: what the host will remember about THIS case for later
episodes, in English — opinions taken, reactions, corrections of an
earlier reading, questions left open, recurring themes ({" | ".join(MEMORY_KINDS)}).
Only what the plan actually expresses and the evidence supports; 0–6 items.

Return JSON only:
{{"notes": "one or two sentences on the host's role in this episode",
  "segments": [{{"id": "S1", "position": "opening", "beat_id": null,
    "pattern": "a detail to remember | competing accounts | ... (short label)",
    "purpose": "why the host appears here",
    "dimensions": ["curiosity"],
    "memory_reference": null,
    "memory_connection": null,
    "delivery": "direct and conversational; slightly faster than narration; ...",
    "intent": "what the host says and does here, in English notes (not dialogue)",
    "claims": [{{"text": "...", "kind": "confirmed_fact", "evidence_ids": ["F003"]}}],
    "target_seconds": 25,
    "transition_back": "how the segment hands back to the narration"}}],
  "memory_updates": [{{"kind": "open_question", "text": "...", "segment_id": "S1"}}]}}
Dimensions come from: {" | ".join(DIMENSIONS)}.
"""


def director_input(case: Case, blueprint: dict, texts: dict[str, str], pack: dict,
                   arch: list[dict], mem: list[dict], recent: list[dict]) -> dict:
    return {
        "case": case.canonical_title,
        "central_question": blueprint.get("central_question"),
        "editorial_thesis": blueprint.get("editorial_thesis"),
        "human_thread": blueprint.get("human_thread"),
        "questions": blueprint.get("questions"),
        "beats": [
            {"beat_id": b["id"], "purpose": b.get("purpose"), "summary": b.get("summary"),
             "reveals": b.get("reveals") or [], "opens": b.get("opens") or [],
             "answers": b.get("answers") or [], "unresolved": b.get("unresolved") or [],
             "human_focus": b.get("human_focus"), "emotional_load": b.get("emotional_load"),
             "mystery_intensity": b.get("mystery_intensity"),
             "narration": texts.get(b["id"], "")}
            for b in blueprint.get("beats") or []
        ],
        "evidence": _evidence_items(pack),
        "archive": arch,
        "host_memory": mem,
        "recent_segments": recent,
    }


def _reveal_index(blueprint: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, b in enumerate(blueprint.get("beats") or []):
        for eid in b.get("reveals") or []:
            out.setdefault(str(eid), i)
    return out


def validate_host_plan(raw: dict, blueprint: dict, evidence_ids: set[str],
                       memory_refs: set[str], narration_seconds: float,
                       cfg: HostConfig | None = None) -> tuple[dict, dict]:
    """Deterministic checks of the director's plan. Errors are sent back
    for repair; adjustments (clamped durations) are logged."""
    cfg = cfg or ai_config.host
    raw = raw if isinstance(raw, dict) else {}
    beats = blueprint.get("beats") or []
    index = {b["id"]: i for i, b in enumerate(beats)}
    revealed_at = _reveal_index(blueprint)
    errors: list[dict] = []
    warnings: list[dict] = []
    adjustments: list[dict] = []
    segments: list[dict] = []

    for n, s in enumerate(raw.get("segments") or [], 1):
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or f"S{n}")
        pos = s.get("position")
        bid = s.get("beat_id")
        if pos not in POSITIONS:
            errors.append({"code": "unknown_position", "segment": sid, "value": pos})
            continue
        if pos == "opening":
            at = -1
            bid = None
        elif pos == "final":
            at = len(beats) - 1
            bid = beats[-1]["id"] if beats else None
        else:
            if bid not in index:
                errors.append({"code": "unknown_beat", "segment": sid, "value": bid})
                continue
            at = index[bid]
            if at == len(beats) - 1:
                errors.append({"code": "mid_after_last_beat_use_final", "segment": sid})
            if beats[at].get("purpose") in NO_SEGMENT_AFTER:
                errors.append({"code": "interrupts_hook", "segment": sid, "beat": bid})
            if at + 1 < len(beats) and beats[at + 1].get("purpose") in NO_SEGMENT_BEFORE:
                errors.append({"code": "interrupts_before_reveal", "segment": sid,
                               "beat": beats[at + 1]["id"]})
        dims = [d for d in (s.get("dimensions") or []) if d in DIMENSIONS]
        if not dims:
            warnings.append({"code": "no_dimension", "segment": sid})
        if len(dims) > 2:
            errors.append({"code": "too_many_dimensions", "segment": sid})
        ref = s.get("memory_reference")
        ref = str(ref).strip() if ref else None
        if ref and ref not in memory_refs:
            errors.append({"code": "unverified_memory", "segment": sid, "ref": ref})
        claims = []
        for c in s.get("claims") or []:
            if not isinstance(c, dict) or not str(c.get("text") or "").strip():
                continue
            kind = c.get("kind")
            if kind not in CLAIM_KINDS:
                errors.append({"code": "unknown_claim_kind", "segment": sid, "value": kind})
            ids = [str(x) for x in c.get("evidence_ids") or []]
            for eid in ids:
                if eid not in evidence_ids:
                    errors.append({"code": "unknown_evidence", "segment": sid, "id": eid})
                elif eid in revealed_at and revealed_at[eid] > max(at, 0):
                    errors.append({"code": "spoils_reveal", "segment": sid, "id": eid,
                                   "revealed_in": beats[revealed_at[eid]]["id"]})
                elif eid not in revealed_at:
                    warnings.append({"code": "evidence_not_narrated", "segment": sid, "id": eid})
            if kind not in ("speculation", "personal_reaction") and not ids:
                errors.append({"code": "claim_without_evidence", "segment": sid,
                               "claim": str(c["text"])[:120]})
            claims.append({"text": str(c["text"])[:400], "kind": kind, "evidence_ids": ids})
        lo, hi = cfg.seconds[pos]
        try:
            t = float(s.get("target_seconds"))
        except (TypeError, ValueError):
            t = (lo + hi) / 2
        clamped = min(max(t, lo), hi)
        if abs(clamped - t) > 1e-6:
            adjustments.append({"segment": sid, "target_seconds": [t, clamped]})
        if not str(s.get("intent") or "").strip():
            errors.append({"code": "no_intent", "segment": sid})
        segments.append({
            "id": sid, "position": pos, "beat_id": bid, "at": at,
            "pattern": str(s.get("pattern") or "")[:80],
            "purpose": str(s.get("purpose") or "")[:400],
            "dimensions": dims[:2], "memory_reference": ref if ref in memory_refs else None,
            "memory_connection": (str(s.get("memory_connection") or "")[:300] or None)
            if ref in memory_refs else None,
            "delivery": str(s.get("delivery") or "")[:300],
            "intent": str(s.get("intent") or "")[:1200],
            "claims": claims, "target_seconds": round(clamped, 1),
            "transition_back": str(s.get("transition_back") or "")[:300],
        })

    segments.sort(key=lambda x: x["at"])
    counts = {p: sum(1 for s in segments if s["position"] == p) for p in POSITIONS}
    if counts["opening"] > 1 or counts["final"] > 1:
        errors.append({"code": "more_than_one_opening_or_final", "counts": counts})
    if counts["mid"] > cfg.max_mid_segments:
        errors.append({"code": "too_many_mid_segments", "count": counts["mid"],
                       "max": cfg.max_mid_segments})
    ids = [s["id"] for s in segments]
    if len(set(ids)) != len(ids):
        errors.append({"code": "duplicate_segment_ids"})
    for a, b in zip(segments, segments[1:]):
        # opening sits at -1, a mid/final segment after beat i at i: the
        # difference is the number of narrated beats between the two
        gap = b["at"] - a["at"]
        if gap < cfg.min_beats_between:
            errors.append({"code": "appearances_too_close", "segments": [a["id"], b["id"]],
                           "beats_between": gap})
    if not segments:
        warnings.append({"code": "no_appearances"})

    host_seconds = sum(s["target_seconds"] for s in segments)
    share = host_seconds / max(narration_seconds, 1.0)
    if share > cfg.max_total_share:
        # short pilots cannot hold an opening within the share; only a
        # real film is held to it
        (errors if narration_seconds >= 600 else warnings).append(
            {"code": "host_share_too_high", "share": round(share, 3),
             "max": cfg.max_total_share})

    updates = []
    for u in raw.get("memory_updates") or []:
        if not isinstance(u, dict) or u.get("kind") not in MEMORY_KINDS:
            continue
        text = str(u.get("text") or "").strip()
        if text:
            updates.append({"kind": u["kind"], "text": text[:500],
                            "segment_id": u.get("segment_id") if u.get("segment_id") in ids
                            else None})
    if len(updates) > 6:
        warnings.append({"code": "memory_updates_trimmed", "count": len(updates)})
        updates = updates[:6]

    for s in segments:
        s.pop("at", None)
    status = "invalid" if errors else ("needs_review" if warnings else "valid")
    plan = {"notes": str(raw.get("notes") or "")[:600], "segments": segments,
            "memory_updates": updates}
    report = {"status": status, "errors": errors, "warnings": warnings,
              "adjustments": adjustments, "segments": len(segments), "counts": counts,
              "host_seconds": round(host_seconds, 1),
              "narration_seconds": round(narration_seconds, 1),
              "host_share": round(share, 4)}
    return plan, report


# ---------------------------------------------------------------------------
# 2. writer: the dialogue, natively per language
# ---------------------------------------------------------------------------


def writer_system_prompt(language: str, cfg: HostConfig | None = None) -> str:
    cfg = cfg or ai_config.host
    name = LANG_NAMES.get(language, language)
    ranges = ", ".join(f"{p} {lo}–{hi} words"
                       for p in POSITIONS for lo, hi in [word_range(p, language, cfg)])
    return f"""{persona_prompt(cfg)}

---

# Your task now: write the host's dialogue in {name} (step 6 of the Procedure)

The host plan is decided. For each segment you receive the plan (purpose,
dimensions, verified memory, delivery, intent, claims with evidence) and
the {name} narration the viewer hears right before and right after it.

Write the Avatar Dialogue directly in {name}, the way a thoughtful native
{name} speaker talks to one viewer — not a translation of the English
notes, not the narrator's voice. Shorter natural sentences, spoken
rhythm, natural emphasis. Persian: the spoken standard of a calm,
educated presenter, not officialese, not street slang. Arabic: clear
modern spoken fusha of a documentary presenter. German: natural spoken
German, Sie-form towards the viewer.

Rules:
- Say only what the plan's claims allow, with the same certainty; label
  speculation and personal reactions as such. No new facts, names,
  numbers, motives or memories.
- A memory callback only when the plan gives one, as a human association,
  not metadata.
- Do not repeat the narration the viewer just heard; reframe it.
- Never reuse the wording, opening or rhythm of the recent segments.
- Length per position: {ranges}. Hit the segment's target_seconds.
- Plain spoken text only: no stage directions, brackets, quotation marks
  around the whole text, headings or notes.

Return JSON only:
{{"segments": [{{"id": "S1", "dialogue": "..."}}]}}
"""


def writer_input(plan: dict, texts: dict[str, str], beat_ids: list[str], language: str,
                 pack_items: dict[str, dict], arch_mem: dict[str, dict],
                 recent: list[dict], cfg: HostConfig | None = None) -> dict:
    cfg = cfg or ai_config.host

    def around(seg: dict) -> dict:
        if seg["position"] == "opening":
            before, after = None, beat_ids[0] if beat_ids else None
        elif seg["position"] == "final":
            before, after = beat_ids[-1] if beat_ids else None, None
        else:
            i = beat_ids.index(seg["beat_id"])
            before = seg["beat_id"]
            after = beat_ids[i + 1] if i + 1 < len(beat_ids) else None
        tail = " ".join((texts.get(before) or "").split()[-160:]) if before else None
        head = " ".join((texts.get(after) or "").split()[:90]) if after else None
        return {"narration_before": tail, "narration_after": head}

    out = []
    for s in plan.get("segments") or []:
        lo, hi = word_range(s["position"], language, cfg)
        target = int(s["target_seconds"] * host_wpm(language, cfg) / 60)
        ids = {e for c in s["claims"] for e in c["evidence_ids"]}
        out.append({
            "id": s["id"], "position": s["position"], "after_beat": s["beat_id"],
            "purpose": s["purpose"], "pattern": s["pattern"], "dimensions": s["dimensions"],
            "memory": arch_mem.get(s["memory_reference"]) if s["memory_reference"] else None,
            "memory_connection": s["memory_connection"], "delivery": s["delivery"],
            "intent": s["intent"], "claims": s["claims"],
            "evidence": [pack_items[e] for e in sorted(ids) if e in pack_items],
            "target_seconds": s["target_seconds"],
            "target_words": max(lo, min(hi, target)), "allowed_words": [lo, hi],
            "transition_back": s["transition_back"], **around(s),
        })
    return {"language": language, "segments": out,
            "recent_segments": [r["dialogue"] for r in recent],
            "avoid_phrases": cfg.stock_phrases.get(language, [])}


_WORD = re.compile(r"\w+", re.UNICODE)
_META = re.compile(r"\[|\]|\(pause\)|\bsegment\s*\w*\d|https?://|www\.|^\s*(S\d+|Host)\s*:",
                   re.IGNORECASE | re.MULTILINE)
_EPISODE_META = re.compile(r"\b(case|episode|folge|fall)\s*(id|#|nr\.?)?\s*\d+", re.IGNORECASE)


def _trigrams(text: str) -> set[tuple[str, ...]]:
    w = [x.lower() for x in _WORD.findall(text or "")]
    return {tuple(w[i:i + 3]) for i in range(len(w) - 2)}


def _first_words(text: str, n: int = 3) -> tuple[str, ...]:
    return tuple(x.lower() for x in _WORD.findall(text or "")[:n])


def dialogue_issues(seg: dict, dialogue: str, language: str, recent: list[dict],
                    others: list[str], cfg: HostConfig | None = None) -> list[dict]:
    """Deterministic checks of one segment's dialogue."""
    cfg = cfg or ai_config.host
    issues: list[dict] = []
    text = (dialogue or "").strip()
    if not text:
        return [{"type": "empty"}]
    words = len(text.split())
    lo, hi = word_range(seg["position"], language, cfg)
    if words < lo:
        issues.append({"type": "too_short", "words": words, "min": lo})
    elif words > hi:
        issues.append({"type": "too_long", "words": words, "max": hi})
    for pat in cfg.stock_phrases.get(language, []):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            issues.append({"type": "stock_phrase", "quote": m.group(0)})
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
    return issues


# ---------------------------------------------------------------------------
# 3. critic: the persona's quality check, facts and memories
# ---------------------------------------------------------------------------


def critic_system_prompt(language: str) -> str:
    name = LANG_NAMES.get(language, language)
    return f"""You are the independent standards editor of a premium true-crime
documentary brand and a native {name} speaker. A recurring on-screen host
appears at a few moments; the narration tells the story, the host adds
perspective. Judge each host segment strictly.

For every segment answer each question true/false:
- adds_value: it adds something the narration cannot (perspective,
  clarity, a question, a verified connection) — not a summary or repeat
  of the narration before it.
- conversational: it sounds like a real person speaking to one viewer,
  clearly different from produced narration; not a lecture, not a news
  anchor, not theatrical.
- native: natural, native {name} — not translated phrasing.
- fresh: no repetition of the recent segments' wording, opening or
  rhythm; no stock phrases ("What do you think?", "I noticed…").
- shows_not_tells: personality shows through reactions and decisions; the
  host never says how intelligent, empathetic or fair they are.
- emotion_justified: any emotion is brief, relevant and earned by the
  material (true if there is none).
- verified: every factual statement is supported by the given claims and
  evidence with the same certainty; speculation is labelled; any memory
  is exactly the given memory; nothing is revealed that the viewer has
  not yet heard (the narration_before shows where we are).
- respectful: no sensationalism, mockery, romanticizing offenders,
  unsupported diagnoses or implied guilt.
- short_enough: the story stays dominant.

For each "false" give a problem with the exact quote and a concrete fix.

Return JSON only:
{{"segments": [{{"id": "S1",
  "checks": {{"adds_value": true, "conversational": true, "native": true, "fresh": true,
             "shows_not_tells": true, "emotion_justified": true, "verified": true,
             "respectful": true, "short_enough": true}},
  "problems": [{{"check": "verified", "quote": "...", "fix": "..."}}]}}]}}
"""


def critic_input(written: dict[str, str], wi: dict) -> dict:
    return {
        "language": wi["language"],
        "recent_segments": wi["recent_segments"],
        "segments": [
            {"id": s["id"], "position": s["position"], "purpose": s["purpose"],
             "claims": s["claims"], "evidence": s["evidence"], "memory": s["memory"],
             "narration_before": s["narration_before"], "narration_after": s["narration_after"],
             "dialogue": written.get(s["id"], "")}
            for s in wi["segments"]
        ],
    }


def critic_verdicts(raw: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in (raw or {}).get("segments") or []:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        checks = s.get("checks") if isinstance(s.get("checks"), dict) else {}
        checks = {k: bool(checks.get(k, True)) for k in CHECKS}
        problems = [p for p in s.get("problems") or [] if isinstance(p, dict)]
        out[str(s["id"])] = {"checks": checks, "problems": problems[:8],
                             "pass": all(checks.values())}
    return out


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _memory_index(arch: list[dict], mem: list[dict]) -> dict[str, dict]:
    return {x["ref"]: x for x in [*arch, *mem]}


class HostDirector:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.host

    async def _json(self, db, case_id: int, label: str, role: str, system: str,
                    payload: dict) -> tuple[dict, object]:
        with track_run(db, case_id, label, input_summary=label) as run:
            raw, res = await self.gen.generate_structured(
                role, system, json.dumps(payload, ensure_ascii=False))
            stamp_run(run, res, role)
        return raw, res

    # -- 1. plan -------------------------------------------------------------
    async def create_plan(self, db: Session, case: Case, bp_row: EditorialBlueprint,
                          master: StoryVersion) -> HostPlan:
        if bp_row.status == "invalid":
            raise RuntimeError("The blueprint is invalid; fix it before planning the host.")
        blueprint = json.loads(bp_row.blueprint_json or "{}")
        texts = beat_texts(master, blueprint)
        pack = _pack(db, case.id)
        arch, mem = archive(db, case.id), memories(db, case.id)
        recent = recent_segments(db, case.id, None)
        payload = director_input(case, blueprint, texts, pack, arch, mem, recent)
        evidence_ids = {e["id"] for e in payload["evidence"]}
        refs = set(_memory_index(arch, mem))
        narration_seconds = sum(len(t.split()) for t in texts.values()) / \
            ai_config.documentary.wpm(master.language or "en") * 60
        system = director_system_prompt(self.cfg)

        raw, res = await self._json(db, case.id, "Host Director", "host_director",
                                    system, payload)
        plan, report = validate_host_plan(raw, blueprint, evidence_ids, refs,
                                          narration_seconds, self.cfg)
        repairs = 0
        while report["errors"] and repairs < self.cfg.max_repair_iterations:
            repairs += 1
            raw, res = await self._json(
                db, case.id, f"Host Director repair {repairs}", "host_director", system,
                {**payload, "previous_plan": raw, "errors_to_fix": report["errors"]})
            plan, report = validate_host_plan(raw, blueprint, evidence_ids, refs,
                                              narration_seconds, self.cfg)
        report["repair_iterations"] = repairs
        count = db.query(HostPlan).filter(HostPlan.blueprint_id == bp_row.id).count()
        row = HostPlan(case_id=case.id, blueprint_id=bp_row.id, version=count + 1,
                       status=report["status"],
                       plan_json=json.dumps(plan, ensure_ascii=False),
                       validation_json=json.dumps(report, ensure_ascii=False),
                       generation_model=getattr(res, "model", None))
        db.add(row)
        db.commit()
        db.refresh(row)
        if row.status != "invalid":
            remember(db, case.id, row, plan.get("memory_updates") or [])
        return row

    # -- 2.+3. dialogue per language ----------------------------------------
    async def write(self, db: Session, case: Case, version: StoryVersion,
                    plan_row: HostPlan) -> HostSegments:
        if plan_row.status == "invalid":
            raise RuntimeError("The host plan is invalid.")
        language = version.language or "en"
        plan = json.loads(plan_row.plan_json or "{}")
        bp_row = db.get(EditorialBlueprint, plan_row.blueprint_id)
        blueprint = json.loads(bp_row.blueprint_json or "{}")
        beat_ids = [b["id"] for b in blueprint.get("beats") or []]
        texts = beat_texts(version, blueprint)
        pack_items = {e["id"]: e for e in _evidence_items(_pack(db, case.id))}
        arch_mem = _memory_index(archive(db, case.id), memories(db, case.id))
        recent = recent_segments(db, case.id, language)
        wi = writer_input(plan, texts, beat_ids, language, pack_items, arch_mem, recent,
                          self.cfg)
        by_id = {s["id"]: s for s in plan.get("segments") or []}
        written: dict[str, str] = {}
        verdicts: dict[str, dict] = {}
        res = None
        if wi["segments"]:
            raw, res = await self._json(db, case.id, f"Host Writer {language}", "host_writer",
                                        writer_system_prompt(language, self.cfg), wi)
            written = _dialogues(raw)
            verdicts = await self._check(db, case.id, language, wi, written, recent, by_id,
                                         set(by_id))
        repairs = 0
        while repairs < self.cfg.max_repair_iterations:
            failing = [sid for sid, v in verdicts.items() if not v["pass"]]
            if not failing:
                break
            repairs += 1
            sub = {**wi, "segments": [
                {**s, "previous_dialogue": written.get(s["id"], ""),
                 "problems_to_fix": verdicts[s["id"]]["problems"]
                 + verdicts[s["id"]]["deterministic"]}
                for s in wi["segments"] if s["id"] in failing]}
            raw, res = await self._json(db, case.id, f"Host Writer {language} repair {repairs}",
                                        "host_writer", writer_system_prompt(language, self.cfg),
                                        sub)
            fixed = {k: v for k, v in _dialogues(raw).items() if k in failing and v.strip()}
            if not fixed:
                break
            written.update(fixed)
            verdicts.update(await self._check(db, case.id, language, wi, written, recent,
                                              by_id, set(fixed)))

        segments = [segment_output(by_id[s["id"]], written.get(s["id"], ""), language,
                                   blueprint, verdicts.get(s["id"]), arch_mem)
                    for s in wi["segments"]]
        failed = [s["segment_id"] for s in segments if not s["quality"]["pass"]]
        status = "needs_review" if failed else "valid"
        report = {"status": status, "failed_segments": failed, "repair_iterations": repairs,
                  "host_seconds": round(sum(s["estimated_seconds"] for s in segments), 1),
                  "plan_status": plan_row.status}
        count = db.query(HostSegments).filter(
            HostSegments.story_version_id == version.id).count()
        row = HostSegments(case_id=case.id, story_version_id=version.id,
                           host_plan_id=plan_row.id, language=language, version=count + 1,
                           status=status,
                           segments_json=json.dumps(segments, ensure_ascii=False),
                           validation_json=json.dumps(report, ensure_ascii=False),
                           generation_model=getattr(res, "model", None))
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    async def _check(self, db, case_id: int, language: str, wi: dict, written: dict[str, str],
                     recent: list[dict], by_id: dict[str, dict], ids: set[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for sid in ids:
            others = [t for k, t in written.items() if k != sid]
            out[sid] = {"deterministic": dialogue_issues(
                by_id[sid], written.get(sid, ""), language, recent, others, self.cfg)}
        sub = {**wi, "segments": [s for s in wi["segments"] if s["id"] in ids]}
        raw, _ = await self._json(db, case_id, f"Host Critic {language}", "host_critic",
                                  critic_system_prompt(language), critic_input(written, sub))
        model = critic_verdicts(raw)
        for sid in ids:
            v = model.get(sid) or {"checks": {k: False for k in ("verified",)},
                                   "problems": [{"check": "not_reviewed",
                                                 "fix": "the critic returned no verdict"}],
                                   "pass": False}
            out[sid].update(checks=v["checks"], problems=v["problems"],
                            **{"pass": v["pass"] and not out[sid]["deterministic"]})
        return out


def _dialogues(raw: dict) -> dict[str, str]:
    out = {}
    for s in (raw or {}).get("segments") or []:
        if isinstance(s, dict) and s.get("id"):
            out[str(s["id"])] = str(s.get("dialogue") or "").strip().strip('"“”«»')
    return out


def _placement_text(seg: dict, blueprint: dict) -> str:
    beats = {b["id"]: b for b in blueprint.get("beats") or []}
    if seg["position"] == "opening":
        first = (blueprint.get("beats") or [{}])[0].get("id")
        return f"Opening — before {first}" if first else "Opening"
    b = beats.get(seg["beat_id"]) or {}
    label = "Final — after" if seg["position"] == "final" else "After"
    summary = f" ({b['summary']})" if b.get("summary") else ""
    return f"{label} {seg['beat_id']}{summary}"


def segment_output(seg: dict, dialogue: str, language: str, blueprint: dict,
                   verdict: dict | None, arch_mem: dict[str, dict]) -> dict:
    """One segment in the persona's output format (plus what later stages
    need: position, beat, claims, the quality check)."""
    ref = seg.get("memory_reference")
    memory = "none"
    if ref and ref in arch_mem:
        m = arch_mem[ref]
        memory = f"{ref} — {m.get('case')}: {seg.get('memory_connection') or m.get('text') or ''}"
    verdict = verdict or {"checks": {}, "problems": [], "deterministic": [],
                          "pass": bool(dialogue)}
    return {
        "segment_id": seg["id"],
        "placement": _placement_text(seg, blueprint),
        "position": seg["position"], "beat_id": seg["beat_id"],
        "purpose": seg["purpose"], "pattern": seg["pattern"],
        "personality_dimension": seg["dimensions"],
        "memory_reference": memory,
        "delivery_direction": seg["delivery"],
        "avatar_dialogue": dialogue,
        "target_duration": seg["target_seconds"],
        "estimated_seconds": estimate_seconds(dialogue, language),
        "transition_back": seg["transition_back"],
        "claims": seg["claims"],
        "quality": {"pass": bool(verdict.get("pass")) and bool(dialogue),
                    "checks": verdict.get("checks", {}),
                    "problems": verdict.get("problems", []),
                    "deterministic": verdict.get("deterministic", [])},
    }


def remember(db: Session, case_id: int, plan_row: HostPlan, updates: list[dict]) -> int:
    """Replace what earlier plans of this case made the host remember
    (editor-made memories stay)."""
    (db.query(HostMemory)
     .filter(HostMemory.case_id == case_id, HostMemory.origin == "host_plan")
     .delete(synchronize_session=False))
    for u in updates:
        db.add(HostMemory(case_id=case_id, kind=u["kind"], text=u["text"],
                          origin="host_plan", host_plan_id=plan_row.id))
    db.commit()
    return len(updates)


def latest_host_plan(db: Session, blueprint_id: int) -> HostPlan | None:
    return (db.query(HostPlan)
            .filter(HostPlan.blueprint_id == blueprint_id, HostPlan.status != "invalid")
            .order_by(HostPlan.version.desc()).first())


def latest_host_segments(db: Session, version_id: int) -> HostSegments | None:
    return (db.query(HostSegments).filter(HostSegments.story_version_id == version_id)
            .order_by(HostSegments.version.desc()).first())


def host_plan_dict(row: HostPlan) -> dict:
    return {"id": row.id, "case_id": row.case_id, "blueprint_id": row.blueprint_id,
            "version": row.version, "status": row.status,
            "generation_model": row.generation_model, "created_at": row.created_at,
            "plan": json.loads(row.plan_json or "{}"),
            "validation": json.loads(row.validation_json or "{}")}


def host_segments_dict(row: HostSegments) -> dict:
    return {"id": row.id, "case_id": row.case_id, "story_version_id": row.story_version_id,
            "host_plan_id": row.host_plan_id, "language": row.language,
            "version": row.version, "status": row.status,
            "generation_model": row.generation_model, "created_at": row.created_at,
            "segments": json.loads(row.segments_json or "[]"),
            "validation": json.loads(row.validation_json or "{}")}


def memory_dict(m: HostMemory) -> dict:
    return {"id": m.id, "ref": f"M{m.id}", "case_id": m.case_id, "kind": m.kind,
            "text": m.text, "origin": m.origin, "host_plan_id": m.host_plan_id,
            "active": m.active, "created_at": m.created_at}
