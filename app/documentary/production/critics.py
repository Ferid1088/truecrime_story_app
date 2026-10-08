"""Documentary critics and the critic loop (Parts 40–42).

Deterministic checks run first (they cost nothing and never hallucinate):
  rhythm          — visual changes per minute within limits (the upper
                    limit follows the cut rhythm of visual_direction);
  repetition      — the same motion pattern too often in a row, an asset
                    reused too often, a picture over its category's limit
                    (generic/contextual/map/person/evidence, see
                    visuals/usage.py), a reuse without a justification;
  map_first       — a map as the film's first picture when the opening
                    strategy is not important_location;
  holds           — shots shorter than the minimum hold;
  reveal          — a visual revealing evidence before the story does;
  honesty         — illustration shown without its label;
  music           — music-only share of the runtime;
  template_repeat — cross-film variety: against the latest production of
                    each of the previous VARIETY_WINDOW other cases in the
                    same language — same opening strategy, same first
                    shot, shared music tracks, near-identical cut rhythm.
                    Reported (what repeats, against which film), never
                    auto-fixed: it is a decision for the next film.
Repetition and map-first issues are fixed deterministically through the
usage tracker (an unused picture first; else the previous picture
holds). Then independent model critics (other model than the visual
director) judge the production per language from a compact, timestamped
description: automation feel, attention, visual accuracy, production.
Fixes are targeted (one shot, one beat) — never a full regeneration.
Each language is judged on its own; scores are never inherited.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import ProductionScript, VisualAsset
from app.documentary.visuals import rights as R
from app.documentary.visuals import spoilers as SP
from app.documentary.visuals.usage import (
    PICTURE_KINDS, UsageTracker, annotate, asset_facts, named_between, record_media_usage,
)
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

# Cross-film variety: how many earlier films (other cases, same language)
# a production is compared with, how many aspects must repeat for the
# film to count as a template copy, and how close two median shot
# lengths may be before the cut rhythm counts as the same.
VARIETY_WINDOW = 3
TEMPLATE_MIN_REPEATS = 2
RHYTHM_SAME_WITHIN = 0.05
# The rhythm check's upper limit leaves this much room above the
# visual_direction cut pattern (sentence snapping shortens some cuts).
RHYTHM_TOLERANCE = 1.3

CRITIC_ROLES = {
    "automation_feel": "automation_feel_critic",
    "attention": "attention_critic",
    "visual_accuracy": "visual_accuracy_critic",
    "production": "production_critic",
}

CRITIC_FOCUS = {
    "automation_feel": (
        "AUTOMATION FEEL. The critical question: at which timestamps does this "
        "feel algorithmically assembled, formulaic, repetitive, synthetic or "
        "machine-generated? (mechanical picture changes on a beat grid, the same "
        "camera move again and again, text cards that pop up on schedule, music "
        "that always enters the same way)."),
    "attention": (
        "ATTENTION. Is the viewer ever overloaded (reading + dense narration + "
        "new picture at once)? Too many cuts? Too little change for too long? "
        "Does each picture support the words instead of competing with them?"),
    "visual_accuracy": (
        "VISUAL ACCURACY. Could any picture mislead: a wrong person or place, a "
        "context/illustration picture that looks like case evidence, a picture "
        "that reveals something before the narration does?"),
    "production": (
        "PRODUCTION. Overall rhythm: music overuse, awkward silences, abrupt or "
        "monotonous transitions, holds that feel too long or too short."),
}

CRITIC_SYSTEM = """
You are a senior documentary editor reviewing a cut of a true-crime
documentary in {language}. You get a timestamped description of the
cut: narration excerpt, the picture on screen (what it shows, its role:
evidence|context|illustration, camera move, transition), on-screen text,
music events. Review ONLY this aspect:

{focus}

Be concrete and sparing: report real problems only (max 8), each with
the exact timestamp and shot index, severity (low|medium|high), why,
and ONE fix from this list:
  replace_picture (another picture or keep the previous one),
  keep_previous (do not change the picture here),
  change_motion (to: none|slow_push|slow_pull|pan_left|pan_right),
  black (words alone), remove_text, shorten_text, none.
Also give a score 0–100 for this aspect.

Return JSON only:
{{"score": 80, "problems": [{{"time": "01:23", "shot": 7, "beat_id": "B04",
  "severity": "medium", "why": "...", "fix": "keep_previous", "fix_detail": "..."}}],
 "summary": "one sentence"}}
