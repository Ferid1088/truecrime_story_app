# ruff: noqa: F811  (`setup` is the flow tests' fixture, imported)
"""The draft's evidence per act and the beats written together: an act package holds what its act needs and
what stands against it, nothing of the other acts; consecutive beats can be one part; the full block stays
the default until a measurement says otherwise."""

import asyncio
import json

import numpy as np
from test_language_pipeline_flow import ACTS, FakeGen, _pipeline, setup  # noqa: F401

from app.core.ai_config import ai_config
from app.db.models import StoryPlan, StoryVersion
from app.language_pipeline.assign import related_to_contradictions
from app.language_pipeline.materials import SharedMaterial, dump
from app.language_pipeline.stages import LanguageStages


def fact(i, claim, sources=(1,), **extra):
    return {"id": f"F{i:03d}", "claim": claim, "evidence_strength": "strong", "uncertain": False,
            "quote_status": None, "supporting_text": None, "source_ids": list(sources), **extra}


def material(facts, contradictions=(), timeline=(), acts=None):
    pack = {"facts": facts, "timeline": list(timeline), "contradictions": list(contradictions), "approved_context": []}
    acts = acts or [{"id": "act1", "title": "A", "purpose": "p", "key_beats": ["a", "b", "c"], "target_words": 100,
                     "evidence_ids": ["F001", "C001", "T001"], "do_not_reveal": []},
                    {"id": "act2", "title": "B", "purpose": "q", "key_beats": ["d"], "target_words": 100,
                     "evidence_ids": ["F002"], "do_not_reveal": []}]
    return SharedMaterial(case_id=1, plan_id=1, plan={"title": "T", "acts": acts}, pack=pack, draft_pack=pack,
                          fingerprint="f")


def test_an_act_package_holds_its_facts_its_timeline_its_contradictions_and_what_stands_against_them():
    facts = [fact(1, "Bridget heard a laugh."), fact(2, "The hatchet had a broken handle."),
             fact(3, "Lizzie said she was downstairs."), fact(4, "Another act's fact."), fact(5, "An extra fact.")]
    contradictions = [{"id": "C001", "topic": "The laugh", "description": "heard vs denied", "severity": "medium"},
                      {"id": "C002", "topic": "Elsewhere", "description": "x", "severity": "low"}]
    timeline = [{"id": "T001", "event_date": "1892-08-04", "claim": "Bridget heard a laugh."},
                {"id": "T002", "event_date": "1893-06-05", "claim": "The trial opened."}]
    m = material(facts, contradictions, timeline)
    act = m.acts[0]
    block = m.act_package(act, extra_ids=["F005"], related={"C001": ["F003"], "C002": ["F004"]})
    assert block.startswith("ACT PACKAGE act1")
    body = json.loads(block.split("\n", 1)[1])
    assert [f["id"] for f in body["evidence"]] == ["F001", "F003", "F005"]          # assigned, against, extra
    assert [c["id"] for c in body["contradictions"]] == ["C001"] and [t["id"] for t in body["timeline"]] == ["T001"]
    assert "Another act's fact" not in block and "The hatchet" not in block and "trial opened" not in block
    assert set(body) == {"plan", "evidence", "timeline", "contradictions"}
    assert len(block) < len(m.draft_block())


def test_facts_that_say_the_same_are_listed_once_with_the_sources_of_both():
    m = material([fact(1, "The door was locked.", (1, 2)), fact(2, "  the DOOR was  locked. ", (2, 3))],
                 acts=[{"id": "act1", "key_beats": ["a"], "target_words": 1, "evidence_ids": ["F001", "F002"]}])
    body = json.loads(m.act_package(m.acts[0], [], {}).split("\n", 1)[1])
    assert len(body["evidence"]) == 1 and body["evidence"][0]["source_ids"] == [1, 2, 3]
    assert body["evidence"][0]["supporting_sources"] == 3


class Embedder:
    async def embed(self, texts):
        keys = ["laugh", "handle", "downstairs"]
        return np.array([[1.0 if k in t.lower() else 0.0 for k in keys] for t in texts], dtype=np.float32) + 0.01


def test_the_facts_nearest_to_a_contradiction_are_found_by_meaning():
    facts = [fact(1, "Bridget heard a laugh upstairs."), fact(2, "The hatchet handle."), fact(3, "She was downstairs.")]
    contradictions = [{"id": "C001", "topic": "laugh", "description": "a laugh vs downstairs", "severity": "medium"}]
    related = asyncio.run(related_to_contradictions(material(facts, contradictions), Embedder(), 2))
    assert set(related["C001"]) == {"F001", "F003"}
    assert asyncio.run(related_to_contradictions(material(facts, contradictions), None, 2)) == {}
    assert asyncio.run(related_to_contradictions(material(facts, contradictions), Embedder(), 0)) == {}


