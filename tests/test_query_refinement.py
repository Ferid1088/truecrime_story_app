"""P11: model-guided query refinement for low-yield entities, budgeted (calls and EUR per
case); the model reads only text (titles/captions/sources of the entity's own sample results,
never an image) and writes better queries using the dossier's aliases/likely sources."""

import asyncio
import json
import uuid

from app.agents.visual_refinement import QueryRefinementAgent
from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary.visuals.research import entity_yield, low_yield_entities
from app.providers.generation.base import GenerationResult


def _asset(entity_key, status, **kw):
    return VisualAsset(entity_key=entity_key, verification_status=status, asset_type="photo",
                      provider="wikimedia", **kw)


# ---- entity_yield / low_yield_entities ---------------------------------------------------------

def test_entity_yield_counts_only_this_entitys_checked_pictures():
    assets = ([_asset("kabar_sheath", "rejected")] * 9 + [_asset("kabar_sheath", "verified")] * 1
             + [_asset("kabar_sheath", "unverified")] * 20
             + [_asset("bryan_kohberger", "verified")] * 8 + [_asset("bryan_kohberger", "rejected")] * 2)
    out = entity_yield(assets)
    assert out["kabar_sheath"] == {"checked": 10, "useful": 1, "share": 0.1}
    assert out["bryan_kohberger"]["share"] == 0.8


def test_an_entity_below_the_minimum_sample_is_never_flagged():
    assets = [_asset("kabar_sheath", "rejected")] * 5
    assert low_yield_entities(assets, min_sample=10, min_share=0.15) == []


def test_a_low_share_entity_after_enough_samples_is_flagged():
    assets = [_asset("kabar_sheath", "rejected")] * 9 + [_asset("kabar_sheath", "verified")]
    assert low_yield_entities(assets, min_sample=10, min_share=0.15) == ["kabar_sheath"]


def test_a_well_covered_entity_is_not_flagged():
    assets = [_asset("bryan_kohberger", "verified")] * 5 + [_asset("bryan_kohberger", "rejected")] * 5
    assert low_yield_entities(assets, min_sample=10, min_share=0.15) == []


# ---- the refinement agent: budgeted, text-only input -------------------------------------------

class FakeGen:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    async def generate_structured(self, role, system, user, images=None):
        self.calls.append((role, json.loads(user)))
        return self.answer, GenerationResult(text="{}", model="m/fake", provider="fake")


def _case(db):
    case = Case(canonical_title="Refine", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


ENTITY = {"key": "kabar_sheath", "name": "Ka-Bar leather knife sheath", "type": "object",
         "aliases": ["USMC Ka-Bar sheath"], "likely_sources": ["Latah County court exhibits"]}


def test_refine_sends_only_text_never_an_image(db_session):
    case = _case(db_session)
    agent = QueryRefinementAgent.__new__(QueryRefinementAgent)
    agent.gen = FakeGen({"queries": ["Ka-Bar sheath courtroom exhibit photo Latah County"],
                        "diagnosis": "too generic"})
    sample = [VisualAsset(title="A knife for sale", caption="eBay listing", source_name="ebay.com")]
    out = asyncio.run(agent.refine(db_session, case, ENTITY, sample))
    assert out == ["Ka-Bar sheath courtroom exhibit photo Latah County"]
    (role, payload), = agent.gen.calls
    assert role == "visual_query_refiner"
    assert payload["entity"]["aliases"] == ["USMC Ka-Bar sheath"]
    assert payload["sample_results"] == [{"title": "A knife for sale", "caption": "eBay listing",
                                         "source": "ebay.com"}]
    assert "images" not in payload and "image" not in json.dumps(payload).lower().replace("images", "")


def test_refine_low_yield_entities_stops_at_max_calls(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "refinement_min_sample", 10)
    monkeypatch.setattr(ai_config.visual_search, "refinement_min_share", 0.15)
    monkeypatch.setattr(ai_config.visual_search, "refinement_max_calls_per_case", 1)
    case = _case(db_session)
    requirements = {"entities": [
        {"key": "kabar_sheath", "name": "Ka-Bar sheath", "type": "object"},
        {"key": "white_sedan", "name": "White sedan", "type": "vehicle"},
    ]}
    assets = ([_asset("kabar_sheath", "rejected") for _ in range(9)] + [_asset("kabar_sheath", "verified")]
             + [_asset("white_sedan", "rejected") for _ in range(9)] + [_asset("white_sedan", "verified")])
    agent = QueryRefinementAgent.__new__(QueryRefinementAgent)
    agent.gen = FakeGen({"queries": ["a better query"]})
    out = asyncio.run(agent.refine_low_yield_entities(db_session, case, requirements, assets))
    assert out["candidates"] == ["kabar_sheath", "white_sedan"]
    assert out["calls_made"] == 1 and out["stopped_because"] == "max_calls_per_case reached"
    assert len(out["refined"]) == 1


def test_refine_low_yield_entities_reports_nothing_to_do(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.visual_search, "refinement_min_sample", 10)
    case = _case(db_session)
    agent = QueryRefinementAgent.__new__(QueryRefinementAgent)
    agent.gen = FakeGen({"queries": []})
    out = asyncio.run(agent.refine_low_yield_entities(db_session, case, {"entities": []}, []))
    assert out["candidates"] == [] and out["calls_made"] == 0
    assert out["stopped_because"] == "no low-yield entity"
    assert agent.gen.calls == []