"""


def _mmss(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def max_changes_per_minute() -> float:
    """Picture changes per minute that are still calm: the attention
    limit, or what the director's cut rhythm produces (plus room for
    sentence snapping), whichever is higher."""
    pattern = [float(x) for x in ai_config.visual_direction.cut_pattern if float(x) > 0]
    by_rhythm = 60.0 / (sum(pattern) / len(pattern)) * RHYTHM_TOLERANCE if pattern else 0.0
    return max(ai_config.attention.max_changes_per_minute, by_rhythm)


def repetition_issues(script: dict, assets: dict[str, VisualAsset] | None) -> list[dict]:
    """Pictures over their category's limit, reuses without a recorded
    justification, and a map as the first picture of a film whose
    opening is not about the place."""
    shots = sorted(script.get("shots") or [], key=lambda x: x["start"])
    tr = UsageTracker({c: asset_facts(a) for c, a in (assets or {}).items()})
    for s in shots:
        tr.learn(s)
    issues, prev = [], None
    for s in shots:
        code = s.get("asset_id")
        if not code or s.get("kind") not in PICTURE_KINDS:
            prev = None
            continue
        cont = prev is not None and tr.key(prev.get("asset_id") or "") == tr.key(code) \
            and abs(prev["end"] - s["start"]) <= 0.5
        if not cont:
            n, lim, cat = tr.appearances(code), tr.limit(code), tr.category(code)
            if n + 1 > lim:
                issues.append({"check": "repetition", "type": "over_limit", "severity": "medium",
                               "shot": s.get("index"), "time": _mmss(s["start"]),
                               "why": f"{code} ({cat}) on screen {n + 1}x — limit {lim}",
                               "fix": "replace_repeat"})
            elif n and (s.get("usage") or {}).get("repeat_justified") is False:
                issues.append({"check": "repetition", "type": "unjustified_repeat",
                               "severity": "medium", "shot": s.get("index"),
                               "time": _mmss(s["start"]),
                               "why": f"{code} shown again without a reason",
                               "fix": "replace_repeat"})
            tr.add(code, s["start"], s["end"])
        else:
            tr.add(code, prev["end"], s["end"])
        prev = s
    first = next((s for s in shots if s.get("kind") in PICTURE_KINDS), None)
    if (first is not None and first.get("kind") == "map"
            and script.get("opening_strategy") != "important_location"):
        issues.append({"check": "map_first", "severity": "medium", "shot": first.get("index"),
                       "time": _mmss(first["start"]),
                       "why": (f"a map is the first picture, but the opening is "
                               f"{script.get('opening_strategy') or 'not about the place'}"),
                       "fix": "replace_first_map"})
    return issues


def first_shot_kind(script: dict) -> str | None:
    """What the film opens on: the first picture's kind (and what it
    shows, when known) — 'image/person', 'map', 'video/place', 'black'."""
    shots = sorted(script.get("shots") or [], key=lambda x: x["start"])
    first = next((s for s in shots if s.get("kind") in PICTURE_KINDS), None) or \
        (shots[0] if shots else None)
    if first is None:
        return None
    kind = first.get("kind") or "black"
    return f"{kind}/{first['entity_type']}" if first.get("entity_type") and kind != "map" else kind


def music_codes(script: dict) -> set[str]:
    """Library tracks a film uses (room tone is not music: one serves every film)."""
    return {m["track_code"] for m in script.get("music") or []
            if m.get("track_code") and m.get("role") not in ("silence", "room_tone")}


def median_shot(script: dict) -> float | None:
    lengths = [s["end"] - s["start"] for s in script.get("shots") or [] if s["end"] > s["start"]]
    return round(statistics.median(lengths), 3) if lengths else None


def variety_check(script: dict, previous: list[dict]) -> list[dict]:
    """Cross-film variety: does this production repeat an earlier film
    (another case, same language) in TEMPLATE_MIN_REPEATS or more of:
    opening strategy, first shot, music tracks, cut rhythm (median shot
    length within RHYTHM_SAME_WITHIN)? previous: [{"id", "case_id",
    "script"}]. Issues list what repeats; they are not auto-fixed."""
    issues = []
    mine_open, mine_first = script.get("opening_strategy"), first_shot_kind(script)
    mine_music, mine_median = music_codes(script), median_shot(script)
    for p in previous or []:
        other = p.get("script") or {}
        repeats = []
        if mine_open and mine_open == other.get("opening_strategy"):
            repeats.append(f"opening strategy '{mine_open}'")
        if mine_first and mine_first == first_shot_kind(other):
            repeats.append(f"first shot '{mine_first}'")
        shared = sorted(mine_music & music_codes(other))
        if shared:
            repeats.append("music tracks " + ", ".join(shared))
        theirs = median_shot(other)
        if mine_median and theirs and abs(mine_median - theirs) / max(mine_median, theirs) \
                <= RHYTHM_SAME_WITHIN:
            repeats.append(f"cut rhythm (median shot {mine_median:.1f}s vs {theirs:.1f}s)")
        if len(repeats) >= TEMPLATE_MIN_REPEATS:
            issues.append({
                "check": "template_repeat", "type": "template_repeat", "severity": "medium",
                "against": {"production_script_id": p.get("id"), "case_id": p.get("case_id")},
                "repeats": repeats,
                "why": (f"repeats the film of case {p.get('case_id')} "
                        f"(production script {p.get('id')}): " + "; ".join(repeats))})
    return issues


def previous_productions(db: Session, row: ProductionScript,
                         n: int = VARIETY_WINDOW) -> list[dict]:
    """The latest production script of each of the n most recent OTHER
    cases in the same language (made before this one)."""
    rows = (db.query(ProductionScript)
            .filter(ProductionScript.language == row.language,
                    ProductionScript.case_id != row.case_id,
                    ProductionScript.id < row.id)
            .order_by(ProductionScript.id.desc()).limit(200).all())
    out, seen = [], set()
    for r in rows:
        if r.case_id in seen:
            continue
        seen.add(r.case_id)
        try:
            script = json.loads(r.script_json or "{}")
        except (TypeError, ValueError):
            continue
        out.append({"id": r.id, "case_id": r.case_id, "script": script})
        if len(out) >= n:
            break
    return out


def deterministic_checks(script: dict, assets: dict[str, VisualAsset],
                         previous: list[dict] | None = None) -> dict:
    att, motion = ai_config.attention, ai_config.motion
    shots = script.get("shots") or []
    duration = max(script.get("duration") or 0.0, 1.0)
    issues: list[dict] = []
    changes = sum(1 for s in shots if s.get("transition_in") in ("CROSSFADE", "FADE_BLACK", "CUT"))
    per_min = changes / (duration / 60)
    if duration >= 60 and per_min > max_changes_per_minute():
        issues.append({"check": "rhythm", "severity": "medium",
                       "why": f"{per_min:.1f} picture changes per minute"})
    if duration >= 120 and per_min < att.min_changes_per_minute:
        issues.append({"check": "rhythm", "severity": "low",
                       "why": f"only {per_min:.1f} picture changes per minute"})
    run, last = 0, None
    for s in shots:
        m = s.get("motion")
        if m in ("NONE", "CONTINUE", None):
            continue
        run = run + 1 if m == last else 1
        last = m
        if run > motion.max_same_motion_run:
            issues.append({"check": "repetition", "severity": "medium", "shot": s["index"],
                           "time": _mmss(s["start"]), "why": f"{m} {run} times in a row",
                           "fix": "change_motion"})
    uses = Counter(s.get("asset_id") for s in shots if s.get("kind") == "image" and not s.get("reframe"))
    for code, n in uses.items():
        if code and n > ai_config.documentary_critics.max_asset_reuse:
            issues.append({"check": "repetition", "severity": "low",
                           "why": f"{code} shown {n} times"})
    issues += repetition_issues(script, assets)
    for s in shots:
        length = s["end"] - s["start"]
        if length < motion.min_hold_seconds * 0.8 and s is not shots[-1]:
            issues.append({"check": "holds", "severity": "low", "shot": s["index"],
                           "time": _mmss(s["start"]), "why": f"shot only {length:.1f}s",
                           "fix": "keep_previous"})
    labels = [(o["start"], o["end"]) for o in script.get("overlays") or [] if o["kind"] == "label"]
    for s in shots:
        a = assets.get(s.get("asset_id") or "")
        if a and a.asset_role == "illustration" and s.get("kind") == "image":
            if not any(st <= s["start"] + 0.5 and en >= s["end"] - 0.5 for st, en in labels):
                issues.append({"check": "honesty", "severity": "high", "shot": s["index"],
                               "time": _mmss(s["start"]),
                               "why": "illustration without an on-screen label"})
    music = sum((m.get("duration") or 0) for m in script.get("music") or []
                if m.get("role") not in ("bed", "silence"))
    share = music / duration
    if share > ai_config.audio_direction.max_music_only_share * 1.2:
        issues.append({"check": "music", "severity": "medium",
                       "why": f"music-only share {share:.0%}"})
    if previous:
        issues += variety_check(script, previous)
    high = sum(1 for i in issues if i["severity"] == "high")
    return {"issues": issues, "changes_per_minute": round(per_min, 2),
            "music_only_share": round(share, 3), "high": high}


def describe_cut(script: dict, assets: dict[str, VisualAsset], words: list[dict]) -> str:
    """Compact, timestamped text version of the cut for the critics."""
    lines = []
    overlays = script.get("overlays") or []
    music = script.get("music") or []
    for s in script.get("shots") or []:
        a = assets.get(s.get("asset_id") or "")
        what = {"black": "black frame", "map": "map"}.get(s.get("kind"), None)
        if what is None:
            what = (a.description or a.caption or a.title) if a else s.get("kind")
        role = a.asset_role if a else "-"
        said = " ".join(w["word"] for w in words if s["start"] <= w["start"] < s["end"])
        texts = [f'{o["kind"]}: "{o["text"]}"' for o in overlays
                 if o["start"] < s["end"] and o["end"] > s["start"] and o["kind"] != "credit"]
        mus = [f'{m["role"]}({m.get("mood")})' for m in music
               if m["start"] < s["end"] and m["start"] + (m.get("duration") or 0) > s["start"]]
        lines.append(
            f"[{_mmss(s['start'])}-{_mmss(s['end'])}] shot {s['index']} {s.get('beat_id')} "
            f"{s['command']} {s.get('transition_in')} motion={s.get('motion')} "
            f"picture=({role}) {str(what)[:140]}"
            + (f" | text {'; '.join(texts)}" if texts else "")
            + (f" | music {', '.join(mus)}" if mus else "")
            + f"\n    narration: {said[:300]}")
    return "\n".join(lines)


def _replace(shots: list[dict], i: int, script: dict | None = None,
             assets: dict[str, VisualAsset] | None = None,
             allow_repeat: bool = True) -> dict | None:
    """Swap a picture for another picture of the same beat (its checked
    candidates and alternatives) that is not on screen just before or
    after — chosen by the usage tracker: an unused one first, a reuse
    only when justified. Returns the tracker's choice or None."""
    from app.documentary.production.script import _asset_info

    s = shots[i]
    near = {x.get("asset_id") for x in shots[max(0, i - 2):i + 3]}
    alts = {a["asset_id"]: a for a in s.get("alternatives") or [] if a.get("asset_id")}
    pool = list((script or {}).get("candidates", {}).get(s.get("beat_id"), [])) + list(alts)
    assets = assets or {}
    fwj = (script or {}).get("firewall") or {}
    fw = SP.Firewall(set(fwj.get("investigation") or ()), set(fwj.get("custody") or ()))
    pool = [c for c in dict.fromkeys(pool)
            if (c in alts and c not in assets)
            or (c in assets and assets[c].asset_type == "photo"
                and assets[c].verification_status != "rejected"
                and R.allowed(assets[c].rights_status)
                and not fw.blocks(s.get("beat_id"), assets[c]))]
    tr = UsageTracker.from_shots(shots, {c: asset_facts(a) for c, a in assets.items()},
                                 skip={i})
    names = named_between((script or {}).get("sentence_entities") or [], s["start"], s["end"])
    choice = tr.pick(pool, s["start"], s["end"], names, avoid=near, allow_repeat=allow_repeat)
    if choice is None:
        return None
    c = choice["asset_id"]
    info = _asset_info(assets[c]) if c in assets else dict(alts[c])
    for k in ("reframe", "clip_start", "clip_end", "map_paths", "place", "marker",
              "repeat_reason", "repeat_justified", "fill_reason", "black_filled"):
        s.pop(k, None)
    s.update({**info, "command": "NEW_IMAGE", "kind": "image",
              "fill_reason": f"critic fix: {choice['reason']}"})
    if choice.get("repeat"):
        s.update({"repeat_justified": choice["repeat_justified"],
                  "repeat_reason": choice["repeat_reason"]})
    if s.get("motion") in ("CROP_FOCUS", "CONTINUE", "NONE", "MAP_ZOOM",
                           "DOCUMENT_HIGHLIGHT", None):
        s["motion"] = "SLOW_PUSH"
    return choice


