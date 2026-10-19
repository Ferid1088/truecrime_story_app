"""The film's picture track: no long stretches of cards, no black, no endless still.

measure() reads a production script's shots; gate() says what breaks the rules of
visuals.coverage (black seconds, card share, longest card stretch, longest still);
repair() fills the offending stretches with illustrations (labelled "Illustration" on screen) and
never touches a rule: the plan is repaired, not the gate. A shot of kind black, title, chapter,
timeline or date is a "card"; pictures, maps, footage and the host are not."""

from __future__ import annotations

import asyncio
import copy
import json
import math

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, EditorialBlueprint, ProductionScript, VisualAsset, VisualPlan
from app.documentary.visuals import illustrations as IL
from app.documentary.visuals.usage import UsageTracker, asset_facts

CARD_KINDS = ("black", "title", "chapter", "timeline", "date")
KEEP_CARD_KINDS = ("title", "chapter", "timeline")      # a short card is allowed, a black stretch never
STILL_KINDS = ("image", "map", "document")
MOTIONS = ("SLOW_PUSH", "SLOW_PULL", "PAN_LEFT", "PAN_RIGHT", "SUBTLE_2_5D", "CROP_FOCUS")
EPS = 0.05


def _is_card(shot: dict) -> bool:
    return shot.get("kind") in CARD_KINDS


def measure(script: dict) -> dict:
    """What the picture track looks like: black seconds, card seconds and share, the longest run of
    consecutive cards and the longest single still, with every offending stretch."""
    shots = sorted(script.get("shots") or [], key=lambda s: s["start"])
    duration = float(script.get("duration") or (shots[-1]["end"] if shots else 0.0)) or 1.0
    black = sum(s["end"] - s["start"] for s in shots if s.get("kind") == "black")
    cards = sum(s["end"] - s["start"] for s in shots if _is_card(s))
    stretches: list[dict] = []
    for s in shots:
        if not _is_card(s):
            continue
        if stretches and abs(stretches[-1]["end"] - s["start"]) <= EPS:
            stretches[-1]["end"] = s["end"]
        else:
            stretches.append({"start": s["start"], "end": s["end"]})
    longest_card = max((x["end"] - x["start"] for x in stretches), default=0.0)
    stills = [{"start": s["start"], "end": s["end"], "asset_id": s.get("asset_id")}
              for s in shots if s.get("kind") in STILL_KINDS]
    longest_still = max((x["end"] - x["start"] for x in stills), default=0.0)
    return {"duration": round(duration, 2), "black_seconds": round(black, 2),
            "card_seconds": round(cards, 2), "card_share": round(cards / duration, 4),
            "longest_card_stretch": round(longest_card, 2),
            "longest_still": round(longest_still, 2),
            "card_stretches": [{"start": round(x["start"], 2), "end": round(x["end"], 2)}
                               for x in stretches if x["end"] - x["start"] > EPS],
            "long_stills": [{**x, "start": round(x["start"], 2), "end": round(x["end"], 2)}
                            for x in stills if x["end"] - x["start"]
                            > ai_config.coverage.max_still_seconds + EPS]}


def gate(report: dict) -> list[str]:
    """The rules the report breaks (empty: the picture track passes)."""
    cfg = ai_config.coverage
    out = []
    if report["black_seconds"] > cfg.max_black_seconds + EPS:
        out.append(f"{report['black_seconds']} s of black (max {cfg.max_black_seconds})")
    if report["card_share"] > cfg.max_card_share + 1e-6:
        out.append(f"cards are {report['card_share'] * 100:.1f}% of the film "
                   f"(max {cfg.max_card_share * 100:.0f}%)")
    if report["longest_card_stretch"] > cfg.max_card_stretch_seconds + EPS:
        out.append(f"longest card stretch {report['longest_card_stretch']} s "
                   f"(max {cfg.max_card_stretch_seconds})")
    if report["longest_still"] > cfg.max_still_seconds + EPS:
        out.append(f"a still is shown for {report['longest_still']} s (max {cfg.max_still_seconds})")
    return out


