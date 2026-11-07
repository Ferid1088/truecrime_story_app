"""A film's maps are the maps of its case's crime scenes, and of no other place: every crime scene has exactly one map, and a film has
at least `min_per_film` of them. Run on a language's finished beat plan (config zoom_map.crime_maps):

1. a map of a place that is not a crime scene, or a second map of a crime scene, becomes a bare stretch (the later passes fill it);
2. a crime scene without a map gets one at the stretch of the film where the story first names it (`mention_share`, the same stretch
   in every language, so no word of the narration has to be matched in another script), after the opening beats when the story never
   names it. The map takes a bare stretch that is long enough, else the time is cut out of a still picture that keeps its minimum.

A scene that no beat can hold is reported, never dropped silently."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.core.ai_config import CrimeMapsConfig
from app.documentary.mvp.crime_scenes import head, words
from app.documentary.mvp.needs import fold

BARE_SHARE = 0.8        # a bare stretch holds a map when it has this share of the map's seconds (as `add_map_cards` asks)


class Scene(Protocol):
    place: str
    mention_share: float | None


GapRuns = Callable[[list[dict], float], list[tuple[int, int, float]]]


def same_place(a: str, b: str) -> bool:
    """Two place strings name the same place when the words of the one's most specific part all stand in the other's."""
    x, y = words(head(a)), words(head(b))
    return bool(x and y) and (x <= y or y <= x)


def map_place(place: str, region: str | None) -> str:
    return f"{place}, {region}" if region and "," not in place else place


def map_shot(place: str, region: str | None, share: float, said: str, scene: str) -> dict:
    return {"command": "SHOW_MAP", "map_place": map_place(place, region), "share": share, "motion": "NONE", "speed": 1.0,
            "transition_in": "CROSSFADE", "said": said, "crime_scene": scene,
            "why": "the crime scene: a map shows where it happened"}


def bare_shot(share: float, said: str) -> dict:
    return {"command": "BLACK_SCREEN", "share": share, "motion": "NONE", "speed": 1.0, "transition_in": "CROSSFADE", "gap": True,
            "said": said, "why": "no approved visual for this moment"}


def _nearness(window: dict, at: float) -> tuple[float, int]:
    """How far the beat is from the moment `at` (0 inside it); of two beats as near, the one that starts at or after it wins."""
    inside = window["start"] <= at < window["end"]
    return (0.0 if inside else min(abs(at - window["start"]), abs(at - window["end"])), 0 if window["start"] >= at or inside else 1)


def _target_time(scene: Scene, windows: list[dict], opening_beats: int) -> float:
    ordered = sorted(windows, key=lambda w: w["start"])
    if scene.mention_share is not None:
        return scene.mention_share * ordered[-1]["end"]
    return ordered[min(opening_beats, len(ordered) - 1)]["start"]


def _into_bare_run(shots: list[dict], window_len: float, need: float, runs: GapRuns, scene: Scene,
                   region: str | None) -> bool:
    run = next((r for r in runs(shots, window_len) if r[2] >= BARE_SHARE * need), None)
    if run is None:
        return False
    first, last, seconds = run
    said = " ".join(s.get("said", "") for s in shots[first:last + 1]).strip()
    length = min(need, seconds)
    replacement = [map_shot(scene.place, region, length / window_len, said, scene.place)]
    if seconds - length > 1e-6:
        replacement.append(bare_shot((seconds - length) / window_len, said))
    shots[first:last + 1] = replacement
    return True


def _into_still(shots: list[dict], window_len: float, need: float, keep: float, scene: Scene, region: str | None) -> bool:
    """The map's seconds cut from the longest still picture of the beat that keeps `keep` seconds; the map follows it."""
    stills = [(i, s) for i, s in enumerate(shots)
              if s.get("command") == "NEW_IMAGE" and not s.get("fixed") and s["share"] * window_len - need >= keep]
    if not stills:
        return False
    i, still = max(stills, key=lambda p: p[1]["share"])
    still["share"] -= need / window_len
    shots.insert(i + 1, map_shot(scene.place, region, need / window_len, "", scene.place))
    return True


def enforce(out_beats: list[dict], window_of: dict[str, dict], scenes: list[Scene], region: str | None,
            need: float, runs: GapRuns, cfg: CrimeMapsConfig) -> dict:
    """Make the beat plan show one map of each crime scene and no other map. Returns what was kept, dropped, placed and left unplaced."""
    report: dict = {"scenes": [s.place for s in scenes], "kept": [], "dropped": [], "placed": [], "unplaced": []}
    held: dict[int, str] = {}
    for beat in out_beats:
        for n, shot in enumerate(beat["shots"]):
            if shot.get("command") != "SHOW_MAP":
                continue
            owner = next((k for k, s in enumerate(scenes) if same_place(shot.get("map_place") or "", s.place)), None)
            if owner is None or owner in held:
                beat["shots"][n] = bare_shot(shot["share"], shot.get("said", ""))
                report["dropped"].append({"beat": beat["beat_id"], "place": shot.get("map_place"),
                                          "why": "not a crime scene" if owner is None else "a second map of the same crime scene"})
            else:
                held[owner] = beat["beat_id"]
                shot["crime_scene"] = scenes[owner].place
                report["kept"].append({"beat": beat["beat_id"], "place": scenes[owner].place})
    windows = [window_of[b["beat_id"]] for b in out_beats]
    for k, scene in enumerate(scenes):
        if k in held:
            continue
        if not windows:
            report["unplaced"].append({"place": scene.place, "why": "the film has no beat"})
            continue
        at = _target_time(scene, windows, cfg.opening_beats)
        for beat in sorted(out_beats, key=lambda b: _nearness(window_of[b["beat_id"]], at)):
            window = window_of[beat["beat_id"]]
            window_len = window["end"] - window["start"]
            if window_len <= 0.05:
                continue
            how = ("bare stretch" if _into_bare_run(beat["shots"], window_len, need, runs, scene, region)
                   else "cut from a still" if _into_still(beat["shots"], window_len, need, cfg.carve_min_remaining_seconds, scene, region)
                   else None)
            if how:
                held[k] = beat["beat_id"]
                report["placed"].append({"beat": beat["beat_id"], "place": scene.place, "how": how})
                break
        else:
            report["unplaced"].append({"place": scene.place, "why": "no beat has a bare stretch or a still long enough"})
    report["maps"] = len(held)
    if len(held) < cfg.min_per_film:
        report["degraded"] = f"{len(held)} crime-scene map(s) in the film, at least {cfg.min_per_film} are required"
    return report
