"""The person-photo inspector: who is named for the first time, which approved photos of them give nothing away there, and the table the
main planner reads."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

from app.db.models import Case, EditorialBlueprint, PersonPhotoHint, StoryVersion, VisualAsset, VisualPlan
from app.documentary.mvp import direct as DR
from app.documentary.mvp import people as PE
from app.documentary.production import picture_rules as PR

SENTENCES = [{"start": 0.0, "end": 8.0, "display": "A call came in about a house in Tipp City."},
             {"start": 20.0, "end": 30.0, "display": "Ashley Flynn had taught children for years."},
             {"start": 60.0, "end": 70.0, "display": "Her husband, Caleb, called 911."},
             {"start": 90.0, "end": 100.0, "display": "The Flynn family lived on Cunningham Court."}]
PERSONS = [{"key": "ashley_flynn", "name": "Ashley Flynn", "type": "person"}, {"key": "caleb_flynn", "name": "Caleb Flynn", "type": "person"},
           {"key": "never_named", "name": "Never Named", "type": "person"}]


def test_the_first_sentence_that_names_a_person_by_full_name_or_first_name_is_the_first_mention_a_shared_surname_is_not():
    got = PE.first_mentions(SENTENCES, PERSONS, lambda t: "B01")
    assert [(m.key, m.start) for m in got] == [("ashley_flynn", 20.0), ("caleb_flynn", 60.0)], "'the Flynn family' does not name Caleb"
    assert got[0].name == "Ashley Flynn" and got[0].beat_id == "B01"


def photo(code, entity, people=(), kind="photo", rank=0, shows="", db_id=None):
    asset = VisualAsset(id=db_id, case_id=1, asset_code=code, asset_type=kind, local_path=f"{code}.jpg", entity_key=entity,
                        sha256=f"sha-{code}", description=shows,
                        spec_json=json.dumps({"picture_card": {"shows": shows, "people": [{"entity": p} for p in people], "sha": f"sha-{code}",
                                                               "setting": "outdoors", "kind": "portrait"}}))
    return DR.Item(asset, "context", entity, rank, [])


def test_the_candidates_are_the_approved_photos_of_the_person_solo_portraits_first_and_never_a_clip():
    items = {"B01": [photo("GROUP", "caleb_flynn", ["ashley_flynn", "caleb_flynn"]), photo("SOLO", "ashley_flynn", ["ashley_flynn"]),
                     photo("CLIP", "ashley_flynn", ["ashley_flynn"], kind="video"), photo("OTHER", "caleb_flynn", ["caleb_flynn"])],
             "*": [photo("EARLIER", "ashley_flynn", ["ashley_flynn", "caleb_flynn"])]}
    assert [i.code for i in PE.candidates(items, "ashley_flynn")] == ["SOLO", "GROUP", "EARLIER"]


def test_the_agents_answer_keeps_only_photos_it_was_asked_about_in_its_order_and_a_wrong_answer_is_refused():
    got = PE.valid({"photos": [{"asset": "B", "ok": True, "reason": "calm portrait"}, {"asset": "X", "ok": True},
                               {"asset": "A", "ok": False, "reason": "jail uniform"}, {"asset": "B", "ok": True}]}, {"A", "B"})
    assert got == [{"asset": "B", "ok": True, "reason": "calm portrait"}, {"asset": "A", "ok": False, "reason": "jail uniform"}]
    assert PE.valid({"nope": 1}, {"A"}) is None and PE.valid("x", {"A"}) is None


def _film(db):
    case = Case(canonical_title="Inspector", slug=f"pe-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, validation_json="{}")
    db.add(plan)
    db.commit()
    return case, plan


def _items(db, case, specs):
    out = []
    for code, entity, people, shows in specs:
        a = VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", local_path=f"{code}.jpg", entity_key=entity,
                        sha256=f"sha-{code}", description=shows,
                        spec_json=json.dumps({"picture_card": {"shows": shows, "people": [{"entity": p} for p in people], "sha": f"sha-{code}",
                                                               "setting": "x", "kind": "portrait"}}))
        db.add(a)
        db.commit()
        out.append(DR.Item(a, "context", entity, 0, []))
    return {"B01": out}


def test_the_table_has_the_text_position_the_photo_and_its_rank_a_spoiler_is_left_out_and_a_second_run_replaces_the_first(db_session, monkeypatch):
    case, plan = _film(db_session)
    items = _items(db_session, case, [("CALM", "ashley_flynn", ["ashley_flynn"], "Ashley smiles outdoors."),
                                      ("GRAVE", "ashley_flynn", ["ashley_flynn"], "A grave with flowers."),
                                      ("HURT", "ashley_flynn", ["ashley_flynn"], "She lies on the floor with a visible wound.")])
    asked = []

    async def fake_run_agent(name, gen, body, images=None):
        payload = json.loads(body)
        asked.append((payload["person"]["key"], [p["asset"] for p in payload["photos"]], payload["first_mention"]["seconds"]))
        return {"photos": [{"asset": "GRAVE", "ok": False, "reason": "a grave: the death has not been told yet"},
                           {"asset": "CALM", "ok": True, "reason": "a calm portrait"}]}, SimpleNamespace(model="m", provider="p", usage=None)

    monkeypatch.setattr(PE, "run_agent", fake_run_agent)
    monkeypatch.setattr(PE, "get_generation_provider", lambda: object())
    monkeypatch.setattr(PE, "stamp_run", lambda *a: None)
    monkeypatch.setattr(PE, "reveal_blocked", lambda *a: False)
    result = asyncio.run(PE.inspect(db_session, case, plan, "en", SENTENCES, PERSONS, items, lambda t: "B01", 1, {"B01": 0}))
    rows = result.rows
    assert [w["entity_key"] for w in result.without] == ["caleb_flynn"] and result.without[0]["why"] == ["no approved photo of this person"]
    assert asked == [("ashley_flynn", ["CALM", "GRAVE"], 20.0)] or sorted(asked[0][1]) == ["CALM", "GRAVE"], "the injury photo was never asked about"
    assert [(r["entity_key"], r["asset"], r["rank"], r["mention_start"]) for r in rows] == [("ashley_flynn", "CALM", 1, 20.0)]
    table = db_session.query(PersonPhotoHint).filter_by(case_id=case.id).all()
    assert len(table) == 1 and table[0].mention_text.startswith("Ashley Flynn had taught") and table[0].reason == "a calm portrait"
    again = asyncio.run(PE.inspect(db_session, case, plan, "en", SENTENCES, PERSONS, items, lambda t: "B01", 1, {"B01": 0}))
    assert db_session.query(PersonPhotoHint).filter_by(case_id=case.id).count() == 1 and len(again.rows) == 1
    assert PE.hints_of(db_session, case.id, "en")[0]["asset"] == "CALM"


def test_a_photo_the_firewall_blocks_at_that_beat_is_not_even_offered_to_the_agent(db_session, monkeypatch):
    case, plan = _film(db_session)
    items = _items(db_session, case, [("OK", "ashley_flynn", ["ashley_flynn"], "A portrait."), ("LATER", "ashley_flynn", ["ashley_flynn"], "At trial.")])
    offered = []

    async def fake_run_agent(name, gen, body, images=None):
        offered.extend(p["asset"] for p in json.loads(body)["photos"])
        return {"photos": [{"asset": a, "ok": True, "reason": "fine"} for a in offered]}, SimpleNamespace(model="m", provider="p", usage=None)

    monkeypatch.setattr(PE, "run_agent", fake_run_agent)
    monkeypatch.setattr(PE, "get_generation_provider", lambda: object())
    monkeypatch.setattr(PE, "stamp_run", lambda *a: None)
    monkeypatch.setattr(PE, "reveal_blocked", lambda db, g, order, beat, asset: asset.asset_code == "LATER")
    asyncio.run(PE.inspect(db_session, case, plan, "en", SENTENCES, PERSONS[:1], items, lambda t: "B01", 1, {"B01": 0}))
    assert offered == ["OK"]


def test_the_planner_gets_the_photos_of_the_persons_named_first_inside_each_beat():
    hints = [{"asset": "A1", "person_name": "Ashley Flynn", "mention_start": 20.0}, {"asset": "C1", "person_name": "Caleb Flynn", "mention_start": 60.0}]
    assert DR.first_mentions_in(hints, {"start": 0.0, "end": 50.0}) == {"A1": "Ashley Flynn"}
    assert DR.first_mentions_in(hints, {"start": 50.0, "end": 80.0}) == {"C1": "Caleb Flynn"}


def test_at_a_mention_the_photo_the_inspector_ranked_first_is_shown_even_when_another_fits_the_words_better():
    shots = [{"kind": "image", "start": 0.0, "end": 60.0, "asset_id": "X", "entities": ["caleb_flynn"], "beat_id": "B01"}]
    sentences = [{"start": 20.0, "end": 30.0, "display": "Ashley Flynn taught art to children."}]
    pool = [SimpleNamespace(asset_code="FITS"), SimpleNamespace(asset_code="INSPECTOR")]
    PR.cover_people(shots, sentences, {"ashley_flynn": ["FITS", "INSPECTOR"]}, lambda beat: pool,
                    lambda target, a, start, end, why: {**target, "asset_id": a.asset_code, "entities": ["ashley_flynn"], "start": start, "end": end},
                    lambda t: "B01", lambda a: ["ashley_flynn"], lambda a, text: 5 if a.asset_code == "FITS" else 0,
                    {"ashley_flynn": ["INSPECTOR"]})
    assert next(s for s in shots if s["start"] <= 25 < s["end"])["asset_id"] == "INSPECTOR"


def test_a_person_whose_every_photo_is_refused_is_reported_with_the_agents_reasons(db_session, monkeypatch):
    case, plan = _film(db_session)
    items = _items(db_session, case, [("COURT", "ashley_flynn", ["ashley_flynn"], "She testifies on the witness stand.")])

    async def fake_run_agent(name, gen, body, images=None):
        return {"photos": [{"asset": "COURT", "ok": False, "reason": "a courtroom: no trial has been told yet"}]}, SimpleNamespace(model="m", provider="p", usage=None)

    monkeypatch.setattr(PE, "run_agent", fake_run_agent)
    monkeypatch.setattr(PE, "get_generation_provider", lambda: object())
    monkeypatch.setattr(PE, "stamp_run", lambda *a: None)
    monkeypatch.setattr(PE, "reveal_blocked", lambda *a: False)
    result = asyncio.run(PE.inspect(db_session, case, plan, "en", SENTENCES, PERSONS[:1], items, lambda t: "B01", 1, {"B01": 0}))
    assert result.rows == [] and result.without[0]["why"] == ["COURT: a courtroom: no trial has been told yet"]
    assert db_session.query(PersonPhotoHint).filter_by(case_id=case.id).count() == 0


def test_an_older_rounds_key_with_the_ent_prefix_is_the_same_person():
    item = photo("OLD", "ent_jill_smith", ["ent_jill_smith", "caleb_flynn"])
    assert item.people == ["jill_smith", "caleb_flynn"] and DR.plain_key("jill_smith") == "jill_smith"
