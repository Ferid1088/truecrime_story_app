"""Documentary critics and the critic loop (Parts 40–42).

Deterministic checks run first (they cost nothing and never hallucinate):
  rhythm        — visual changes per minute within limits;
  repetition    — the same motion pattern too often in a row, an asset
                  reused too often;
  holds         — shots shorter than the minimum hold;
  reveal        — a visual revealing evidence before the story does;
  honesty       — illustration shown without its label;
  music         — music-only share of the runtime.
Then independent model critics (other model than the visual director)
judge the production per language from a compact, timestamped
description: automation feel, attention, visual accuracy, production.
Fixes are targeted (one shot, one beat) — never a full regeneration.
Each language is judged on its own; scores are never inherited.
"""

from __future__ import annotations

import json
from collections import Counter

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import ProductionScript, VisualAsset
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

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


def deterministic_checks(script: dict, assets: dict[str, VisualAsset]) -> dict:
    att, motion = ai_config.attention, ai_config.motion
    shots = script.get("shots") or []
    duration = max(script.get("duration") or 0.0, 1.0)
    issues: list[dict] = []
    changes = sum(1 for s in shots if s.get("transition_in") in ("CROSSFADE", "FADE_BLACK", "CUT"))
    per_min = changes / (duration / 60)
    if duration >= 60 and per_min > att.max_changes_per_minute:
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


def _replace(shots: list[dict], i: int) -> bool:
    """Swap a picture for another candidate of the same beat that is not
    on screen just before or after."""
    s = shots[i]
    near = {x.get("asset_id") for x in shots[max(0, i - 2):i + 3]}
    alt = next((a for a in s.get("alternatives") or [] if a["asset_id"] not in near), None)
    if not alt:
        return False
    s.update({**alt, "command": "NEW_IMAGE", "kind": "image"})
    s.pop("reframe", None)
    if s.get("motion") in ("CROP_FOCUS", "CONTINUE", "NONE"):
        s["motion"] = "SLOW_PUSH"
    return True


def apply_fixes(script: dict, problems: list[dict]) -> list[dict]:
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
        if fix == "change_motion":
            to = str(p.get("fix_detail") or "slow_push").upper()
            if to not in ("NONE", "SLOW_PUSH", "SLOW_PULL", "PAN_LEFT", "PAN_RIGHT"):
                to = "SLOW_PUSH" if s.get("motion") != "SLOW_PUSH" else "SLOW_PULL"
            s["motion"] = to
            done.append({"shot": p["shot"], "fix": "change_motion", "to": to})
        elif fix == "replace_picture" and _replace(shots, i):
            done.append({"shot": p["shot"], "fix": "replace_picture", "to": s.get("asset_id")})
        elif fix in ("keep_previous", "replace_picture") and i > 0:
            prev = shots[i - 1]
            limit = ai_config.motion.max_still_seconds * 2
            if prev.get("kind") in ("image", "map", "document") and s["end"] - prev["start"] <= limit:
                prev["end"] = s["end"]
                shots.remove(s)
                done.append({"shot": p["shot"], "fix": "keep_previous"})
        elif fix == "black":
            s.update({"kind": "black", "motion": "NONE", "path": None})
            done.append({"shot": p["shot"], "fix": "black"})
        elif fix in ("remove_text", "shorten_text"):
            before = len(script.get("overlays") or [])
            script["overlays"] = [o for o in script.get("overlays") or []
                                  if not (o["kind"] in ("quote", "date", "place")
                                          and o["start"] < s["end"] and o["end"] > s["start"])]
            if len(script["overlays"]) != before:
                done.append({"shot": p["shot"], "fix": "remove_text"})
    for n, sh in enumerate(shots):
        sh["index"] = n
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
        report = {"deterministic": deterministic_checks(script, assets), "critics": {},
                  "fixes": []}
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
            report["fixes"] = apply_fixes(script, problems)
            if report["fixes"]:
                report["after_fixes"] = deterministic_checks(script, assets)
                row.script_json = json.dumps(script, ensure_ascii=False)
        scores = [c["score"] for c in report["critics"].values()
                  if isinstance(c.get("score"), (int, float))]
        report["score"] = round(sum(scores) / len(scores), 1) if scores else None
        row.critique_json = json.dumps(report, ensure_ascii=False)
        row.status = "reviewed"
        db.commit()
        return report