def _windows(script: dict) -> list[tuple[float, float]]:
    """The time ranges that need other pictures: black, cards beyond the stretch and share limits,
    and what a still shows beyond max_still_seconds. Returns them merged and sorted; the shots they
    cover are shortened or removed by apply()."""
    cfg = ai_config.coverage
    shots = sorted(script.get("shots") or [], key=lambda s: s["start"])
    duration = float(script.get("duration") or shots[-1]["end"])
    budget = cfg.max_card_share * duration
    wins: list[tuple[float, float]] = []
    run_start = None
    kept_run = 0.0
    for s in shots:
        if _is_card(s):
            if run_start is None or abs(run_end - s["start"]) > EPS:        # noqa: F821
                run_start, kept_run = s["start"], 0.0
            run_end = s["end"]
            length = s["end"] - s["start"]
            keep = (s["kind"] in KEEP_CARD_KINDS
                    and kept_run < cfg.max_card_stretch_seconds - EPS and budget > 0)
            if keep:
                room = min(length, cfg.max_card_stretch_seconds - kept_run, budget)
                kept_run += room
                budget -= room
                if length - room > EPS:
                    wins.append((s["start"] + room, s["end"]))
            else:
                wins.append((s["start"], s["end"]))
        elif s.get("kind") in STILL_KINDS and s["end"] - s["start"] > cfg.max_still_seconds + EPS:
            wins.append((s["start"] + cfg.max_still_seconds, s["end"]))
    merged: list[list[float]] = []
    for a, b in sorted(wins):
        if merged and a - merged[-1][1] <= EPS:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a > 0.5]


def _beat_at(script: dict, t: float) -> str | None:
    best = None
    for b in script.get("beats") or []:
        if b["start"] <= t < b["end"] + 1e-6:
            return b["beat_id"]
        if b["start"] <= t:
            best = b["beat_id"]
    return best or (script["beats"][0]["beat_id"] if script.get("beats") else None)


def _pieces(a: float, b: float) -> list[tuple[float, float]]:
    """Split a window into pictures of about seconds_per_picture each (none shorter than 3 s)."""
    target = ai_config.illustrations.seconds_per_picture
    n = max(1, round((b - a) / target))
    step = (b - a) / n
    return [(round(a + i * step, 3), round(a + (i + 1) * step, 3)) for i in range(n)]


def _cut(shots: list[dict], windows: list[tuple[float, float]]) -> list[dict]:
    """The shots without the windows: a shot overlapping one is shortened or split around it."""
    out: list[dict] = []
    for s in shots:
        pieces = [(s["start"], s["end"])]
        for a, b in windows:
            nxt = []
            for p0, p1 in pieces:
                if b <= p0 + EPS or a >= p1 - EPS:
                    nxt.append((p0, p1))
                    continue
                if a > p0 + EPS:
                    nxt.append((p0, a))
                if b < p1 - EPS:
                    nxt.append((b, p1))
            pieces = nxt
        for i, (p0, p1) in enumerate(pieces):
            c = copy.copy(s)
            c["start"], c["end"] = round(p0, 3), round(p1, 3)
            if i or p0 > s["start"] + EPS:
                c["transition_in"] = "CROSSFADE"
                if c.get("kind") == "video" and c.get("clip_start") is not None:
                    c["clip_start"] = round(float(c["clip_start"]) + (p0 - s["start"]), 3)
            if c["end"] - c["start"] > EPS:
                out.append(c)
    return out


def _illustration_shot(a: VisualAsset, start: float, end: float, beat_id: str | None,
                       n: int) -> dict:
    from app.documentary.production.script import _asset_info

    return {"beat_id": beat_id, "start": start, "end": end, "command": "ATMOSPHERIC_BROLL",
            "motion": MOTIONS[n % len(MOTIONS)], "speed": 0.8, "transition_in": "CROSSFADE",
            "why": "coverage repair: an illustration instead of a card or an endless still",
            "kind": "image", **_asset_info(a), "role": "illustration"}


