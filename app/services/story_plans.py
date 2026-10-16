"""The story plan before writing (Part 5a).

A StoryPlan stores the story director's plan as the single source of the
story's structure: beats (the plan's acts) in order, each with its purpose,
what it reveals for the first time, its evidence ids, target length and the
people it introduces, plus the host slots (one opening before beat 1, one or
two mid-film slots after named beats; the final slot stays off).

Relation to the editorial blueprint: the blueprint is derived from the story
that was written to this plan. Its beats are paragraph ranges that nest inside
the plan's beats (every blueprint beat names its act_id, checked by
validate_blueprint) and the blueprint row records story_plan_id, so there is
one structure, not two.

Everything here is deterministic; the only model call is the director's.
"""

from __future__ import annotations

import copy

from app.core.ai_config import ai_config


def _beat_ids(plan: dict) -> list[str]:
    return [str(a.get("id")) for a in plan.get("acts") or []]


def pack_item_ids(pack: dict) -> set[str]:
    return {i["id"] for key in ("facts", "timeline", "contradictions")
            for i in pack.get(key) or []}


def _people_by_evidence(pack: dict) -> dict[str, list[str]]:
    return {f["id"]: [p for p in f.get("people") or [] if p]
            for f in pack.get("facts") or []}


def host_slots(plan: dict) -> list[dict]:
    """One opening before the first beat and up to `mid_host_slots` mid slots
    after beats at about the 1/3 and 2/3 marks of the word budget, never after
    the last beat and never twice after the same beat."""
    ids = _beat_ids(plan)
    if not ids:
        return []
    slots = [{"id": "H1", "position": "opening", "before_beat": ids[0]}]
    wanted = min(ai_config.story_plan.mid_host_slots, ai_config.host.max_mid_segments,
                 max(len(ids) - 2, 0))
    words = [max(int(a.get("target_words") or 0), 0) for a in plan.get("acts") or []]
    total = sum(words) or len(ids)
    used: set[int] = set()
    for n in range(1, wanted + 1):
        mark = total * n / (wanted + 1)
        run, pick = 0, 0
        for i, w in enumerate(words or [1] * len(ids)):
            run += w or 1
            pick = i
            if run >= mark:
                break
        pick = min(pick, len(ids) - 2)
        while pick in used and pick > 0:
            pick -= 1
        if pick in used:
            continue
        used.add(pick)
        slots.append({"id": f"H{len(slots) + 1}", "position": "mid", "after_beat": ids[pick]})
    return slots


def enrich(plan: dict, pack: dict) -> dict:
    """Adds, deterministically, what the writer and the host need and the
    director's schema does not carry: per beat `reveals` (evidence first used
    here) and `introduces_people` (people first named here), and `host_slots`."""
    plan = copy.deepcopy(plan)
    people = _people_by_evidence(pack)
    seen_evidence: set[str] = set()
    seen_people: set[str] = set()
    for act in plan.get("acts") or []:
        ids = [str(i) for i in act.get("evidence_ids") or []]
        act["reveals"] = [i for i in ids if i not in seen_evidence]
        intro: list[str] = []
        for i in ids:
            for person in people.get(i, []):
                if person not in seen_people and person not in intro:
                    intro.append(person)
        act["introduces_people"] = intro
        seen_evidence.update(ids)
        seen_people.update(intro)
    plan["host_slots"] = host_slots(plan)
    return plan


def beats_view(plan: dict) -> list[dict]:
    """The plan's beats as the API and the owner see them."""
    return [{
        "id": a.get("id"), "title": a.get("title"), "purpose": a.get("purpose"),
        "key_beats": a.get("key_beats") or [], "target_words": a.get("target_words"),
        "evidence_ids": a.get("evidence_ids") or [], "reveals": a.get("reveals") or [],
        "introduces_people": a.get("introduces_people") or [],
        "open_loops": a.get("open_loops") or [], "resolved_loops": a.get("resolved_loops") or [],
        "do_not_reveal": a.get("do_not_reveal") or [],
    } for a in plan.get("acts") or []]


