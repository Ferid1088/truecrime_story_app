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
    status = "invalid" if errors else ("needs_review" if warnings else "valid")
    return {"status": status, "errors": errors, "warnings": warnings}


