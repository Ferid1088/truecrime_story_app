"""Where and how fast the pencil drawing runs, and whether every gap of the film has a portrait.

A portrait is drawn in `base_seconds` of drawing time at normal speed; the planner runs it between `min_speed` and
`max_speed`. It is on screen in a corner window over the film's pictures and clips, and over the whole screen in every gap of
`gap_full_seconds` or more; over a card, the board or a map it is hidden and waits. A gap is never left without a portrait: a
portrait may only start once its person has been named in the film (no spoiler), so for each gap the planner takes the first
photo, in the owner's priority order, that is allowed at that moment. A gap that no photo of the pool may cover is returned, not
dropped: the run stops and asks the owner."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.ai_config import ai_config


@dataclass
class Portrait:
    code: str
    priority: int
    earned_from: float = 0.0              # the first moment the film may show this person (their name has been spoken)
    refused: str | None = None            # why the photo cannot be drawn at all (injury, nothing to draw), if so


@dataclass
class Interval:
    start: float
    end: float
    mode: str                              # "window" over a picture or clip, "full" in a gap

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass
class DrawingPlan:
    segments: list[dict] = field(default_factory=list)      # {start, end, mode, portrait, from, speed}
    portraits: list[dict] = field(default_factory=list)     # {code, started, finished} in the order they are drawn
    speed: float = 1.0
    fits: bool = False                                      # a number of portraits that finish by the film's end inside the speed range, no gap bare
    uncovered: list[dict] = field(default_factory=list)     # gaps of `gap_full_seconds` or more that no photo may cover
    tried: list[dict] = field(default_factory=list)         # for each photo of the pool: why it could not cover the first gap


def intervals_of(shots: list[dict]) -> list[Interval]:
    """The moments the drawing is on screen: a window over every picture and clip, the whole screen in every black stretch of
    `gap_full_seconds` or more (a shorter one is covered by its neighbours and never drawn), cards hide the drawing. Gaps one after the
    other are one gap."""
    cfg = ai_config.drawing_settings()
    out = []
    ordered: list[dict] = []
    for shot in sorted(shots, key=lambda s: s["start"]):                  # black stretches next to each other are one stretch
        if ordered and shot.get("kind") == "black" and ordered[-1].get("kind") == "black" and shot["start"] - ordered[-1]["end"] < 1e-3:
            ordered[-1] = {**ordered[-1], "end": shot["end"], "bare": bool(ordered[-1].get("bare") or shot.get("bare"))}
        else:
            ordered.append(shot)
    for shot in ordered:
        if shot.get("kind") in ("image", "video"):
            out.append(Interval(shot["start"], shot["end"], "window"))
        elif (shot.get("bare") or shot.get("kind") == "black") and shot["end"] - shot["start"] >= cfg.gap_full_seconds:
            out.append(Interval(shot["start"], shot["end"], "full"))
    merged: list[Interval] = []
    for item in out:                                          # gaps one after the other are one gap: the window grows once
        if merged and item.mode == "full" and merged[-1].mode == "full" and item.start - merged[-1].end < 1e-3:
            merged[-1].end = item.end
        else:
            merged.append(item)
    return merged


def _simulate(intervals: list[Interval], pool: list[Portrait], speed: float, limit: int) -> DrawingPlan:
    """The drawing over the film at `speed` with at most `limit` portraits started, each started when the film may show it."""
    cfg = ai_config.drawing_settings()
    plan = DrawingPlan(speed=speed)
    usable = sorted((p for p in pool if p.refused is None), key=lambda p: p.priority)
    done: set[str] = set()
    current: Portrait | None = None
    progress = 0.0                                          # drawing seconds already drawn of the current portrait
    started = 0
    for item in intervals:
        t = item.start
        while t < item.end - 1e-6:
            if current is None:
                waiting = [p for p in usable if p.code not in done] if started < limit else []
                current = next((p for p in waiting if p.earned_from <= t + 1e-6), None)
                if current is None:
                    later = min((p.earned_from for p in waiting if p.earned_from < item.end), default=None)
                    resume = item.end if later is None else later    # until a portrait may be shown: nothing to draw
                    if item.mode == "full":
                        plan.uncovered.append({"start": round(t, 3), "end": round(resume, 3)})
                    if later is None:
                        break
                    t = later
                    continue
                progress, started = 0.0, started + 1
                plan.portraits.append({"code": current.code, "started": round(t, 3), "finished": None})
            take = min(item.end - t, (cfg.base_seconds - progress) / speed)
            plan.segments.append({"start": round(t, 3), "end": round(t + take, 3), "mode": item.mode,
                                  "portrait": current.code, "from": round(progress, 3), "speed": speed})
            progress += take * speed
            t += take
            if progress >= cfg.base_seconds - 1e-6:
                plan.portraits[-1]["finished"] = round(t, 3)
                done.add(current.code)
                current = None
    return plan


def plan_drawing(shots: list[dict], pool: list[Portrait]) -> DrawingPlan:
    """The drawing for this film: the number of portraits and the speed that make every drawing finish by the film's end, nearest
    to normal speed, with no gap left without a portrait. When no number works, the plan with the fewest uncovered gaps."""
    cfg = ai_config.drawing_settings()
    intervals = intervals_of(shots)
    usable = [p for p in pool if p.refused is None]
    first = min((p.earned_from for p in usable), default=0.0)         # nothing is drawn before the film may show a portrait
    total = sum(max(i.end - max(i.start, first), 0.0) for i in intervals)
    best: DrawingPlan | None = None
    for count in sorted(range(1, len(usable) + 1), key=lambda n: abs(cfg.base_seconds * n / max(total, 1e-6) - 1.0)):
        speed = cfg.base_seconds * count / max(total, 1e-6)
        if not cfg.min_speed <= speed <= cfg.max_speed:
            continue
        plan = _simulate(intervals, pool, speed, count)
        if not plan.uncovered:
            plan.fits = True
            return plan
        if best is None or len(plan.uncovered) < len(best.uncovered):
            best = plan
    if usable and total * cfg.max_speed <= cfg.base_seconds:     # a film too short to finish even one portrait: one portrait, fastest speed
        short = _simulate(intervals, pool, cfg.max_speed, 1)
        if not short.uncovered:
            short.fits = True
            return short
    if best is None:                                        # no count is inside the speed range: the film is too short or too long
        speed = min(max(cfg.base_seconds * max(len(usable), 1) / max(total, 1e-6), cfg.min_speed), cfg.max_speed)
        best = _simulate(intervals, pool, speed, max(len(usable), 1))
    first_gap = next((i for i in intervals if i.mode == "full"), None)
    best.tried = [{"code": p.code, "priority": p.priority,
                   "why": p.refused or (f"the person is first named at {p.earned_from:.0f} s"
                                        if first_gap is not None and p.earned_from > first_gap.start else "usable")}
                  for p in sorted(pool, key=lambda p: p.priority)]
    return best


def uncoverable(gaps: list[dict], pool: list[Portrait]) -> list[dict]:
    """The gaps of `gap_full_seconds` or more that no photo of the pool may cover when they come (every photo is refused, or its person
    has not been named yet): {start, end}. A quick check before the film is planned in detail."""
    cfg = ai_config.drawing_settings()
    usable = [p for p in pool if p.refused is None]
    return [{"start": g["start"], "end": g["end"]} for g in sorted(gaps, key=lambda g: g["start"])
            if g["end"] - g["start"] >= cfg.gap_full_seconds and not any(p.earned_from <= g["start"] + 1e-6 for p in usable)]


def reasons(pool: list[Portrait], at: float) -> list[dict]:
    """For each photo of the pool, why it cannot be shown at `at` (or that it can)."""
    return [{"code": p.code, "priority": p.priority,
             "why": p.refused or (f"the person is first named at {p.earned_from:.0f} s" if p.earned_from > at else "usable")}
            for p in sorted(pool, key=lambda p: p.priority)]


def estimated_shots(duration: float, gaps: list[dict], card_seconds: float) -> list[dict]:
    """The film's shots as far as the picture plan knows them (before the production script exists): every gap as it is, and the
    pictures spread over the rest of the film, less the seconds the cards take."""
    cfg = ai_config.drawing_settings()
    ordered = sorted((g for g in gaps if g["end"] - g["start"] >= cfg.gap_full_seconds), key=lambda g: g["start"])   # shorter gaps: neighbours
    spans, t = [], 0.0
    for g in ordered:
        spans.append((t, g["start"]))
        t = g["end"]
    spans.append((t, duration))
    room = sum(max(b - a, 0.0) for a, b in spans)
    pictures = max(room - card_seconds, 0.0) / room if room else 0.0
    shots = []
    for (a, b), gap in zip(spans, [*ordered, None], strict=True):
        if b - a > 1e-6 and pictures > 0:
            shots.append({"kind": "image", "start": a, "end": a + (b - a) * pictures})
        if gap is not None:
            shots.append({"kind": "black", "start": gap["start"], "end": gap["end"],
                          "bare": True})
    return shots