def validate_plan(plan: dict, pack: dict, target_words: int) -> dict:
    """status: invalid (errors) | needs_review (warnings) | valid."""
    errors: list[dict] = []
    warnings: list[dict] = []
    acts = plan.get("acts") if isinstance(plan, dict) else None
    if not acts:
        return {"status": "invalid", "errors": [{"code": "no_beats"}], "warnings": []}
    known = pack_item_ids(pack)
    seen: set[str] = set()
    for a in acts:
        bid = str(a.get("id") or "")
        if not bid or bid in seen:
            errors.append({"code": "missing_or_duplicate_beat_id", "beat": bid})
        seen.add(bid)
        if not (a.get("purpose") or "").strip() or not (a.get("title") or "").strip():
            errors.append({"code": "beat_without_title_or_purpose", "beat": bid})
        if int(a.get("target_words") or 0) <= 0:
            errors.append({"code": "beat_without_target_length", "beat": bid})
        unknown = [i for i in a.get("evidence_ids") or [] if i not in known]
        if unknown:
            errors.append({"code": "unknown_evidence_ids", "beat": bid, "ids": unknown})
        early = sorted(set(a.get("do_not_reveal") or []) & set(a.get("evidence_ids") or []))
        if early:
            errors.append({"code": "beat_reveals_what_it_reserves", "beat": bid, "ids": early})
    total = sum(int(a.get("target_words") or 0) for a in acts)
    limit = ai_config.story_plan.max_total_words_deviation
    deviation = abs(total - target_words) / target_words if target_words else 0.0
    if deviation > limit:
        errors.append({"code": "beats_do_not_add_up_to_the_film_length", "words": total,
                       "target_words": target_words})
    elif deviation > limit / 2:
        warnings.append({"code": "beat_lengths_drift_from_the_film_length", "words": total,
                         "target_words": target_words})
    errors += _slot_errors(plan, [str(a.get("id")) for a in acts])
    status = "invalid" if errors else ("needs_review" if warnings else "valid")
    return {"status": status, "errors": errors, "warnings": warnings}


def _slot_errors(plan: dict, ids: list[str]) -> list[dict]:
    slots = plan.get("host_slots") or []
    out: list[dict] = []
    openings = [s for s in slots if s.get("position") == "opening"]
    mids = [s for s in slots if s.get("position") == "mid"]
    if len(openings) != 1 or openings[0].get("before_beat") != ids[0]:
        out.append({"code": "host_opening_must_be_one_slot_before_the_first_beat"})
    if any(s.get("position") == "final" for s in slots):
        out.append({"code": "host_final_slot_is_off"})
    if len(mids) > ai_config.host.max_mid_segments:
        out.append({"code": "too_many_mid_host_slots", "count": len(mids)})
    after = [s.get("after_beat") for s in mids]
    if any(b not in ids[:-1] for b in after) or len(set(after)) != len(after):
        out.append({"code": "mid_host_slot_must_follow_a_distinct_non_final_beat"})
    return out


def check_plan_mapping(sections: list[dict], plan: dict) -> dict:
    """The written story must map 1:1 onto the plan's beats: the same ids in
    the same order, each within the per-beat length band."""
    ids = _beat_ids(plan)
    got = [str(s.get("id")) for s in sections or []]
    issues: list[dict] = []
    if got == ["full"] or not got:
        issues.append({"code": "structure_lost", "why": "the story has no per-beat sections"})
    elif got != ids:
        issues.append({"code": "beats_do_not_match", "plan": ids, "story": got})
    else:
        tol = ai_config.master_generation.section_word_tolerance
        for a, s in zip(plan.get("acts") or [], sections):
            target = int(a.get("target_words") or 0)
            words = len(s["text"].split())
            if target and abs(words - target) / target > tol:
                issues.append({"code": "beat_length", "beat": a.get("id"),
                               "words": words, "target_words": target})
    return {"ok": not issues, "issues": issues}
