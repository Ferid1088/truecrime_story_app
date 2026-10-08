"""Visual Director (Master task §9): entity-aware planning, sentence-level
direction with tiers and footage, maps by geography, production-time
search for weak sentences, repetition control with MediaUsage, the
status card, and the cross-film variety critic. Fakes only, no network."""
import asyncio
import json
import shutil

import pytest

from app.core.ai_config import ai_config
from app.db.models import (
    DocumentaryJob, EditorialBlueprint, MediaUsage, MusicUsage, ProductionScript, VisualAsset,
    VisualPlan,
)
from app.documentary.blueprint import validate_blueprint
from app.documentary.production.critics import (
    DocumentaryCritics, apply_fixes, deterministic_checks, repetition_issues, variety_check,
)
from app.documentary.production.script import compose, link_music_usage, status_overlays
from app.documentary.visuals.director import (
    VisualDirector, assign_motion, ensure_found_used, max_shots, sentence_marks,
    validate_visual_plan,
)
from app.documentary.visuals.gaps import build_requests, fill_visual_gaps, request_queries
from app.documentary.visuals.planner import (
    research_queries, sentence_entities, validate_requirements,
)
from app.documentary.visuals.typography import overlay
from app.documentary.visuals.usage import UsageTracker, record_media_usage
from app.providers.generation.base import GenerationResult
from test_blueprint_performance import PACK, SECTIONS, _good, _no_contradiction, _story
from test_visual_production import _asset, _jpg
from test_visual_production import documentary_env  # noqa: F401,F811 (fixture)

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


def _bp():
    return validate_blueprint(_good(), SECTIONS, PACK)[0]


def _res(role):
    return GenerationResult(text="{}", model=f"m/{role}", provider="fake")


ENTITIES = [
    {"key": "anna_keller", "type": "person", "name": "Anna Keller", "aliases": ["Anna"],
     "search_queries": ["Anna Keller Stendal"], "context_queries": [], "footage": False},
    {"key": "st_marys_church", "type": "building", "name": "St Mary's Church", "aliases": [],
     "search_queries": ["St Mary's Church Stendal"],
     "context_queries": ["Stendal old town brick church"], "footage": False},
    {"key": "market_square", "type": "place", "name": "Stendal market square",
     "aliases": ["market square"], "search_queries": ["Stendal Marktplatz"],
     "context_queries": [], "footage": True},
    {"key": "nannup", "type": "place", "name": "Nannup, Western Australia", "aliases": [],
     "search_queries": ["Nannup"], "context_queries": [], "footage": True},
    {"key": "perth", "type": "place", "name": "Perth", "aliases": [],
     "search_queries": ["Perth"], "context_queries": [], "footage": True},
]


# ---------------------------------------------------------------------------
# planner: entity types, exact/context queries, which entities a sentence names
# ---------------------------------------------------------------------------


def test_planner_entities_carry_type_aliases_exact_and_context_queries():
    raw = {"entities": [
        {"key": "Anna Keller", "type": "person", "name": "Anna Keller", "aliases": ["Anna", "Anna"],
         "search_queries": ["Anna Keller Stendal"], "context_queries": [], "footage": "maybe"},
        {"key": "forest", "type": "landscape", "name": "Letzlinger Heide",
         "search_queries": ["Letzlinger Heide"],
         "context_queries": ["pine forest Altmark", "heathland Saxony-Anhalt"]},
        {"key": "car", "type": "vehicle", "name": "the blue Golf", "search_queries": [],
         "context_queries": ["1998 Volkswagen Golf blue"], "footage": False},
    ], "beats": [{"beat_id": "B01", "requirements": [
        {"entity": "car", "priority": "low"}, {"entity": "anna_keller", "priority": "high"},
        {"entity": "forest", "priority": "medium"}]}]}
    reqs, _ = validate_requirements(raw, _bp(), {"facts": [], "timeline": []}, [])
    ents = {e["key"]: e for e in reqs["entities"]}
    assert ents["anna_keller"]["aliases"] == ["Anna"] and ents["anna_keller"]["footage"] is False
    assert ents["forest"]["type"] == "place" and ents["forest"]["footage"] is True
    assert ents["car"]["search_queries"] == ["the blue Golf"]  # never empty: the name
    q = research_queries(reqs)
    # exact queries (by need) before every context query
    assert [x["kind"] for x in q] == ["exact"] * 3 + ["context"] * 3
    assert q[0] == {"query": "Anna Keller Stendal", "entity": "anna_keller",
                    "entities": ["anna_keller"], "entity_type": "person", "kind": "exact",
                    "footage": False}
    assert q[1]["entity"] == "forest" and q[1]["footage"] is True
    assert q[3]["query"] == "pine forest Altmark" and q[3]["entity_type"] == "place"
    assert [x["query"] for x in research_queries(reqs, max_queries=4)][-1] == "pine forest Altmark"


@pytest.mark.parametrize("text, keys", [
    ("Anna Keller was nineteen.", ["anna_keller"]),
    ("That evening KELLER was seen near the market square.", ["anna_keller", "market_square"]),
    ("Anna never came home.", ["anna_keller"]),
    ("Annabelle never came home.", []),                       # whole words only
    ("He walked into St Mary's Church shortly before 7 PM.", ["st_marys_church"]),
    ("He walked into st. mary’s church.", ["st_marys_church"]),
    ("Police searched the Stendal Market Square.", ["market_square"]),
    ("They flew from Perth to Nannup.", ["perth", "nannup"]),
    ("Nobody saw him leave.", []),
])
def test_sentence_entities_full_names_last_names_places_aliases(text, keys):
    assert sentence_entities(text, ENTITIES) == keys


def test_sentence_entities_ignore_accents_and_case():
    ents = [{"key": "jurgen", "type": "person", "name": "Jürgen Weiß"},
            {"key": "zurich", "type": "place", "name": "Zürich"}]
    assert sentence_entities("JURGEN WEISS left Zurich in 2004.", ents) == ["jurgen", "zurich"]
    assert sentence_entities("Mr Weiss stayed.", ents) == ["jurgen"]


# ---------------------------------------------------------------------------
# director validation: density, clips, search requests, fallbacks
# ---------------------------------------------------------------------------


def _reqs(**beats):
    out = {"entities": ENTITIES, "beats": []}
    for bid in ("B01", "B02", "B03", "B04", "B05"):
        out["beats"].append({"beat_id": bid, "requirements": [], "map_place": None,
                             "date_text": None, "quote": None, "document": None,
                             **beats.get(bid, {})})
    return out


def test_density_follows_seconds_per_shot_and_one_shot_per_sentence():
    vd = ai_config.visual_direction
    long_beat = {"id": "BX", "words": 600}          # 240 s
    short_beat = {"id": "BY", "words": 10}          # 4 s
    assert max_shots(long_beat) == vd.max_shots_per_beat
    assert max_shots(short_beat) == 2
    assert max_shots({"id": "BZ", "words": 150}) == round(60 / vd.seconds_per_shot)
    assert max_shots(long_beat, sentences=3) == 3
    bp = _bp()
    a = {f"VIS_00000{i}": _asset(f"VIS_00000{i}") for i in range(1, 4)}
    marks = {"B02": sentence_marks(" ".join(["One two three four five six seven eight."] * 3))}
    raw = {"beats": [{"beat_id": "B02", "shots": [
        {"command": "NEW_IMAGE", "asset_id": "VIS_000001", "from_sentence": 0},
        {"command": "NEW_IMAGE", "asset_id": "VIS_000002", "from_sentence": 0},  # same sentence
        {"command": "NEW_IMAGE", "asset_id": "VIS_000003", "from_sentence": 2}]}]}
    plan, rep = validate_visual_plan(raw, bp, _reqs(), {"B02": list(a)}, a, marks)
    b2 = next(b for b in plan["beats"] if b["beat_id"] == "B02")
    assert [s["asset_id"] for s in b2["shots"]] == ["VIS_000001", "VIS_000003"]
    assert {"beat": "B02", "dropped": "NEW_IMAGE", "reason": "one_shot_per_sentence"} \
        in rep["adjustments"]