def _hold_previous(shots: list[dict], s: dict) -> bool:
    i = shots.index(s)
    if i == 0:
        return False
    prev = shots[i - 1]
    limit = ai_config.motion.max_hold_seconds
    if prev.get("kind") in ("image", "map", "document", "video") and s["end"] - prev["start"] <= limit:
        prev["end"] = s["end"]
        shots.remove(s)
        return True
    return False


def apply_fixes(script: dict, problems: list[dict],
                assets: dict[str, VisualAsset] | None = None) -> list[dict]:
    """Targeted fixes on this language's timeline (medium/high severity
    only, one fix per shot). Returns what was done."""
    done = []
    shots = script.get("shots") or []
    by_index = {s["index"]: s for s in shots}
    seen: set = set()
    ordered = sorted((p for p in problems if isinstance(p.get("shot"), int)),
                     key=lambda p: p["shot"])
    for p in ordered:
        if p.get("severity") not in ("medium", "high"):
            continue
        s = by_index.get(p["shot"])
        if s is None or p["shot"] in seen or s not in shots:
            continue
        seen.add(p["shot"])
        fix = p.get("fix")
        i = shots.index(s)
        if fix == "replace_repeat":
            before = s.get("asset_id")
            if _replace(shots, i, script, assets):
                done.append({"shot": p["shot"], "fix": "replace_repeat", "from": before,
                             "to": s.get("asset_id")})
            elif _hold_previous(shots, s):
                done.append({"shot": p["shot"], "fix": "keep_previous", "from": before,
                             "reason": "no other picture may be shown here"})
            continue
        if fix == "replace_first_map":
            if _replace(shots, i, script, assets, allow_repeat=False):
                done.append({"shot": p["shot"], "fix": "replace_first_map",
                             "to": s.get("asset_id")})
            elif i + 1 < len(shots):
                shots[i + 1]["start"] = s["start"]
                shots.remove(s)
                done.append({"shot": p["shot"], "fix": "drop_first_map"})
            if shots:
                shots[0]["transition_in"] = "FADE_BLACK"
            continue
        if fix == "change_motion":
            to = str(p.get("fix_detail") or "slow_push").upper()
            if to not in ("NONE", "SLOW_PUSH", "SLOW_PULL", "PAN_LEFT", "PAN_RIGHT"):
                to = "SLOW_PUSH" if s.get("motion") != "SLOW_PUSH" else "SLOW_PULL"
            s["motion"] = to
            done.append({"shot": p["shot"], "fix": "change_motion", "to": to})
        elif fix == "replace_picture" and _replace(shots, i, script, assets):
            done.append({"shot": p["shot"], "fix": "replace_picture", "to": s.get("asset_id")})
        elif fix in ("keep_previous", "replace_picture") and i > 0:
            prev = shots[i - 1]
            limit = ai_config.motion.max_hold_seconds
            if prev.get("kind") in ("image", "map", "document") and s["end"] - prev["start"] <= limit:
                prev["end"] = s["end"]
                shots.remove(s)
                done.append({"shot": p["shot"], "fix": "keep_previous"})
        elif fix == "black":
            # a black screen stays a short pause (attention.max_black_seconds):
            # a longer shot gets another picture, else black only for the
            # pause and the next picture comes early, else it stays
            cap = ai_config.attention.max_black_seconds
            nxt = shots[i + 1] if i + 1 < len(shots) else None
            cut = round(s["start"] + cap, 3)
            if s["end"] - s["start"] <= cap + 1.0:
                s.update({"kind": "black", "motion": "NONE", "path": None, "asset_id": None})
                done.append({"shot": p["shot"], "fix": "black"})
            elif _replace(shots, i, script, assets, allow_repeat=False):
                done.append({"shot": p["shot"], "fix": "replace_picture",
                             "to": s.get("asset_id"), "reason": "black would be too long"})
            elif (nxt is not None and nxt.get("kind") in ("image", "map", "document")
                  and nxt["end"] - cut <= ai_config.motion.max_hold_seconds):
                s.update({"kind": "black", "motion": "NONE", "path": None, "asset_id": None, "end": cut})
                nxt["start"] = cut
                done.append({"shot": p["shot"], "fix": "black", "seconds": cap})
        elif fix in ("remove_text", "shorten_text"):
            before = len(script.get("overlays") or [])
            script["overlays"] = [o for o in script.get("overlays") or []
                                  if not (o["kind"] in ("quote", "date", "place")
                                          and o["start"] < s["end"] and o["end"] > s["start"])]
            if len(script["overlays"]) != before:
                done.append({"shot": p["shot"], "fix": "remove_text"})
    for n, sh in enumerate(shots):
        sh["index"] = n
    if done and shots:
        # credits and the illustration label follow the corrected cut
        from app.documentary.production.script import credit_overlays, label_overlays

        dur = float(script.get("duration") or shots[-1]["end"])
        script["overlays"] = sorted(
            [o for o in script.get("overlays") or [] if o["kind"] not in ("credit", "label")]
            + credit_overlays(shots, dur)
            + label_overlays(shots, script.get("language") or "en", dur),
            key=lambda o: o["start"])
    return done


