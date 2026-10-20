"""Media coverage of a film (Part 4a): how much of the film's time is
carried by case-specific pictures rather than generic filler, per minute and
per beat, with a gap list. Deterministic, from the production script's own
timeline (every picture shot already carries its usage category).

  case-specific  evidence + person + map + context pictures
  exact          evidence + person + map only (the strict number)
  generic        generic atmosphere pictures
  uncovered      time with no picture shot (holding the last picture, black)
"""

from __future__ import annotations

from app.core.ai_config import ai_config
from app.documentary.visuals.usage import (
    CONTEXT,
    EVIDENCE,
    GENERIC,
    MAP,
    PERSON,
    PICTURE_KINDS,
)

CASE_SPECIFIC = (EVIDENCE, PERSON, MAP, CONTEXT)
EXACT = (EVIDENCE, PERSON, MAP)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _seconds(shots: list[dict], start: float, end: float) -> dict[str, float]:
    out = {EVIDENCE: 0.0, PERSON: 0.0, MAP: 0.0, CONTEXT: 0.0, GENERIC: 0.0}
    for s in shots:
        cat = (s.get("usage") or {}).get("category")
        if cat in out:
            out[cat] += _overlap(s["start"], s["end"], start, end)
    return out


def _share(value: float, total: float) -> float:
    return round(value / total, 4) if total > 0 else 0.0


def coverage_report(shots: list[dict], duration: float, beats: list[dict] | None = None) -> dict:
    """shots: production-script shots ({start, end, kind, beat_id, usage}).
    beats: [{"beat_id", "start", "end"}] on the film's timeline (optional)."""
    pictures = [s for s in shots or [] if s.get("kind") in PICTURE_KINDS and s.get("usage")]
    total = _seconds(pictures, 0.0, duration)
    case_specific = sum(total[c] for c in CASE_SPECIFIC)
    exact = sum(total[c] for c in EXACT)
    covered = case_specific + total[GENERIC]
    minutes = []
    for m in range(int(duration // 60) + (1 if duration % 60 else 0)):
        lo, hi = m * 60.0, min((m + 1) * 60.0, duration)
        sec = _seconds(pictures, lo, hi)
        minutes.append({"minute": m + 1,
                        "case_specific_share": _share(sum(sec[c] for c in CASE_SPECIFIC), hi - lo)})
    threshold = ai_config.visual_direction.min_beat_coverage
    per_beat, gaps = [], []
    for b in beats or []:
        length = b["end"] - b["start"]
        sec = _seconds(pictures, b["start"], b["end"])
        share = _share(sum(sec[c] for c in CASE_SPECIFIC), length)
        row = {"beat_id": b["beat_id"], "seconds": round(length, 2), "case_specific_share": share}
        per_beat.append(row)
        if share < threshold:
            gaps.append({**row, "reason": (
                f"only {round(share * 100)}% of the beat shows case-specific pictures "
                f"(minimum {round(threshold * 100)}%)")})
    return {
        "duration_seconds": round(duration, 2),
        "case_specific_share": _share(case_specific, duration),
        "exact_share": _share(exact, duration),
        "generic_share": _share(total[GENERIC], duration),
        "uncovered_share": _share(max(duration - covered, 0.0), duration),
        "seconds_by_category": {k: round(v, 2) for k, v in total.items()},
        "per_minute": minutes,
        "per_beat": per_beat,
        "gaps": gaps,
        "min_beat_coverage": threshold,
    }


def beat_windows(shots: list[dict]) -> list[dict]:
    """Beat start/end on the film's timeline, from the shots that name a beat."""
    spans: dict[str, list[float]] = {}
    for s in shots or []:
        bid = s.get("beat_id")
        if not bid:
            continue
        lo, hi = spans.setdefault(bid, [s["start"], s["end"]])
        spans[bid] = [min(lo, s["start"]), max(hi, s["end"])]
    return [{"beat_id": b, "start": w[0], "end": w[1]}
            for b, w in sorted(spans.items(), key=lambda kv: kv[1][0])]
