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
        spec = cfg.roles.get(role)
        if spec is None:
            errors.append({"code": "unknown_role", "segment": sid, "value": role,
                           "roles": sorted(cfg.roles)})
            continue
        if s.get("position") != spec.position:
            errors.append({"code": "role_position_mismatch", "segment": sid, "role": role,
                           "position": s.get("position"), "expected": spec.position})
            continue
        position = spec.position
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
            "target_seconds": appearance_seconds(story, role, cutoff, cfg),
            "reason": str(s.get("reason") or "")[:300]})

    segments.sort(key=lambda x: x["cutoff"])
    counts = {p: sum(1 for s in segments if s["position"] == p) for p in POSITIONS}
    if counts["opening"] != 1:
        errors.append({"code": "needs_exactly_one_opening", "count": counts["opening"]})
    if counts["final"] > 1 or (cfg.final_enabled and counts["final"] != 1):
        errors.append({"code": "needs_exactly_one_outro", "count": counts["final"]})
    if not cfg.min_appearances <= len(segments) <= cfg.max_appearances:
        errors.append({"code": "appearance_count_out_of_range", "count": len(segments),
                       "min": cfg.min_appearances, "max": cfg.max_appearances})
    ids = [s["id"] for s in segments]
    if len(set(ids)) != len(ids):
        errors.append({"code": "duplicate_segment_ids"})
    for a, b in zip(segments, segments[1:]):
        if b["cutoff"] - a["cutoff"] < cfg.min_acts_between:
            errors.append({"code": "appearances_too_close", "segments": [a["id"], b["id"]],
                           "acts_between": b["cutoff"] - a["cutoff"]})
        elif seconds_between(story, a["cutoff"], b["cutoff"]) < cfg.min_gap_seconds:
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
        (errors if narration >= 600 else warnings).append(
            {"code": "host_share_too_high", "share": round(share, 3), "max": cfg.max_total_share})

    status = "invalid" if errors else ("needs_review" if warnings else "valid")
    plan = {"notes": str(raw.get("notes") or "")[:600], "segments": segments}
    report = {"status": status, "errors": errors, "warnings": warnings,
              "segments": len(segments), "counts": counts,
              "host_seconds": round(host_seconds, 1), "narration_seconds": round(narration, 1),
              "host_share": round(share, 4), "time_accounting": cfg.time_accounting}
    return plan, report
