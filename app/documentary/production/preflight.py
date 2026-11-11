"""The check of a finished timeline before anything is rendered, the same for every language. `repair` runs the passes that make a timeline
sound, in the order they depend on each other (no evidence on two graphics, every card its own time, no picture held too long, no stretch
that no shot covers, a picture between two gaps held, a short gap covered by its neighbours, what follows a gap cuts in); `problems`
reads the timeline again and lists what is still wrong. A render is never the place where a hole, a repeat or a stretch of black is found."""

from __future__ import annotations

from app.core.ai_config import ai_config
from app.documentary.production import card_audit as CA
from app.documentary.production import picture_rules as PR

EPS = 0.05


def fill_holes(shots: list[dict], duration: float | None = None) -> list[dict]:
    """Every stretch of the film that no shot covers becomes a bare moment (the gap rules then cover it with the neighbours or the drawing)."""
    shots.sort(key=lambda s: (s["start"], s["end"]))
    holes: list[dict] = []
    reach = 0.0
    for shot in shots:
        if shot["start"] > reach + EPS:
            holes.append(PR.bare(reach, shot["start"], shot.get("beat_id")))
        reach = max(reach, shot["end"])
    if duration is not None and duration > reach + EPS and shots:
        holes.append(PR.bare(reach, duration, shots[-1].get("beat_id")))
    shots.extend(holes)
    shots.sort(key=lambda s: (s["start"], s["end"]))
    return [{"fix": "hole_filled", "start": round(h["start"], 2), "seconds": round(h["end"] - h["start"], 2)} for h in holes]


def repair(shots: list[dict], duration: float | None, board: list[dict] | None = None) -> dict:
    """The passes, in order. Returns what each one changed: {pass: fixes}."""
    report = {"repeated_graphics": PR.drop_repeated_graphics(shots)}
    report["card_audit"] = CA.audit(shots, board)
    report["held_too_long"] = PR.limit_picture_seconds(shots)
    report["holes"] = fill_holes(shots, duration)
    report["between_gaps"] = PR.hold_pictures_between_gaps(shots)
    report["short_gaps"] = PR.cover_short_gaps(shots, duration)
    report["cut_in_after_gaps"] = PR.cut_in_after_gaps(shots)
    return report


def problems(shots: list[dict], duration: float | None = None, host: list[dict] | None = None) -> list[str]:
    """What is still wrong with the timeline, in plain words (nothing when `repair` has done its work). `host` is the film's host items:
    the avatar's video starts where its audio does and runs as long as its clip."""
    found: list[str] = []
    for shot in (s for s in shots if s.get("host_scene_id")):
        clip = float(shot.get("clip_end") or 0.0) - float(shot.get("clip_start") or 0.0)
        if abs((shot["end"] - shot["start"]) - clip) > 0.5:
            found.append(f"the host's video at {shot['start']:.1f}s runs {shot['end'] - shot['start']:.1f}s but its clip is {clip:.1f}s")
        item = next((h for h in host or [] if h.get("scene_id") == shot["host_scene_id"]), None)
        if item is not None and abs(item["start"] - shot["start"]) > 0.05:
            found.append(f"the host's video starts at {shot['start']:.1f}s but its audio is at {item['start']:.1f}s")
    ordered = sorted(shots, key=lambda s: (s["start"], s["end"]))
    for before, after in zip(ordered, ordered[1:], strict=False):
        if after["start"] > before["end"] + EPS:
            found.append(f"{after['start'] - before['end']:.1f}s at {before['end']:.1f}s is covered by no shot")
        elif after["start"] < before["end"] - EPS and before["kind"] != "black" and after["kind"] != "black":
            found.append(f"a {after['kind']} starts at {after['start']:.1f}s inside a {before['kind']} that runs to {before['end']:.1f}s")
    if duration is not None and ordered and duration > ordered[-1]["end"] + EPS:
        found.append(f"the last {duration - ordered[-1]['end']:.1f}s of the film is covered by no shot")
    seen: set[str] = set()
    for shot in (s for s in ordered if s["kind"] == "graphic"):
        ids = PR.fact_ids((shot.get("graphic") or {}).get("data"))
        if ids & seen and not shot.get("detective_reuse_reason"):
            found.append(f"the evidence {sorted(ids & seen)[0]} is on a second graphic at {shot['start']:.1f}s")
        seen |= ids
    limit = ai_config.motion.max_picture_seconds
    found += [f"a picture at {s['start']:.1f}s is held {s['end'] - s['start'] - s.get('gap_cover_seconds', 0.0):.1f}s (limit {limit:.0f}s)"
              for s in ordered if s["kind"] == "image" and s["end"] - s["start"] - s.get("gap_cover_seconds", 0.0) > limit + 0.5]
    gap = ai_config.drawing_settings().gap_full_seconds
    end = duration if duration is not None else ordered[-1]["end"] if ordered else 0.0
    found += [f"{s['end'] - s['start']:.1f}s of black at {s['start']:.1f}s is neither covered by its neighbours nor drawn"
              for s in ordered if s["kind"] == "black" and EPS < s["end"] - s["start"] < gap and s["start"] > 0.5 and s["end"] < end - 0.5
              and not s.get("left_black") and not s.get("intentional_gap")]
    found += [f"a shot fades up from black at {b['end']:.1f}s right after a gap" for b, s in zip(ordered, ordered[1:], strict=False)
              if b["kind"] == "black" and s.get("transition_in") == "FADE_BLACK"]
    return found