async def _fill_slot(db: Session, case: Case, slot: tuple[float, float], beat: str | None,
                     sentences: list[str], story: dict, firewall, auditor, free: dict, everything: list,
                     used: set, blueprint: dict, requirements: dict, index: list[int]) -> dict | None:
    """One picture for one gap: an unused pool illustration the auditor approves for these exact
    words, else a new one drawn for them (and drawn again with the auditor's reasons when it
    rejects). None when nothing was approved."""
    cfg = ai_config.coverage
    beat_summary = next((b.get("summary") for b in blueprint.get("beats") or [] if b["id"] == beat), None)
    avoid: list[str] = []
    seen: set[str] = set()
    while len(seen) < cfg.pool_tries:
        a = next((x for items in (free.get(beat) or [], everything) for x in items
                  if x.asset_code not in used and x.asset_code not in seen), None)
        if a is None:
            break
        seen.add(a.asset_code)
        used.add(a.asset_code)                      # reserved while the auditor looks at it
        verdict, shown_as, reasons = await auditor.check(db, case, a, sentences, story, firewall)
        if verdict == "approved":
            return {"asset": a, "as": shown_as, "reasons": reasons}
        used.discard(a.asset_code)
        avoid += reasons[:2]
    keys = IL._beat_entities(requirements, beat or "")
    for attempt in range(cfg.slot_attempts):
        try:
            subject = await IL.slot_brief(db, case, sentences, beat_summary, avoid)
            png = await IL.generate_png(IL.image_prompt(subject))
        except Exception as e:  # noqa: BLE001 — a failed draw is one attempt less, not a failed film
            avoid.append(f"the picture could not be made ({type(e).__name__})")
            continue
        index[0] += 1
        a = IL.make_asset(db, case, png, {"subject": subject}, beat or "", 1000 + index[0], keys)
        IL.book(db, case.id, 1)
        used.add(a.asset_code)
        verdict, shown_as, reasons = await auditor.check(db, case, a, sentences, story, firewall)
        if verdict == "approved":
            return {"asset": a, "as": shown_as, "reasons": reasons, "attempts": attempt + 1}
        used.discard(a.asset_code)
        avoid += reasons[:2]
    return None


async def repair(db: Session, row: ProductionScript, script: dict) -> dict:
    """Fill the windows the gate names. Every new picture is audited for the exact words spoken over
    it before it goes in; a gap nothing approved fills is held by the neighbouring picture (never
    black) and reported with its seconds."""
    from app.agents.visual_auditor import VisualAuditor
    from app.documentary.visuals import spoilers as SP
    from app.documentary.visuals.auditor import plan_sentences, shot_sentences
    from app.documentary.visuals.usage import annotate, record_media_usage

    windows = _windows(script)
    if not windows:
        return {"windows": 0, "filled": 0}
    case = db.get(Case, row.case_id)
    bp_row = db.get(EditorialBlueprint, row.blueprint_id)
    plan_row = db.get(VisualPlan, row.visual_plan_id)
    blueprint = json.loads(bp_row.blueprint_json or "{}") if bp_row else {}
    requirements = json.loads(plan_row.requirements_json or "{}") if plan_row else {}
    plan = json.loads(plan_row.plan_json or "{}") if plan_row else {}
    texts, spans = plan_sentences(plan), script.get("sentence_entities") or []
    firewall = SP.Firewall.from_json(script.get("firewall"))
    shots = _cut(sorted(script.get("shots") or [], key=lambda s: s["start"]), windows)
    used = {s.get("asset_id") for s in shots if s.get("asset_id")}
    free = {b: [a for a in items if a.asset_code not in used]
            for b, items in IL.existing(db, row.case_id).items()}
    everything = [a for items in free.values() for a in items]
    slots = [(p, _beat_at(script, p[0])) for w in windows for p in _pieces(*w)]
    auditor = VisualAuditor()
    sem = asyncio.Semaphore(ai_config.illustrations.concurrency)
    counter = [0]

    async def one(slot, beat):
        sentences = shot_sentences({"start": slot[0], "end": slot[1]}, spans, texts)
        story = SP.story_point(blueprint, beat)
        async with sem:
            return await _fill_slot(db, case, slot, beat, sentences, story, firewall, auditor, free,
                                    everything, used, blueprint, requirements, counter)

    results = await asyncio.gather(*(one(sl, b) for sl, b in slots))
    filled, count, missing = 0, 0, []
    for ((start, end), beat), got in zip(slots, results):
        if got is None:
            missing.append((start, end))
            continue
        shot = _illustration_shot(got["asset"], start, end, beat, count)
        shot["audit"] = {"verdict": "approved", "as": got["as"], "reasons": got["reasons"][:3],
                         "try": got.get("attempts", 0)}
        shots.append(shot)
        filled += 1
        count += 1
    shots.sort(key=lambda s: (s["start"], s["end"]))
    held = _hold_gaps(shots, missing)
    for n, s in enumerate(shots):
        s["index"] = n
    script["shots"] = shots
    _relabel(script)
    annotate(shots, {a.asset_code: asset_facts(a) for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == row.case_id).all()}, spans)
    row.script_json = json.dumps(script, ensure_ascii=False)
    record_media_usage(db, row, script)
    db.commit()
    return {"windows": len(windows), "slots": len(slots), "filled": filled,
            "missing_seconds": round(sum(b - a for a, b in missing), 2), "held": held}


