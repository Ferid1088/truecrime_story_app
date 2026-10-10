"""Editorial blueprint: the documentary strategy behind a finished story.

The Narrative Director reads an approved story (act by act, paragraphs
numbered) and divides it into BEATS — contiguous paragraph ranges, each
with a job in the film: what the listener learns for the first time
(evidence ids), which questions open or close, how dense and how
emotional it is, and the intended attention mode, look, sound and pause.
It segments the existing text; it never rewrites it.

A deterministic validator then checks what a model cannot be trusted to
get right: every paragraph is covered exactly once and in order, every
evidence id exists, nothing is revealed twice, questions are answered
after they are asked, and the listener is never juggling too many open
questions or too many dense beats in a row. One repair round fixes
structural errors; warnings are kept for human review.

Everything is ordinal (low/medium/high) or an enum — no fake-precise
0.92 scores.
"""

from __future__ import annotations

from app.agents.runner import run_agent

from app.core.prompts import prompt

import json

from sqlalchemy.orm import Session

from app.agents.story import (
    _paragraphs,
    stored_sections,
)
from app.core.ai_config import BlueprintConfig, ai_config
from app.db.models import (
    EditorialBlueprint, StoryVersion,
)

PURPOSES = {
    "hook": "the opening anomaly that makes the listener need to know more",
    "orientation": "where/when we are; the setting and its people",
    "human_introduction": "a person becomes real (role, ties, documented life)",
    "timeline": "events in order; what happened when",
    "investigation": "what investigators did, found or concluded",
    "evidence": "a concrete document, object, record or statement",
    "contradiction": "two accounts or facts that do not fit together",
    "false_lead": "a lead or theory the story will later weaken (only if documented)",
    "reveal": "information that changes how earlier events are understood",
    "recovery": "lighter, slower beat after dense material so the listener can breathe",
    "reflection": "what it means for the people involved; what remains unknown",
    "transition": "bridge between threads, places or times",
    "chapter_end": "closes an act and leaves a reason to continue",
}
LEVELS = ("low", "medium", "high")
ATTENTION = {
    "listen": "the words carry it; visuals stay calm",
    "look": "a picture adds what words cannot",
    "read": "a short on-screen text/document matters (keep narration sparse)",
    "orient": "place or time orientation (map, date)",
    "feel": "emotion leads; restraint, maybe silence",
}
VISUAL_INTENTS = {
    "hold_current": "keep the current image — a valid, often best choice",
    "person": "a documented photo of a person in the story",
    "place_orientation": "the real location or area",
    "map": "a map movement for orientation",
    "document": "a real document or record, a passage highlighted",
    "timeline_date": "a date or timeline card",
    "evidence_object": "a real object/vehicle/item from the case",
    "atmosphere": "contextual, clearly non-evidential footage",
    "typography": "a designed text card (quote, name, question)",
    "black": "near-black screen; the words alone",
}
PAUSES = ("none", "short", "dramatic", "silence")
MUSIC = ("none", "enter", "sustain", "build", "thin", "out")

_DEFAULTS = {
    "purpose": "timeline", "emotional_load": "medium",
    "information_density": "medium", "mystery_intensity": "medium",
    "attention": "listen", "visual_intent": "hold_current",
    "audio_intent": "neutral", "pause_after": "none", "music_intent": "none",
}


def _audio_intents() -> list[str]:
    return sorted(ai_config.performance.style_for_intent)


# ---------------------------------------------------------------------------
# director (LLM)
# ---------------------------------------------------------------------------


def _director_input(sections: list[dict], pack: dict) -> dict:
    return {
        "acts": [
            {
                "act_id": s["id"],
                "paragraphs": [
                    {"n": i, "words": len(p.split()), "text": p}
                    for i, p in enumerate(_paragraphs(s["text"]), 1)
                ],
            }
            for s in sections
        ],
        "evidence": {
            "facts": [
                {"id": f["id"], "claim": (f.get("claim") or "")[:240],
                 "uncertain": bool(f.get("uncertain"))}
                for f in pack.get("facts") or []
            ],
            "timeline": [
                {"id": t["id"], "date": t.get("event_date"),
                 "event": (t.get("claim") or "")[:200]}
                for t in pack.get("timeline") or []
            ],
            "contradictions": [
                {"id": c["id"], "topic": c.get("topic"),
                 "description": (c.get("description") or "")[:240]}
                for c in pack.get("contradictions") or []
            ],
        },
    }