def test_consecutive_beats_are_grouped_by_the_configured_size_and_their_budget_adds_up():
    act = {"id": "a", "key_beats": ["1", "2", "3", "4", "5"]}
    assert LanguageStages.scene_groups(act, 1) == [(0, 1), (1, 1), (2, 1), (3, 1), (4, 1)]
    assert LanguageStages.scene_groups(act, 2) == [(0, 2), (2, 2), (4, 1)]
    assert LanguageStages.scene_groups(act, 3) == [(0, 3), (3, 2)]
    assert LanguageStages.scene_groups({"id": "x"}, 3) == [(0, 1)]
    m = material([], acts=[{"id": "a", "key_beats": ["1", "2", "3", "4"], "target_words": 100}])
    one, three = (LanguageStages.scene_target_words(m, m.acts[0], n) for n in (1, 3))
    assert three == 3 * one


def _beats(db_session, case, n):
    plan = db_session.query(StoryPlan).filter_by(case_id=case.id).one()
    data = json.loads(plan.plan_json)
    for act in data["acts"]:
        act["key_beats"] = [f"{act['id']} beat {i}" for i in range(1, n + 1)]
    plan.plan_json = json.dumps(data)
    db_session.commit()


def test_with_two_beats_to_a_call_the_draft_makes_fewer_calls_and_hands_the_writer_one_part(setup, db_session, monkeypatch):
    _beats(db_session, setup, 5)
    monkeypatch.setattr(ai_config.language_pipeline.draft_packaging, "scenes_per_call", 2)
    gen = FakeGen()
    out = asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    calls = [c["user"] for c in gen.calls if c["role"] == "language_draft_writer"]
    assert len(calls) == 2 * 3                                                    # 5 beats in 3 calls per act
    first = calls[0]
    assert first["beat"] == "act1 beat 1 | act1 beat 2" and first["beat_index"] == 1
    assert first["beats_after_in_this_act"] == ["act1 beat 3", "act1 beat 4", "act1 beat 5"]
    assert calls[2]["beat"] == "act1 beat 5" and calls[2]["beat_index"] == 5
    single = LanguageStages.scene_target_words(
        asyncio.run(_pipeline(gen)._material(db_session, db_session.get(type(setup), setup.id))), json.loads(
            db_session.query(StoryPlan).filter_by(case_id=setup.id).order_by(StoryPlan.id.desc()).first().plan_json)["acts"][0], 1)
    assert first["unit_target_words"] == 2 * single
    db_session.expire_all()
    scenes = json.loads(db_session.get(StoryVersion, out["draft_version_id"]).narrative_structure)["pipeline"]["scenes"]
    assert [s["index"] for s in scenes if s["act"] == "act1"] == [0, 2, 4]       # a part is stored at its first beat


def test_in_act_mode_each_call_carries_the_package_of_its_act_and_a_cache_key_of_its_own(setup, db_session, monkeypatch):
    monkeypatch.setattr(ai_config.language_pipeline.draft_packaging, "mode", "act")
    gen = FakeGen()
    asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    drafts = [c for c in gen.calls if c["role"] == "language_draft_writer"]
    assert [c["prefix"].split("\n")[0] for c in drafts] == ["ACT PACKAGE act1 (JSON: the evidence of this act only)",
                                                           "ACT PACKAGE act2 (JSON: the evidence of this act only)"]
    assert drafts[0]["cache_key"].endswith(":act1") and drafts[1]["cache_key"].endswith(":act2")
    assert dump(drafts[0]["user"]) and "SHARED MATERIAL" not in drafts[0]["prefix"]


def test_the_default_is_the_full_block_one_beat_per_call_until_a_measurement_says_otherwise(setup, db_session):
    cfg = ai_config.language_pipeline.draft_packaging
    assert (cfg.mode, cfg.scenes_per_call) == ("full", 1)
    gen = FakeGen()
    asyncio.run(_pipeline(gen).run_case(setup.id, ["en"], 60))
    assert all(c["prefix"].startswith("SHARED MATERIAL") for c in gen.calls if c["role"] == "language_draft_writer")
    assert ACTS == ["act1", "act2"]