def _hold_gaps(shots: list[dict], missing: list[tuple[float, float]]) -> int:
    """A gap nothing approved fills is covered by the picture before it (else the one after), never
    by black; the gate then reports a still that runs long."""
    held = 0
    for a, b in missing:
        before = [s for s in shots if s["end"] <= a + EPS and s.get("kind") in ("image", "map", "video")]
        after = [s for s in shots if s["start"] >= b - EPS and s.get("kind") in ("image", "map")]
        if before and abs(before[-1]["end"] - a) <= EPS:
            before[-1]["end"] = b
            before[-1].setdefault("held", []).append({"at": round(a, 2), "why": "no approved picture"})
        elif after:
            after[0]["start"] = a
            after[0].setdefault("held", []).append({"at": round(a, 2), "why": "no approved picture"})
        else:
            continue
        held += 1
    return held


def no_black_fades(script: dict) -> int:
    """A picture, map or clip never fades in from black (the card shots keep their short fade): its
    first second would be a black screen. Returns how many shots were changed."""
    changed = 0
    for s in script.get("shots") or []:
        if s.get("transition_in") == "FADE_BLACK" and s.get("kind") in ("image", "map", "video", "document"):
            s["transition_in"] = "CROSSFADE"
            changed += 1
    return changed


def _relabel(script: dict) -> None:
    from app.documentary.production.script import credit_overlays, label_overlays

    shots = script["shots"]
    dur = float(script.get("duration") or shots[-1]["end"])
    script["overlays"] = sorted(
        [o for o in script.get("overlays") or [] if o["kind"] not in ("credit", "label")]
        + credit_overlays(shots, dur) + label_overlays(shots, script.get("language") or "en", dur),
        key=lambda o: o["start"])


async def ensure_coverage(db: Session, row: ProductionScript) -> dict:
    """Repair the film's picture track until the gate passes (visuals.coverage.max_repair_rounds)
    and have the visual auditor look at what was added; a picture it rejects is refilled."""
    cfg = ai_config.coverage
    script = json.loads(row.script_json)
    before = measure(script)
    rounds = []
    for n in range(1, cfg.max_repair_rounds + 1):
        problems = gate(measure(script))
        if not problems:
            break
        step = await repair(db, row, script)
        script["coverage_repairs"] = (script.get("coverage_repairs") or []) + [{"round": n, **step}]
        rounds.append({"round": n, **step, "after": gate(measure(script))})
        if not step.get("filled"):
            break                                   # nothing more can be made: report, do not loop
    script["faded_in_from_black_fixed"] = no_black_fades(script)
    after = measure(script)
    problems = gate(after)
    script["coverage"] = {"before": {k: before[k] for k in ("black_seconds", "card_share",
                                                            "longest_card_stretch", "longest_still")},
                          "after": {k: after[k] for k in ("black_seconds", "card_share",
                                                          "longest_card_stretch", "longest_still")},
                          "problems": problems, "rounds": rounds}
    row.script_json = json.dumps(script, ensure_ascii=False)
    db.commit()
    return {"rounds": len(rounds), "before": script["coverage"]["before"],
            "after": script["coverage"]["after"],
            **({"degraded": "picture track still breaks: " + "; ".join(problems)} if problems else {})}


def render_check(video, script: dict) -> dict:
    """After the render: black seconds measured in the FILE (ffmpeg blackdetect), plus the card share,
    longest card stretch and longest still of the script it was made from."""
    import re
    import subprocess

    from app.core.media_tools import ffmpeg_bin

    run = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(video), "-vf",
         f"blackdetect=d={ai_config.coverage.black_screen_min_seconds}:pix_th=0.05", "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    black = sum(float(m) for m in re.findall(r"black_duration:([\d.]+)", run.stderr))
    report = measure(script)
    report["file_black_seconds"] = round(black, 2)
    problems = gate({**report, "black_seconds": max(report["black_seconds"], black)})
    return {**{k: report[k] for k in ("black_seconds", "file_black_seconds", "card_share",
                                       "longest_card_stretch", "longest_still")},
            "problems": problems}


def window_cards(script: dict, start: float, end: float) -> tuple[float, float]:
    """(card seconds, longest card stretch) of the film inside [start, end]: a short cut from a
    stretch of cards would be a text frame, not a picture."""
    total, longest, run_end, run = 0.0, 0.0, None, 0.0
    for s in sorted(script.get("shots") or [], key=lambda x: x["start"]):
        a, b = max(s["start"], start), min(s["end"], end)
        if b <= a or not _is_card(s):
            continue
        total += b - a
        run = run + (b - a) if run_end is not None and abs(run_end - a) <= EPS else (b - a)
        run_end = b
        longest = max(longest, run)
    return round(total, 2), round(longest, 2)