def test_request_search_leaves_the_shots_and_fallback_prefers_an_unused_candidate():
    bp = _bp()
    unused = _asset("VIS_000005", relevance_tier=2)
    worse = _asset("VIS_000006", relevance_tier=4)
    marks = {"B01": sentence_marks("Anna Keller lived in Stendal. "
                                   "He walked into St Mary's Church shortly before 7 PM.")}
    raw = {"beats": [{"beat_id": "B01", "shots": [
        {"command": "REQUEST_SEARCH", "from_sentence": 1, "entity": "St Mary's Church",
         "queries": ["St Mary's Church Stendal interior"], "why": "no church picture"}]}]}
    plan, rep = validate_visual_plan(raw, bp, _reqs(), {"B01": ["VIS_000006", "VIS_000005"]},
                                     {"VIS_000005": unused, "VIS_000006": worse}, marks)
    req = plan["search_requests"][0]
    assert req == {"beat_id": "B01", "from_sentence": 1,
                   "sentence": "He walked into St Mary's Church shortly before 7 PM.",
                   "entity": "st_marys_church", "entity_name": "St Mary's Church",
                   "queries": ["St Mary's Church Stendal interior"], "why": "no church picture",
                   "source": "director"}
    b1 = plan["beats"][0]
    # never a REQUEST_SEARCH shot; nothing else planned -> the unused
    # verified candidate of the lowest tier (not a map, not black)
    assert [s["command"] for s in b1["shots"]] == ["NEW_IMAGE"]
    assert b1["shots"][0]["asset_id"] == "VIS_000005"
    assert b1["shots"][0]["why"].startswith("fallback: unused verified candidate (tier 2)")
    assert b1["sentences"][1]["entities"] == ["st_marys_church"]
    assert b1["sentences"][0]["entities"] == ["anna_keller"]
    assert rep["search_requests"] == 1


def test_fallback_order_document_date_hold_black_never_a_map():
    bp = _bp()
    reqs = _reqs(B01={"map_place": "Nannup, Western Australia", "date_text": "May 2006"},
                 B02={"map_place": "Perth"})
    plan, _ = validate_visual_plan({"beats": []}, bp, reqs, {}, {})
    cmds = [b["shots"][0]["command"] for b in plan["beats"]]
    assert cmds[0] == "SHOW_DATE"          # a date card, not the map
    assert cmds[1] == "BLACK_SCREEN"       # nothing on screen yet, no map by default
    assert "SHOW_MAP" not in cmds


# ---------------------------------------------------------------------------
# J: maps by geography
# ---------------------------------------------------------------------------


def _map_case(opening):
    bp = _bp()
    v1, v2 = _asset("VIS_000001", entity_type="person"), _asset("VIS_000002")
    marks = {
        "B01": sentence_marks("Leela was nineteen. She loved the river."),
        "B02": sentence_marks("Every morning she walked to work along the road. "
                              "The family had moved to Nannup two years before."),
        "B04": sentence_marks("Back in Nannup the house was empty."),
        "B05": sentence_marks("The trail led to Perth."),
    }
    reqs = _reqs(B01={"map_place": "Nannup, Western Australia"},
                 B02={"map_place": "Nannup, Western Australia"},
                 B04={"map_place": "Nannup, Western Australia"})
    raw = {"beats": [
        {"beat_id": "B01", "shots": [{"command": "SHOW_MAP", "from_sentence": 0},
                                     {"command": "NEW_IMAGE", "asset_id": "VIS_000001",
                                      "from_sentence": 1}]},
        {"beat_id": "B02", "shots": [{"command": "NEW_IMAGE", "asset_id": "VIS_000002",
                                      "from_sentence": 0},
                                     {"command": "SHOW_MAP", "place": "Nannup",
                                      "from_sentence": 1}]},
        {"beat_id": "B04", "shots": [{"command": "SHOW_MAP", "from_sentence": 0}]},
        {"beat_id": "B05", "shots": [{"command": "SHOW_MAP", "place": "Perth",
                                      "from_sentence": 0}]},
    ]}
    return validate_visual_plan(raw, bp, reqs, {"B01": ["VIS_000001"], "B02": ["VIS_000002"]},
                                {"VIS_000001": v1, "VIS_000002": v2}, marks,
                                opening={"strategy": opening})


def test_maps_appear_where_a_place_is_introduced_never_first_never_twice():
    plan, rep = _map_case("victim_introduction")
    b = {x["beat_id"]: x for x in plan["beats"]}
    # the opening shows the person, not a map
    assert [s["command"] for s in b["B01"]["shots"]] == ["NEW_IMAGE"]
    assert b["B01"]["shots"][0]["share"] == 1.0
    # the map comes at the sentence that introduces Nannup
    b2 = b["B02"]["shots"]
    assert [s["command"] for s in b2] == ["NEW_IMAGE", "SHOW_MAP"]
    assert b2[1]["from_sentence"] == 1 and b2[1]["map_place"] == "Nannup, Western Australia"
    assert b["B02"]["sentences"][1]["entities"] == ["nannup"]
    # the same place again: removed (the beat holds the picture instead)
    assert all(s["command"] != "SHOW_MAP" for s in b["B04"]["shots"])
    # another place is fine
    assert b["B05"]["shots"][0]["command"] == "SHOW_MAP"
    assert b["B05"]["shots"][0]["map_place"] == "Perth"
    codes = [w["code"] for w in rep["warnings"]]
    assert "map_too_early" in codes and "place_already_mapped" in codes


def test_an_opening_about_the_place_may_start_on_its_map():
    plan, rep = _map_case("important_location")
    b = {x["beat_id"]: x for x in plan["beats"]}
    assert b["B01"]["shots"][0]["command"] == "SHOW_MAP"
    # mapped once: the B02 map of the same place is removed
    assert [s["command"] for s in b["B02"]["shots"]] == ["NEW_IMAGE"]
    assert "map_too_early" not in [w["code"] for w in rep["warnings"]]


def test_map_first_in_a_script_is_flagged_and_replaced():
    shots = [
        {"index": 0, "kind": "map", "start": 0.0, "end": 10.0, "asset_id": "VIS_000050",
         "beat_id": "B01", "transition_in": "FADE_BLACK", "motion": "MAP_ZOOM",
         "map_paths": ["m.jpg"], "type": "map", "place": "Nannup"},
        {"index": 1, "kind": "image", "start": 10.0, "end": 20.0, "asset_id": "VIS_000001",
         "beat_id": "B01", "transition_in": "CROSSFADE", "motion": "SLOW_PUSH",
         "alternatives": [{"asset_id": "VIS_000007", "path": "p7.jpg", "kind": "image"}]}]
    script = {"duration": 20.0, "shots": shots, "opening_strategy": "victim_introduction",
              "candidates": {"B01": ["VIS_000007"]}}
    issues = repetition_issues(script, {})
    assert [i["check"] for i in issues] == ["map_first"]
    done = apply_fixes(script, issues, {"VIS_000007": _asset("VIS_000007")})
    assert done[0]["fix"] == "replace_first_map" and shots[0]["asset_id"] == "VIS_000007"
    assert shots[0]["kind"] == "image" and "map_paths" not in shots[0]
    script["opening_strategy"] = "important_location"
    shots[0].update({"kind": "map", "asset_id": "VIS_000050"})
    assert repetition_issues(script, {}) == []


# ---------------------------------------------------------------------------
# footage: SHOW_CLIP
# ---------------------------------------------------------------------------


def _clip(code="VIS_000020", **kw):
    return _asset(code, asset_type="video", asset_role="context", local_path="clips/c.mp4",
                  clip_start=0.0, clip_end=20.0, duration_seconds=20.0, relevance_tier=3,
                  entity_type="place", **kw)


def test_show_clip_only_for_footage_and_becomes_a_video_shot():
    bp = _bp()
    clip, photo = _clip(), _asset("VIS_000001")
    assets = {"VIS_000020": clip, "VIS_000001": photo}
    marks = {"B01": sentence_marks("The town lay under fog. The road ran north.")}
    raw = {"beats": [
        {"beat_id": "B01", "shots": [{"command": "SHOW_CLIP", "asset_id": "VIS_000020",
                                      "from_sentence": 0, "clip_start": 4.0}]},
        {"beat_id": "B02", "shots": [{"command": "SHOW_CLIP", "asset_id": "VIS_000001",
                                      "from_sentence": 0}]},
        {"beat_id": "B03", "shots": [{"command": "NEW_IMAGE", "asset_id": "VIS_000020",
                                      "from_sentence": 0}]},
    ]}
    cands = {"B01": ["VIS_000020"], "B02": ["VIS_000001"], "B03": ["VIS_000020"]}
    plan, rep = validate_visual_plan(raw, bp, _reqs(), cands, assets, marks)
    b = {x["beat_id"]: x["shots"][0] for x in plan["beats"]}
    assert b["B01"]["command"] == "SHOW_CLIP"
    assert (b["B01"]["clip_start"], b["B01"]["clip_end"]) == (4.0, 20.0)
    assert b["B02"]["command"] == "NEW_IMAGE"                  # a photo is no clip
    assert "clip_not_video" in [w["code"] for w in rep["warnings"]]
    assert b["B03"]["command"] == "SHOW_CLIP"                  # footage plays as a clip
    plan = assign_motion(plan, bp, None, assets)
    assert b["B01"]["motion"] == "NONE"
    # composed: a "video" shot with its clip window, no camera move
    m = {"duration_seconds": 24.0, "files": {"narration_wav": "n.wav"},
         "timeline": {"beats": [{"beat_id": "B01", "start": 0.0, "end": 24.0}],
                      "blocks": [], "words": []}}
    one = {"beats": [plan["beats"][0]], "candidates": {"B01": ["VIS_000020"]}}
    s = compose(m, one, assets, {}, "en")
    shot = s["shots"][0]
    assert shot["kind"] == "video" and shot["command"] == "SHOW_CLIP"
    assert shot["path"] == "clips/c.mp4" and shot["motion"] == "NONE"
    assert (shot["clip_start"], shot["clip_end"]) == (4.0, 20.0)