def _enum_doc(name: str, items) -> str:
    head = f"{name}:\n" if name else ""
    if isinstance(items, dict):
        return head + "\n".join(f"    - {k}: {v}" for k, v in items.items())
    return head + " | ".join(items)


def director_system_prompt(cfg: BlueprintConfig | None = None) -> str:
    cfg = cfg or ai_config.blueprint
    return prompt("documentary/blueprint/director_system_prompt").format(max_beat_words=cfg.max_beat_words, purposes=_enum_doc("", PURPOSES), max_open_questions=cfg.max_open_questions, attention_modes=_enum_doc("", ATTENTION), visual_intents=_enum_doc("", VISUAL_INTENTS), audio_intents=" | ".join(_audio_intents()))


async def _call_director(gen, system: str, payload: dict):
    return await run_agent("documentary.blueprint", gen, json.dumps(payload, ensure_ascii=False), system=system)


# ---------------------------------------------------------------------------
# validation (deterministic)
# ---------------------------------------------------------------------------


def _as_list(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def validate_blueprint(
    raw: dict, sections: list[dict], pack: dict, cfg: BlueprintConfig | None = None
) -> tuple[dict, dict]:
    """Return (normalized blueprint, report). report.status is
    'invalid' (structural errors), 'needs_review' (warnings) or 'valid'."""
    cfg = cfg or ai_config.blueprint
    errors: list[dict] = []
    warnings: list[dict] = []
    raw = raw if isinstance(raw, dict) else {}

    act_paras = {s["id"]: _paragraphs(s["text"]) for s in sections}
    act_sizes = {k: len(v) for k, v in act_paras.items()}
    expected = [(s["id"], n) for s in sections for n in range(1, act_sizes[s["id"]] + 1)]
    evidence_ids = {
        i["id"] for key in ("facts", "timeline", "contradictions")
        for i in pack.get(key) or []
    }
    audio_intents = set(_audio_intents())
    enums = {
        "purpose": set(PURPOSES), "emotional_load": set(LEVELS),
        "information_density": set(LEVELS), "mystery_intensity": set(LEVELS),
        "attention": set(ATTENTION), "visual_intent": set(VISUAL_INTENTS),
        "audio_intent": audio_intents, "pause_after": set(PAUSES),
        "music_intent": set(MUSIC),
    }

    beats: list[dict] = []
    seen_ids: set[str] = set()
    covered: list[tuple[str, int]] = []
    for idx, b in enumerate(raw.get("beats") or [], 1):
        if not isinstance(b, dict):
            errors.append({"code": "beat_not_object", "index": idx})
            continue
        bid = str(b.get("id") or f"B{idx:02d}")
        if bid in seen_ids:
            errors.append({"code": "duplicate_beat_id", "beat": bid})
        seen_ids.add(bid)
        act = str(b.get("act_id") or "")
        para = _as_list(b.get("paragraphs"))
        try:
            first, last = int(para[0]), int(para[-1])
        except (IndexError, TypeError, ValueError):
            errors.append({"code": "bad_paragraph_range", "beat": bid})
            continue
        if act not in act_sizes:
            errors.append({"code": "unknown_act", "beat": bid, "act_id": act})
            continue
        if not (1 <= first <= last <= act_sizes[act]):
            errors.append({"code": "paragraph_out_of_range", "beat": bid,
                           "act_id": act, "paragraphs": [first, last],
                           "act_paragraphs": act_sizes[act]})
            continue
        covered.extend((act, n) for n in range(first, last + 1))
        beat = {
            "id": bid, "act_id": act, "paragraphs": [first, last],
            "summary": str(b.get("summary") or "")[:300],
            "human_focus": b.get("human_focus") or None,
        }
        for field, allowed in enums.items():
            value = b.get(field)
            if value not in allowed:
                if value is not None:
                    warnings.append({"code": "coerced_enum", "beat": bid,
                                     "field": field, "value": value})
                value = _DEFAULTS[field]
            beat[field] = value
        beat["words"] = sum(
            len(p.split()) for p in act_paras[act][first - 1:last]
        )
        for field in ("reveals", "relies_on", "opens", "answers", "unresolved"):
            beat[field] = [str(x) for x in _as_list(b.get(field))]
        beats.append(beat)

    if not beats:
        errors.append({"code": "no_beats"})
    elif covered != expected:
        first_bad = next(
            (i for i, (a, e) in enumerate(zip(covered, expected)) if a != e),
            min(len(covered), len(expected)),
        )
        errors.append({
            "code": "coverage_mismatch",
            "detail": "beats must cover every paragraph of every act exactly once, in order",
            "expected": expected[first_bad] if first_bad < len(expected) else None,
            "got": covered[first_bad] if first_bad < len(covered) else None,
            "covered": len(covered), "paragraphs": len(expected),
        })

    # --- evidence: known ids, revealed once, relied on only after reveal
    revealed_in: dict[str, str] = {}
    for b in beats:
        for eid in b["reveals"] + b["relies_on"]:
            if eid not in evidence_ids:
                errors.append({"code": "unknown_evidence", "beat": b["id"], "id": eid})
        for eid in b["reveals"]:
            if eid in revealed_in:
                errors.append({"code": "revealed_twice", "id": eid,
                               "beats": [revealed_in[eid], b["id"]]})
            else:
                revealed_in[eid] = b["id"]
    known: set[str] = set()
    for b in beats:
        for eid in b["relies_on"]:
            if eid in evidence_ids and eid not in known and eid not in b["reveals"]:
                warnings.append({"code": "relies_on_unrevealed", "beat": b["id"],
                                 "id": eid})
        known |= set(b["reveals"])
        b["viewer_knows"] = sorted(known)

    # --- questions: asked before answered, few open at a time
    q_text = {}
    for q in raw.get("questions") or []:
        if isinstance(q, dict) and q.get("id"):
            q_text[str(q["id"])] = {
                "id": str(q["id"]), "question": str(q.get("question") or ""),
                "kind": q.get("kind") if q.get("kind") in
                ("mystery", "human", "investigation") else "mystery",
            }
    opened_in: dict[str, str] = {}
    closed: dict[str, tuple[str, str]] = {}  # qid -> (beat, answered|unresolved)
    open_now: list[str] = []
    for b in beats:
        for qid in b["opens"]:
            if qid not in q_text:
                errors.append({"code": "unknown_question", "beat": b["id"], "id": qid})
            elif qid in opened_in:
                errors.append({"code": "question_opened_twice", "id": qid})
            else:
                opened_in[qid] = b["id"]
                open_now.append(qid)
        for field, outcome in (("answers", "answered"), ("unresolved", "unresolved")):
            for qid in b[field]:
                if qid not in q_text:
                    errors.append({"code": "unknown_question", "beat": b["id"],
                                   "id": qid})
                elif qid not in opened_in:
                    errors.append({"code": "answered_before_asked",
                                   "beat": b["id"], "id": qid})
                elif qid in closed:
                    errors.append({"code": "question_closed_twice", "id": qid})
                else:
                    closed[qid] = (b["id"], outcome)
                    if qid in open_now:
                        open_now.remove(qid)
        b["open_questions"] = list(open_now)
        if len(open_now) > cfg.max_open_questions:
            warnings.append({"code": "too_many_open_questions", "beat": b["id"],
                             "open": list(open_now)})
    for qid in q_text:
        if qid not in opened_in:
            warnings.append({"code": "question_never_opened", "id": qid})
    # Questions the film honestly marks unresolved are closed, not dropped.
    unanswered = [q for q in opened_in if q not in closed]
    if len(unanswered) > cfg.max_unanswered_questions:
        warnings.append({"code": "many_unanswered_questions", "ids": unanswered})

    # --- beat shape: one listener moment; hooks open, chapter ends close
    act_first = {}
    act_last = {}
    for b in beats:
        act_first.setdefault(b["act_id"], b["id"])
        act_last[b["act_id"]] = b["id"]
    for b in beats:
        if b["words"] > cfg.max_beat_words and b["paragraphs"][0] != b["paragraphs"][1]:
            warnings.append({"code": "beat_too_long", "beat": b["id"],
                             "words": b["words"]})
        if b["purpose"] == "hook" and act_first[b["act_id"]] != b["id"]:
            warnings.append({"code": "hook_not_at_act_start", "beat": b["id"]})
        if b["purpose"] == "chapter_end" and act_last[b["act_id"]] != b["id"]:
            warnings.append({"code": "chapter_end_not_at_act_end", "beat": b["id"]})

    # --- pacing: listener needs recovery after dense stretches
    run: list[str] = []
    for b in beats:
        if b["information_density"] == "high":
            run.append(b["id"])
            if len(run) == cfg.max_dense_run + 1:
                warnings.append({"code": "dense_run_without_recovery",
                                 "beats": list(run)})
        else:
            run = []
        if b["information_density"] == "high" and b["attention"] == "read":
            warnings.append({"code": "read_during_dense_narration", "beat": b["id"]})

    questions = []
    for qid, q in q_text.items():
        beat_closed, outcome = closed.get(qid, (None, "open"))
        questions.append({**q, "opened_in": opened_in.get(qid),
                          "resolved_in": beat_closed,
                          "status": outcome if qid in opened_in else "never_opened"})

    blueprint = {
        "central_question": str(raw.get("central_question") or ""),
        "editorial_thesis": str(raw.get("editorial_thesis") or ""),
        "human_thread": str(raw.get("human_thread") or ""),
        "arcs": raw.get("arcs") if isinstance(raw.get("arcs"), dict) else {},
        "questions": questions,
        "beats": beats,
    }
    status = "invalid" if errors else ("needs_review" if warnings else "valid")
    report = {"status": status, "errors": errors, "warnings": warnings,
              "beats": len(beats), "questions": len(questions),
              "unanswered_questions": unanswered}
    return blueprint, report


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def version_sections(version: StoryVersion) -> list[dict]:
    return stored_sections(version) or [
        {"id": "full", "text": version.story_text or ""}
    ]


def latest_blueprint(db: Session, story_version_id: int) -> EditorialBlueprint | None:
    return (
        db.query(EditorialBlueprint)
        .filter(EditorialBlueprint.story_version_id == story_version_id)
        .order_by(EditorialBlueprint.version.desc())
        .first()
    )


def usable_blueprint(db: Session, version: StoryVersion) -> dict | None:
    """The newest non-invalid blueprint made for exactly this text."""
    row = latest_blueprint(db, version.id)
    if not row or row.status == "invalid":
        return None
    if row.story_text_hash and row.story_text_hash != version.text_hash:
        return None
    return json.loads(row.blueprint_json or "{}")


def blueprint_dict(row: EditorialBlueprint) -> dict:
    return {
        "id": row.id, "case_id": row.case_id,
        "story_version_id": row.story_version_id, "version": row.version,
        "status": row.status, "evidence_fingerprint": row.evidence_fingerprint,
        "generation_model": row.generation_model, "created_at": row.created_at,
        "blueprint": json.loads(row.blueprint_json or "{}"),
        "validation": json.loads(row.validation_json or "{}"),
    }


def __getattr__(name: str):
    # the agent class lives in app/agents/blueprint.py (imported when first asked for)
    if name == "NarrativeDirector":
        from app.agents.blueprint import NarrativeDirector

        return NarrativeDirector
    raise AttributeError(name)