class DocumentaryCritics:
    def __init__(self):
        self.gen = get_generation_provider()

    async def review(self, db: Session, row: ProductionScript,
                     manifest_words: list[dict] | None = None, apply: bool = True) -> dict:
        from app.documentary.spoken import LANG_NAMES

        script = json.loads(row.script_json or "{}")
        if not manifest_words:  # caption chunks are precise enough for review
            manifest_words = [{"word": s["text"], "start": s["start"], "end": s["end"]}
                              for s in script.get("subtitles") or []]
        assets = {a.asset_code: a for a in db.query(VisualAsset).filter(
            VisualAsset.case_id == row.case_id).all()}
        previous = previous_productions(db, row)
        report = {"deterministic": deterministic_checks(script, assets, previous),
                  "critics": {}, "fixes": [],
                  "compared_with": [{"production_script_id": p["id"], "case_id": p["case_id"]}
                                    for p in previous]}
        cut = describe_cut(script, assets, manifest_words)
        async def critic(name: str, role: str):
            system = CRITIC_SYSTEM.format(language=LANG_NAMES.get(row.language, row.language),
                                          focus=CRITIC_FOCUS[name])
            with track_run(db, row.case_id, f"Documentary Critic: {name} ({row.language})",
                           input_summary=f"production_script={row.id}") as run:
                data, res = await self.gen.generate_structured(
                    role, system, json.dumps({"cut": cut}, ensure_ascii=False))
                stamp_run(run, res, role)
            data = data if isinstance(data, dict) else {}
            return name, {
                "score": data.get("score"), "summary": data.get("summary"),
                "problems": [p for p in data.get("problems") or [] if isinstance(p, dict)][:8],
                "model": getattr(res, "model", None),
            }

        from app.core.concurrency import gather_limited

        named = [(n, CRITIC_ROLES[n]) for n in ai_config.documentary_critics.critics
                 if CRITIC_ROLES.get(n)]
        # the critics are independent: all at once
        for name, entry in await gather_limited(None, [critic(n, r) for n, r in named]):
            report["critics"][name] = entry
        if apply and ai_config.documentary_critics.max_fix_iterations:
            problems = [p for c in report["critics"].values() for p in c["problems"]]
            problems += [i for i in report["deterministic"]["issues"] if i.get("fix")]
            report["fixes"] = apply_fixes(script, problems, assets)
            if report["fixes"]:
                # appearance numbers and justifications follow the fixed cut
                annotate(script.get("shots") or [],
                         {c: asset_facts(a) for c, a in assets.items()},
                         script.get("sentence_entities"))
                report["after_fixes"] = deterministic_checks(script, assets, previous)
                row.script_json = json.dumps(script, ensure_ascii=False)
                record_media_usage(db, row, script)
        scores = [c["score"] for c in report["critics"].values()
                  if isinstance(c.get("score"), (int, float))]
        report["score"] = round(sum(scores) / len(scores), 1) if scores else None
        row.critique_json = json.dumps(report, ensure_ascii=False)
        row.status = "reviewed"
        db.commit()
        return report