# ---------------------------------------------------------------------------
# I: repetition control + MediaUsage
# ---------------------------------------------------------------------------


def _usage_assets():
    common = dict(rights_status="creative_commons", verification_status="verified")
    return {
        # the victim's portrait: a person, exact (tier 2)
        "VIS_000011": _asset("VIS_000011", relevance_tier=2, entity_type="person",
                             entity_key="anna_keller", entities_json='["anna_keller"]', **common),
        # a contextual street picture: generic for repetition purposes
        "VIS_000012": _asset("VIS_000012", relevance_tier=4, asset_role="context",
                             entity_type="place", entity_key="market_square",
                             entities_json='["market_square"]', **common),
        "VIS_000013": _asset("VIS_000013", relevance_tier=3, entity_type="building",
                             entities_json='["the_house"]', **common),
        "VIS_000014": _asset("VIS_000014", relevance_tier=3, entity_type="building",
                             entities_json='["the_house"]', **common),
        "VIS_000015": _asset("VIS_000015", relevance_tier=3, entity_type="building",
                             entities_json='["the_house"]', **common),
    }


def _usage_case():
    m = {"duration_seconds": 120.0, "files": {"narration_wav": "n.wav"},
         "timeline": {"beats": [{"beat_id": f"B0{i}", "start": (i - 1) * 30.0,
                                 "end": i * 30.0 - 0.5} for i in range(1, 5)],
                      "blocks": [], "words": [],
                      "sentences": [{"start": t, "end": t + 14.5, "display": f"s{t:g}",
                                     "speech": f"s{t:g}"} for t in range(0, 120, 15)]}}

    def sents(*ents):
        return [{"n": k, "at": k * 0.5, "text": "", "entities": list(e)} for k, e in enumerate(ents)]

    plan = {"candidates": {"B01": ["VIS_000011", "VIS_000012"], "B02": ["VIS_000013"],
                           "B03": ["VIS_000014", "VIS_000012", "VIS_000011"],
                           "B04": ["VIS_000015", "VIS_000011"]},
            "beats": [
                {"beat_id": "B01", "sentences": sents(["anna_keller"], []), "shots": [
                    {"command": "NEW_IMAGE", "asset_id": "VIS_000011", "share": 0.5,
                     "motion": "SLOW_PUSH", "why": "we meet Anna"},
                    {"command": "NEW_IMAGE", "asset_id": "VIS_000012", "share": 0.5,
                     "motion": "PAN_LEFT", "why": "her street"}]},
                {"beat_id": "B02", "sentences": sents([], []), "shots": [
                    {"command": "NEW_IMAGE", "asset_id": "VIS_000013", "share": 1.0,
                     "motion": "SLOW_PULL", "why": "the house"}]},
                {"beat_id": "B03", "sentences": sents([], ["anna_keller"]), "shots": [
                    {"command": "NEW_IMAGE", "asset_id": "VIS_000012", "share": 0.5,
                     "motion": "PAN_RIGHT", "why": "the street again"},
                    {"command": "NEW_IMAGE", "asset_id": "VIS_000011", "share": 0.5,
                     "motion": "SLOW_PUSH", "why": "Anna again"}]},
                {"beat_id": "B04", "sentences": sents([], ["anna_keller"]), "shots": [
                    {"command": "NEW_IMAGE", "asset_id": "VIS_000015", "share": 0.5,
                     "motion": "SLOW_PULL", "why": "the back door"},
                    {"command": "NEW_IMAGE", "asset_id": "VIS_000011", "share": 0.5,
                     "motion": "SLOW_PUSH", "why": "Anna, once more"}]},
            ]}
    return m, plan


def test_generic_picture_not_twice_person_recurs_when_named_with_gap(monkeypatch):
    # repetition only: the 30 s holds stay holds (long holds are tested elsewhere)
    monkeypatch.setattr(ai_config.motion, "max_hold_seconds", 40.0)
    m, plan = _usage_case()
    s = compose(m, plan, _usage_assets(), {}, "en")
    shots = s["shots"]
    seq = [(x["asset_id"], x["start"], x["end"]) for x in shots]
    assert seq == [("VIS_000011", 0.0, 15.0), ("VIS_000012", 15.0, 30.0),
                   ("VIS_000013", 30.0, 60.0), ("VIS_000014", 60.0, 75.0),
                   ("VIS_000011", 75.0, 90.0), ("VIS_000015", 90.0, 120.0)]
    # the street (generic) was planned twice: an unused picture took its place
    assert "VIS_000012 was already shown 1x" in shots[3]["fill_reason"]
    assert sum(1 for x in shots if x["asset_id"] == "VIS_000012") == 1
    # Anna returns 60 s later while the narration names her: justified
    u = shots[4]["usage"]
    assert u["appearance"] == 2 and u["repeat_justified"] is True
    assert "named in the sentence being spoken (anna_keller)" in u["repeat_reason"]
    assert u["category"] == "person" and u["tier"] == 2
    # ... but not 15 s after that: the picture on screen holds instead
    gap = ai_config.visual_direction.min_repeat_gap_seconds
    assert shots[5]["held"][0]["instead_of"] == "VIS_000011"
    assert f"min {gap:.0f}s apart" in shots[5]["held"][0]["why"]
    assert all(x["usage"]["appearance"] == 1 for x in shots if x["asset_id"] != "VIS_000011")


