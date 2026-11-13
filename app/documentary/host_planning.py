"""Planning the host's appearances once per film, for all languages.

The host director decides only the structure: how many appearances, after which act, in which
role. Everything else is computed here from the story: the seconds of an appearance follow its
role, the weight of the act before it and its place in the film; the knowledge cutoff is the
number of acts the viewer has heard. The director never writes content, so no content can leak
through the plan."""

from __future__ import annotations

from app.core.ai_config import HostConfig, ai_config
from app.documentary.host_story import HostStory

POSITIONS = ("opening", "mid", "final")


def appearances_of_case(db, case_id: int) -> int | None:
    """How many appearances the case's newest host plan has, or None when the case has no host plan yet."""
    import json

    from app.db.models import HostPlan

    plan = db.query(HostPlan).filter(HostPlan.case_id == case_id).order_by(HostPlan.id.desc()).first()
    segments = json.loads(plan.plan_json or "{}").get("segments") if plan is not None else None
    return len(segments) if segments else None


def cfg_for_case(db, case_id: int, cfg: HostConfig | None = None) -> HostConfig:
    """The host settings for one case: a case that already has a host plan keeps that plan's number of appearances (and its mid roles),
    whatever the configuration says now; only a case without a host plan follows the configuration."""
    cfg = cfg or ai_config.host
    have = appearances_of_case(db, case_id)
    if have is None or (have == cfg.max_appearances and have >= cfg.min_appearances):
        return cfg
    return cfg.model_copy(update={"min_appearances": have, "max_appearances": have, "short_film_min_appearances": have, "mid_roles": []})


def story_seconds(story: HostStory) -> float:
    """The narration's length from the planned words, at the draft language's pace."""
    wpm = ai_config.language_pipeline.words_per_minute[ai_config.language_pipeline.draft_language]
    return story.total_words() / wpm * 60


def appearance_seconds(story: HostStory, role: str, cutoff: int,
                       cfg: HostConfig | None = None) -> float:
    """Seconds of one appearance: the role's base, grown by the weight of the act before it (its
    share of the film against an even share) and by its place in the film, kept inside the role's
    bounds. An opening has no act before it: the first act's weight counts."""
    cfg = cfg or ai_config.host
    spec = cfg.roles[role]
    acts = story.acts
    if not acts:
        return spec.base_seconds
    weight_act = acts[min(max(cutoff, 1), len(acts)) - 1]
    weight = weight_act.words / (story.total_words() / len(acts)) if story.total_words() else 1.0
    told = sum(a.words for a in story.visible(cutoff))
    fraction = told / story.total_words() if story.total_words() else 0.0
    seconds = (spec.base_seconds * (1 + cfg.act_weight_gain * (weight - 1))
               * (1 + cfg.position_gain * (fraction - 0.5)))
    return round(min(max(seconds, spec.min_seconds), spec.max_seconds,
                     cfg.max_appearance_seconds), 1)


def seconds_between(story: HostStory, first_cutoff: int, second_cutoff: int) -> float:
    """Story seconds that play between two appearances."""
    wpm = ai_config.language_pipeline.words_per_minute[ai_config.language_pipeline.draft_language]
    words = sum(a.words for a in story.acts[first_cutoff:second_cutoff])
    return words / wpm * 60


INHERITED_OK = ("interrupts_hook", "interrupts_before_payoff")


def inherit(report: dict) -> dict:
    """A plan copied from the English film keeps the English film's sections: the findings about where a hook or a payoff lies are taken from
    each language's own blueprint (they can differ although the story is the same), so for such a plan they are recorded, not errors."""
    kept = [e for e in report["errors"] if e["code"] not in INHERITED_OK]
    report["inherited_findings"] = [e for e in report["errors"] if e["code"] in INHERITED_OK]
    report["errors"] = kept
    report["status"] = "invalid" if kept else ("needs_review" if report["warnings"] else "valid")
    return report


def fit_plan(segments: list[dict], made_for: HostStory, story: HostStory, cfg: HostConfig | None = None) -> list[dict]:
    """The appearances of a plan placed in the story of the version that is voiced. A plan is made for the film's story in its acts (a few large ones); a
    spoken version is told beat by beat (dozens of small ones), so "after act 7" would be after the seventh beat. Each middle appearance keeps its place in
    the story (its share of the words heard) and goes to the beat that is nearest to it, not on a hook and not right before a payoff; the opening stays the
    opening and the outro stays after the last beat. Raises when the placed plan is not valid."""
    cfg = cfg or ai_config.host
    if [a.id for a in made_for.acts] == [a.id for a in story.acts]:
        return segments
    total_for, total = sum(a.words for a in made_for.acts) or 1, sum(a.words for a in story.acts) or 1
    heard = [sum(a.words for a in story.acts[:k]) / total for k in range(len(story.acts) + 1)]
    placed, last = [], 0
    for seg in segments:
        entry = {"id": seg["id"], "position": seg["position"], "role": seg["role"], "reason": seg.get("reason", "")}
        if seg["position"] == "mid":
            share = sum(a.words for a in made_for.acts[:seg["cutoff"]]) / total_for
            k = max(min(range(1, len(story.acts)), key=lambda i: abs(heard[i] - share)), last + cfg.min_acts_between)
            while k < len(story.acts) and (story.acts[k - 1].hook or story.acts[k].payoff):
                k += 1 if story.acts[k - 1].hook else -1
            entry["after_act"], last = story.acts[k - 1].id, k
        placed.append(entry)
    plan, report = validate_host_plan({"notes": "placed by the share of the story heard", "segments": placed}, story, cfg)
    if report["errors"]:
        raise RuntimeError("the host plan does not fit the story of this version: " + "; ".join(e["code"] for e in report["errors"][:4]))
    return plan["segments"]


