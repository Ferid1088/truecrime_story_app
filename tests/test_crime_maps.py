"""Every film shows one map of each crime scene and of no other place (app/documentary/mvp/crime_maps.py)."""

from types import SimpleNamespace

from app.core.ai_config import ai_config
from app.documentary.mvp import crime_maps as CM
from app.documentary.mvp.direct import gap_runs, map_seconds

CFG = ai_config.zoom().crime_maps
NEED = map_seconds()


def scene(place, share=None):
    return SimpleNamespace(place=place, mention_share=share)


def image(share):
    return {"command": "NEW_IMAGE", "share": share, "asset_id": "A", "motion": "NONE", "speed": 1.0}


def gap(share, said=""):
    return CM.bare_shot(share, said)


def show(place, share):
    return {"command": "SHOW_MAP", "map_place": place, "share": share, "motion": "NONE", "speed": 1.0}


def film(*beats):
    """beats: (seconds, shots). Returns out_beats, window_of."""
    out, windows, at = [], {}, 0.0
    for n, (seconds, shots) in enumerate(beats, 1):
        bid = f"B{n:02d}"
        out.append({"beat_id": bid, "shots": shots})
        windows[bid] = {"beat_id": bid, "start": at, "end": at + seconds, "sentences": []}
        at += seconds
    return out, windows


def maps_of(out):
    return [s for b in out for s in b["shots"] if s.get("command") == "SHOW_MAP"]


def run(out, windows, scenes, region="Ohio, United States"):
    return CM.enforce(out, windows, scenes, region, NEED, gap_runs, CFG)


def test_a_map_of_a_place_that_is_no_crime_scene_is_dropped_and_the_scene_gets_its_map():
    out, windows = film((120.0, [image(0.5), show("Tipp City, Ohio", 0.2), image(0.3)]))
    report = run(out, windows, [scene("Cunningham Court, Tipp City, Ohio, United States", 0.1)])
    maps = maps_of(out)
    assert [m["crime_scene"] for m in maps] == ["Cunningham Court, Tipp City, Ohio, United States"]
    assert report["dropped"][0]["why"] == "not a crime scene" and report["maps"] == 1 and "degraded" not in report


def test_a_scene_the_story_never_names_gets_a_map_after_the_opening_beat():
    out, windows = film((60.0, [image(1.0)]), (60.0, [image(1.0)]), (60.0, [image(1.0)]))
    report = run(out, windows, [scene("Troy, Miami County, Ohio, United States", None)])
    assert [b["beat_id"] for b in out if maps_of([b])] == ["B02"] and report["placed"][0]["how"] == "cut from a still"


def test_the_map_takes_the_stretch_where_the_story_first_names_the_scene_in_any_language():
    out, windows = film((100.0, [image(1.0)]), (100.0, [image(1.0)]), (100.0, [image(1.0)]))
    run(out, windows, [scene("Cunningham Court, Ohio", 0.8)])
    assert [b["beat_id"] for b in out if maps_of([b])] == ["B03"]


def test_a_bare_stretch_is_used_before_a_still_is_cut():
    out, windows = film((100.0, [image(0.4), gap(0.5, "said here"), image(0.1)]))
    report = run(out, windows, [scene("Cunningham Court, Ohio", 0.5)])
    assert report["placed"][0]["how"] == "bare stretch" and maps_of(out)[0]["said"] == "said here"
    assert abs(sum(s["share"] for s in out[0]["shots"]) - 1.0) < 1e-9


def test_cutting_a_still_keeps_the_beat_whole_and_the_still_above_its_minimum():
    out, windows = film((100.0, [image(0.6), image(0.4)]))
    run(out, windows, [scene("Cunningham Court, Ohio", 0.5)])
    shares = [s["share"] for s in out[0]["shots"]]
    assert abs(sum(shares) - 1.0) < 1e-9
    assert all(s["share"] * 100.0 >= CFG.carve_min_remaining_seconds for s in out[0]["shots"] if s["command"] == "NEW_IMAGE")


def test_two_crime_scenes_get_one_map_each_and_a_second_map_of_a_scene_is_dropped():
    out, windows = film((120.0, [show("Cunningham Court, Ohio", 0.2), image(0.4), show("Cunningham Court home, Ohio", 0.1), image(0.3)]),
                        (120.0, [image(1.0)]))
    report = run(out, windows, [scene("Cunningham Court, Ohio", 0.1), scene("Garage, Ohio", 0.9)])
    assert sorted(m["crime_scene"] for m in maps_of(out)) == ["Cunningham Court, Ohio", "Garage, Ohio"]
    assert [d["why"] for d in report["dropped"]] == ["a second map of the same crime scene"]


def test_a_film_that_cannot_hold_a_map_reports_it_instead_of_dropping_it_silently():
    out, windows = film((8.0, [image(1.0)]))
    report = run(out, windows, [scene("Cunningham Court, Ohio", 0.5)])
    assert maps_of(out) == [] and report["unplaced"] and "at least 1" in report["degraded"]


def test_a_case_without_a_crime_scene_has_no_map_and_says_so():
    out, windows = film((100.0, [image(1.0)]))
    assert "degraded" in run(out, windows, [])


def test_the_place_given_to_the_map_search_carries_the_region_only_when_it_has_no_comma():
    assert CM.map_place("Troy", "Ohio, United States") == "Troy, Ohio, United States"
    assert CM.map_place("Troy, Ohio", "Ohio, United States") == "Troy, Ohio"