def test_media_usage_rows_record_reasons_and_appearances(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    case, master = _story(db_session)
    assets = _usage_assets()
    for a in assets.values():
        a.id = None
        a.case_id = case.id
        db_session.add(a)
    db_session.commit()
    monkeypatch.setattr(ai_config.motion, "max_hold_seconds", 40.0)
    m, plan = _usage_case()
    script = compose(m, plan, assets, {}, "en")
    row = ProductionScript(case_id=case.id, story_version_id=master.id, language="en",
                           script_json=json.dumps(script))
    db_session.add(row)
    db_session.commit()
    n = record_media_usage(db_session, row, script, "bp77")
    rows = (db_session.query(MediaUsage).filter_by(production_script_id=row.id)
            .order_by(MediaUsage.shot_index).all())
    assert n == len(rows) == 6
    ids = {a.asset_code: a.id for a in assets.values()}
    anna = [r for r in rows if r.asset_id == ids["VIS_000011"]]
    assert [r.appearance for r in anna] == [1, 2]
    assert anna[0].reason == "we meet Anna" and anna[0].repeat_justified is None
    assert anna[1].repeat_justified is True and "named" in anna[1].repeat_reason
    assert anna[1].sentence == "s75" and anna[1].seconds == 15.0
    swap = rows[3]
    assert swap.asset_id == ids["VIS_000014"] and "already shown" in swap.reason
    assert all(r.film_key == "bp77" and r.language == "en" and r.kind == "image" for r in rows)
    assert rows[0].tier == 2 and rows[1].tier == 4
    # delete + insert: a second write replaces the rows
    record_media_usage(db_session, row, script, "bp77")
    assert db_session.query(MediaUsage).filter_by(production_script_id=row.id).count() == 6


def test_fills_prefer_unused_pictures_and_never_repeat_a_generic_one():
    assets = _usage_assets()
    m = {"duration_seconds": 60.0, "files": {"narration_wav": "n.wav"},
         "timeline": {"beats": [{"beat_id": "B01", "start": 0.0, "end": 19.5},
                                {"beat_id": "B02", "start": 20.0, "end": 59.5}],
                      "blocks": [], "words": []}}
    plan = {"candidates": {"B01": ["VIS_000012"], "B02": ["VIS_000013", "VIS_000014"]},
            "beats": [
                {"beat_id": "B01", "shots": [{"command": "NEW_IMAGE", "asset_id": "VIS_000012",
                                              "share": 1.0, "motion": "PAN_LEFT"}]},
                {"beat_id": "B02", "shots": [{"command": "BLACK_SCREEN", "share": 1.0}]}]}
    s = compose(m, plan, assets, {}, "en")
    fills = [x for x in s["shots"] if x.get("black_filled")]
    # the long black pause is filled with the unused house pictures, never
    # with the generic street picture shown before
    assert fills and all(x["asset_id"] in ("VIS_000013", "VIS_000014") for x in fills)
    assert all("unused picture" in x["fill_reason"] for x in fills[:2])
    assert sum(1 for x in s["shots"] if x["asset_id"] == "VIS_000012") == 1 if all(
        "asset_id" in x for x in s["shots"]) else True


def test_tracker_limits_per_category():
    vd = ai_config.visual_direction
    f = {"G": {"asset_id": "G", "type": "photo", "tier": 5, "entities": []},
         "C": {"asset_id": "C", "type": "photo", "tier": 3, "role": "context", "entities": []},
         "P": {"asset_id": "P", "type": "photo", "tier": 2, "entity_type": "person",
               "entities": ["anna"]},
         "M1": {"asset_id": "M1", "type": "map", "tier": 3, "place": "Nannup"},
         "M2": {"asset_id": "M2", "type": "map", "tier": 3, "place": "nannup"}}
    tr = UsageTracker(f)
    for code in f:
        tr.add(code, 0.0, 5.0) if code != "M2" else None
    assert tr.category("G") == "generic" and tr.category("C") == "context"
    assert tr.allows("G", 100, 105) == (False, f"generic picture already shown 1x (limit {vd.max_generic_appearances})")
    assert tr.allows("C", 100, 105)[0] is (vd.max_context_appearances > 1)
    assert tr.allows("M2", 100, 105)[0] is False          # one map per place
    assert tr.allows("P", 10, 15)[0] is False              # too soon
    assert tr.allows("P", 5.0, 9.0)[0] is True             # continues the shot
    choice = tr.pick(["G", "P"], 100, 105, names={"anna"})
    assert choice["asset_id"] == "P" and choice["repeat_justified"] is True


# ---------------------------------------------------------------------------
# C: status card
# ---------------------------------------------------------------------------


def _short_manifest(duration=60.0):
    return {"duration_seconds": duration, "files": {"narration_wav": "n.wav"},
            "timeline": {"beats": [{"beat_id": "B01", "start": 0.0, "end": duration}],
                         "blocks": [], "words": []}}


def _one_picture_plan():
    return {"candidates": {}, "beats": [{"beat_id": "B01", "shots": [
        {"command": "NEW_IMAGE", "asset_id": "VIS_000013", "share": 1.0, "motion": "SLOW_PUSH"}]}]}


def test_unsolved_case_gets_the_status_card_at_start_and_end():
    ym = ai_config.youtube_metadata
    assets = _usage_assets()
    s = compose(_short_manifest(), _one_picture_plan(), assets, {}, "en", case_status="UNSOLVED",
                opening_strategy="unanswered_question")
    status = [o for o in s["overlays"] if o["kind"] == "status"]
    assert [o["text"] for o in status] == ["UNSOLVED CASE", "UNSOLVED CASE"]
    assert (status[0]["start"], status[0]["end"]) == tuple(ym.status_card_seconds)
    assert status[1]["start"] == 60.0 - ym.status_card_end_seconds and status[1]["end"] == 59.0
    assert s["case_status"] == "UNSOLVED" and s["opening_strategy"] == "unanswered_question"
    de = compose(_short_manifest(), _one_picture_plan(), assets, {}, "de", case_status="UNSOLVED")
    assert {o["text"] for o in de["overlays"] if o["kind"] == "status"} == {"UNGEKLÄRTER FALL"}
    solved = compose(_short_manifest(), _one_picture_plan(), assets, {}, "en", case_status="SOLVED")
    assert not [o for o in solved["overlays"] if o["kind"] == "status"]
    assert status_overlays("UNKNOWN", "original", "en", 60.0) == []
    # a follow-up opens with "now solved"; a short pilot has no closing card
    fu = status_overlays("SOLVED", "follow_up", "en", 60.0)
    assert [(o["text"], o["start"]) for o in fu] == [("CASE NOW SOLVED", ym.status_card_seconds[0])]
    assert len(status_overlays("UNSOLVED", "original", "en", 15.0)) == 1


def test_script_music_carries_track_and_reasons():
    m = _short_manifest()
    m["mix"] = {"film_key": "bp12", "placements": [
        {"role": "bridge", "cue_id": "bridge_mystery_v2", "mood": "mystery", "start": 20.0,
         "duration": 6.0, "level_db": -18, "track_code": "bridge_mystery_v2",
         "why": "after the reveal", "selection_reason": "unused in the last 10 films",
         "path": "x.wav"}]}
    s = compose(m, _one_picture_plan(), _usage_assets(), {}, "en")
    assert s["music"] == [{"role": "bridge", "cue_id": "bridge_mystery_v2", "mood": "mystery",
                           "start": 20.0, "duration": 6.0, "level_db": -18,
                           "track_code": "bridge_mystery_v2", "why": "after the reveal",
                           "selection_reason": "unused in the last 10 films"}]


def test_status_stamp_is_drawn_on_the_reading_side():
    W, H = 640, 360
    for lang, text, side in (("en", "UNSOLVED CASE", "left"), ("fa", "پرونده‌ی حل‌نشده", "right")):
        layer = overlay("status", text, lang, W, H)
        alpha = layer.split()[-1]
        left = alpha.crop((0, 0, W // 2, H // 3)).getbbox()
        right = alpha.crop((W // 2, 0, W, H // 3)).getbbox()
        assert (left if side == "left" else right) is not None
        assert (right if side == "left" else left) is None
        assert alpha.crop((0, H // 2, W, H)).getbbox() is None   # never over subtitles


# ---------------------------------------------------------------------------
# N: cross-film variety
# ---------------------------------------------------------------------------


def _film(opening, first_kind="image", first_type="person", tracks=("bridge_mystery_v1",),
          shot=8.0, n=10):
    shots = [{"index": i, "kind": first_kind if i == 0 else "image", "command": "NEW_IMAGE",
              "entity_type": first_type if i == 0 else None,
              "asset_id": f"VIS_{i:06d}", "start": i * shot, "end": (i + 1) * shot}
             for i in range(n)]
    return {"duration": n * shot, "opening_strategy": opening, "shots": shots, "overlays": [],
            "music": [{"role": "bridge", "track_code": t, "start": 10.0, "duration": 5.0}
                      for t in tracks] + [{"role": "silence", "track_code": "room_tone_v1",
                                           "start": 30.0, "duration": 3.0}]}


def test_variety_critic_flags_a_template_copy_and_passes_a_different_film():
    prev = [{"id": 41, "case_id": 7, "script": _film("victim_introduction")}]
    same = _film("victim_introduction", shot=8.2)
    issues = variety_check(same, prev)
    assert len(issues) == 1 and issues[0]["check"] == "template_repeat"
    rep = " | ".join(issues[0]["repeats"])
    assert "opening strategy 'victim_introduction'" in rep
    assert "first shot 'image/person'" in rep and "bridge_mystery_v1" in rep
    assert "cut rhythm" in rep and issues[0]["against"] == {"production_script_id": 41,
                                                            "case_id": 7}
    other = _film("evidence_discovery", first_kind="video", first_type="object",
                  tracks=("bridge_mystery_v3",), shot=6.0)
    assert variety_check(other, prev) == []
    # room tone is not music: two films sharing it share no track
    assert variety_check(_film("evidence_discovery", first_type="object",
                               tracks=(), shot=11.0), prev) == []


def test_variety_check_runs_in_review_against_other_cases(db_session, monkeypatch):
    class Critics:
        async def generate_structured(self, role, system, user, images=None):
            return {"score": 75, "problems": [], "summary": "ok"}, _res(role)

    monkeypatch.setattr("app.documentary.production.critics.get_generation_provider",
                        lambda: Critics())
    other_case, other_master = _story(db_session)
    old = ProductionScript(case_id=other_case.id, story_version_id=other_master.id,
                           language="en", script_json=json.dumps(_film("courtroom_outcome")))
    db_session.add(old)
    db_session.commit()
    case, master = _story(db_session)
    row = ProductionScript(case_id=case.id, story_version_id=master.id, language="en",
                           script_json=json.dumps(_film("courtroom_outcome")))
    db_session.add(row)
    db_session.commit()
    rep = asyncio.run(DocumentaryCritics().review(db_session, row, apply=False))
    flagged = [i for i in rep["deterministic"]["issues"] if i["check"] == "template_repeat"]
    assert flagged and flagged[0]["against"]["production_script_id"] == old.id
    assert {"production_script_id": old.id, "case_id": other_case.id} in rep["compared_with"]
    assert not any(i.get("fix") for i in flagged)   # reported, never auto-fixed


def test_repetition_critic_replaces_unjustified_repeats_through_the_tracker():
    assets = _usage_assets()
    shots = [
        {"index": 0, "kind": "image", "asset_id": "VIS_000012", "start": 0.0, "end": 10.0,
         "beat_id": "B01", "motion": "SLOW_PUSH", "tier": 4, "type": "photo", "role": "context"},
        {"index": 1, "kind": "image", "asset_id": "VIS_000013", "start": 10.0, "end": 20.0,
         "beat_id": "B01", "motion": "PAN_LEFT"},
        {"index": 2, "kind": "image", "asset_id": "VIS_000012", "start": 20.0, "end": 30.0,
         "beat_id": "B01", "motion": "SLOW_PULL"},
    ]
    script = {"duration": 30.0, "shots": shots, "candidates": {"B01": ["VIS_000014"]},
              "opening_strategy": "victim_introduction"}
    rep = deterministic_checks(script, assets)
    over = [i for i in rep["issues"] if i.get("type") == "over_limit"]
    assert len(over) == 1 and over[0]["shot"] == 2
    done = apply_fixes(script, rep["issues"], assets)
    assert {"shot": 2, "fix": "replace_repeat", "from": "VIS_000012", "to": "VIS_000014"} in done
    assert "critic fix: unused picture" in shots[2]["fill_reason"]
    assert not [i for i in deterministic_checks(script, assets)["issues"]
                if i["check"] == "repetition" and i.get("type")]


# ---------------------------------------------------------------------------
# H: production-time search for weak sentences
# ---------------------------------------------------------------------------

H_SECTIONS = [
    {"id": "act1", "text": "\n\n".join([
        "Anna Keller was nineteen when she moved to Stendal. She worked in a bakery near the "
        "market square. Every morning she opened the shop before six. The customers knew her "
        "by name and asked about her studies.",
        "On the evening of the third of May the town was quiet. He walked into St Mary's "
        "Church shortly before 7 PM. Nobody saw him leave the building again. The doors were "
        "locked at eight as on every other evening of the year.",
        "The next morning the bakery stayed closed. Her colleagues waited in the cold for an "
        "hour before they called her parents. Nobody had heard from her since the evening "
        "before, and her phone was switched off.",
        "By noon the family reported her missing. The officers took the report seriously from "
        "the first hour. They asked about friends, about routes, about the last time anyone "
        "had seen her in town.",
    ])},
    {"id": "act2", "text": "\n\n".join([
        "Weeks passed without a trace. The posters faded in the rain. The town went back to "
        "its routines, but the family did not, and they kept asking the same questions.",
        "Then a witness came forward with a detail nobody had checked before. It changed the "
        "direction of the whole investigation within a single afternoon.",
        "What happened that evening is still argued about today. The answers that exist are "
        "incomplete, and some of them contradict each other in ways that matter.",
    ])},
]


def _h_requirements():
    beats = [{"beat_id": f"B0{i}", "requirements": [
        {"entity": "market_square", "purpose": "orientation", "priority": "high",
         "acceptable_roles": ["evidence", "context"]}],
        "map_place": None, "date_text": None, "quote": None, "document": None}
        for i in range(1, 6)]
    return {"entities": [e for e in ENTITIES if e["key"] in
                         ("anna_keller", "st_marys_church", "market_square")], "beats": beats}


class SearchGen:
    """visual_director picks, per sentence, an unused candidate of an
    entity the sentence names, else asks for a search for that entity;
    visual_verifier confirms what each picture was found for."""

    def __init__(self, names: dict[str, str], ask_for: set[str]):
        self.names, self.ask_for = names, ask_for
        self.calls: list[tuple[str, dict]] = []

    async def generate_structured(self, role, system, user, images=None):
        data = json.loads(user)
        self.calls.append((role, data))
        if role == "visual_verifier":
            key = (data["claimed_entities"] or [None])[0]
            return {"depicts": data["title"], "subject_type":
                    "person" if key == "anna_keller" else "building",
                    "matches_claim": "yes", "role": "evidence", "period_ok": "yes",
                    "entities": [key], "reveals": [], "quality": 0.8, "watermark": False,
                    "graphic_or_sensitive": False, "confidence": 0.9}, _res(role)
        assert role == "visual_director"
        out, used = [], set()
        for b in data["beats"]:
            shots = []
            for sn in b["narration"]:
                fit = [c for c in b["candidates"] if self.names.get(c["entity"]) in sn["names"]
                       and c["asset_id"] not in used and c["used"] == 0]
                if fit:
                    used.add(fit[0]["asset_id"])
                    shots.append({"command": "NEW_IMAGE", "asset_id": fit[0]["asset_id"],
                                  "from_sentence": sn["n"], "why": f"shows {sn['names'][0]}"})
                    continue
                for name in sn["names"]:
                    key = next(k for k, v in self.names.items() if v == name)
                    if key in self.ask_for:
                        shots.append({"command": "REQUEST_SEARCH", "from_sentence": sn["n"],
                                      "entity": key, "why": f"no picture of {name}"})
            fresh = [c for c in b["candidates"] if c["used"] == 0 and c["asset_id"] not in used]
            if not [s for s in shots if s["command"] != "REQUEST_SEARCH"]:
                if fresh:  # an unused picture of the beat, never a repeat
                    used.add(fresh[0]["asset_id"])
                    shots.insert(0, {"command": "NEW_IMAGE", "from_sentence": 0,
                                     "asset_id": fresh[0]["asset_id"], "why": "the place"})
                else:
                    shots.insert(0, {"command": "KEEP_CURRENT_IMAGE", "from_sentence": 0,
                                     "why": "nothing new to show"})
            shots.sort(key=lambda s: s["from_sentence"])
            out.append({"beat_id": b["beat_id"], "attention": {"listen": 0.8}, "shots": shots})
        return {"beats": out}, _res(role)


class FakeSearch:
    """VisualResearchAgent stand-in: one picture per searched entity."""

    def __init__(self):
        self.calls: list[tuple[list[dict], str]] = []

    async def run(self, db, case, queries, progress=None, found_during="research"):
        from app.documentary import storage
        from app.documentary.visuals.research import next_asset_code

        self.calls.append((queries, found_during))
        done = set()
        for q in queries:
            if q["entity"] in done:
                continue
            done.add(q["entity"])
            code = next_asset_code(db)
            p = _jpg(storage.visuals_dir(case.id) / f"{code}.jpg", color=(70, 90, 110))
            t = _jpg(storage.thumbs_dir(case.id) / f"{code}.jpg", (200, 125))
            db.add(VisualAsset(
                case_id=case.id, asset_code=code, provider="wikimedia", asset_type="photo",
                rights_status="creative_commons", asset_role="context",
                title=f"{q['query']} (Commons)", local_path=str(p), thumbnail_path=str(t),
                width=800, height=500, entities_json=json.dumps([q["entity"]]),
                entity_key=q["entity"], entity_type=q["entity_type"], found_for=q["query"],
                found_during=found_during, relevance_tier=2 if q["entity_type"] == "person" else 3,
                verification_status="unverified"))
            db.commit()
        return {"added": len(done)}


def _h_setup(db, tmp_path, monkeypatch):
    from app.documentary import storage

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    case, master = _story(db, sections=H_SECTIONS)
    case.location = "Stendal, Germany"
    bp = validate_blueprint(_no_contradiction(_good()), H_SECTIONS, PACK)[0]
    bp_row = EditorialBlueprint(case_id=case.id, story_version_id=master.id, status="valid",
                                blueprint_json=json.dumps(bp))
    db.add(bp_row)
    db.commit()
    plan_row = VisualPlan(case_id=case.id, blueprint_id=bp_row.id, status="requirements",
                          requirements_json=json.dumps(_h_requirements()),
                          validation_json="{}")
    db.add(plan_row)
    # the library has the market square (exact place), nothing of the
    # church or of Anna
    for i in range(2):
        code = f"VIS_H{case.id:03d}{i}"
        p = _jpg(storage.visuals_dir(case.id) / f"{code}.jpg")
        db.add(VisualAsset(case_id=case.id, asset_code=code, provider="wikimedia",
                           rights_status="creative_commons", asset_role="context",
                           title=f"Stendal market square {i}", local_path=str(p),
                           entities_json='["market_square"]', entity_key="market_square",
                           entity_type="place", relevance_tier=3, quality_score=0.8,
                           verification_status="verified"))
    db.commit()
    return case, bp_row, plan_row


def test_weak_sentences_become_searches_and_the_found_pictures_are_used(
        db_session, tmp_path, monkeypatch):
    names = {e["key"]: e["name"] for e in ENTITIES}
    gen = SearchGen(names, ask_for={"st_marys_church"})
    monkeypatch.setattr("app.documentary.visuals.director.get_generation_provider", lambda: gen)
    monkeypatch.setattr("app.documentary.visuals.verification.get_generation_provider",
                        lambda: gen)
    case, bp_row, plan_row = _h_setup(db_session, tmp_path, monkeypatch)
    asyncio.run(VisualDirector().create(db_session, case, plan_row, bp_row, None))
    plan = json.loads(plan_row.plan_json)
    church_sentence = "He walked into St Mary's Church shortly before 7 PM."
    assert [(r["entity"], r["sentence"]) for r in plan["search_requests"]] == [
        ("st_marys_church", church_sentence)]

    search = FakeSearch()
    out = asyncio.run(fill_visual_gaps(db_session, case, plan_row, bp_row, None,
                                       profile="preview", agent=search))
    (queries, found_during), = search.calls
    assert found_during == "production_search"
    church_q = [q["query"] for q in queries if q["entity"] == "st_marys_church"]
    # the exact searches first; one stand-in (context) search is always part
    # of a request — here the planner's own context query for the church
    assert church_q == ["St Mary's Church Stendal", "St Mary's Church Stendal exterior",
                        "Stendal old town brick church"]
    assert {q["entity"] for q in queries} == {"st_marys_church", "anna_keller"}
    for ent in ("st_marys_church", "anna_keller"):
        kinds = [q["kind"] for q in queries if q["entity"] == ent]
        assert kinds.count("context") == 1 and kinds[-1] == "context"
    assert out["requests"] == 2 and out["verified"] == 2 and out["redirected_beats"] == ["B01", "B02"]

    found = {a.entity_key: a for a in db_session.query(VisualAsset).filter_by(
        case_id=case.id, found_during="production_search")}
    assert found["st_marys_church"].verification_status == "verified"
    plan = json.loads(plan_row.plan_json)
    beats = {b["beat_id"]: b for b in plan["beats"]}
    n = next(s["n"] for s in beats["B02"]["sentences"] if s["text"] == church_sentence)
    at_church = [s for s in beats["B02"]["shots"] if s.get("from_sentence") == n]
    assert at_church and at_church[0]["asset_id"] == found["st_marys_church"].asset_code
    assert beats["B01"]["shots"][0]["asset_id"] == found["anna_keller"].asset_code
    # only the affected beats were directed again
    director_calls = [d for r, d in gen.calls if r == "visual_director"]
    assert [b["beat_id"] for b in director_calls[-1]["beats"]] == ["B01", "B02"]
    assert director_calls[-1]["beats"][1]["searched"][0]["entity"] == "st_marys_church"

    audit = {a["entity"]: a for a in json.loads(plan_row.validation_json)["search_requests"]}
    church = audit["st_marys_church"]
    assert church["sentence"] == church_sentence and church["source"] == "director"
    assert church["queries"] == church_q and church["used"] is True
    assert church["assets_found"] == [found["st_marys_church"].asset_code]
    anna = audit["anna_keller"]
    assert anna["source"] == "entity_gap" and anna["used"] is True
    assert anna["why"].startswith("names Anna Keller but the library has no usable picture")
    assert "market_square" not in audit   # the library already shows it


def test_found_picture_lands_on_its_sentence_even_when_the_director_ignores_it():
    a = _asset("VIS_000031")
    raw = [{"beat_id": "B02", "shots": [
        {"command": "NEW_IMAGE", "asset_id": "VIS_000001", "from_sentence": 0},
        {"command": "KEEP_CURRENT_IMAGE", "from_sentence": 1},
        {"command": "REQUEST_SEARCH", "from_sentence": 1, "entity": "st_marys_church"}]}]
    out = ensure_found_used(raw, {"B02": [{"from_sentence": 1, "entity": "st_marys_church",
                                           "found": ["VIS_000031"]}]},
                            {"B02": ["VIS_000001", "VIS_000031"]}, {"VIS_000031": a}, {"B02": 4})
    assert [(s["command"], s.get("asset_id"), s["from_sentence"]) for s in out[0]["shots"]] == [
        ("NEW_IMAGE", "VIS_000001", 0), ("NEW_IMAGE", "VIS_000031", 1)]


def test_requests_are_bounded_and_skippable(db_session, monkeypatch):
    weak = [{"beat_id": "B01", "from_sentence": i, "entity": f"e{i}", "entity_name": f"Thing {i}",
             "queries": [], "why": "x", "source": "entity_gap"} for i in range(30)]
    case = type("C", (), {"location": "Stendal, Germany", "country": "Germany"})()
    reqs = build_requests(weak, {"entities": []}, case)
    vd = ai_config.visual_direction
    assert len(reqs) == vd.max_search_requests
    assert all(len(r["queries"]) == vd.queries_per_request for r in reqs)
    assert reqs[0]["queries"][0] == "Thing 0 Stendal"
    person = request_queries({"queries": ["Anna Keller bakery"]}, ENTITIES[0], "Stendal", 3)
    assert [q["query"] for q in person] == ["Anna Keller bakery", "Anna Keller Stendal",
                                            "Anna Keller photograph"]
    monkeypatch.setattr(ai_config.visual_direction, "production_search", False)
    out = asyncio.run(fill_visual_gaps(db_session, None, None, None, None))
    assert out == {"skipped": True, "reason": "visual_direction.production_search is off"}


# ---------------------------------------------------------------------------
# the pipeline: visual_gaps stage, MediaUsage and MusicUsage links
# ---------------------------------------------------------------------------


@needs_ffmpeg
def test_pipeline_runs_the_gap_stage_and_records_usage(db_session, documentary_env, monkeypatch):
    from app.documentary import jobs as J

    # the on-screen host is tested on its own (its memories would carry
    # over into the next film of the shared test database)
    monkeypatch.setattr(ai_config.host, "enabled", False)

    case, master = _story(db_session)
    case.resolution_status = "UNSOLVED"
    db_session.commit()
    job = J.create_job(db_session, case, master, ["en"], "pilot", 60.0, "preview")
    names = [s["name"] for s in json.loads(job.stages_json)]
    assert names.index("visual_gaps") == names.index("visual_plan") + 1
    asyncio.run(J.run_job(job.id))
    db_session.expire_all()
    job = db_session.get(DocumentaryJob, job.id)
    stages = {s["name"]: s for s in json.loads(job.stages_json)}
    assert job.status == "completed", (job.error, stages)
    gaps = stages["visual_gaps"]["detail"]
    assert stages["visual_gaps"]["status"] == "done" and "requests" in gaps
    if gaps["requests"]:
        assert "production_search" in documentary_env.research_calls
    ps_id = json.loads(job.result_json)["renders"]["en"]["production_script_id"]
    ps = db_session.get(ProductionScript, ps_id)
    script = json.loads(ps.script_json)
    assert script["case_status"] == "UNSOLVED" and script["production_type"] == "original"
    assert any(o["kind"] == "status" for o in script["overlays"])
    usage = db_session.query(MediaUsage).filter_by(production_script_id=ps.id).all()
    assert usage and all(u.reason for u in usage) and all(u.appearance >= 1 for u in usage)
    assert {u.film_key for u in usage} == {script["film_key"]}
    # (this short pilot has no music cue; linking: test_link_music_usage_...)
    music = db_session.query(MusicUsage).filter_by(film_key=script["film_key"],
                                                    language="en").all()
    assert all(m.production_script_id == ps.id for m in music)
    assert all(set(m) >= {"track_code", "why", "selection_reason"} for m in script["music"])


def test_link_music_usage_only_touches_that_film_and_language(db_session):
    case, master = _story(db_session)
    rows = [MusicUsage(case_id=case.id, film_key="bp990001", language=l, purpose="bridge")
            for l in ("en", "de")] + [MusicUsage(case_id=case.id, film_key="bp990002",
                                                 language="en", purpose="bridge")]
    db_session.add_all(rows)
    db_session.commit()
    assert link_music_usage(db_session, "bp990001", "en", 555) == 1
    db_session.expire_all()
    assert [r.production_script_id for r in rows] == [555, None, None]



def test_a_map_held_back_from_the_opening_returns_when_allowed():
    """J: the opening (not about the place) cannot start on the map; the
    map of the opening's place comes back in the next beat after
    first_map_not_before_seconds — at the sentence naming the place."""
    from app.documentary.visuals.director import sentence_marks, validate_visual_plan

    bp = {"beats": [
        {"id": "B01", "purpose": "hook", "words": 80, "act": "act1"},
        {"id": "B02", "purpose": "orientation", "words": 120, "act": "act1"},
    ]}
    v1, v2 = _asset("VIS_000001", entity_type="person"), _asset("VIS_000002")
    marks = {"B01": sentence_marks("At dawn a call reached the dispatcher in Tipp City. "
                                   "A woman had been shot."),
             "B02": sentence_marks("The house stood on a quiet street. "
                                   "Tipp City is a small town north of Dayton.")}
    reqs = _reqs(B01={"map_place": "Tipp City, Ohio"})
    raw = {"beats": [
        {"beat_id": "B01", "shots": [{"command": "SHOW_MAP", "from_sentence": 0},
                                     {"command": "NEW_IMAGE", "asset_id": "VIS_000001",
                                      "from_sentence": 1}]},
        {"beat_id": "B02", "shots": [{"command": "NEW_IMAGE", "asset_id": "VIS_000002",
                                      "from_sentence": 0}]},
    ]}
    plan, rep = validate_visual_plan(raw, bp, reqs, {"B01": ["VIS_000001"], "B02": ["VIS_000002"]},
                                     {"VIS_000001": v1, "VIS_000002": v2}, marks,
                                     opening={"strategy": "emergency_call"})
    b = {x["beat_id"]: x for x in plan["beats"]}
    assert all(s["command"] != "SHOW_MAP" for s in b["B01"]["shots"])
    b2 = b["B02"]["shots"]
    cmds = [s["command"] for s in b2]
    assert cmds[:2] == ["NEW_IMAGE", "SHOW_MAP"]
    assert b2[1]["map_place"] == "Tipp City, Ohio" and b2[1]["from_sentence"] == 1
    assert abs(sum(s["share"] for s in b2) - 1.0) < 1e-3
    # a short orientation, then the picture returns
    from app.documentary.visuals.director import DEFERRED_MAP_SECONDS, beat_seconds
    assert b2[1]["share"] * beat_seconds(bp["beats"][1]) <= DEFERRED_MAP_SECONDS + 0.01
    if len(b2) > 2:
        assert b2[2]["command"] == "NEW_IMAGE" and b2[2]["asset_id"] == "VIS_000002"
    assert {"beat": "B02", "deferred_map": "Tipp City, Ohio"} in rep["adjustments"]



def test_a_deferred_map_never_turns_a_keep_into_a_long_map():
    """A beat that opens by keeping the previous picture and then zooms
    on it must not keep/zoom the inserted map for the rest of the beat."""
    from app.documentary.visuals.director import _insert_deferred_map

    shots = [{"command": "KEEP_CURRENT_IMAGE", "from_sentence": 0, "share": 0.4},
             {"command": "ZOOM_EXISTING", "from_sentence": 3, "share": 0.6}]
    marks = [{"n": i, "at": i / 7, "text": t} for i, t in enumerate(
        ["Officers arrived.", "The house was quiet.", "x", "The gun was gone.", "y", "z", "w"])]
    out = _insert_deferred_map(shots, "Tipp City, Ohio", marks, 48.8, 5.0, before="VIS_000008")
    assert out[0]["command"] == "SHOW_MAP" and out[0]["share"] * 48.8 <= 9.01
    assert out[1]["command"] == "KEEP_CURRENT_IMAGE" or out[1].get("asset_id") == "VIS_000008"
    assert all(s["command"] != "ZOOM_EXISTING" or i > 0 and out[i - 1]["command"] != "SHOW_MAP"
               for i, s in enumerate(out))
    assert abs(sum(s["share"] for s in out) - 1.0) < 1e-3


def test_a_place_the_planner_maps_gets_one_map_even_if_the_director_forgot():
    from app.documentary.visuals.director import sentence_marks, validate_visual_plan

    bp = {"beats": [
        {"id": "B01", "purpose": "hook", "words": 80, "act": "act1"},
        {"id": "B02", "purpose": "orientation", "words": 120, "act": "act1"},
        {"id": "B03", "purpose": "timeline", "words": 120, "act": "act1"},
    ]}
    v1, v2, v3 = (_asset("VIS_000001", entity_type="person"), _asset("VIS_000002"),
                  _asset("VIS_000003"))
    marks = {"B01": sentence_marks("A call came from Tipp City. A woman had been shot."),
             "B02": sentence_marks("The house stood on a quiet street. Police arrived fast."),
             "B03": sentence_marks("Days passed. Then came an arrest.")}
    reqs = _reqs(B01={"map_place": "Tipp City, Ohio"})
    raw = {"beats": [{"beat_id": b, "shots": [{"command": "NEW_IMAGE", "asset_id": a,
                                               "from_sentence": 0}]}
                     for b, a in (("B01", "VIS_000001"), ("B02", "VIS_000002"),
                                  ("B03", "VIS_000003"))]}
    cands = {"B01": ["VIS_000001"], "B02": ["VIS_000002"], "B03": ["VIS_000003"]}
    plan, rep = validate_visual_plan(raw, bp, reqs, cands, {"VIS_000001": v1, "VIS_000002": v2,
                                                            "VIS_000003": v3}, marks,
                                     opening={"strategy": "emergency_call"})
    maps = [(b["beat_id"], s["map_place"]) for b in plan["beats"] for s in b["shots"]
            if s["command"] == "SHOW_MAP"]
    assert maps == [("B02", "Tipp City, Ohio")]
    assert {"beat": "B01", "map_wanted": "Tipp City, Ohio"} in rep["adjustments"]


def test_a_map_is_orientation_not_a_backdrop():
    """A map whose next picture is held back (repetition gap) must not run
    on: after motion.max_map_seconds an earned picture takes over, and the
    map credit is on screen exactly while the map is."""
    m = {"duration_seconds": 60.0, "files": {"narration_wav": "n.wav"},
         "timeline": {"beats": [{"beat_id": "B01", "start": 0.0, "end": 14.5},
                                {"beat_id": "B02", "start": 15.0, "end": 59.5}],
                      "blocks": [], "words": [],
                      "sentences": [{"start": t, "end": t + 7.0, "display": f"s{t:g}",
                                     "speech": f"s{t:g}"} for t in range(0, 60, 8)]}}
    caleb = dict(entity_type="person", entity_key="caleb", entities_json='["caleb"]',
                 relevance_tier=2)
    assets = {"VIS_000011": _asset("VIS_000011", **caleb),
              "VIS_000012": _asset("VIS_000012", relevance_tier=1, entity_type="building",
                                   entities_json='["the_house"]'),
              "VIS_000013": _asset("VIS_000013", relevance_tier=1, entity_type="building",
                                   entities_json='["the_driveway"]'),
              "VIS_000020": _asset("VIS_000020", asset_type="map", asset_role="context",
                                   rights_status="open_data", local_path="m.jpg",
                                   spec_json='{"marker": [10, 10]}', relevance_tier=3,
                                   entities_json="[]")}
    # the house pictures were earned in B01 (not offered to B02's swap)
    plan = {"candidates": {"B01": ["VIS_000011", "VIS_000012", "VIS_000013"], "B02": []},
            "beats": [
                {"beat_id": "B01", "shots": [{"command": "NEW_IMAGE", "asset_id": "VIS_000011",
                                              "share": 1.0, "motion": "SLOW_PUSH"}]},
                {"beat_id": "B02", "shots": [
                    {"command": "SHOW_MAP", "map_assets": ["VIS_000020"], "share": 0.25,
                     "map_place": "Tipp City, Ohio", "motion": "MAP_ZOOM"},
                    # Caleb again 15 s later: held back by the repetition gap
                    {"command": "NEW_IMAGE", "asset_id": "VIS_000011", "share": 0.75,
                     "motion": "SLOW_PULL"}]}]}
    s = compose(m, plan, assets, {}, "en")
    shots = s["shots"]
    mp = [x for x in shots if x["kind"] == "map"]
    assert mp[0]["held"][0]["instead_of"] == "VIS_000011"
    assert len(mp) == 1 and mp[0]["start"] == 15.0
    cap = ai_config.motion.max_map_seconds
    assert mp[0]["end"] - mp[0]["start"] <= cap + 3.01
    after = shots[shots.index(mp[0]) + 1:]
    assert after and after[0]["kind"] == "image"
    assert after[0]["asset_id"] in ("VIS_000012", "VIS_000013")
    assert "map varied" in after[0]["fill_reason"]
    assert all(x["end"] - x["start"] <= ai_config.motion.max_hold_seconds + 0.01
               for x in shots)
    osm = [o for o in s["overlays"] if o["kind"] == "credit"
           and o["text"] == ai_config.maps.attribution]
    assert [(o["start"], o["end"]) for o in osm] == [(mp[0]["start"], mp[0]["end"])]


def test_custody_pictures_wait_for_the_arrest():
    """A man in an orange jumpsuit answers the film's question before it
    is asked: custody/court pictures (what the vision check saw, not the
    headline) are swapped out before the beat that tells of the arrest."""
    from app.documentary.visuals import spoilers as SP

    bp = {"beats": [{"id": "B01", "summary": "A 911 call reports an intruder."},
                    {"id": "B02", "summary": "The side door was blocked from inside."},
                    {"id": "B03", "summary": "The autopsy leads to Caleb's arrest within days."}]}
    assert SP.arrest_beat(bp) == "B03"
    assert SP.arrest_beat({"beats": [{"id": "B01", "summary": "Still missing."}]}) is None
    court = _asset("VIS_000031", entity_type="person", entities_json='["caleb"]',
                   relevance_tier=2, title="Caleb Flynn sentenced to life",
                   description="Caleb Flynn in an orange inmate jumpsuit in a courtroom.")
    portrait = _asset("VIS_000032", entity_type="person", entities_json='["ashley"]',
                      relevance_tier=2, title="Daughters speak out as he is sentenced",
                      description="Portrait photo of Ashley Flynn smiling outdoors.")
    house = _asset("VIS_000033", relevance_tier=1, entity_type="building",
                   entities_json='["the_house"]', description="The family home.")
    assert SP.shows_custody(court) and not SP.shows_custody(portrait)
    m = {"duration_seconds": 45.0, "files": {"narration_wav": "n.wav"},
         "timeline": {"beats": [{"beat_id": b, "start": k * 15.0, "end": k * 15.0 + 14.5}
                                for k, b in enumerate(("B01", "B02", "B03"))],
                      "blocks": [], "words": [], "sentences": []}}
    assets = {a.asset_code: a for a in (court, portrait, house)}
    plan = {"candidates": {"B01": ["VIS_000032"], "B02": ["VIS_000031", "VIS_000033"],
                           "B03": ["VIS_000031"]},
            "beats": [{"beat_id": b, "shots": [{"command": "NEW_IMAGE", "asset_id": c,
                                                "share": 1.0, "motion": "SLOW_PUSH"}]}
                      for b, c in (("B01", "VIS_000032"), ("B02", "VIS_000031"),
                                   ("B03", "VIS_000031"))]}
    s = compose(m, plan, assets, {}, "en", arrest_beat="B03")
    seq = [(x["beat_id"], x["asset_id"]) for x in s["shots"]]
    assert seq == [("B01", "VIS_000032"), ("B02", "VIS_000033"), ("B03", "VIS_000031")]
    assert "custody/court before the story reaches the arrest" in s["shots"][1]["fill_reason"]
    assert s["firewall"]["custody"] == ["B01", "B02"]
    # without an arrest in the story nothing is blocked by this rule
    s2 = compose(m, plan, assets, {}, "en")
    assert [x["asset_id"] for x in s2["shots"]][1] == "VIS_000031"


def test_a_critic_black_fix_stays_a_short_pause():
    cap = ai_config.attention.max_black_seconds
    shots = [{"index": 0, "beat_id": "B01", "start": 0.0, "end": 10.0, "kind": "image",
              "asset_id": "VIS_000001", "path": "a.jpg"},
             {"index": 1, "beat_id": "B01", "start": 10.0, "end": 24.0, "kind": "image",
              "asset_id": "VIS_000002", "path": "b.jpg"},
             {"index": 2, "beat_id": "B02", "start": 24.0, "end": 30.0, "kind": "image",
              "asset_id": "VIS_000003", "path": "c.jpg"}]
    script = {"duration": 30.0, "shots": shots, "candidates": {}, "overlays": []}
    done = apply_fixes(script, [{"shot": 1, "severity": "high", "fix": "black"}], {})
    s = script["shots"]
    assert done == [{"shot": 1, "fix": "black", "seconds": cap}]
    assert s[1]["kind"] == "black" and s[1]["end"] - s[1]["start"] == pytest.approx(cap)
    assert s[1]["asset_id"] is None and s[2]["start"] == s[1]["end"]


def test_found_stand_ins_become_candidates_of_their_beat_within_the_firewall():
    from app.documentary.visuals.director import add_found_candidates

    bp = {"beats": [{"id": "B01", "summary": "The dogs never barked.", "purpose": "investigation"},
                    {"id": "B02", "summary": "Caleb's arrest.", "purpose": "timeline"}]}
    dog = _asset("VIS_000041", asset_role="illustration", relevance_tier=5,
                 rights_status="creative_commons", description="A goldendoodle on a couch.")
    court = _asset("VIS_000042", description="A man in an orange jumpsuit in a courtroom.")
    unchecked = _asset("VIS_000043", verification_status="unverified")
    cands = {"B01": []}
    searched = {"B01": [{"from_sentence": 0, "found": ["VIS_000041", "VIS_000042",
                                                       "VIS_000043"]}]}
    added = add_found_candidates(cands, searched,
                                 {a.asset_code: a for a in (dog, court, unchecked)}, bp, "preview")
    assert added == {"B01": ["VIS_000041"]}
    assert [a.asset_code for _, a in cands["B01"]] == ["VIS_000041"]


def test_a_stand_in_is_a_moment_not_a_backdrop():
    """A labelled illustration planned for a whole long beat is cut after
    motion.max_map_seconds; when every case picture is at its limit, case
    material is shown once more rather than stretching the stand-in, and
    the illustration label is on screen exactly while the stand-in is."""
    m = {"duration_seconds": 70.0, "files": {"narration_wav": "n.wav"},
         "timeline": {"beats": [{"beat_id": "B01", "start": 0.0, "end": 19.5},
                                {"beat_id": "B02", "start": 20.0, "end": 69.5}],
                      "blocks": [], "words": [],
                      "sentences": [{"start": t, "end": t + 6.5, "display": f"s{t:g}",
                                     "speech": f"s{t:g}"} for t in range(0, 70, 7)]}}
    house = _asset("VIS_000051", relevance_tier=1, entity_type="building",
                   entities_json='["the_house"]', description="The house.")
    dog = _asset("VIS_000052", asset_role="illustration", relevance_tier=5,
                 rights_status="creative_commons", credit="A / Wikimedia Commons",
                 entities_json="[]", entity_type="object", description="A goldendoodle.")
    plan = {"candidates": {"B01": ["VIS_000051"], "B02": []},
            "beats": [{"beat_id": "B01", "shots": [{"command": "NEW_IMAGE",
                                                    "asset_id": "VIS_000051", "share": 1.0}]},
                      {"beat_id": "B02", "shots": [{"command": "ATMOSPHERIC_BROLL",
                                                    "asset_id": "VIS_000052", "share": 1.0,
                                                    "label": "illustration"}]}]}
    monkey_limit = ai_config.visual_direction.max_evidence_appearances
    try:
        ai_config.visual_direction.max_evidence_appearances = 1  # the house is at its limit
        s = compose(m, plan, {"VIS_000051": house, "VIS_000052": dog}, {}, "en")
    finally:
        ai_config.visual_direction.max_evidence_appearances = monkey_limit
    shots = s["shots"]
    dogs = [x for x in shots if x["asset_id"] == "VIS_000052"]
    assert len(dogs) == 1 and dogs[0]["end"] - dogs[0]["start"] <= ai_config.motion.max_map_seconds + 3.01
    after = shots[shots.index(dogs[0]) + 1]
    assert after["asset_id"] == "VIS_000051" and "over its limit" in after["repeat_reason"]
    labels = [(o["start"], o["end"]) for o in s["overlays"] if o["kind"] == "label"]
    assert labels == [(dogs[0]["start"], dogs[0]["end"])]


def test_a_critic_fix_takes_the_illustration_label_with_the_picture():
    shots = [{"index": 0, "beat_id": "B01", "start": 0.0, "end": 10.0, "kind": "image",
              "asset_id": "VIS_000001", "path": "a.jpg", "role": "evidence"},
             {"index": 1, "beat_id": "B01", "start": 10.0, "end": 18.0, "kind": "image",
              "asset_id": "VIS_000002", "path": "b.jpg", "role": "illustration"}]
    script = {"duration": 18.0, "language": "de", "shots": shots, "candidates": {},
              "overlays": [{"kind": "label", "text": "Symbolbild", "start": 10.0, "end": 18.0}]}
    apply_fixes(script, [{"shot": 1, "severity": "high", "fix": "keep_previous"}], {})
    assert [s["asset_id"] for s in script["shots"]] == ["VIS_000001"]
    assert not [o for o in script["overlays"] if o["kind"] == "label"]