def validate_host_plan(raw: dict, story: HostStory, cfg: HostConfig | None = None
                       ) -> tuple[dict, dict]:
    """The director's structure, checked against the story and the config. Errors go back to the
    director; the plan keeps what is valid, with the seconds and cutoffs the code computes."""
    cfg = cfg or ai_config.host
    raw = raw if isinstance(raw, dict) else {}
    acts = story.acts
    index = {a.id: a.index for a in acts}
    errors: list[dict] = []
    warnings: list[dict] = []
    segments: list[dict] = []

    for n, s in enumerate(raw.get("segments") or [], 1):
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or f"S{n}")
        role = s.get("role")
        spec = cfg.roles.get(role) if isinstance(role, str) else None
        if spec is None:
            errors.append({"code": "unknown_role", "segment": sid, "value": role,
                           "roles": sorted(cfg.roles)})
            continue
        if s.get("position") != spec.position:
            errors.append({"code": "role_position_mismatch", "segment": sid, "role": role,
                           "position": s.get("position"), "expected": spec.position})
            continue
        position = spec.position
        if position == "mid" and cfg.mid_roles and role not in cfg.mid_roles:
            errors.append({"code": "mid_role_not_allowed", "segment": sid, "role": role, "allowed": cfg.mid_roles})
            continue
        if position == "opening":
            cutoff, act_id = 0, None
        elif position == "final":
            if not cfg.final_enabled:
                errors.append({"code": "outro_is_off", "segment": sid})
                continue
            cutoff, act_id = len(acts), acts[-1].id if acts else None
        else:
            act_id = s.get("after_act")
            if act_id not in index:
                errors.append({"code": "unknown_act", "segment": sid, "value": act_id,
                               "acts": story.act_ids})
                continue
            cutoff = index[act_id]
            if cutoff == len(acts):
                errors.append({"code": "mid_after_last_act_use_outro", "segment": sid})
            elif acts[cutoff - 1].hook:
                errors.append({"code": "interrupts_hook", "segment": sid, "act": act_id})
            if cutoff < len(acts) and acts[cutoff].payoff:
                errors.append({"code": "interrupts_before_payoff", "segment": sid,
                               "next_act": acts[cutoff].id})
        segments.append({
            "id": sid, "position": position, "role": role, "beat_id": act_id, "cutoff": cutoff,
            "target_seconds": appearance_seconds(story, str(role), cutoff, cfg),
            "reason": str(s.get("reason") or "")[:300]})

    segments.sort(key=lambda x: x["cutoff"])
    for number, seg in enumerate(segments, 1):
        seg["id"] = f"S{number}"          # the appearances of a film are always S1, S2, ... in film order
    counts = {p: sum(1 for s in segments if s["position"] == p) for p in POSITIONS}
    if counts["opening"] != 1:
        errors.append({"code": "needs_exactly_one_opening", "count": counts["opening"]})
    if counts["final"] > 1 or (cfg.final_enabled and counts["final"] != 1):
        errors.append({"code": "needs_exactly_one_outro", "count": counts["final"]})
    least = cfg.min_appearances_for(story_seconds(story))
    if not least <= len(segments) <= cfg.max_appearances:
        errors.append({"code": "appearance_count_out_of_range", "count": len(segments),
                       "min": least, "max": cfg.max_appearances})
    for a, b in zip(segments, segments[1:], strict=False):
        if b["cutoff"] - a["cutoff"] < cfg.min_acts_between:
            errors.append({"code": "appearances_too_close", "segments": [a["id"], b["id"]],
                           "acts_between": b["cutoff"] - a["cutoff"]})
        elif seconds_between(story, a["cutoff"], b["cutoff"]) < cfg.min_gap_for(
                story_seconds(story)):
            errors.append({"code": "appearances_too_close_in_time", "segments": [a["id"], b["id"]],
                           "seconds_between": round(seconds_between(story, a["cutoff"],
                                                                    b["cutoff"]))})

    narration = story_seconds(story)
    host_seconds = sum(s["target_seconds"] for s in segments)
    if host_seconds > cfg.max_total_seconds:
        errors.append({"code": "host_time_too_long", "seconds": round(host_seconds, 1),
                       "max": cfg.max_total_seconds})
    share = host_seconds / max(narration, 1.0)
    if share > cfg.max_total_share:
        # a short film cannot hold an opening within the share; only a real film is held to it
        (warnings if cfg.is_short_film(narration) else errors).append(
            {"code": "host_share_too_high", "share": round(share, 3), "max": cfg.max_total_share})

    status = "invalid" if errors else ("needs_review" if warnings else "valid")
    plan = {"notes": str(raw.get("notes") or "")[:600], "segments": segments}
    report = {"status": status, "errors": errors, "warnings": warnings,
              "segments": len(segments), "counts": counts,
              "host_seconds": round(host_seconds, 1), "narration_seconds": round(narration, 1),
              "host_share": round(share, 4), "time_accounting": cfg.time_accounting}
    return plan, report
